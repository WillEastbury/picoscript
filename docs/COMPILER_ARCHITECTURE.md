# PicoScript Compiler & Runtime Architecture

Multi-frontend, multi-target lowering pipeline (implements LANGUAGE_SPEC.md §10).

```
  Frontends                Shared IL (PicoIL)            Backends / Lowering
  ─────────                ──────────────────            ───────────────────
  C-syntax   ──┐                                    ┌──> lower_to_bytecode ──> .pbc ──> PicoVM (Python ref)
  (.pc)        │                                    ├──> lower_to_bytecode ──> .pbc ──> picovm.c (bare metal)
               ├──> AST ──> lower ──> PicoIL ───────┼──> lower_to_bytecode ──> .pbc ──> picovm.js (browser/Node)
  BASIC-like ──┘            (typed three-address     ├──> lower_to_c  (toC)  ──> .c  ──> host cc ──> Thumb/AArch64
  (.pbas)                    + vreg alloc)            └──> lower_to_js (toJS) ──> .js ──> browser / Node
```

Both frontends are case-insensitive for keywords and variable names; namespace and
method names resolve case-insensitively to the canonical host ABI spelling.

## Components

| File | Role |
|------|------|
| `picoscript_il.py`     | PicoIL definition, optimizer (const-fold/peephole), loop-aware linear-scan register allocator, `lower_to_bytecode`, `lower_to_c`, `lower_to_js` |
| `picoscript_vm.py`     | Python reference VM: executes the 16-op bytecode ISA + host-hook dispatch (the runtime) |
| `picoscript_cfront.py` | C-syntax frontend: lexer + Pratt parser + AST → PicoIL |
| `picoscript_basic.py`  | BASIC-like frontend: block-structured parser + AST → PicoIL |
| `picoscript_build.py`  | Unified driver: `.pc`/`.pbas` → IL → {run \| bytecode \| C \| JS \| native} |
| `vm/picovm.h` `vm/picovm.c` | Portable C VM for embedding on RP2354B / PIOS (freestanding-clean) |
| `vm/picovm.js`         | JavaScript VM for browser/Node with a step-debugging API |
| `vm/picoc.js`          | In-browser compiler: faithful JS port of the IL + both frontends (byte-identical to Python) |
| `docs/playground.html` | Side-by-side language guide + live in-browser compile/run/step (inlines `picoc.js` + `picovm.js`) |

## Lowering = performance lever

The IL is the place where we choose how "close to the metal" the emitted shape is:

- `regalloc` — loop-aware linear-scan allocation; spill to scratch cards when >16 live vregs.
- `opt=True` — constant folding, dead-store elimination, redundant-move removal, INC fusion.
- backend `bytecode` — compact 32-bit ISA for the interpreters (smallest footprint).
- backend `c` — straight-line C the native toolchain optimizes to Thumb/AArch64 (highest throughput).
- backend `js` — block state-machine JS (no `goto`); subroutines become functions; runs in-browser.

All frontends and backends share one IL, so any source runs on any target with identical
semantics and queue ABI (per spec §10). `tests/test_pipeline.py` asserts cross-target parity:
PicoVM (Python) = picovm.c = picovm.js, plus emitted C and JS run and match, and the
in-browser compiler's bytecode is byte-for-byte identical to Python's.
