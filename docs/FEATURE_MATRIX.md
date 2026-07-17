# Feature matrix — dialects x VMs x namespaces, current status

A single reference table for "does X work on Y". Two independent axes:

1. **Language features** (statements/keywords — control flow, declarations,
   exceptions, events) vary **by dialect** (the source-level grammar), because
   each dialect has its own parser. All dialects that share the AST
   (BASIC/Python-style/English/COBOL/Report/Functional) compile to
   byte-identical bytecode once parsed; C-style and v1 have their own
   independent AST/compiler islands (see `docs/DIALECT_PARITY.md`).
2. **Host namespaces** (`String.*`, `Map.*`, `Crypto.*`, …) vary **by VM/
   runtime**, not by dialect — every dialect (including v1) compiles down to
   the same bytecode host-hook codes (`HOST_HOOK_CODES` in
   `picoscript_lang.py`), executed by exactly **three** independent runtime
   implementations: the Python VM (`picoscript_vm.py`), the JS VM
   (`vm/picovm.js`), and the C VM (`vm/picovm.c`). Native-C transpile
   (`lower_to_c`) and native-JS transpile (`lower_to_js`) are **not** a fourth
   and fifth implementation — they call directly into the *same* C (`pv_host2`
   -> `pv_default_host`) and JS (`rt.host` -> the same `picovm.js` dispatch)
   runtime code that the interpretive VMs use, so their namespace coverage is
   structurally identical to "C VM" / "JS VM" by construction (verified:
   `picoscript_il.py`'s `lower_to_c`/`lower_to_js` emit a generic
   `pv_host2(ctx, code, a, b)` / `rt.host(code, a, b)` call for **every**
   `HOST_HOOK_CODES` entry — there is no per-namespace allowlist at the
   transpile layer, only at the runtime that receives the call).

## 1. Control-flow / statement keywords by dialect

| Feature | BASIC | Python | English | COBOL | Report | Functional | C-style | v1 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| If / While / For | Y | Y | Y | Y | Y | Y | Y | Y |
| ForEach | Y | Y | Y | Y | Y | Y | Y | N |
| DoLoop | Y | Y | Y (`Repeat`) | N | N | N | Y (`do…while`) | N |
| Skip (continue) | Y | Y | Y | N | Y | Y | Y (`continue`) | N |
| Ternary | Y (`IIF`) | Y | Y | N | N | Y | Y (`?:`) | N |
| Dim / IncDec | Y | N \* | N \* | N \* | Y (IncDec only) | N \* | Y | N |
| Gosub | Y | Y | Y | Y | Y | N \*\* | N/A | Y |
| ServerMain | Y | N | Y | N | N | Y | Y | N |
| ConstDecl / EnumDecl | Y | Y | Y | Y | Y | Y | Y | N |
| Dispatch (`Ns.Method(...)` call) | Y | Y | Y | Y | Y | Y | Y | Y |
| **TryExcept / Raise / OnBlock** | **Y** | **Y** | **Y** | **Y** | **Y** | **Y** | **Y** | **N** |
| `EVENT POST` / `EVENT RAISE` sugar | Y | Y (host-call form) | Y | Y | Y | Y | Y (`on Ns.Method{}`) | N \*\*\* |

\* Not a real gap — `Let`/assignment already covers the same ground; verified
per-frontend that adding a redundant declaration form would add no capability
(see `docs/DIALECT_PARITY.md`).
\*\* Not a real gap — Functional's `f 1 2` / `f(1,2)` application already
covers subroutine calls.
\*\*\* v1 has a raw `THREAD.RAISE <irq>` opcode-level primitive (`OP_RAISE`)
but no `TryExcept`/handler-stack mechanism — it is a frozen, IL-less ISA by
design (`picoscript_lang.py`: "Each statement compiles 1:1 to a single 32-bit
instruction. No optimisation. No reordering."), explicitly excluded from the
language-equivalence pass. All v1 bytecode still executes on the **same**
`PicoVM` as every other dialect (`picoscript_build.py` compiles v1 source with
`picoscript_lang.Compiler`, then runs the resulting words on `picoscript_vm.PicoVM`
— same runtime, only the frontend/grammar differs).

## 2. Exception & eventing mechanism by execution path

| Path | TryExcept/Raise (handler stack) | OnBlock (event dispatch) |
|---|:-:|:-:|
| Python VM (bytecode) | Y — real, nested-safe (`docs/EXCEPTION_ENGINE.md`) | Y (`docs/EVENTING.md`) |
| JS VM (bytecode, `vm/picovm.js`) | Y — byte-identical to Python | Y — byte-identical to Python |
| C VM (bytecode, `vm/picovm.c`) | Y (shares the `laddr`/handler-stack bytecode contract) | Y |
| Native C transpile (`lower_to_c`) | **N — explicitly rejected** at compile time (`ValueError`), not silently mis-compiled | N (same reason) |
| Native JS transpile (`lower_to_js`) | **N — explicitly rejected** at compile time | N (same reason) |

Native transpile rejects these because `laddr` (load-label-address) has no
PC-addressable/fault-catching equivalent in straight-line native C/JS output —
a real architectural limitation, documented in `docs/EXCEPTION_ENGINE.md`'s
scope section, not an oversight.

## 3. Host namespaces by runtime (Python VM / JS VM+native-JS / C VM+native-C)

70 namespaces are registered in `HOST_HOOK_CODES`. Status below was verified
directly (grep for each runtime's actual dispatch branches — `if ns == "X"` in
`picoscript_vm.py`, `name.indexOf("X.")` in `vm/picovm.js`, hook-code-range
checks in `vm/picovm.c`), not inferred from documentation claims.

| Namespace | Methods | Python VM | JS VM (+native JS) | C VM (+native C) | Notes |
|---|:-:|:-:|:-:|:-:|---|
| Arena | 3 | Y | Y | Y | |
| Assert | 5 | Y | Y | Y | |
| Attention | 4 | Y | Y | Y | |
| **Auth** | 10 | **N** | **N** | **N** | Host-injected by design — needs identity provider/trust store + entropy |
| Base64 | 4 | Y | Y | Y | |
| Binary | 6 | Y | Y | Y | PSC1/BSO1 card <-> Map |
| BitLinear | 8 | Y | Y | Y | |
| Bits | 7 | Y | Y | Y | |
| Capability | 3 | Y | Y | Y | |
| Capsule | 5 | Y | Y | Y | |
| **Card** | 3 | **N** | **N** | **N** | Reserved/hardware-injected, not wired in any reference runtime |
| Compress | 8 | Y | Y | Y | Pico/Brotli/Gzip/Deflate |
| **Context** | 15 | **N** | **N** | **N** | Host-injected by design — live request/connection state |
| **Data** | 3 | **N** (silent no-op, `rd` untouched) | **Y** (explicit stub: 0 / empty span) | **N** | **Asymmetry found this audit** — see note below |
| DateTime | 15 | Partial | Partial | Partial | `Now`/`UtcNow` host-injected (wall clock); rest pure and implemented |
| **Descriptor** | 6 | **N** | **N** | **N** | Reserved/hardware-injected |
| Encoding | 12 | Y | Y | Y | ASCII/UTF-8/UTF-16/UTF-7/Hex |
| Env | 4 | Y | Y | Y | |
| **Environment** | 9 | **N** | **N** | **N** | Host-injected by design — OS/host facts |
| Error | 8 | Y | Y | Y | Handler stack, `Raise`/`PopHandler` (this session) |
| Event | 10 | Y | Y | Y | FIFO queue + `OnBlock` dispatch |
| **Fifo** | 4 | **N** | **N** | **N** | Reserved/hardware-injected |
| Gpio | 7 | Y | Y | Y | Reference emulator; PIOS injects real driver |
| Html | 10 | Partial | Partial | Partial | `Encode`/`Decode` implemented; DOM tree ops (`CreateNode`/`QuerySelector`/…) not built |
| Http | 12 | Partial | Partial | Partial | `ParseQuery/ParseForm/ParseJson/EncodeJson` implemented (pure); `ReadHeader/ReadBody/GenerateHeaders/GenerateResponse` host-injected (live connection) |
| Io | 2 | Y | Y | Y | |
| Json | 11 | Y | Y | Y | |
| **Kernel** | 6 | **N** | **N** | **N** | `WaitIRQ`/`FireSWIRQ`/`ProfileStart`/`ProfileEnd`/`TracePoint` unimplemented as a namespace; underlying wait/raise capability exists only via raw opcodes `OP_WAIT`/`OP_RAISE`, not this call surface |
| Kv | 12 | Y | Y | Y | |
| **Lease** | 6 | **N** | **N** | **N** | Reserved/hardware-injected |
| Locale | 7 | Y | Y | Y | Needs `tzdata` on Windows for non-empty `zoneinfo` — see note below |
| Log | 5 | Y | Y | Y | Real `Log.*` subsystem (this session) |
| Map | 27 | Y | Y | Y | |
| Maths | 12 | Partial | Partial | Partial | `Sin/Cos/Tan/Log/Log10/Exp` (Q16.16 CORDIC), `Power/Sqrt/Clamp/Lerp` implemented; `Random`/`RandomRange` host-injected (entropy) |
| Memory | 9 | Y | Y | Y | |
| Model | 12 | Y | Y | Y | |
| **Net** | 7 | **N** | **N** | **N** | Reserved/hardware-injected |
| Number | 11 | Y | Y | Y | |
| **Pack** | 1 | **N** | **N** | **N** | Reserved/hardware-injected |
| Principal | 3 | Y | Y | Y | |
| Process | 8 | Y | Y | Y | |
| Quant | 5 | Y | Y | Y | |
| Query | 2 | Y | Y | Y | |
| Queue | 5 | Y | Y | Y | |
| Random | 1 | Y (seeded, non-deterministic by design) | Y | Y | |
| Req | 13 | Y | Y | Y | Host-fed request context; native C has a real HTTP server (`docs/NATIVE_HTTP_SERVER.md`) |
| Resp | 13 | Y | Y | Y | |
| Sampling | 4 | Y | Y | Y | |
| Sandbox | 1 | Y | Y | Y | |
| Scheduler | 1 | Y | Y | Y | |
| Search | 28 | Y | Y | Y | |
| Span | 5 | Y | Y | Y | |
| Status | 1 | Y | Y | Y | |
| Storage | 21 | Y | Y | Y | Card-store; host-injected persistence backend, in-VM logic is pure |
| Stream | 8 | Y | Y | Y | |
| String | 14 | **Y (fixed this session — see below)** | Y | Y | `Split`/`Join` registered codes, unimplemented on **every** runtime (pre-existing gap, not part of the regression — see note below) |
| Template | 2 | Y | Y | Y | |
| Tensor | 12 | Y | Y | Y | |
| TextRender | 9 | Y | Y | Y | |
| **Thread** | 1 | **N** | **N** | **N** | Reserved/hardware-injected (distinct from v1's `THREAD.*` opcode-level compiler sugar, which is unrelated) |
| Timer | 4 | Y | Y | Y | |
| Tokenizer | 7 | Y | Y | Y | |
| Ui | 12 | Y | Y | Y | |
| Utf8Reader | 8 | Y | Y | Y | |
| Utf8Writer | 7 | Y | Y | Y | |
| **X509** | 8 | **N** | **N** | **N** | Host-injected by design — needs a trust store + entropy |

**13 namespaces are unimplemented on all three runtimes** (`Auth`, `Card`,
`Context`, `Descriptor`, `Environment`, `Fifo`, `Kernel`, `Lease`, `Net`,
`Pack`, `Thread`, `X509`, and — see below — `Data` on Python/C). All 13 are
either genuinely external/nondeterministic state that must be host-injected
by design (`Auth`, `Context`, `Environment`, `X509` need a clock/OS/entropy/
trust-store/identity-provider the deterministic VM deliberately doesn't have),
or reserved/hardware-facing primitives (`Card`, `Descriptor`, `Fifo`, `Lease`,
`Net`, `Pack`, `Thread`) not yet wired into any of the three reference
runtimes. Verified by checking `pv_cap_for_hook` in `vm/picovm.c`: it
capability-gates hook-code ranges for `Kernel`/`Context`/`X509`/`Auth` but no
runtime dispatches them — confirming these are deliberately reserved, not
silently broken.

### Found during this audit: `Data.*` Python/C vs JS asymmetry

`Data.Lookup`/`FieldNum`/`FieldStr` (read-only host-bound data binding) is
**explicitly stubbed in JS** (`vm/picovm.js`, intentional: "the browser has no
server data, so return empty/0 and let the authoritative server enforce
data-dependent rules" — sets `rd` to `0` or an empty span). **Python
(`picoscript_vm.py`) and C (`vm/picovm.c`) have no `Data.*` branch at all** —
calls fall through to the generic "unknown hook: record and continue" path,
which does **not** write `rd`, leaving whatever value was already in that
register. Functionally this rarely matters (scripts should not read an
uninitialized register), but it is a real, verified inconsistency: JS
guarantees a defined `0`/empty result, Python/C do not. Not fixed as part of
this pass (flagged, not silently left as "already Y") — a good small follow-up
if `Data.*` is exercised by any real program.

### `String.*` — fixed this session, one sub-gap remains

`String.*` was **completely broken in the Python VM** (`AttributeError`,
every method) due to a real regression: commit `644acd1` overwrote the
`_stringlib` method body with the unrelated `_parse_hook` while leaving the
dispatcher's call site intact. Fixed (see commit `485d7d4`) by restoring the
original implementation verbatim, verified byte-identical to `vm/picovm.js`'s
always-intact version. Full regression suite: 50 failures -> 2 (see below).

`String.Split`/`String.Join` are registered `HOST_HOOK_CODES` entries but
**were never implemented on any runtime**, before or after this fix (not part
of the `_stringlib` regression — checked the pre-regression implementation
too, both methods were always absent). Left unimplemented deliberately rather
than inventing unverified semantics under time pressure and risking a new
Python/JS asymmetry — a real, pre-existing, low-priority gap, not a bug
introduced or missed by this fix.

## 4. Test-environment dependency: `tzdata`

The 2 remaining failures after the `String.*` fix were unrelated to any VM/
dialect bug: this Windows Python install has no `tzdata` package, so the
standard-library `zoneinfo.ZoneInfo` cannot resolve **any** timezone key —
including `"UTC"` — at all (Windows, unlike Linux, does not ship the IANA tz
database as OS files; `zoneinfo` depends on either the OS database or the
`tzdata` PyPI package as a fallback). `Locale.SetLocale` is implemented
correctly; it was failing purely because of this missing environment package.

**Fixed**: installed `tzdata` (`pip install tzdata`, user-level). Full suite is
now **2454 passed, 48 skipped, 0 failed**. If you set up a fresh dev/CI
environment on Windows for this repo, install `tzdata` alongside any other
Python test dependencies — it is a pure IANA-database data package for
Python's own standard library, not a third-party framework.
