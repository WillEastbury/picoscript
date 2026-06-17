# PicoScript Event-Driven Runtime: Thread-Pooled Native Server

## Context

PicoForge has proven that PicoScript handlers compiled via `lower_to_c()` execute in
**3–9 microseconds** (MSVC /O2, Windows x86_64). The next step is eliminating the
Python host entirely: a single native binary that serves HTTP using a thread-pooled
event-driven architecture, with PicoWAL storage compiled in.

This spec defines the implementation work inside the `picoscript` repo.

---

## What to Build

### 1. `ON` keyword — event-driven blocks in PicoScript

Add a new top-level construct to all frontends (Python, BASIC, C-style):

```picoscript
# Python frontend syntax
ON Net.Connection:
    conn = Net.Accept()
    raw = Net.Read(conn, 0)
    # ... handle request ...
    Net.Write(conn, Resp.Collect())
    Net.Close(conn)
END ON
```

#### AST
Add `OnBlock(event_ns, event_method, body)` node alongside `Sub`, `If`, `While`, etc.

#### Lowerer
`ON Net.Connection:` lowers to:
1. A labelled subroutine (`__on_net_connection:`)
2. A startup registration call: `host("Net", "Register", EVENT_CONNECTION, label_addr)`
3. The body is a normal code block that ends with an implicit `ret` (worker returns to pool)

#### IL
No new IL ops needed. `ON` is sugar over:
```
host Net.Register(EVENT_ID, handler_label)  ; at startup
label __on_net_connection:
  ; ... body ...
  ret
```

#### Frontends
- **Python**: `ON Net.Connection:` ... `END ON` (indentation-delimited like `def`)
- **BASIC**: `ON NET.CONNECTION:` ... `END ON`
- **C**: `on(Net.Connection) { ... }`

---

### 2. Thread Pool Runtime (C, in `vm/picovm_pool.c`)

New file implementing the native event loop with pre-allocated workers:

```c
// vm/picovm_pool.c — thread-pooled event-driven runtime

#include "picovm.h"
#include <stdint.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <pthread.h>
#endif

#define PV_POOL_MAX 64
#define PV_POOL_DEFAULT 8
#define PV_ARENA_SIZE (512 * 1024)

typedef struct {
    pv_ctx ctx;                     // Each worker owns its own VM context
    uint8_t arena[PV_ARENA_SIZE];   // Pre-allocated arena (no malloc per request)
    int conn_fd;                    // Injected connection handle
    int busy;                       // 0 = sleeping, 1 = handling
} pv_worker;

typedef struct {
    int server_fd;
    int port;
    int pool_size;
    pv_worker workers[PV_POOL_MAX];
    // The compiled handler function pointer
    int64_t (*handler)(pv_ctx *ctx);
} pv_server;

// Platform-specific thread/event primitives
#ifdef _WIN32
// IOCP or simple thread pool with WaitForSingleObject
#else
// pthread_cond_wait / epoll
#endif
```

#### Worker lifecycle:
```
1. Startup: create N workers, each with pre-allocated pv_ctx + arena
2. Workers sleep (WFI on PIOS / futex on Linux / Event on Windows)
3. Accept loop: accept() → find idle worker → inject conn_fd → wake worker
4. Worker: runs handler(ctx) → resets arena (ctx.arena_top = base) → marks idle → sleeps
5. Repeat
```

#### Arena reset (zero-cost cleanup):
```c
void pv_worker_reset(pv_worker *w) {
    w->ctx.arena_top = 0x8000;  // Reset to base (same as pv_init)
    w->ctx.out_len = 0;         // Clear output buffer
    w->ctx.http_status = -1;    // Reset status
    w->busy = 0;                // Return to pool
}
```

No malloc, no free, no GC. Just one integer assignment to reclaim all per-request memory.

---

### 3. Net.* Host Hook Implementations

Add to `vm/picovm.c` (or a new `vm/picovm_net.c`):

| Hook Code | Function | Implementation |
|-----------|----------|----------------|
| `Net.Listen` (new, assign 0x50) | `pv_net_listen(ctx, port)` | `socket() + bind() + listen()` |
| `Net.Accept` (new, 0x51) | `pv_net_accept(ctx, server_fd)` | `accept()` → returns conn fd |
| `Net.Read` (new, 0x52) | `pv_net_read(ctx, conn_fd, buf_size)` | `recv()` → span in arena |
| `Net.Write` (new, 0x53) | `pv_net_write(ctx, conn_fd, span)` | `send()` from span bytes |
| `Net.Close` (new, 0x54) | `pv_net_close(ctx, fd)` | `close()` / `closesocket()` |
| `Net.PoolSize` (new, 0x55) | `pv_net_pool_size(ctx, n)` | Configure worker count |
| `Net.Register` (new, 0x56) | `pv_net_register(ctx, event, label)` | Register ON handler |

Platform abstraction:
```c
#ifdef _WIN32
#define pv_socket_close(fd) closesocket(fd)
#define pv_socket_read(fd, buf, len) recv(fd, buf, len, 0)
#define pv_socket_write(fd, buf, len) send(fd, buf, len, 0)
#else
#define pv_socket_close(fd) close(fd)
#define pv_socket_read(fd, buf, len) read(fd, buf, len)
#define pv_socket_write(fd, buf, len) write(fd, buf, len)
#endif
```

---

### 4. PicoWAL Compiled In

Move (or symlink) `picowal_api.h` and `picowal_api.c` into `vm/`:

```
picoscript/vm/
  picovm.h          (existing)
  picovm.c          (existing — add picowal dispatch to pv_default_host)
  picovm_pool.c     (NEW — thread pool server)
  picovm_net.c      (NEW — Net.* socket implementations)
  picowal_api.h     (from picowal repo)
  picowal_api.c     (from picowal repo)
  picowal_store_fs.c (filesystem backend for picowal)
```

Wire into `pv_default_host`:
```c
case 0x7066: /* Storage.ReadCard */
    rd_val = picowal_get(&ctx->store, pack, card_id, 
                         ctx->mem + ctx->arena_top, PV_ARENA_SIZE - ctx->arena_top);
    if (rd_val > 0) {
        ctx->regs[rd] = pv_new_span(ctx, ctx->arena_top, rd_val);
        ctx->arena_top += rd_val;
    } else {
        ctx->regs[rd] = 0;
    }
    break;
```

---

### 5. HTTP Response Framing

The `Resp.*` calls build the response in `ctx->out`. The pool worker needs to
frame it as HTTP/1.1 before sending:

```c
void pv_send_http_response(int conn_fd, pv_ctx *ctx) {
    char header[256];
    int hlen = snprintf(header, sizeof(header),
        "HTTP/1.1 %d OK\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Connection: keep-alive\r\n"
        "\r\n",
        ctx->http_status > 0 ? ctx->http_status : 200,
        ctx->http_type ? ctx->http_type : "application/json",
        ctx->out_len);
    pv_socket_write(conn_fd, header, hlen);
    pv_socket_write(conn_fd, (char*)ctx->out, ctx->out_len);
}
```

Or — since PicoWeb is IN PicoScript — the handler itself builds the full HTTP
response bytes via `Resp.Write("HTTP/1.1 200 OK\r\n...")` and the worker just
does a raw `send()` of `ctx->out`.

---

### 6. Standalone Runner (replaces picovm_run.c for servers)

New `vm/picovm_serve.c`:

```c
// picovm_serve.c — run a compiled PicoScript server binary
// Links: picovm.c + picovm_pool.c + picovm_net.c + picowal_api.c + generated handlers

#include "picovm.h"

extern int64_t handler_picoweb_runtime(pv_ctx *ctx);  // The ON block
extern int64_t handler_entity_list(pv_ctx *ctx);
extern int64_t handler_entity_get(pv_ctx *ctx);
// ... etc

int main(int argc, char **argv) {
    int port = 8100;
    int pool_size = 8;
    
    // Parse args
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i+1 < argc) port = atoi(argv[++i]);
        if (strcmp(argv[i], "--workers") == 0 && i+1 < argc) pool_size = atoi(argv[++i]);
    }
    
    // Init server with thread pool
    pv_server server;
    pv_server_init(&server, port, pool_size, handler_picoweb_runtime);
    
    printf("PicoForge native: port=%d workers=%d\n", port, pool_size);
    
    // Run (blocks forever, dispatching events to pool workers)
    pv_server_run(&server);
    return 0;
}
```

Build command:
```bash
cl /O2 /Fe:picoforge.exe ^
    generated_handlers.c picoweb_runtime.c ^
    picovm.c picovm_pool.c picovm_net.c picowal_api.c picowal_store_fs.c ^
    ws2_32.lib
```

Result: **single ~250KB .exe** — HTTP server + CRUD handlers + storage. No dependencies.

---

### 7. Compilation Pipeline (end-to-end)

```
Input:
  picoweb_runtime.ps        (ON Net.Connection: ... dispatch ...)
  handlers/health.py        (Resp.Status(200) ...)
  handlers/entity_list.py   (Storage.QueryCard ... while ... Resp.Write ...)
  handlers/entity_get.py    (Storage.ReadCard ... Resp.Write ...)
  ... etc

Step 1: compile_python(source) → IL for each file
Step 2: lower_to_c(il, func_name="handler_xxx") → C for each
Step 3: Concatenate all .c + picovm.c + picovm_pool.c + picovm_net.c + picowal
Step 4: cl /O2 → picoforge.exe (or gcc -O2 on Linux/ARM)

Output:
  picoforge.exe (250KB, no dependencies, serves HTTP at 50,000+ rps)
```

---

### 8. Testing

#### Unit tests (add to tests/)
- `test_on_keyword.py` — parse ON blocks in all frontends, verify AST + IL
- `test_net_hooks.py` — Net.Listen/Accept/Read/Write/Close round-trip
- `test_pool_worker.py` — worker init/reset/dispatch cycle
- `test_native_server.py` — build + run the server, hit with curl, verify response

#### Integration test
```python
# test_picoforge_native.py
import subprocess, time, urllib.request

# Build
subprocess.run(["python", "native_build.py"], check=True)

# Start server (background)
proc = subprocess.Popen(["build/picoforge.exe", "--port", "9999", "--workers", "4"])
time.sleep(0.5)

# Hit it
resp = urllib.request.urlopen("http://127.0.0.1:9999/api/health")
assert resp.status == 200
assert b"picoforge-vm" in resp.read()

# Benchmark
# ... (same benchmark.py from PicoForge)

proc.terminate()
```

---

### 9. Priority Order

1. **ON keyword parsing** in Python/BASIC/C frontends + Lowerer (1 day)
2. **Net.* hook codes** registered in picoscript_lang.py (0.5 day)
3. **picovm_net.c** — socket implementations, platform-abstracted (1 day)
4. **picovm_pool.c** — thread pool with worker init/reset/dispatch (2 days)
5. **PicoWAL integration** — move picowal into vm/, wire Storage.* direct (1 day)
6. **picovm_serve.c** — standalone server main() (0.5 day)
7. **HTTP framing** — response builder in PicoScript or C helper (0.5 day)
8. **Build pipeline** — `native_build.py` extended to produce full server (1 day)
9. **Tests** — all of the above (1 day)

**Total: ~8 days** to go from current state → native HTTP server binary.

---

### 10. Constraints

- **Five-path parity for ON blocks** — ON must compile to valid bytecode (VM),
  valid C (lower_to_c), and valid JS (lower_to_js / Service Worker)
- **Thread safety** — each worker has its own `pv_ctx`. Storage.* must be
  thread-safe (picowal uses its own WAL locking internally)
- **Arena isolation** — workers must NOT share arena memory. Each gets PV_ARENA_SIZE.
- **No malloc in hot path** — all per-request state lives in the pre-allocated arena
- **Graceful shutdown** — SIGTERM/Ctrl-C drains in-flight requests before exit
- **Backpressure** — if all workers busy, accept() blocks (TCP backlog handles queueing)
