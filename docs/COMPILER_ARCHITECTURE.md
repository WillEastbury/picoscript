# PicoScript Compiler & Runtime Architecture

Multi-frontend, multi-target lowering pipeline (implements LANGUAGE_SPEC.md §10).

```
  Frontends                Shared IL (PicoIL)            Backends / Lowering
  ─────────                ──────────────────            ───────────────────
  C-syntax     ──┐                                  ┌──> lower_to_bytecode ──> .pbc ──> PicoVM (Python ref)
  (.pc)          │                                  ├──> lower_to_bytecode ──> .pbc ──> picovm.c (bare metal)
  BASIC-like   ──┤                                  ├──> lower_to_bytecode ──> .pbc ──> picovm.js (browser/Node)
  (.pbas)        ├──> AST ──> lower ──> PicoIL ──────┤
  Python-style ──┤          (typed three-address     ├──> lower_to_c  (toC)  ──> .c  ──> host cc ──> Thumb/AArch64
  (.ppy)         │           + vreg alloc)           └──> lower_to_js (toJS) ──> .js ──> browser / Node
  Natural-Eng. ──┘
  (.eng)
```

The Python-style and natural-English frontends **reuse the BASIC AST and lowerer
verbatim** (only their tokenizer/parser differ), so equivalent programs in any of the
four surfaces lower to byte-for-byte identical bytecode. All four frontends are
case-insensitive for keywords and variable names; namespace and
method names resolve case-insensitively to the canonical host ABI spelling.

## Components

| File | Role |
|------|------|
| `picoscript_il.py`     | PicoIL definition, optimizer (const-fold/peephole), loop-aware linear-scan register allocator, `lower_to_bytecode`, `lower_to_c`, `lower_to_js` |
| `picoscript_vm.py`     | Python reference VM: executes the 16-op bytecode ISA + host-hook dispatch (the runtime) |
| `picoscript_cfront.py` | C-syntax frontend: lexer + Pratt parser + AST → PicoIL |
| `picoscript_basic.py`  | BASIC-like frontend: block-structured parser + AST → PicoIL |
| `picoscript_python.py` | Python-style frontend: indentation tokenizer + parser → reuses BASIC AST/lowerer |
| `picoscript_english.py`| Natural-English frontend: controlled-NL parser → reuses BASIC AST/lowerer |
| `picoscript_build.py`  | Unified driver: `.pc`/`.pbas`/`.ppy`/`.eng` → IL → {run \| bytecode \| C \| JS \| native} |
| `vm/picovm.h` `vm/picovm.c` | Portable C VM for embedding on RP2354B / PIOS (freestanding-clean) |
| `vm/picovm.js`         | JavaScript VM for browser/Node with a step-debugging API |
| `vm/picoc.js`          | In-browser compiler: faithful JS port of the IL + all four frontends (byte-identical to Python) |
| `docs/playground.html` | Four-style language guide + live in-browser compile/run/step (inlines `picoc.js` + `picovm.js`) |

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

## Jump-table dispatch (one primitive, many subsystems)

The `dispatch` construct lowers to a single IL op, `jmptab`, which is a self-contained
indexed jump:

```
  selector ──► bounds guard ──► jmptab ──► handler
   (state)     (0 ≤ s < N?)      │            (case body)
                  └─ else ───────┴──► default
```

`jmptab` carries the selector vreg + an ordered list of case labels + the default, so
each backend lowers it idiomatically:

- **bytecode** — one *indexed* `JUMP` (`Rs2 = 0x3` ⇒ `PC = Rs1 + imm16`, selector + table
  base) into an inline table of `N` absolute `JUMP`s. O(1). The indexed/indirect modes are
  a backward-compatible use of the addressing-mode field; the 16-opcode ISA is frozen and
  an ordinary `JUMP` (`Rs2 = 0`) is unchanged.
- **toC / toJS** — a native `switch`, which the host compiler turns into its own jump table.

This one primitive is the shared core for `switch`, `match`, and event / hook / interrupt /
**protocol** dispatch — which is what lets a protocol state machine be written in PicoScript
itself (e.g. an EL0 framing parser feeding the descriptor model).

## Metrics (`picoscript_build.py stats`)

`picoscript_metrics.py` reports, for a program across backends: IL instruction count
(raw + optimised), bytecode words/bytes, a static opcode histogram (host calls split out,
computed jumps shown as `JUMP*`), an analytical cycle estimate, and the emitted C/JS source
sizes. With `--run` it adds profiled **dynamic** instruction and cycle counts via an opt-in
VM profiler (`PicoVM.profile`, near-zero cost when off). The cycle model is a comparative
estimate, not cycle-accurate — tune `CYCLE_COST` against real Pi5 / RP2350 measurements.
