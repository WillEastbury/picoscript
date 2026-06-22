# picoweb-in-picoscript

An HTTP router/server written **entirely in PicoScript** — request inspection,
routing, path parameters, and JSON/HTML response building are all PicoScript.
The only host-provided pieces are the raw socket pump (`Net.*`) and the request
context the runtime fills before each call (`Req.*` reads it).

## Run

```
python run_demo.py
```

Compiles `httpd.ppy`, installs simulated request contexts (the same path the
native pool worker uses), and prints framed responses:

```
GET   /                  -> 200 text/html
GET   /api/ping          -> 200 application/json   {"pong":true,"engine":"picoscript"}
GET   /api/headers       -> 200 application/json   {"user_agent":"..."}
GET   /api/echo/hello    -> 200 application/json   {"echo":"hello"}
POST  /api/create        -> 201 application/json   {"received":{...}}
PUT   /api/create        -> 405 application/json   {"error":"method_not_allowed"}
GET   /nope              -> 404 application/json   {"error":"not_found"}
```

It then benchmarks **cold vs warm** request handling on the VM.

## How it runs three ways

The same `httpd.ppy` source runs on:
- the **bytecode VM** (this demo, via `install_request_context`),
- a **native binary** (`lower_to_c` + the `picovm_pool.c` thread pool),
- the **browser** (`lower_to_js`) for debug.

## Warm VM reuse (the performance lever)

The first measurement was ~1.65 ms/req because it rebuilt the VM every request.
A real server keeps the VM warm: `PicoVM.reset_for_request()` clears the
per-request execution state (regs, stack, output, spans, arena) but keeps the
loaded program, the arena buffer, and the persistent `cards` store, and skips
re-verification. Result: **~3.5× faster**, identical responses. The residual
cost is pure interpreter dispatch (413 instructions/req at ~1.1 µs each) and
disappears under `lower_to_c`.

## Gaps surfaced (see ../FINDINGS.md)

- **No map / no function-pointer values** → the route table is a hand-written
  `if/elif` chain on `(method, path segments)`, not data. This is the single
  biggest webserver-shaped gap.
- **No string interpolation / concat operator** → every JSON/HTML fragment is a
  separate `Resp.Write` or `String.Concat`.
- **`Req.Param` VM bug** (path stored as a span but decoded as a string) —
  **fixed** in this branch so path-param routing works on the VM too.
- **No warm entry point** → each warm request still re-runs global/const setup
  from pc 0; a resumable post-setup entry would cut the residual further.
