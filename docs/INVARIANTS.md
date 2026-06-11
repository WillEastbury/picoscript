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
| 4 | Every hook has a declared contract | enforced (table) |
| 5 | No hidden allocation in hot hooks (declare arena use or forbid) | enforced (no-alloc mode) |
| 6 | Arena scope is explicit; scope exit rewinds or transfers | enforced (handler scope) |
| 7 | Seal consumes ownership (use-after-seal = compile error or trap) | enforced (compile-time iso-lease + runtime sim; EL1 owner-flag external) |
| 8 | Spans are fat and bounded (ptr+len); no null-terminated authority | enforced |
| 9 | Literals are immutable (const segment + write-trap) | enforced |
| 10 | Bytecode verification before execution | enforced (static pre-pass) |
| 11 | Computed jumps are range-checked | enforced |
| 12 | No unbounded loops without budget (yield or fault) | enforced |
| 13 | Register spill is compiler-owned (no RegisterPressureError on real code) | enforced |
| 14 | Numeric overflow / division policy is explicit and identical per target | enforced |
| 15 | Deterministic mode exists (disable clock/random/unordered iteration) | enforced (seed inject) |
| 16 | Case-insensitive namespaces are canonicalised | enforced |
| 17 | Capability check before hook dispatch | enforced (mechanism) |
| 18 | Hook failures are typed (no magic -1/0) | enforced (status channel) |
| 19 | Template rendering is bounded (depth/each/recursion/output) | enforced |
| 20 | JSON/HTTP parsers are budgeted (depth/token/length/bytes) | enforced (depth + input-bounded) |
| 21 | Source card is truth (artefacts carry source hash + compiler version + profile) | enforced |
| 22 | Generated artefacts are disposable (never edited as source) | convention |
| 23 | ABI version is embedded and checked (refuse mismatch) | enforced (module container) |
| 24 | Parity runner is the gatekeeper (every hook/opcode/lowering has parity tests) | enforced (gate) |
| 25 | Debug trace is structured (span, IL op, pc, hook id, capsule, binding) | enforced (source-span+IL-op; capsule/binding = PIOS) |

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

### 4. Every hook has a declared contract — *enforced (machine-readable table)*
`hook_contracts.py` declares, for every hook in the registry (259), its namespace,
method, **capability class** (mirroring the INV-17 classifier), and **arena-allocation
flag** (INV-5). `tests/test_hook_contracts.py` asserts completeness + spot-checks. Future
work can extend each entry with input/output/ownership/failure-mode fields, but the
contract table and its capability + allocation declarations now exist and are tested.

### 5. No hidden allocation in hot hooks — *enforced (declared + no-alloc mode)*
Two layers: (1) allocation is **declared** per hook via the `allocates` flag in
`hook_contracts.py`; (2) a runtime **no-alloc mode** *forbids* it on demand — `ctx->no_alloc`
(C), `HostApi.no_alloc` / `PicoVM(no_alloc=True)` (Python), `new PicoVM({noAlloc})` (JS),
`PICOVM_NOALLOC` env. When set, the single arena-allocation choke point (`pv_arena_finish`
/ `_new_span_bytes`) faults `PV_FAULT_ALLOC`=9 instead of bumping the arena. Default off →
no behaviour change; a host enables it for a hot/request path so a hook that would
allocate (e.g. `String.Concat`, `Crypto.Sha256`) traps rather than silently allocating.
`tests/test_vm_safety.py` proves an allocating hook faults (9) under no-alloc on
Python/C/JS while a non-allocating program is unaffected.

### 6. Arena scope is explicit — *enforced (handler scope + manual API)*
The invariant's subject is **handlers**, and every handler runs inside an auto-rewound
scope: `install_request_context` / `setRequestContext` snapshot `(arena_top, span_count)`
and rewind to it on each request (commit `5a09aa3`, `tests/test_arena.py`), so a long-lived
server VM cannot leak across requests. `Arena.Mark/Rewind/Reset` provide explicit manual
scoping for nested regions. A one-shot non-handler invocation needs no scope (the process
exits and frees everything). The remaining refinement is automatic scoping around
arbitrary (non-request) entry points, which currently relies on the caller.

### 7. Seal consumes ownership — *enforced (compile-time iso-lease + runtime sim backstop; EL1 owner-flag external)*
**Authoritative enforcement is layered** (`docs/PIOS_IO_BINDING.md` I2/I3, D6):
(1) **compile-time** — `seal`/`respond`/`end` *consume* the `iso` (move-only) response arena, so
**use-after-seal is a compile error** in the AOT compiler (zero runtime cost); (2) a **runtime
owner flag** (`pooldesc.owner = kernel`), authoritative in the **PIOS kernel (EL1)**, backstops
dynamically-assembled descriptors.

**(1) is now built — `verify_response_ownership`** (`picoscript_il.py`, mirrored byte-for-byte in
`vm/picoc.js` `verifyResponseOwnership`), run as a compile gate inside `lower_to_bytecode_safe` /
`lowerToBytecode` (default on; early-out when a program has no `Resp.*` op). It is a forward
**must-dataflow** over the IL control-flow graph: per-point state is a 4-bit mask
(SEALED/ENDED/BODY/STREAM_CLOSED, all monotonic) merged with **AND** over predecessors, so a
violation is flagged only when it holds on *every* path to that op. Branchy code that seals (or
starts the body) on only one arm is therefore never falsely rejected. Compile errors raised:
`Resp.Status`/`Header` after `Seal`; any graph op after `End`/`Respond`/`Abort` (use-after-end);
double explicit `Seal`; `Header` after a body `Write`; `Write` after `EndStream`. (EndStream-
without-seal is a may-violation left to the runtime sim.) Because AND-merge is commutative and the
check walks reachable points in ascending index order, the Python and JS gates return **byte-
identical accept/reject decisions and the identical first violation** — proven in
`tests/test_iso_lease.py` (the INV-24 gatekeeper), across the C and Python dialects.

**Runtime sim backstop (fixture)** — the PicoScript-side `Resp.*` (mirrored in `picoscript_vm.py`
and `vm/picovm.js`; the C VM does not host it) faithfully models the spec's phased descriptor
graph and traps the same conditions at runtime for gate-bypassed / dynamically-assembled graphs
(`tests/test_io_hooks.py`): I3 (`seal` freezes preamble+headers only — body `Write` after seal is
correct in *stream* mode per §4 "seal ≠ complete"; re-`Seal` traps), I6 phase order (`Header` after
body; `EndStream` closes the stream phase; `EndStream` outside stream mode), and I2 (one open graph;
anything after a terminal verb traps). A `unary`/`stream` mode flag records the lifecycle.

**External (not VM/compiler):** the EL1 kernel owner-flag backstop for descriptors assembled
outside the static check's view.

### 8. Spans are fat and bounded — *enforced*
Every buffer is `ptr+len` (`pv_span_p`/`pv_span_n` in C; `{ptr,len}` in JS/Python).
No `strlen`/`strcpy` authority over script data.

### 9. Literals are immutable — *enforced (const segment + write-trap)*
String literals are interned into a deduplicated pool that grows **down** from `0x8000`
(commit `d550b7c`). The compiler now writes those bytes with a dedicated `Memory.SetConst`
hook (`0x5F`) instead of `Memory.Set` (`emit_str_span` in `picoscript_cfront.py`,
`picoscript_basic.py` and the JS frontend `vm/picoc.js`, so the literal bytecode stays
byte-identical across frontends). Each VM tracks `const_floor` — the lowest literal
address, initialised to `0x8000` and lowered only by `Memory.SetConst`. A *user*
`Memory.Set` whose address lands in `[const_floor, 0x8000)` now faults
`PV_FAULT_CONST_WRITE`=10 (`vm/picovm.c` `pv_default_host`, `picoscript_vm.py` HostApi,
`vm/picovm.js` `_host`); `Memory.SetConst` is the only writer allowed there, and it also
lowers the floor. Reads of the const segment are unrestricted. `tests/test_vm_safety.py`
proves a user write into the literal region faults (10) identically on Python / C / JS,
while a below-floor user write and the literal's own rendering succeed.

### 10. Bytecode verification before execution — *enforced (static pre-pass)*
`run` now performs a static verification pass **before executing any instruction**
(`pv_verify` in C, `PicoVM._verify` in Python, `PicoVM.verify` in JS): every immediate
`JUMP`/`CALL`/`BRANCH` target is range-checked, and an out-of-range target faults
(`PV_FAULT_BAD_JUMP`) up front — so a malformed/tampered module is rejected with **no side
effects** (`tests/test_vm_safety.py` proves a side-effecting instruction before a bad jump
produces no output). Register/indexed jumps are dynamic and stay runtime-checked (INV-11).
Opcode and register fields are 4-bit and therefore structurally always valid; unknown host
hook ids are left to the host-injection model (the default host ignores them, a real host
supplies them). Combined with the module-container ABI check (INV-23), a loaded program is
validated for structure + ABI before it runs.

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

### 15. Deterministic mode exists — *enforced (injectable seed)*
The RNG seed is now host-injectable: `PicoVM(seed=...)` (Python), `new PicoVM({seed})`
(JS), and `PICOVM_SEED` env in the harnesses (C sets `ctx->rng_state`). Default seeds are
unchanged, so a program is already reproducible on a given runtime, and a host can pin or
vary the seed for replay/tests. `tests/test_determinism.py` proves per-runtime
reproducibility (same seed → same Random sequence) and seed-sensitivity on Python/C/JS.
Note: Random is host-injected (INV-3), so the 64-bit (Python/C) vs 32-bit (JS) generators
intentionally differ *across* runtimes; map/dict iteration is used only for existence
checks (order-insensitive). A unified cross-runtime generator + a single "deterministic
mode" flag bundling clock+random+iteration is a possible future refinement.

### 16. Case-insensitive namespaces are canonicalised — *enforced*
`canon_host(ns, method)` lowercases and resolves to the canonical hook
(`picoscript_il.py:63-66`); `Net.Status`, `NET.STATUS`, `net.status` map to one code.

### 17. Capability check before hook dispatch — *enforced (the killer rule: bindings are not ambient)*
Every binding hook is classified into a capability class — `KERNEL`=1, `QUEUE`=2,
`RANDOM`=4, `STORAGE`=8, `TIME`=16, `NET`=32, `CONTEXT`=64, `AUTH`=128, `ENV`=256
(`CAP_ALL`=0x1FF) — by an identical classifier on all three runtimes (`pv_hook_cap` in
`vm/picovm.c`, `hook_cap` in `picoscript_vm.py`, `hookCap` in `vm/picovm.js`). Before
dispatch the VM checks the capsule's granted set against the hook's class and faults
`PV_FAULT_CAPABILITY`=8 when the binding is not permitted — hook existence is no longer
sufficient. Pure hooks (`Status`/`Memory`/`String`/`Number`/`Maths`/`Span`/`Io`/`Template`
…) are class `0` and are never gated. The default grant is `CAP_ALL` (backward-compatible);
the harness sets the mask via `PICOVM_CAPS`, and a capsule's caps are part of the VM
context. `tests/test_vm_safety.py` proves `Random.U32` is denied (fault 8) without
`CAP_RANDOM` identically on Python / C / JS, while pure `Io.WriteByte` stays ungated.
Committed `38755fb`.

### 18. Hook failures are typed — *enforced (out-of-band status channel)*
A fallible hook now records a **typed status** in an out-of-band per-VM register
(`ctx->host_status` in C, `HostApi.host_status` in Python, `this.hostStatus` in JS),
readable via the new `Status.Last()` hook (code `0x5E`): `0`=OK, `1`=NOT_FOUND,
`2`=PARSE_ERROR, `3`=EMPTY. `Number.Parse`, `String.IndexOf`, and `Queue.Dequeue` set it
(0 on success, the typed code on failure). The **primary return value is unchanged** — the
value-domain sentinels (`-1`, `0`) still flow for code that ignores them — so existing
programs are byte-identical and parity holds; programs that care call `Status.Last()` right
after the fallible op (errno-style). The three runtimes set identical codes for identical
inputs (`tests/test_native_toc.py` checks `Status.Last` 5-path; the parse success condition
was aligned so empty/invalid input is `PARSE_ERROR` everywhere). Adding the hook bumped the
host-hook-table version, which the INV-23 module check picks up automatically. Remaining
hooks can adopt the same channel incrementally.

### 19. Template rendering is bounded — *enforced*
All four explosion vectors are capped, faulting `PV_FAULT_TEMPLATE`=7 identically on
Python/C/JS: **nesting depth** (`TPL_MAXDEPTH`=32), **`{{#each}}` iteration count**
(`TPL_MAXEACH`=100000), **total output** (`TPL_MAXOUTPUT`=256 KB, checked each render
step), and **model entries** (`TPL_MAXMODEL`=512). The model cap also closed a prior
parity divergence — C silently used only the first 512 model entries while Python/JS used
all; now all three fault on a >512-entry model. `tests/test_vm_safety.py` exercises the
model-cap and output-cap faults across the three VMs.

### 20. JSON/HTTP parsers are budgeted — *enforced (depth + input-bounded)*
`Http.ParseJson` caps nesting depth at 64, faulting/truncating identically on all paths
(INV-20 recursion vector). `ParseQuery`/`ParseForm`/`ParseJson` produce output
proportional to their input with **no amplification** (unlike the template `{{#each}}`
repetition vector), and the input is a span in the arena, which is itself bounded — so the
parsers are inherently input-budgeted. An explicit hard input-byte cap could be layered on
if a deployment wants a tighter limit than the arena size, but there is no unbounded-growth
path.

### 21. Source card is truth — *enforced*
`lower_to_c` and `lower_to_js` now emit a provenance header on every artefact:
compiler version, ABI version, target profile (`c`/`js`), and a deterministic
`source_hash` (sha256 of the IL, stable across runs, changes with the program).
`tests/test_provenance.py` asserts presence + determinism + that a different program
yields a different hash. An artefact can now be traced to its source IL.

### 22. Generated artefacts are disposable — *convention*
`vm/pico_hooks.js` / `vm/pico_hooks.h` are marked "AUTO-GENERATED … do not edit by
hand". Honoured by convention; no automated check that the generated file matches its
source.

### 23. ABI version is embedded — *enforced (module container)*
A persisted/shipped program is wrapped in a versioned container
`[MAGIC, ABI_VERSION, HOOK_TABLE_VERSION, count, …words]` (`pico_module.py`
`pack_module`/`load_module`, mirrored in `vm/picovm.js` `packModule`/`loadModule`).
`load` **refuses** (raises `ModuleAbiError`) any magic / ABI-version / hook-table-version
/ length mismatch. The hook-table version is a content hash (FNV-1a/32 of the canonical
`code:Ns.Method` lines) that bumps automatically when a hook is added/removed/renumbered,
and is computed **identically** by Python and JS (`tests/test_abi_version.py` asserts they
agree — `0xE7771083` today — and that a Python-packed module loads in JS).
*Design choice (Option C):* the **raw in-memory** word array that the VMs and parity tests
execute stays headerless — the container is applied only when bytecode is saved/loaded/
distributed — so the byte-identical hot path is untouched (zero parity/test disruption). The
C runtime has `pv_load_module` (validating the generated `PV_MODULE_*` / `PV_HOOK_TABLE_VERSION`
in `pico_hooks.h`); `tests/test_abi_version.py` confirms C runs a Python-packed module and
rejects a tampered hook-table version, so all three runtimes agree on the wire format.

### 24. Parity runner is the gatekeeper — *enforced (automated gate)*
`tests/test_parity_gate.py` parses the full hook registry (`vm/pico_hooks.js`) and fails
if any non-allowlisted host namespace lacks a parity-test reference — so a new pure hook
in a new namespace without a test trips the gate. The allowlist holds the host-injected
namespaces (`DateTime`/`Context`/`Auth`/`X509`/`Environment`/`Locale`/`Kernel`/`Req`/
`Resp`/`Net`) plus the low-level primitives (`Descriptor`/`Lease`/`Thread`), each with a
justification. The gate caught a real gap on first run (`Queue` was untested) which was
closed with a 5-path `queue_depth` test rather than allowlisted.

### 25. Debug trace is structured — *enforced (source-span + IL-op; capsule/binding = PIOS)*
Faults carry machine coordinates `code` + `pc` + `detail` on all three VMs (C
`ctx->fault/fault_pc/fault_detail`, harness prints `FAULT <code> <pc> <detail>`; Python
`PicoFault(code, pc, detail, message)`, a `RuntimeError` subclass; JS `Error` with
`.fault/.pc/.detail`). The compiler emits a **side-band debug table**
`pc -> (src_off, op, ns, method)` (`lower_to_bytecode_safe(..., debug=)` /
`lower_to_bytecode_with_debug`; JS `picoc.js` `compileWithDebug`) that leaves the word
stream **byte-identical** — it is a separate symbol artifact, like a stripped binary plus a
symbol file. `symbolize(code, pc, detail, debug, source)` (Python `picoscript_il`, JS
`picoc.js`) resolves a fault into `{code, fault, pc, detail, op, target, off, line, col,
source_line}`. The debug table **and** the symbolize() record are byte-identical between
the Python and JS toolchains, and a fault from the Python VM **or** the portable C VM at a
given pc symbolicates to the same record — proven in `tests/test_debuginfo.py`.

Source offsets are stamped onto each IL inst via `ILBuilder.cur_pos` (the tokenizers carry
token offsets; the parser stamps each statement; the lowerer sets `cur_pos` at statement
dispatch). The **C frontend** is wired with full byte offsets (line + column);
BASIC/Python/English currently symbolicate **IL-op + hook id + pc** (their statement-line
stamping is the next increment). The embedded **C runtime stays lean** — it emits only
`pc`, with no on-device debug table (a deliberate performance-invariant choice);
symbolication happens off-device (developer tooling now; the PIOS kernel in production).

The remaining two fields — **capsule id** and **binding id** — are PIOS / EL1 runtime
context that neither the compiler nor the standalone VM can produce; the exact work handed
to the kernel build (ship the debug table in the INV-23 module container, reuse
`symbolize()`, add the two context fields) is specified in `docs/INV25_PIOS_TRACE.md`.
