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
| 2 | Lowering parity — VM/toC/toJS/native produce identical observable output | **violated** |
| 3 | Host hooks are the only outside world | enforced |
| 4 | Every hook has a declared contract | target |
| 5 | No hidden allocation in hot hooks (declare arena use or forbid) | target |
| 6 | Arena scope is explicit; scope exit rewinds or transfers | partial |
| 7 | Seal consumes ownership (use-after-seal = compile error or trap) | partial |
| 8 | Spans are fat and bounded (ptr+len); no null-terminated authority | enforced |
| 9 | Literals are immutable (const pool unless copied) | partial |
| 10 | Bytecode verification before execution | **violated** |
| 11 | Computed jumps are range-checked | **violated** |
| 12 | No unbounded loops without budget (yield or fault) | partial |
| 13 | Register spill is compiler-owned (no RegisterPressureError on real code) | **violated** |
| 14 | Numeric overflow / division policy is explicit and identical per target | **violated** |
| 15 | Deterministic mode exists (disable clock/random/unordered iteration) | target |
| 16 | Case-insensitive namespaces are canonicalised | enforced |
| 17 | Capability check before hook dispatch | **violated** |
| 18 | Hook failures are typed (no magic -1/0) | **violated** |
| 19 | Template rendering is bounded (depth/each/recursion/output) | partial |
| 20 | JSON/HTTP parsers are budgeted (depth/token/length/bytes) | partial |
| 21 | Source card is truth (artefacts carry source hash + compiler version + profile) | target |
| 22 | Generated artefacts are disposable (never edited as source) | convention |
| 23 | ABI version is embedded and checked (refuse mismatch) | target |
| 24 | Parity runner is the gatekeeper (every hook/opcode/lowering has parity tests) | partial |
| 25 | Debug trace is structured (span, IL op, pc, hook id, capsule, binding) | target |

## Detail and evidence

### 1. Same source, same semantics — *enforced*
One IL pipeline; the Python and JS frontends emit byte-identical bytecode
(`tests/test_pipeline.py` `check_jscompile`, `tests/test_io_hooks.py`). Subject to the
INV-2 caveats below.

### 2. Lowering parity is mandatory — **violated**
The runtimes must produce identical observable output. Known divergences:
- **Signed division** (proven): Python VM uses floor division `a // b`
  (`picoscript_vm.py:1281`), while C uses `int32 a / b` (`vm/picovm.c:1554`) and JS uses
  `(a / b) | 0` (`vm/picovm.js:115`) — both truncate toward zero. For `(-7) / 2` the
  Python VM yields **-4** but C/JS yield **-3**. The constant-folders diverge the same
  way (`picoscript_il.py:261` `av // bv` vs `vm/picoc.js` optimizer `Math.trunc`), which
  can also break byte-identical bytecode for a foldable negative division.
- **Budget-exceeded behaviour**: Python/JS raise (`step budget exceeded`), C silently
  `break`s (see INV-12) — a program at the step limit halts cleanly on C but faults on
  Python/JS.
- **Resource bounds**: the C runtime caps template depth/model size and JSON depth
  (see INV-19/INV-20) where Python/JS do not — output diverges near those limits.
- **Register pressure**: a >16-live-value program compiles on Python (spills) but
  throws on the in-browser JS compiler (see INV-13).

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

### 7. Seal consumes ownership — *partial*
The Python VM traps mutation of sealed preamble/headers
(`picoscript_vm.py:780-807`, "I3 violation"). There is no compile-time use-after-seal
check, and body/trailer/control access is not fully fenced after seal; C/JS trapping
is weaker. Target: AOT iso-lease consumption at seal (D6) + runtime ownership-flag
backstop on all response descriptors.

### 8. Spans are fat and bounded — *enforced*
Every buffer is `ptr+len` (`pv_span_p`/`pv_span_n` in C; `{ptr,len}` in JS/Python).
No `strlen`/`strcpy` authority over script data.

### 9. Literals are immutable — *partial*
String/number/template literals live in a deduplicated const pool growing down from
`0x8000` (commit `d550b7c`). Immutability is by convention only: the arena is
byte-addressable and nothing prevents `Memory.Set` into the const-pool region. Target:
mark the const region read-only (trap on write).

### 10. Bytecode verification before execution — **violated**
No verifier pass in any runtime. `load()` stores words and runs; bad opcodes trap
ad hoc mid-execution (`picoscript_vm.py:1187,1265-1266`; `vm/picovm.js:75-78,133-134`;
`vm/picovm.c:1515-1533`). Target: a load-time verifier rejecting invalid opcode, bad
jump target, bad register, unknown hook id, return-stack underflow, malformed jmptab.

### 11. Computed jumps are range-checked — **violated**
Indirect/indexed `JUMP` writes a raw `& 0xFFFF` PC with no bounds or instruction-
boundary check in all three VMs (`picoscript_vm.py:1240-1246`; `vm/picovm.js:120-124`;
`vm/picovm.c:1561-1565`). A bad selector can jump anywhere in range. (jmptab lowers to
an indexed JUMP over an inline table — same unchecked path.)

### 12. No unbounded loops without budget — *partial*
A fixed step budget exists (`max_steps`, default 1,000,000). Python/JS **fault** on
exceed (`picoscript_vm.py:1197-1203`; `vm/picovm.js:82-84`); **C silently `break`s**
(`vm/picovm.c:1520-1522`) — both an INV-12 weakness (silent stop, not a trap) and an
INV-2 divergence. No per-capsule time budget or cooperative-yield requirement.

### 13. Register spill is compiler-owned — **violated**
The Python compiler auto-spills (`lower_to_bytecode_safe` → `spill=True` →
`_legalize_spills`; "always beats a RegisterPressureError on real code",
`picoscript_il.py:435`). The in-browser JS compiler does **not**: it throws
`"register pressure exceeds 16 live values; simplify the program"`
(`vm/picoc.js:166`). Normal code that exceeds 16 live values compiles on Python and
fails in the browser.

### 14. Numeric overflow / division policy is explicit — **violated**
32-bit wrap is consistent (`& MASK32` / `int32_t` / `| 0`), but signed **division**
(and the same class for modulo) is **not**: Python floors, C/JS truncate toward zero
(see INV-2). The policy is neither declared nor uniform. Target: pick one rule
(truncate-toward-zero is the C/JS/most-CPU convention) and make all paths — including
the const-folders — obey it.

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

### 18. Hook failures are typed — **violated**
Magic sentinels throughout: `Queue.Dequeue`→`0`, `String.IndexOf`→`-1`/`0xFFFFFFFF`,
`Number.Parse`→`0`, `pv_load` missing card→`0`, `pv_card_slot`→`-1`
(`picoscript_vm.py:115-118,315-316,336-340`; `vm/picovm.js:235-238,336,379-381`;
`vm/picovm.c:27-48,1083-1094,1174-1191`). Target: typed status / trap semantics.

### 19. Template rendering is bounded — *partial*
C caps it (`TPL_MAXDEPTH 32`, `TPL_MAXMODEL 512`, fixed render stack;
`vm/picovm.c:469-472,651-735`). Python/JS render with unbounded stack / `each` count /
output growth (`picoscript_vm.py:630-749`; `vm/picovm.js:690-749`) — DoS + INV-2
divergence near the C limits.

### 20. JSON/HTTP parsers are budgeted — *partial*
C bounds JSON nesting (`if (depth > 64) return;`, `vm/picovm.c:173-203`); Python/JS
recurse unbounded (`picoscript_vm.py:473-561`; `vm/picovm.js:491-560`). `ParseQuery`/
`ParseForm` are unbounded on every path. Target: uniform depth/token/length/byte caps.

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
