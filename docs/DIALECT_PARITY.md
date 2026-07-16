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

Even among the six frontends that share the same AST+Lowerer, most of them
only implement a subset of the node kinds the shared `Lowerer` can handle —
their *tokenizer/parser* simply never emits certain nodes.

| Node kind | BASIC | Python | English | COBOL | Report | Functional |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Dim / IncDec | Y | N | N | N | N | N |
| Ternary | Y (`IIF`) | Y | Y | N | N | Y |
| DoLoop | Y | Y | Y (`Repeat`) | N | N | N |
| Dispatch | Y | Y | Y | N | N | N |
| ForEach | Y | Y | Y | N | Y | Y |
| Skip (continue) | Y | Y | Y | N | Y | Y |
| Gosub | Y | Y | Y | Y | Y | N |
| ServerMain | Y | N | N | N | N | N |
| **TryExcept / Raise / OnBlock** | **Y** | **Y** | **N** | **N** | **N** | **N** |
| ConstDecl / EnumDecl | Y | Y | Y | N | N | N |

Verified by grep for each frontend's actual `return <NodeKind>(...)` construction
sites (not just imports — COBOL/Report/Functional all `import` several node
names, e.g. `Dispatch`/`TryExcept`/`Raise`, that their parsers never
construct). Notably **COBOL has no `ForEach` or `Skip`/continue construct at
all** — its iteration is `PERFORM VARYING` (→ `ForTo` only) and it has no
loop-continue keyword.

The standout gap: **`TryExcept`/`Raise`/`OnBlock` (exception handling and
`ON Ns.Method: ... END ON` event blocks) are parseable only from BASIC and
Python-style source.** COBOL/Report/Functional even `import` those three
names from `picoscript_basic` (`picoscript_cobol.py:16`,
`picoscript_report.py:17`, `picoscript_functional.py:22`) but never
construct them — confirmed by grep: no `return TryExcept(...)` /
`return Raise(...)` in any of the three files. English has no import of
them at all. So a program using try/except or an `ON` event handler, written
in BASIC or Python-style, **cannot be transliterated by hand into English,
COBOL, Report, or Functional syntax** — there is no surface grammar for it in
those four, even though the shared `Lowerer` would happily lower the node if
it existed.

Workflow routes around this entirely rather than closing it: its `RAISE`/`ON`
steps lower to plain `Event.Post(...)` host calls and a hand-rolled
while-loop drain (`picoscript_workflow.py:322-325`, `:381`), not to actual
`Raise`/`OnBlock` AST nodes — so Workflow's "equivalent" feature is really a
different, lower-level desugaring, not evidence that the gap is closed.

## The JS port (`vm/picoc.js`) vs Python

The JS port's own `BLowerer` (shared by JS BASIC/Python/English/COBOL/
Report/Functional, mirroring the Python side) **does** support
`TryExcept`/`Raise`/`OnBlock` in its statement dispatch — so JS BASIC/Python
source using them compiles fine. The gap is narrower and newer: the
**AST-JSON bridge added to `vm/picoc.js`** (`jsonToAst`) explicitly rejects
those three kinds via `AST_JSON_UNSUPPORTED = { TryExcept: 1, Raise: 1,
OnBlock: 1 }` — i.e. the JS visual/AST-designer path can't accept them yet,
even though the JS *compiler* can. This mirrors the Python side's situation
before this audit (Python's `compile_ast` always supported them fine — only
the JS AST-JSON bridge has this restriction).

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

## Bottom line

- **Control flow, arithmetic, calls, host namespaces**: equivalent everywhere
  they're claimed to be (proven by the 70/70 translator round-trip suite +
  five-path VM/transpile parity tests).
- **Exception handling / event blocks (`TryExcept`/`Raise`/`OnBlock`)**: a
  real, undocumented-until-now gap — parseable only in BASIC and Python-style
  syntax among the six shared-AST frontends; no English/COBOL/Report/
  Functional grammar for it.
- **C-style and v1** are architecturally separate compilers proven
  equivalent only by output testing, not by sharing code — a latent risk if
  either drifts (no shared `Lowerer` to keep them honest automatically).
- **Workflow** is intentionally a lossy subset/projection, not a peer.
- **AST-JSON** has 100% parity with the six shared-AST frontends by
  construction, and (after this audit) now fails loudly rather than
  silently when handed a foreign (C-style/v1) tree it can't represent.
