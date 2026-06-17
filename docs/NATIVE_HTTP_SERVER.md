# PicoScript Native HTTP Server — implementation reference

> **Status:** shipped. This documents what is *implemented* in the runtime.
> The forward-looking design lives in `PICOSCRIPT_NATIVE_SERVER.md`,
> `PICOSCRIPT_THREAD_POOL.md` and `PICOSCRIPT_UNIFIED_RUNTIME.md`.

A PicoScript program can be compiled — via `lower_to_c()` — into a **single
standalone native HTTP server binary** with no runtime dependencies. The HTTP
request/response surface (`Req.*` / `Resp.*`), a thread-pooled accept loop, an
app-pluggable storage backend, and a trusted-header authorization model are all
native. The same source also runs on the bytecode VM and (via `lower_to_js`) in
the browser.

This was proven end-to-end by **PicoForge** (a metadata-driven CRUD app whose
entire API — routing, CRUD, JSON, metadata — is PicoScript compiled to a ~250 KB
native binary).

---

## 1. Components

| File | Role |
|------|------|
| `vm/picovm.c` / `picovm.h` | Core C VM + host hooks. Implements native `Req.*` and `Resp.*`, and the `pv_storage_hook` extension point. |
| `vm/picovm_pool.c` / `picovm_pool.h` | Thread-pooled native runtime: socket accept loop, per-worker `pv_ctx`+arena, `pv_http_parse_request`, HTTP response framing. |
| `vm/pico_hooks.h` | Host-hook codes (`Net.*` `0x02E0–0x02E6`, `Req.*`, `Resp.*`). |

The application provides: the compiled handler(s) (the router), and optionally a
storage backend (`pv_storage_hook`) and a static-file/dispatch shim.

---

## 2. Request lifecycle (native)

```
 main: socket() → bind() → listen()
   └─ accept loop: accept() → claim an idle worker → wake it
        worker thread (pre-allocated pv_ctx + arena, zero malloc/request):
          1. recv() into the worker's persistent reqbuf
          2. pv_http_parse_request(): method / path / headers / body
             (reads more if Content-Length exceeds the first recv) →
             populates ctx->req_method/req_path/req_headers/req_body
          3. handler(ctx)            ← the compiled PicoScript router
               Req.Method()/Path()/Header()/Param()/Principal()/BodySpan()
               Resp.Status()/Header()/Write()/End()
          4. pv_send_http_response(): frame HTTP/1.1 from ctx->http_status,
             ctx->http_type, ctx->out → send()
          5. close(); reset arena (one pointer bump); sleep
```

Per-worker query/handler state is **thread-local** so concurrent requests never
interfere. The hot path performs no heap allocation — the response and all spans
bump-allocate in the worker's arena, which is rewound after each request.

---

## 3. Host hooks for the server surface

### `Req.*` — read the request (native; populated by the pool)

| Hook | Code | Returns |
|------|------|---------|
| `Req.Method()` | `0x09` | method span (`GET`/`POST`/…) |
| `Req.Path()` | `0x0A` | full request path span |
| `Req.Header(name)` | `0x0B` | header value span (case-insensitive), 0 if absent |
| `Req.BodySpan(_)` | `0x0E` | request body span |
| `Req.BodyLen()` | `0x1B2` | body length |
| `Req.Param(i)` | `0x1B6` | 0-based `/`-delimited path segment span |
| `Req.ParamCount()` | `0x1B7` | number of path segments |
| `Req.Principal()` | `0x08` | **authenticated subject** — the value of the trusted `X-Forge-Principal` header (see §5); empty span if unauthenticated |

### `Resp.*` — write the response (native)

| Hook | Code | Effect |
|------|------|--------|
| `Resp.Status(code)` | `0x15` | sets `ctx->http_status` |
| `Resp.Header(n,v)` | `0x16` | accepted (pool frames standard headers) |
| `Resp.Write(span)` | `0x17` | appends bytes to `ctx->out` |
| `Resp.End()` | `0x1A` | marks the response complete |

### `Net.*` — raw sockets / pool (host-injected)

`Net.Listen 0x02E0`, `Net.Accept 0x02E1`, `Net.Read 0x02E2`, `Net.Write 0x02E3`,
`Net.Shutdown 0x02E4`, `Net.PoolSize 0x02E5`, `Net.Register 0x02E6`. The pool
runtime uses these internally; the `ON Net.Connection:` block lowers to an
`Net.Register` + a labelled handler sub.

---

## 4. Storage: the `pv_storage_hook` extension point

Storage stays host-injected by design (INV: bindings are not ambient). A native
binary installs its own pack/card backend without modifying the runtime:

```c
/* picovm.h */
typedef int (*pv_storage_fn)(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);
extern pv_storage_fn pv_storage_hook;   /* return non-zero if handled */
```

`pv_default_host` delegates `Storage.*` (`0x60–0x6F`) and `Search.*` card-pack
(`0x1A0–0x1A4`) hooks to `pv_storage_hook` when set. The backend reads argument
spans via `ctx->span_ptr/span_len/mem` and returns result spans by bump-allocating
at `ctx->arena_top` (see PicoForge's `picoforge_store.c` for a file-backed
reference, or wire PicoWAL’s `put/get/list/delete` over numeric pack/card keys).

---

## 5. Authorization model (trusted-header principal)

Authentication is the **front’s** job (PIOS kernel TLS+SSO, or a reverse proxy /
oauth2-proxy). The front authenticates and injects trusted request headers; the
PicoScript handler does **authorization**:

| Header | Meaning |
|--------|---------|
| `X-Forge-Principal` | authenticated subject — read by `Req.Principal()` |
| `X-Forge-Caps` | capabilities (space-delimited, or `*` for admin) — read by `Req.Header("x-forge-caps")` |

> The front **must strip any client-supplied copy** of these headers and set its
> own. With no front (localhost/dev) the binary is open — bind to loopback or
> place it behind the proxy. The native binary has no built-in authentication.

A handler enforces capabilities in PicoScript, e.g.:

```python
def cap_allows(needed):
    caps = Req.Header("x-forge-caps")
    if String.Length(caps) == 0:      # no front → open (dev)
        return 1
    if String.IndexOf(caps, "*") >= 0:
        return 1
    if String.IndexOf(caps, needed) >= 0:
        return 1
    return 0
```

---

## 6. Build & run

`native_build.py` (in the consuming project, e.g. PicoForge) drives the pipeline:
`compile_<frontend>` → `lower_to_c` → MSVC/gcc → link `picovm.c` +
`picovm_pool.c` (+ app store) → one binary.

```bash
python native_build.py --server --forge \
    --handlers handlers_native --entry router \
    --pool-slots 8 --arena-kb 512
```

| Flag | Effect |
|------|--------|
| `--server` | build the standalone pool server (vs a benchmark DLL) |
| `--entry NAME` | the compiled handler the pool dispatches per connection |
| `--pool-slots N` | static worker slots baked into the binary (BSS) |
| `--arena-kb K` | per-worker request arena (BSS) |

Buffer sizes that size `pv_ctx` (`PV_MAX_OUT`, `PV_MAX_SPANS`) are overridable via
`-D` and **must be identical across every translation unit**.

> `picovm.c` is compiled with `__STDC_HOSTED__=0` (skips the brotli/compress
> includes) while `picovm_pool.c` needs the hosted build (real sockets/threads),
> so they are compiled as separate objects.

---

## 7. Minimal example

```python
# echo.py — a complete native HTTP handler
method = Req.Method()
path   = Req.Path()
Resp.Status(200)
Resp.Header("content-type", "application/json")
Resp.Write("{\"method\":\"")
Resp.Write(method)
Resp.Write("\",\"path\":\"")
Resp.Write(path)
Resp.Write("\",\"seg2\":\"")
Resp.Write(Req.Param(2))
Resp.Write("\"}")
Resp.End()
```

`python native_build.py --server --entry echo …` → a binary that answers every
request with its parsed method/path. Swap the body for path-dispatched CRUD over
`Storage.*` to build a full API (see PicoForge `handlers_native/router.py`).

---

## 8. Status

| Capability | State |
|-----------|-------|
| Native `Req.Method/Path/Header/BodySpan/BodyLen/Param/ParamCount` | ✅ implemented (`picovm.c`) |
| Native `Req.Principal` (trusted header) | ✅ implemented |
| Native `Resp.Status/Header/Write/End` | ✅ implemented |
| HTTP request parsing in the pool (incl. Content-Length body) | ✅ implemented (`picovm_pool.c`) |
| Thread pool (pthreads / Win32 / PIOS holster) | ✅ implemented |
| `pv_storage_hook` storage extension point | ✅ implemented |
| `ON` keyword + `Net.*` hooks | ✅ implemented |
| `Resp.Header` custom-header emission in pool framing | partial (standard headers framed; custom headers accepted but not yet emitted) |
| Streaming / chunked / keep-alive | not yet (one response per connection) |
| Built-in authentication | by design **not** in the binary — host/kernel/proxy concern |
