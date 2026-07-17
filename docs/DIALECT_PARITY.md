# Dialect / frontend parity — what's NOT equivalent

The README claims C-style/BASIC/Python-style/English produce **byte-identical
bytecode** for equivalent programs, and that claim holds (`tests/test_pipeline.py`,
`tests/test_examples_parity.py`, `tests/test_translator_roundtrip.py`: 70/70).
But "byte-identical output for the constructs both sides support" is a
narrower claim than "every dialect supports every language feature." This
document is the gap list: where the *surface grammars* diverge, not the
runtime/bytecode semantics.

## Two independent AST islands, not one

- **`picoscript_basic.py`'s dataclasses + `Lowerer`** are shared, unchanged,
  by **six** frontends: BASIC, Python-style, English, COBOL, Report,
  Functional (each imports `Lowerer` and the node classes directly —
  `picoscript_cobol.py:13`, `picoscript_report.py:14`,
  `picoscript_functional.py:19`). Each frontend only supplies its own
  tokenizer/parser; the AST shape and lowering are identical code.
- **`picoscript_cfront.py` (C-style) is a fully separate island** — its own
  `Ternary`/`ConstDecl`/`EnumDecl`/`Dispatch`/`ServerMain` dataclasses and its
  own `Lowerer` (`picoscript_cfront.py:229` `Parser`, `:637` `Lowerer`), with
  **zero imports from `picoscript_basic`**. Several of its classes share a
  *name* with a `picoscript_basic` class (e.g. both have `ConstDecl(name,
  value)`) but are different Python types entirely — verified by constructing
  a C-style `ConstDecl` and checking it fails `isinstance` against
  `picoscript_basic.ConstDecl`. C-style bytecode parity with the other three
  is therefore proven only empirically (matching test output), not
  structurally (shared AST).
- **`picoscript_lang.py` (v1) is a third, total island**: its own opcode
  table and bytecode encoder, compiling source straight to bytecode with
  **no PicoIL, no optimizer, no register allocator** ("Each statement
  compiles 1:1 to a single 32-bit instruction. No optimisation. No
  reordering." — `picoscript_lang.py:17-18`). It shares nothing with any
  other frontend.
- **AST-JSON (`picoscript_ast.py`)** is, by construction, exactly the
  `picoscript_basic` AST serialized to JSON — so it has 100% node-kind parity
  with BASIC/Python/English/COBOL/Report/Functional, and **cannot represent a
  C-style or v1 program at all** (their ASTs are different classes). Until
  this audit, `ast_to_json` matched nodes by class *name* rather than
  identity, so a C-style `ConstDecl` would silently half-serialize before
  failing confusingly on the first node without a same-named counterpart
  (e.g. `Decl`); this is now a clear, immediate `TypeError` (see
  `picoscript_ast.ast_to_json`'s identity check, added after this audit —
  `tests/test_ast_frontend.py::test_ast_to_json_rejects_foreign_same_named_node_class`).

## Frontend parser coverage of the shared AST (BASIC/Python/English/COBOL/Report/Functional)

**Update: closed as part of the full-language-equivalence pass.** The table
below is kept for historical record (it reflects the state this audit
originally found), but every gap it documents has since been closed —
English/COBOL/Report/Functional all gained grammar for `TryExcept`/`Raise`/
`OnBlock`/`Dispatch`/`ConstDecl`/`EnumDecl` and other missing node kinds
(see `docs/EXCEPTION_ENGINE.md`, `docs/EVENTING.md`, and the per-frontend
commits). `Dim`/`IncDec`/`Gosub`/`Ternary`/`ServerMain` gaps were each
individually investigated per-frontend and either closed with idiomatic
grammar or deliberately, explicitly left alone with a documented reason
(e.g. Functional's `let` already covers `Dim`'s role; Report's `DATA`
statement intentionally lowers through `Let`, not `Dim`, to avoid breaking
existing bytecode parity; Functional's subroutine calls already work via
function application, making `Gosub` a non-issue rather than a real gap).

| Node kind | BASIC | Python | English | COBOL | Report | Functional |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Dim / IncDec | Y | N \*\* | N \*\* | N \*\* | Y (IncDec only) | N \*\* |
| Ternary | Y (`IIF`) | Y | Y | N | N | Y |
| DoLoop | Y | Y | Y (`Repeat`) | N | N | N |
| Dispatch | Y | Y | Y | Y | Y | Y |
| ForEach | Y | Y | Y | Y | Y | Y |
| Skip (continue) | Y | Y | Y | Y | Y | Y |
| Gosub | Y | Y | Y | Y | Y | N \*\*\* |
| ServerMain | Y | N | Y | N | N | Y |
| **TryExcept / Raise / OnBlock** | **Y** | **Y** | **Y** | **Y** | **Y** | **Y** |
| ConstDecl / EnumDecl | Y | Y | Y | Y | Y | Y |

\*\* Not a real gap: verified that no frontend (including Python-style, the
"reference" for most of these constructs) actually needs a separate
`Dim`/`IncDec` — `Let`/assignment already covers the same ground, and each
frontend that investigated this explicitly (Report, Functional) confirmed
adding a redundant declaration form would risk breaking existing bytecode
parity for no real capability gain. Report gained real `IncDec` via `ADD 1
TO x.`/`SUBTRACT 1 FROM x.` (a genuine, distinct capability from plain
assignment) while correctly leaving `Dim` alone.
\*\*\* Not a real gap: Functional's subroutine calls already work via
function application (`f 1 2` / `f(1,2)`, lowering through generic
`Call`/`CallStmt`), so `Gosub` was never a missing capability — just a
differently-named mechanism already covered.


Verified by grep for each frontend's actual `return <NodeKind>(...)` construction
sites (not just imports — COBOL/Report/Functional all `import` several node
names, e.g. `Dispatch`/`TryExcept`/`Raise`, that their parsers never
construct). Notably **COBOL has no `ForEach` or `Skip`/continue construct at
all** — its iteration is `PERFORM VARYING` (→ `ForTo` only) and it has no
loop-continue keyword.

The standout gap: **`TryExcept`/`Raise`/`OnBlock` (exception handling and
`ON Ns.Method: ... END ON` event blocks) are parseable only from BASIC and
Python-style source** (BASIC gained `TryExcept`/`Raise` grammar after this
audit — see the fix note below; it already had `OnBlock`). COBOL/Report/
Functional even `import` those three names from `picoscript_basic`
(`picoscript_cobol.py:16`, `picoscript_report.py:17`,
`picoscript_functional.py:22`) but never construct them — confirmed by grep:
no `return TryExcept(...)` / `return Raise(...)` in any of the three files.
English has no import of them at all. So a program using try/except or an
`ON` event handler, written in BASIC or Python-style, **cannot be
transliterated by hand into English, COBOL, Report, or Functional syntax** —
there is no surface grammar for it in those four, even though the shared
`Lowerer` would happily lower the node if it existed.

Workflow routes around this entirely rather than closing it: its `RAISE`/`ON`
steps lower to plain `Event.Post(...)` host calls and a hand-rolled
while-loop drain (`picoscript_workflow.py:322-325`, `:381`), not to actual
`Raise`/`OnBlock` AST nodes — so Workflow's "equivalent" feature is really a
different, lower-level desugaring, not evidence that the gap is closed.

## A deeper, pre-existing bug found while closing the BASIC gap

While adding `TryExcept`/`Raise` grammar to BASIC (below), constructing and
lowering a `Raise` node crashed with `AttributeError: 'ILBuilder' object has
no attribute 'raise_sw'` — **this is not new**; it reproduces identically for
Python-style `raise` too (`compile_python` + `lower_to_bytecode_safe` on any
program containing `raise`), so **`Raise` has never actually worked from any
frontend**. Worse than the crash itself: the surrounding code
(`self.b.host("Error", "SetHandler", (v,), None)`) passed the *raised value*
as if it were a jump-target PC — `Error.SetHandler` (`picoscript_vm.py:4076`)
registers a **fault-handler PC** for genuine VM-level faults (bad opcode/jump,
div-by-zero, step budget — see the `PicoFault` handling in `PicoVM.run`,
`picoscript_vm.py:4577-4586`), not an arbitrary script value. Had the
`AttributeError` not fired first, a later fault could have jumped to a
garbage address.

**Fixed** (`picoscript_basic.py`, `Lowerer.stmt`'s `Raise` branch): the
erroneous `SetHandler` call is removed, and `Raise` now lowers to the VM's
actual, already-safe `RAISE` opcode (`raise_irq` — logs `"raise swirq
channel=N"`, a software-IRQ signal, and cannot corrupt VM state). **This does
not make `Raise`/`TryExcept` functionally complete** — `Raise` still doesn't
set any state `Error.Code()` reads, so `TryExcept`'s except-branch is
reachable only if some *other*, unrelated host call happens to set the VM's
fault state first. A real fix needs a new host op (e.g. `Error.Raise(code)`
that sets `_error_code` directly) plumbed through all three VMs plus both
transpilers, and `lower_try()` would need to actually call
`Error.SetHandler` around the try body so genuine faults are caught too —
a separate, larger effort, not attempted here. `tests/test_basic_100.py`
previously asserted the crash itself as expected behavior
(`test_raise_with_value_lowers`); it's been updated to assert the fixed,
safe (but still non-functional-as-exceptions) behavior instead.

## The JS port (`vm/picoc.js`) vs Python — now closed

**Update:** the JS `BLowerer` gap described below has since been closed (as
part of the full-language-equivalence pass): `BLowerer` gained `lowerTry`/
`lowerOnBlock` methods (mirroring `picoscript_basic.py`'s `lower_try`/
`lower_on_block` exactly, including a JS `labelAddr`/`laddr` IL instruction
and its bytecode-assembler width-expansion handling), and `BParser` gained
matching `TRY`/`EXCEPT`/`FINALLY`/`ENDTRY`/`RAISE`/`ON`/`END` grammar. The
`AST_JSON_UNSUPPORTED` blocklist (below) has been removed — the JS AST-JSON
bridge now accepts these node kinds too. Verified byte-identical bytecode
and matching runtime output between the JS and Python compilers for both
constructs (`tests/test_js_port_exception_eventing.py`).

**Historical record** (kept for context — this was correct at the time):
an earlier draft of this document (based on an unverified sub-agent
citation) claimed the JS `BLowerer` already supported
`TryExcept`/`Raise`/`OnBlock`. That was wrong — verified directly at the
time: `BLowerer`'s statement dispatch had **no branch for any of the
three**; an unrecognized node kind fell through to `else throw new
Error("BASIC: cannot lower " + s.t)`. So the JS port could not parse **or**
lower `TryExcept`/`Raise`/`OnBlock` at all, from any source dialect. Given
that, the `AST_JSON_UNSUPPORTED` guard was correct and necessary *at the
time* — it failed clearly in `jsonToAst` instead of letting a node through
to crash confusingly in `BLowerer.stmt`. That condition (no JS lowering
support) is what has now changed.

## Why Workflow and AST are excluded from `tests/test_translator_roundtrip.py`

`LANGS = ["c", "basic", "python", "english", "cobol", "report", "functional"]`
(`tests/test_translator_roundtrip.py:20`) deliberately omits `workflow` and
`ast`:
- **Workflow** is a lossy target by design: `astToWorkflow` falls back to an
  opaque `RAW` English-string step for anything outside its flat step
  vocabulary (ternaries, sub-definitions, etc. — see
  `docs/WORKFLOW_DIALECT.md`), so `X → workflow → X` is not guaranteed
  byte-identical for arbitrary programs the way `X → Y → X` is for the seven
  languages that do share the full AST.
- **AST-JSON** is a serialization of the shared AST, not an independent
  *source* dialect with its own grammar/ambiguity to stress — round-tripping
  it against itself would just test JSON encoding, not translation. (Its
  actual correctness axis — byte-identical bytecode vs. compiling the
  equivalent BASIC/Workflow source directly — is covered by
  `tests/test_ast_frontend.py`, `tests/test_ast_json_cross_lang.py`, and
  `tests/test_workflow_ast_bridge.py` instead.)

## Host namespace / runtime parity — a different axis entirely

Separately from *frontend/grammar* parity above, `docs/NAMESPACE_STATUS.md`
documents namespace/method gaps across the **five execution paths** (Python
VM, JS VM, C VM, native-C transpile, native-JS transpile) — this is a runtime
capability question, not a surface-syntax one:
- **External nondeterministic state** (`DateTime.Now`, `Environment.*`,
  `Maths.Random`, live `Context.*`) is host-injected by design, not a VM
  primitive gap.
- **64-bit-word crypto** (`Sha512`/`Blake2b`/`Blake3`) is deferred because JS
  has no native 64-bit integers.
- **3-argument ops** (`Clamp`, `Lerp`) are ABI-limited (2-in/1-out host
  hooks) rather than missing outright.
- Everything else marked "pure" in that doc is confirmed on all five paths.

## Update: the exception engine is now real

The sections above (and point 3/4 below) described `TryExcept`/`Raise` as a
"documented, safe no-op" and left "build the real exception engine" as an
explicitly deferred, separate task. That task is now done — see
**`docs/EXCEPTION_ENGINE.md`** for the full design (a handler *stack*, a new
`Error.Raise`/`Error.PopHandler` host op pair, and a `laddr` IL instruction
for loading a label's address as a value). Scope actually delivered:

- **Fully working**: BASIC and Python-style source, on **both** interpretive
  bytecode VMs (Python `picoscript_vm.py` and JS `vm/picovm.js` — they share
  byte-identical bytecode, so implementing this once at the IL/bytecode
  layer covers both). Nested try/except, genuine VM faults, and script-level
  `Raise` are all covered by `tests/test_exception_engine.py`.
- **Still not done, and explicitly rejected rather than silently
  mis-compiled**: the native C transpile (`lower_to_c`) and native JS
  transpile (`lower_to_js`) backends — neither has a PC-addressable /
  fault-catching model compatible with this mechanism yet (see
  `docs/EXCEPTION_ENGINE.md`'s "Scope" section for why). Feeding a program
  using `TryExcept`/`Raise` to `--as c` or `--as js` (native) now raises a
  clear `ValueError` naming the limitation, instead of emitting silently
  wrong code.
- **Still not done**: propagating `TRY`/`EXCEPT`/`RAISE`/`ON` grammar to
  English/COBOL/Report/Functional, or building `TryExcept`/`Raise`/`OnBlock`
  support in the JS `BLowerer` (`vm/picoc.js`) — this remains a real,
  separate task (see point 4 below, which is otherwise unchanged: the JS
  compiler still can't parse or lower these at all, from any dialect).

## Bottom line

- **Control flow, arithmetic, calls, host namespaces**: equivalent everywhere
  they're claimed to be (proven by the 70/70 translator round-trip suite +
  five-path VM/transpile parity tests).
- **Exception handling (`TryExcept`/`Raise`)**: a REAL, working mechanism on
  the two interpretive bytecode VMs (Python + JS) — see
  `docs/EXCEPTION_ENGINE.md`. As of the full-language-equivalence pass, ALL
  SIX shared-AST frontends (BASIC/Python/English/COBOL/Report/Functional)
  have grammar for it, and the JS compiler (`vm/picoc.js`) can lower it from
  any of them, byte-identical to Python. Native C/JS transpile remain
  unsupported, clearly rejected at compile time rather than silently broken
  (an architectural limitation, not a "didn't get to it yet" gap — see
  `docs/EXCEPTION_ENGINE.md`'s scope section).
- **Event blocks (`OnBlock`)**: was a real dead-code bug (the compiled
  subroutine was never reachable) — now fixed, see `docs/EVENTING.md`, and
  (as of the equivalence pass) available in all six shared-AST frontends and
  the JS compiler, byte-identical to Python.
- **Grammar parity across BASIC/Python/English/COBOL/Report/Functional**: as
  of the full-language-equivalence pass, all six frontends support the same
  AST node-kind surface (see the coverage table above), each verified
  byte-identical to BASIC/Python-style for every newly-added construct.
  `Dim`/`IncDec`/`Gosub`/`ServerMain` gaps that remain are each individually
  investigated and either not real gaps (equivalent capability already
  exists under a different name/mechanism) or intentionally left alone to
  avoid breaking existing bytecode parity — not overlooked.
- **C-style** gained real `try`/`catch`/`finally`/`raise`/`on Ns.Method { }`
  support too (`picoscript_cfront.py`'s own, independent `TryCatch`/`Raise`/
  `OnBlock` node classes + `lower_try`/`lower_on_block` methods) — verified
  byte-identical bytecode vs. the equivalent BASIC source
  (`tests/test_cfront_exception_eventing.py`), because cfront shares the
  same underlying `picoscript_il.ILBuilder` (including `label_addr`) and the
  same `Error.SetHandler`/`PopHandler`/`Raise`/`Clear` host ops as the BASIC
  family — the mechanism is identical, just re-expressed against cfront's
  own AST/Lowerer rather than the shared one. **Not yet ported to the JS
  mirror** (`CParser`/`CLowerer` in `vm/picoc.js`) — that would be a THIRD,
  independent implementation of the same mechanism (the BASIC family's JS
  `BLowerer` port was the second), deliberately not attempted in this pass
  to avoid rushing a third from-scratch implementation without adequate
  time to verify it as carefully as the first two. A legitimate, explicitly
  scoped-out follow-up, not an oversight.
- **v1** remains architecturally separate and fully excluded from this
  equivalence pass by design — a frozen, stable bytecode ISA
  ("Each statement compiles 1:1 to a single 32-bit instruction. No
  optimisation. No reordering." — `picoscript_lang.py`) with no shared
  AST/IL at all; evolving its grammar would contradict its own stated
  purpose.
- **Workflow** is intentionally a lossy subset/projection, not a peer.
- **AST-JSON** has 100% parity with the six shared-AST frontends by
  construction, on BOTH Python and JS now, and fails loudly rather than
  silently when handed a foreign (C-style/v1) tree it can't represent.

## What was actually merged/fixed as a result of this audit

1. **`picoscript_ast.ast_to_json`** now checks class *identity*, not just
   class *name*, before serializing a node — closes a real latent bug where
   a C-style (`picoscript_cfront.py`) AST could be silently miscoerced into
   the wrong (`picoscript_basic`) node class. Covered by
   `tests/test_ast_frontend.py::test_ast_to_json_rejects_foreign_same_named_node_class`.
2. **BASIC gained `TRY`/`EXCEPT`/`FINALLY`/`ENDTRY`/`RAISE` grammar**
   (`picoscript_basic.py`'s `KEYWORDS`, `_parse_stmt`, and new `parse_try`),
   closing its only remaining gap versus Python-style among the shared-AST
   node kinds it was missing (it already had `OnBlock`).
3. **Fixed a real, pre-existing, cross-dialect crash, then built the real
   exception engine on top of it**: `Lowerer.stmt`'s `Raise` branch called a
   nonexistent `ILBuilder.raise_sw` method (crashing with `AttributeError`
   for BASIC and Python-style alike) and separately misused
   `Error.SetHandler` with the raised value instead of a jump-target PC (a
   latent bad-jump risk). First fixed to a safe no-op, then -- see
   `docs/EXCEPTION_ENGINE.md` -- built out fully: `Raise` now genuinely
   throws (jumps to the nearest handler, or propagates as an uncaught
   `PicoFault`), on both the Python and JS bytecode VMs.
   `tests/test_basic_100.py`'s `test_raise_with_value_lowers` — which had
   asserted the crash as expected behavior — and
   `tests/test_basic_final_90.py`'s `test_basic_raise_with_value` were
   updated to assert the real behavior instead; full coverage in
   `tests/test_exception_engine.py`.
4. **Deliberately not done**: propagating `TryExcept`/`Raise`/`OnBlock`
   grammar to English/COBOL/Report/Functional, or porting them to the JS
   `BLowerer`. Both are real, larger, separate efforts (a working exception
   engine needs a new `Error.Raise(code)` host op across all 3 VMs + 2
   transpilers, plus `lower_try()` actually registering `Error.SetHandler`;
   the JS port needs the grammar *and* lowering built from scratch) that
   go beyond "close the parity gap" into "build a feature that never fully
   existed" — flagged here for a scoping decision rather than attempted
   silently.
