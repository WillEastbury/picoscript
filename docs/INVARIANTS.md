# PicoScript invariants — compiler / runtime / security boundary

PicoScript is no longer "a little language": it is a compiler, three byte-identical
runtimes, two native transpilers, and an EL0/EL1 capability boundary. These are the
**acceptance criteria** for any change. Each is a rule the implementation must uphold
and a contributor may rely on. The `Status` column reflects an audit at the date of
the last edit — `enforced` = checked/tested today, `partial` = held in some
runtimes/paths only, `target` = agreed rule not yet enforced.

> **The one above all others — bindings are not ambient.**
> PicoScript *behaviour* is portable (the same program means the same thing on every
> path). PicoScript *bindings* — the hooks that touch the outside world — are **not**
> globally available. A capsule reaches time, entropy, storage, sockets, or any host
> resource **only** through a binding it has been granted. Existence of a hook is not
> permission to call it.

## Status summary

| # | Invariant | Status |
|---|-----------|--------|
| 1 | Same source, same semantics — all frontends lower to equivalent IL | enforced |
| 2 | Lowering parity — VM/toC/toJS/native produce identical observable output | enforced* |
| 3 | Host hooks are the only outside world | enforced |
| 4 | Every hook has a declared contract | target |
| 5 | No hidden allocation in hot hooks (declare arena use or forbid) | target |
| 6 | Arena scope is explicit; scope exit rewinds or transfers | partial |
| 7 | Seal consumes ownership (use-after-seal = compile error or trap) | partial |
| 8 | Spans are fat and bounded (ptr+len); no null-terminated authority | enforced |
| 9 | Literals are immutable (const pool unless copied) | partial |
| 10 | Bytecode verification before execution | partial |
| 11 | Computed jumps are range-checked | enforced |
| 12 | No unbounded loops without budget (yield or fault) | enforced |
| 13 | Register spill is compiler-owned (no RegisterPressureError on real code) | enforced |
| 14 | Numeric overflow / division policy is explicit and identical per target | enforced |
| 15 | Deterministic mode exists (disable clock/random/unordered iteration) | target |
| 16 | Case-insensitive namespaces are canonicalised | enforced |
| 17 | Capability check before hook dispatch | enforced (mechanism) |
| 18 | Hook failures are typed (no magic -1/0) | partial (diagnosed) |
| 19 | Template rendering is bounded (depth/each/recursion/output) | partial |
| 20 | JSON/HTTP parsers are budgeted (depth/token/length/bytes) | partial |
| 21 | Source card is truth (artefacts carry source hash + compiler version + profile) | target |
| 22 | Generated artefacts are disposable (never edited as source) | convention |
| 23 | ABI version is embedded and checked (refuse mismatch) | target |
| 24 | Parity runner is the gatekeeper (every hook/opcode/lowering has parity tests) | partial |
| 25 | Debug trace is structured (span, IL op, pc, hook id, capsule, binding) | target |

\* INV-2 (lowering parity): the known signed-division divergence is fixed (truncate
toward zero everywhere). The remaining nuance is that on a *fault* (step budget,
out-of-range jump, template-depth overflow) the three bytecode VMs all stop, but the
transpiled toC/toJS-native paths are best-effort (no VM loop / step budget) — faults
are a malicious-input edge, not normal observable output, so pure programs stay
byte-identical on all five paths.

## Detail and evidence

### 1. Same source, same semantics — *enforced*
One IL pipeline; the Python and JS frontends emit byte-identical bytecode
(`tests/test_pipeline.py` `check_jscompile`, `tests/test_io_hooks.py`). Subject to the
INV-2 caveats below.

### 2. Lowering parity is mandatory — *enforced (signed-division divergence fixed)*
The runtimes must produce identical observable output. **Fixed:** signed division/modulo
now truncates toward zero on every path — `picoscript_vm.py` `_arith`, the IL
const-folder (`trunc_div32`), `vm/picovm.c` (with the `INT_MIN/-1` case defined), JS
`(a/b)|0`, and the `picoc.js` const-folder. `(-7)/2 == -3` everywhere
(`tests/test_native_toc.py` div/mod cases, 5-path). The step-budget and template-depth
*faults* are now also consistent across the three bytecode VMs (below); the transpiled
toC/toJS-native paths remain best-effort on those malicious edges (no VM loop), which
does not affect byte-identical output for pure programs.

### 3. Host hooks are the only outside world — *enforced*
No direct file/socket/clock/entropy/`getenv` access bypasses the hook layer in
`vm/picovm.c`, `vm/picovm.js`, or `picoscript_vm.py`; entropy is the declared
`Random.U32` xorshift only.

### 4. Every hook has a declared contract — *target*
Hooks are implemented but not accompanied by a machine-checkable contract declaring
inputs, outputs, ownership, mutability, allocation behaviour, failure modes, and
capability requirement. To be added per hook (a contract table keyed by hook code).

### 5. No hidden allocation in hot hooks — *target*
Allocating hooks (`String.Concat`, `Span.Materialize`, `Crypto.*` digest spans,
`Number.ToString`, …) bump the arena without declaring it. Request-path hooks must
declare arena use (so it can be scoped/rewound) or be forbidden on the hot path.

### 6. Arena scope is explicit — *partial*
`install_request_context` / `setRequestContext` auto-rewind the arena per request
(commit `5a09aa3`), and `Arena.Mark/Rewind/Reset` are available. Not every entry path
declares a scope; non-server invocations rely on the caller.

### 7. Seal consumes ownership — *partial (diagnosed)*
The Python VM traps mutation of sealed preamble/headers
(`picoscript_vm.py`, "I3 violation"; `tests/test_io_hooks.py::test_i3_header_after_seal_rejected`).
**Diagnosis:** full enforcement spans the EL0/EL1 boundary — per `docs/PIOS_IO_BINDING.md`
[D6], the strongest form is an `iso` (move-only) lease consumed at `seal` so use-after-seal
is a **compile-time** error (AOT, zero runtime cost), backstopped by a runtime ownership
flag on the descriptors. The PicoScript-side `Resp.*` is a simulation for parity tests;
the authoritative enforcement is the PIOS kernel's. Tractable next step on the VM side:
extend the post-seal trap to body/trailer/control ops (not just headers). The compile-time
iso-lease check is a larger frontend feature.

### 9. Literals are immutable — *partial (diagnosed; needs const segment)*
Literals are interned into a deduplicated pool growing down from `0x8000` (commit
`d550b7c`). **Diagnosis:** the pool is populated by `Memory.Set` ops emitted *in the
bytecode itself* (before each `Span.Make`), so at runtime the VM cannot distinguish a
compiler const-write from a user `Memory.Set` into the same address range — they are the
same op. Enforcing read-only therefore needs an **architectural change**: relocate
literals into a separate const segment loaded at init (not via `Memory.Set` bytecode) and
range-check user writes against it. That is a deliberate redesign, deferred; flagged so no
one assumes literals are tamper-proof today.

### 8. Spans are fat and bounded — *enforced*
Every buffer is `ptr+len` (`pv_span_p`/`pv_span_n` in C; `{ptr,len}` in JS/Python).
No `strlen`/`strcpy` authority over script data.

### 9. Literals are immutable — *partial (diagnosed; needs const segment)*
Literals are interned into a deduplicated pool growing down from `0x8000` (commit
`d550b7c`). **Diagnosis:** the pool is populated by `Memory.Set` ops emitted *in the
bytecode itself* (before each `Span.Make`), so at runtime the VM cannot distinguish a
compiler const-write from a user `Memory.Set` into the same address range — they are the
same op. Enforcing read-only therefore needs an **architectural change**: relocate
literals into a separate const segment loaded at init (not via `Memory.Set` bytecode) and
range-check user writes against it. That is a deliberate redesign, deferred; flagged so no
one assumes literals are tamper-proof today.

### 10. Bytecode verification before execution — *partial (typed runtime traps; no pre-pass)*
There is still no load-time verifier pass, but ad-hoc traps are now **typed and
consistent**: the C runtime has `ctx->fault` (`PV_FAULT_*`), and JS now throws on an
unknown opcode instead of silently halting. The dangerous *computed* cases (jump targets,
budget) are checked at the moment they occur (INV-11/12). The 4-bit opcode field is
exhaustive (every value 0–15 is a defined op), and register indices are 4-bit, so
"invalid opcode/register" cannot arise from a well-formed word — the remaining value of
a pre-pass is rejecting unknown hook ids / malformed `jmptab` up-front, which is future
work.

### 11. Computed jumps are range-checked — *enforced*
**Fixed:** indirect/indexed `JUMP`, taken `BRANCH`, and `CALL` targets are range-checked
(`0 ≤ t ≤ len`; `t == len` is a clean halt) in all three VMs. Out of range →
`PV_FAULT_BAD_JUMP` (C) / `raise`/`throw` (Python/JS). `tests/test_vm_safety.py` asserts
identical faulting. (jmptab lowers to an indexed JUMP, so it shares this check.)

### 12. No unbounded loops without budget — *enforced (fault), per-capsule budget still target*
**Fixed:** the C step-budget exceed now sets `PV_FAULT_STEP_BUDGET` and halts instead of
silently `break`-ing, matching Python/JS which raise. `tests/test_vm_safety.py` asserts
all three fault. A per-*capsule* time budget and cooperative-yield requirement remain a
target (the global step budget is the current mechanism).

### 13. Register spill is compiler-owned — *enforced*
**Fixed:** the in-browser `picoc.js` compiler now auto-spills (`allocateOrSpill` +
`legalizeSpills`, mirroring `picoscript_il`) instead of throwing
`RegisterPressureError`, and produces **byte-identical** bytecode to the Python
compiler. `tests/test_spill.py` now asserts `picoc.js` bytecode == Python bytecode for
the >16-live programs (the gap that previously hid this).

### 14. Numeric overflow / division policy is explicit — *enforced*
**Fixed:** 32-bit wrap was already consistent; signed division/modulo now truncates
toward zero on every path (see INV-2). The policy is uniform and the divergence is gone.

### 15. Deterministic mode exists — *target*
The RNG seed is hardcoded (`picoscript_vm.py` `rng_state=0x2545…`; `vm/picovm.c`
`pv_init`) rather than host-injected, and there is no flag to disable wall-clock /
randomness / unordered iteration. Map/object iteration (`for..in`, dict scans in
template `{{#each}}` existence checks) is currently order-insensitive but fragile.
Target: an explicit deterministic mode + injectable seed.

### 16. Case-insensitive namespaces are canonicalised — *enforced*
`canon_host(ns, method)` lowercases and resolves to the canonical hook
(`picoscript_il.py:63-66`); `Net.Status`, `NET.STATUS`, `net.status` map to one code.

### 17. Capability check before hook dispatch — **violated** (the killer rule)
No permission/grant check precedes any hook. Dispatch is a bare lookup
(`picoscript_vm.py:99-103`; `vm/picovm.js:175-239` by name; `vm/picovm.c:908-1032`
`if (hook == …)`). Any program that can encode a hook code can call it — bindings are
ambient. Target: a per-capsule capability set checked before dispatch (cf. the
`FIRE_SW_IRQ` permission model).

### 18. Hook failures are typed — *partial (diagnosed; design tension)*
Most flagged sentinels are **value-domain results, not errors**, and are intentional:
`String.IndexOf`→`-1` ("not found", as in most languages), `Number.Parse`→`0`,
`Queue.Dequeue` on empty→`0`, missing card→`0`. The 2-in/1-out host ABI has no separate
error channel, so a "typed status" would require an ABI change (e.g. a side error
register), which would break byte-parity and existing programs. Separately, the C default
host **silently ignores** an unimplemented hook *by design* — that is the host-injection
model (`DateTime`/`Context`/`Auth`/`X509` etc. are supplied by a real host, not the
deterministic default), so faulting there would be wrong. **Recommendation (needs an ABI
decision):** introduce an out-of-band typed-status register for genuinely fallible
decoders (parse/decode/crypto-verify) without changing their primary return value, and
keep value-domain sentinels as-is. Deferred pending that decision (not a security gap —
the capability gate, INV-17, governs access).

### 19. Template rendering is bounded — *partial (depth fault added)*
**Improved:** nesting beyond `TPL_MAXDEPTH` (32) now raises a typed fault on all three
bytecode VMs (`PV_FAULT_TEMPLATE`/`raise`/`throw`), replacing C's previous buggy silent
truncation; `tests/test_vm_safety.py` asserts identical faulting. C still caps model
size (512) and key length (512). Remaining target: bound `{{#each}}` iteration count and
total output size uniformly (a huge each can still grow output until the arena fills).

### 20. JSON/HTTP parsers are budgeted — *partial (JSON depth unified)*
**Fixed (JSON depth):** `Http.ParseJson` now stops at depth > 64 on all three VMs
(Python/JS thread a depth counter matching C's `pjs_emit`), silent truncation that is
byte-identical across all five paths (`tests/test_native_toc.py` `json_depth_cap`).
Remaining target: `ParseQuery`/`ParseForm` are unbounded on *every* path (no parity
issue, but a DoS one) and JSON token-count/string-length/total-bytes caps.

### 21. Source card is truth — *target*
`lower_to_c` / `lower_to_js` emit only an "AUTO-GENERATED" comment
(`picoscript_il.py:713-718,915-923`) — no source hash, compiler version, or target
profile. Target: a provenance header on every artefact.

### 22. Generated artefacts are disposable — *convention*
`vm/pico_hooks.js` / `vm/pico_hooks.h` are marked "AUTO-GENERATED … do not edit by
hand". Honoured by convention; no automated check that the generated file matches its
source.

### 23. ABI version is embedded — *target*
No version/magic in the bytecode container, host-hook table, or descriptor ABI, and no
load-time refusal of a mismatch (`picoscript_vm.py:1187-1226`). Target: an embedded
ABI version, checked at load.

### 24. Parity runner is the gatekeeper — *partial*
Strong multi-runtime parity tests exist for the pure namespaces (`tests/test_native_toc.py`,
`tests/test_pipeline.py`, per-namespace tests). Gaps: host-injected namespaces
(`DateTime`, `Context`, `Auth`, `X509`, `Environment`, `Locale`, most of `Http`/`Html`)
are unimplemented/untested, and there is no CI gate forcing "new hook ⇒ parity test".

### 25. Debug trace is structured — *target*
Traps are bare `RuntimeError`/`throw` strings with no structured record of source span,
IL op, bytecode pc, hook id, capsule id, or binding id. Target: a structured trap record.
