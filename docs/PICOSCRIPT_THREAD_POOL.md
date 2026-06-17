# PicoScript Thread Pool Runtime + PIOS Native Implementation

## Context

The PicoScript `ON` keyword and `Net.*` socket hooks are implemented (commit bdb4464).
The language can now express event-driven HTTP servers. Two runtime implementations
are needed to execute them:

1. **`vm/picovm_pool.c`** — Thread-pooled native server for desktop/server (Windows/Linux/macOS)
2. **PIOS kernel integration** — Wire `ON Net.Connection:` to PIOS capsule process pool

---

## Part A: `vm/picovm_pool.c` — Native Thread Pool Runtime

### What it does

A C file that provides a pre-allocated thread pool for running compiled PicoScript
`ON Net.Connection:` handlers. It replaces the need for any Python in the request path.

### Architecture

```
Main thread:
  1. pv_server_init(port, pool_size, handler_fn)
  2. socket() → bind() → listen()
  3. Loop: accept() → pick idle worker → inject conn_fd → signal worker

Worker threads (N pre-allocated):
  1. Sleep (WaitForSingleObject / pthread_cond_wait / futex)
  2. Wake: conn_fd injected
  3. pv_init(&ctx) already done at startup (just reset arena)
  4. Run handler_fn(&ctx) — the compiled ON block
  5. Frame HTTP response from ctx.out + ctx.http_status
  6. send() response bytes
  7. close(conn_fd)
  8. Reset: ctx.arena_top = base, ctx.out_len = 0
  9. Mark idle → sleep again
```

### API

```c
// vm/picovm_pool.h
#ifndef PICOVM_POOL_H
#define PICOVM_POOL_H

#include "picovm.h"

#define PV_POOL_MAX 64
#define PV_POOL_DEFAULT 8
#define PV_WORKER_ARENA (512 * 1024)

typedef int64_t (*pv_handler_fn)(pv_ctx *ctx);

typedef struct pv_server {
    int server_fd;
    int port;
    int pool_size;
    int running;
    pv_handler_fn handler;
    // ... platform-specific thread/sync primitives
} pv_server;

// Lifecycle
int  pv_server_init(pv_server *s, int port, int pool_size, pv_handler_fn handler);
int  pv_server_run(pv_server *s);   // blocks, dispatching to pool workers
void pv_server_stop(pv_server *s);  // graceful shutdown (drain in-flight)

#endif
```

### Worker struct

```c
typedef struct pv_worker {
    pv_ctx ctx;                        // Own VM context (registers, spans, etc.)
    uint8_t arena[PV_WORKER_ARENA];    // Own memory arena (no sharing)
    int conn_fd;                       // Injected per-request
    int busy;                          // 0=idle, 1=running
    int id;                            // Worker index
#ifdef _WIN32
    HANDLE thread;
    HANDLE wake_event;                 // SetEvent to wake
#else
    pthread_t thread;
    pthread_mutex_t mutex;
    pthread_cond_t cond;
#endif
} pv_worker;
```

### Worker lifecycle (critical path — zero allocation)

```c
void pv_worker_run(pv_worker *w, pv_server *s) {
    while (s->running) {
        // 1. Sleep until woken
        PLATFORM_WAIT(w);
        
        if (!s->running) break;
        
        // 2. Install request context from raw TCP read
        char buf[8192];
        int n = recv(w->conn_fd, buf, sizeof(buf), 0);
        if (n <= 0) { pv_socket_close(w->conn_fd); w->busy = 0; continue; }
        
        // 3. Parse HTTP request line (minimal — just method + path + headers)
        //    Inject into ctx via the standard Req.* context
        pv_http_parse_request(&w->ctx, buf, n);
        
        // 4. Run the compiled handler
        s->handler(&w->ctx);
        
        // 5. Frame and send HTTP response
        pv_http_send_response(w->conn_fd, &w->ctx);
        
        // 6. Close connection (or keep-alive if header says so)
        pv_socket_close(w->conn_fd);
        
        // 7. Reset for next request (ONE integer assignment reclaims all memory)
        w->ctx.arena_top = PV_ARENA_BASE;
        w->ctx.out_len = 0;
        w->ctx.http_status = -1;
        w->ctx.http_type = NULL;
        w->busy = 0;
    }
}
```

### HTTP parsing (minimal, in C)

```c
// Parse "GET /api/data/account HTTP/1.1\r\nHost: ...\r\n\r\nbody"
void pv_http_parse_request(pv_ctx *ctx, const char *raw, int len) {
    // Extract method (GET/POST/PUT/DELETE)
    // Extract path
    // Extract headers (Content-Type, Content-Length, Authorization)
    // Extract body (if Content-Length > 0)
    // Install into ctx so Req.Method(), Req.Path(), Req.Header() work
}
```

### HTTP response framing

```c
void pv_http_send_response(int fd, pv_ctx *ctx) {
    // Build: "HTTP/1.1 {status} OK\r\nContent-Type: {type}\r\nContent-Length: {len}\r\n\r\n{body}"
    char header[512];
    int status = ctx->http_status > 0 ? ctx->http_status : 200;
    const char *type = ctx->http_type ? ctx->http_type : "application/json";
    int hlen = snprintf(header, sizeof(header),
        "HTTP/1.1 %d OK\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n"
        "\r\n",
        status, type, ctx->out_len);
    send(fd, header, hlen, 0);
    if (ctx->out_len > 0) send(fd, (char*)ctx->out, ctx->out_len, 0);
}
```

### Platform abstraction

```c
// vm/picovm_pool_platform.h
#ifdef _WIN32
  #include <winsock2.h>
  #include <ws2tcpip.h>
  #pragma comment(lib, "ws2_32.lib")
  #define pv_socket_close(fd) closesocket(fd)
  typedef HANDLE pv_thread_t;
  // Worker wake: SetEvent / WaitForSingleObject
#else
  #include <sys/socket.h>
  #include <netinet/in.h>
  #include <unistd.h>
  #include <pthread.h>
  #define pv_socket_close(fd) close(fd)
  typedef pthread_t pv_thread_t;
  // Worker wake: pthread_cond_signal / pthread_cond_wait
#endif
```

### Accept loop (main thread)

```c
int pv_server_run(pv_server *s) {
    s->running = 1;
    while (s->running) {
        struct sockaddr_in client;
        socklen_t client_len = sizeof(client);
        int conn_fd = accept(s->server_fd, (struct sockaddr*)&client, &client_len);
        if (conn_fd < 0) continue;
        
        // Find idle worker (round-robin with fallback to blocking)
        pv_worker *w = pv_find_idle_worker(s);
        if (!w) {
            // All busy — backpressure (TCP backlog handles queuing)
            // Or: block until one frees up
            w = pv_wait_for_worker(s);
        }
        
        // Inject connection and wake worker
        w->conn_fd = conn_fd;
        w->busy = 1;
        PLATFORM_SIGNAL(w);  // SetEvent / pthread_cond_signal
    }
    return 0;
}
```

### Build integration

The standalone server binary links:
```
picovm_pool.c + picovm.c + picovm_net.c + picowal_api.c + generated_handlers.c
```

Build command (Windows):
```bat
cl /O2 /Fe:picoforge.exe ^
    picovm_pool.c picovm.c picowal_api.c picowal_store_fs.c ^
    generated_handlers.c ^
    ws2_32.lib
```

Build command (Linux):
```bash
gcc -O2 -o picoforge \
    picovm_pool.c picovm.c picowal_api.c picowal_store_fs.c \
    generated_handlers.c \
    -lpthread
```

### Tests

- `test_pool_basic.c` — init pool, fake accept, verify worker wakes + runs + resets
- `test_pool_http.c` — full HTTP round-trip: connect, send request, verify response
- `test_pool_stress.c` — 1000 concurrent connections, verify no crash/leak
- Integration: Python test that builds the binary, starts it, hits with urllib

---

## Part B: PIOS Kernel Integration

### What changes in PIOS

On PIOS, the thread pool is **not needed** — the kernel already provides the
equivalent via its process model + FIFO dispatch + SGI doorbells.

The mapping:

| picovm_pool.c concept | PIOS equivalent |
|----------------------|-----------------|
| Thread pool (N workers) | N capsule processes (`proc.c`) |
| accept() in main thread | Kernel ETH RX IRQ → HTTP header decode at EL1 |
| Inject conn_fd to worker | Post request descriptor to process FIFO |
| Worker wakes | Process wakes from WFI via SGI doorbell |
| Worker runs handler | EL0 PicoScript VM executes bytecode |
| Worker sends response | Process writes response to kernel return FIFO |
| Worker resets arena | Process resets `arena_top` (one store instruction) |
| Worker sleeps | Process executes WFI |

### What to implement in PIOS (`src/`)

1. **`src/capsule_pool.c`** — Multi-process capsule pool manager:
   ```c
   // At capsule load time, spawn N processes (from capsule descriptor)
   void capsule_pool_init(capsule_t *cap, int n_workers) {
       for (int i = 0; i < n_workers; i++) {
           proc_t *p = proc_create(cap->entry, cap->stack_base + i * STACK_SIZE);
           p->capsule = cap;
           p->worker_id = i;
           cap->workers[i] = p;
       }
   }
   ```

2. **`src/httpd_dispatch.c`** — HTTP → capsule dispatch:
   ```c
   // Called from httpd after TLS termination + header decode
   void httpd_dispatch_to_capsule(http_request_t *req) {
       capsule_t *cap = route_resolve(req->path);  // /api/... → capsule
       proc_t *worker = capsule_find_idle(cap);     // Round-robin idle process
       if (!worker) { http_respond_503(req); return; }
       
       // Post request descriptor to worker's FIFO
       fifo_post(worker->inbox, &(fifo_msg_t){
           .type = MSG_HTTP_REQUEST,
           .conn_id = req->conn_id,
           .method = req->method,
           .path_span = req->path_span,
           .body_span = req->body_span,
           .header_span = req->header_span,
       });
       
       // Wake worker via SGI doorbell
       sgi_send(worker->core, SGI_PROCESS_WAKE);
   }
   ```

3. **`src/proc.c`** — Process wake handler (existing, extend):
   ```c
   // In the process main loop (already exists as WFI + check FIFO)
   void proc_main(proc_t *self) {
       while (1) {
           wfi();  // Sleep until SGI
           
           fifo_msg_t msg;
           if (fifo_dequeue(self->inbox, &msg)) {
               if (msg.type == MSG_HTTP_REQUEST) {
                   // Install request context into VM
                   pv_install_from_fifo(&self->vm_ctx, &msg);
                   
                   // Run the ON handler bytecode
                   pv_run(&self->vm_ctx, self->capsule->on_connection_entry);
                   
                   // Post response back to kernel
                   fifo_post(kernel_response_fifo, &(fifo_msg_t){
                       .type = MSG_HTTP_RESPONSE,
                       .conn_id = msg.conn_id,
                       .status = self->vm_ctx.http_status,
                       .body = self->vm_ctx.out,
                       .body_len = self->vm_ctx.out_len,
                   });
                   
                   // Reset arena for next request
                   self->vm_ctx.arena_top = PV_ARENA_BASE;
                   self->vm_ctx.out_len = 0;
               }
           }
       }
   }
   ```

4. **Capsule descriptor** — specify worker count:
   ```c
   // In capsule metadata (WALFS card or header)
   typedef struct capsule_desc {
       uint32_t entry_point;       // Bytecode start address
       uint32_t on_connection;     // ON Net.Connection handler offset
       uint8_t  worker_count;      // Number of pool processes (default 4)
       uint8_t  arena_size_kb;     // Per-worker arena (default 512)
       // ...
   } capsule_desc_t;
   ```

### What already exists in PIOS (no changes needed)

- ETH RX IRQ → HTTP header decode at EL1 ✅ (uhttp_bridge.h)
- TLS termination at kernel layer ✅
- Process model with WFI + FIFO ✅ (proc.c)
- SGI doorbell for cross-core wake ✅
- PicoScript VM at EL0 ✅ (picovm.c)
- WALFS storage via SVC ✅
- Response write back to kernel ✅

### What's new for PIOS

| Item | Effort | Notes |
|------|--------|-------|
| `capsule_pool_init` — spawn N processes | 0.5 day | Extend existing `proc_create` |
| `httpd_dispatch_to_capsule` — route + dispatch | 1 day | Extend existing `uhttp_bridge.h` |
| `pv_install_from_fifo` — FIFO msg → Req.* ctx | 0.5 day | Map FIFO fields to ctx |
| Response FIFO → HTTP response framing | 0.5 day | Kernel reads ctx.out, frames HTTP |
| Capsule descriptor `worker_count` field | 0.5 day | Add to WALFS card schema |
| **Total** | **3 days** | |

---

## Priority

1. **`picovm_pool.c`** first (desktop/server) — enables the native benchmark and proves the architecture
2. **PIOS integration** second — same model, kernel-native, smaller delta from existing code

## Constraints

- **No malloc in hot path** — all per-request state in pre-allocated arena
- **No locks between workers** — each has its own ctx. Only the accept loop touches conn_fd assignment
- **Graceful shutdown** — SIGTERM/Ctrl-C sets `running=0`, workers drain current request then exit
- **Backpressure** — if all workers busy, accept blocks (TCP backlog = OS queue)
- **Keep-alive optional** — v1 closes after each response; v2 can re-use worker for pipelined requests
