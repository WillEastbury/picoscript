# FINDINGS — building a webserver and a database server in PicoScript

Two trial rewrites, both written **entirely in PicoScript** and run on the
bytecode VM:

| Sample | What it is | Status |
|--------|-----------|--------|
| [`picoweb-in-picoscript/`](./picoweb-in-picoscript/) | an HTTP router/server (parse → route → respond) | ✅ all routes work on the VM |
| [`picowal-in-picoscript/`](./picowal-in-picoscript/) | a WAL-backed pack/card key-value store with crash recovery | ✅ put/get/delete/list + WAL replay work on the VM |

The point was **not** to ship these — the real picoweb/picowal are C — but to
find out *what the language and runtime are still missing* to build a webserver
and a database server natively. This file is that gap analysis.

---

## TL;DR

PicoScript can already express **both** a working HTTP server and a working
write-ahead-logged store. The control flow, function model, string/JSON/number
libraries, request/response namespaces and raw `Memory.*`/`Span.*` primitives
are enough to get end-to-end behaviour on the VM and (via `lower_to_c`) as a
native binary.

The gaps fall into three buckets:

1. **Two real bugs found** — one fixed here, one a known limitation.
2. **Ergonomic gaps** — things that work but fight you (no map, no bitwise ops,
   no string interpolation, byte-packed integers by hand).
3. **Missing hardware/runtime bindings** — the big one for a *database* server:
   there is no persistence primitive and no block-device / bus / serial driver
   surface. A store engine can be written, but it has nowhere durable to write.

---

## 1. Bugs found

### 1.1 Argument/return clobber across nested calls — **FIXED** in this branch

User-subroutine calls passed arguments through shared global slots
(`__arg0__`, `__arg1__`, …) and returned through a shared `__ret__`. Because
those slots are *single pinned registers reused by every call site*, a nested
call inside a later argument overwrote an earlier argument before the outer
call consumed it:

```python
def mid(n):
    c = 0
    i = 0
    while i < n:
        c = c + i * 2
        i = i + 1
    return c
def show(tag, text):
    Io.Write(tag)
    Io.Write(text)
show("LABEL= ", Number.ToString(mid(3)))   # printed "66", not "LABEL= 6"
```

`f() + g()` was broken the same way (computed `g() + g()`). This blocks normal
composition of functions — exactly what you do constantly in a router or a
store engine.

**Fix** (`picoscript_basic.py`): stage each argument through a *fresh temp*
vreg and only move them into the `__arg` slots immediately before the call;
copy `__ret__` into a fresh temp on return. The IL allocator already pins
call-spanning vregs, so the temps survive nested calls. Verified:

```
show("LABEL= ", Number.ToString(mid(3)))  -> "LABEL= 6"   ✅
Number.ToString(f() + g())                -> "13"          ✅
three("A", Number.ToString(k(4)), "C")    -> "A5C"         ✅
```

> **Still a limitation:** true re-entrant **recursion** is not safe — a
> subroutine's parameters live in shared named slots, not per-call activation
> records. Iterative code (which is what Forge/webserver/store code is) is fine;
> deep recursion needs a real call stack. Documented, not fixed.

### 1.2 `Req.Param` decoded the path wrongly on the VM — **FIXED** in this branch

`Req.Param` / `Req.ParamCount` treated the request path as a Python string, but
`install_request_context` stores it as a **span handle**. The native C runtime
reads `ctx->req_path` bytes directly and worked; the reference VM raised
`'int' object has no attribute 'split'`. Fixed by decoding the span
(`picoscript_vm.py`), so path-parameter routing now works on both the VM and
native.

---

## 2. Runtime capability added: warm VM reuse

The picoweb demo first measured **1.65 ms/request** — but that constructed a new
`PicoVM` + `HostApi` and reloaded bytecode *per request*. A server keeps the VM
warm. Added `PicoVM.reset_for_request()` (clears regs/stack/output/spans/arena,
**keeps** the loaded program, the `mem` arena and the persistent `cards` store)
plus a verify-once cache so `_verify()` isn't re-run every request.

| path | µs/req | req/s | notes |
|------|-------:|------:|-------|
| cold (new VM each req) | ~1650 | ~600 | reconstructs everything |
| **warm (`reset_for_request`)** | **~470** | **~2100** | **~3.5× faster**, identical responses |

413 instructions execute per `/api/ping`; at ~1.1 µs each that residual is pure
Python-interpreter dispatch — it disappears under `lower_to_c` (PicoForge
measured ~3.5 µs/handler native). One remaining lever, noted below: a **warm
entry point** so per-request runs skip re-executing global/const setup.

---

## 3. Ergonomic gaps (work, but fight you)

| Gap | Impact | Workaround used | Fix shape |
|-----|--------|-----------------|-----------|
| **No map/dict type** | route tables and store indexes can't be data; every lookup is an O(n) linear scan | `if/elif` route chains; linear slot scan | add `Map.*` (arena hash map) |
| **No route table of function pointers** | dispatch is a hand-written `if/elif` on method+segments | `Req.Param(i)` + `String.StartsWith` chain | first-class function values / a `Router.*` host |
| **No bitwise operators** (`<<`,`>>`,`&`,`|`,`^`) | byte-packing multi-byte integers for records/WAL done with `*256 / /256 / %256` | arithmetic packing helpers | add bitwise ops to all frontends |
| **No string interpolation / concat operator** | every response/JSON fragment is a `Resp.Write` or `String.Concat` call | fragment writes | `+` on strings or f-strings |
| **`Span.Make` truncates pointer to 16 bits** | a span can only view the low 64 KB, but `Memory.*` addresses the whole arena — so a byte store that is *also* span-viewable is confined to 64 KB shared with the span bump-allocator | kept the store under 64 KB; capped value size | widen span ptr to full arena width, or a dedicated store region |
| **`print` emits raw bytes, not numbers** | `print(65)` writes `"A"`; numbers need `Number.ToString` | always `Number.ToString` | a number-aware print, or document |
| **`label` is a reserved word** | can't name a parameter/var `label` | renamed to `tag` | minor; document reserved words |
| **`Resp.*`/`Json.*` don't compose** | `Json.*` writes neither output nor a graph; JSON bodies are built as `Resp.Write` string fragments | manual JSON strings | make `Json.*` emit into the response body |
| **No warm entry point** | every warm request re-runs global/const setup from pc 0 (413 instrs incl. the const preamble) | full re-run each request | a resumable post-setup entry pc (`_handler_mark` hints at it) |

None of these blocked the build — they raised the line count and the footgun
count.

---

## 4. The big one: missing hardware / persistence bindings

A *webserver* mostly needs CPU + a socket, and PicoScript has `Net.*` + the
native thread pool. A *database server* needs **durable block storage**, and
that is where the platform is currently bare. The store engine
(`picowal-in-picoscript`) implements slots, an append-only WAL, and
crash-recovery replay — but everything lives in volatile arena memory. There is
**no pure-PicoScript way to persist a byte to a device.**

### What exists today

| Capability bit | Namespace | What it gives you |
|---|---|---|
| `CAP_GPIO` | `Gpio.*` | device pins (read/write/mode) |
| `CAP_DEVICE` | `Device.*` | open/caps/status/close a device **by string id** — but **no `Read`/`Write`** backend is implemented |
| `CAP_DMA` | `Stream.*` | DMA-ring buffers (framed tx/rx) over a `Device` handle |
| `CAP_STORAGE` | `Storage.*` | a **host-injected** card store (the host owns the actual persistence; in PicoForge that's a file/NVMe behind C) |

So the model today is: *the host owns all real I/O*; PicoScript reaches it only
through `Storage.*` (host-backed) or the generic `Device.*`/`Stream.*` handles,
which have no concrete drivers behind them in the reference runtime.

### What is missing — concrete hardware drivers

These are the bindings a real webserver + DB server want, and none exist yet:

| Missing binding | Why it's needed | Suggested shape |
|---|---|---|
| **UART / serial** | bring-up console, logging, and a transport on bare metal (Pico 2 W has UART; many MCUs have *only* UART before networking is up) | `Uart.Open(id, baud, bits, parity, stop)`, `Uart.Write(span)`, `Uart.Read(buf, n)`, `Uart.Available()` |
| **SPI / I²C** | reach SD cards, flash, sensors, displays — the usual MCU peripheral buses | `Spi.*` (transfer/cs), `I2c.*` (read/write reg) |
| **PCIe** | the bus that reaches NVMe and NICs on real hardware — enumeration, config space, BAR mapping, MSI/MSI-X | `Pcie.Enumerate()`, `Pcie.Config(bdf, off)`, `Pcie.MapBar(bdf, bar)` |
| **M.2 / NVMe block storage** | **the actual durable target the WAL must fsync to** — submission/completion queues, LBA read/write, flush | `Nvme.Open(bdf)`, `Block.Read(dev, lba, span)`, `Block.Write(dev, lba, span)`, `Block.Flush(dev)` |
| **Generic block device** | SD/eMMC/flash fallback where there's no NVMe; same LBA contract | `Block.*` as above, device-agnostic |
| **`Device.Read`/`Device.Write`** | even the *generic* device abstraction can't move bytes yet — only open/caps/status/close are implemented | implement read/write on `Device.*`, then layer `Uart`/`Block` on it |

> **Architectural note (matches PicoForge's split):** the EL0 PicoScript program
> should *not* own raw hardware. The right shape is a thin, capability-gated
> host binding (`Block.*`, `Uart.*`, `Pcie.*`) — like `Net.*` and `Storage.*`
> today — that the kernel/proxy grants. Then `picowal-in-picoscript` swaps its
> `Memory.*` backing for `Block.Read/Write/Flush` and becomes a **real**
> durable store: WAL appended to LBAs, fsync on commit, replay on boot. The
> store *logic* in this sample is already written against that future contract.

---

## 5. Scorecard

| To build a … | Have today | Missing |
|---|---|---|
| **HTTP server** | routing, params (fixed), request/response, JSON/HTML build, `Net.*` sockets, native thread pool, warm VM reuse | map-based routing, string ergonomics, warm entry point |
| **Database server** | store engine logic, WAL append + crash-recovery replay, byte-packed records, `Storage.*` (host-backed) | **durable block I/O (`Block.*`/`Nvme.*` over `Pcie.*`)**, a real index (`Map.*`), 64-bit keys, wider `Span.Make` window, `Uart.*` for console/log |

**Bottom line:** the *language* is close — the two real bugs are fixed and the
remaining language gaps are ergonomic. The *runtime* is the frontier: a webserver
is reachable now, but a self-hosting database server is gated on **hardware
driver bindings (UART, SPI/I²C, PCIe, M.2/NVMe block storage)** that don't exist
yet. Those are the next things to add.
