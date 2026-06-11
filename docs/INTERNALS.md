# PicoScript deep internals

> **Deep technical internals.** This page is for implementers and contributors who
> want to understand the compiler, bytecode, runtimes, and size trade-offs. It is
> intentionally separate from the app-author language/reference docs.

PicoScript looks larger than it is: four source surfaces, a browser compiler, three
bytecode VMs, source backends for C and JavaScript, jump-table dispatch, card
storage, spans, descriptors, HTTP output, and a growing host-hook standard library.
The trick is that almost none of that adds new core machinery. The platform is a
small frozen ISA plus one shared IL pipeline; everything higher level is either a
parser, a lowering rule, or a host hook.

```
    C syntax       ─┐
    BASIC          ─┼── AST/lowerer ── PicoIL ──┬── 16-op bytecode ── Python VM
    Python style   ─┘                            │                  ├─ C VM
    English style  ──────────────────────────────┘                  └─ JS VM
                                                   ├── toC  ── host cc / zig cc
                                                   └── toJS ── browser / Node
```

The important invariant is not "there are many compilers"; it is "there is one
semantic path." Equivalent programs should converge to the same PicoIL shape and,
for the Python/English/BASIC family, byte-for-byte identical bytecode. The full set
of compiler/runtime/security invariants — and their current compliance status — lives
in [`INVARIANTS.md`](INVARIANTS.md).

## 1. The 16-opcode ISA

The hardware contract lives in `picoscript.py` and is mirrored by the VMs. Every
instruction is one 32-bit word:

```
  31        28 27     24 23     20 19         16 15                0
 +------------+---------+---------+-------------+-------------------+
 | opcode     | Rd      | Rs1     | Rs2 / mode  | imm16             |
 +------------+---------+---------+-------------+-------------------+
```

`picoscript.encode_instruction()` is the canonical encoder:

```text
[31:28]=opcode [27:24]=Rd [23:20]=Rs1 [19:16]=Rs2/mode [15:0]=imm16
```

The primary opcode field is only four bits, so the frozen core is exactly sixteen
operations:

| Group | Opcodes |
| --- | --- |
| Storage / output | `LOAD`, `SAVE`, `PIPE` |
| ALU | `ADD`, `SUB`, `MUL`, `DIV`, `INC` |
| Control | `JUMP`, `BRANCH`, `CALL`, `RETURN` |
| Wait/signalling | `WAIT`, `RAISE` |
| Extension slots | `NOOP` markers for host/Net hooks, `DSP` with 16 sub-ops |

The deceptively powerful field is `Rs2`. For ordinary ALU ops it selects register
or immediate form. For card ops it is an addressing mode:

| `Rs2` | Meaning |
| --- | --- |
| `0x0` | immediate address: `card[imm16]` |
| `0x1` | register indirect: `card[Rs1]` |
| `0x2` | base + offset: `card[BASE + imm16]` |
| `0x3` | register + offset: `card[Rs1 + imm16]` |

The same mode nibble is what made computed control flow possible without adding an
opcode. `JUMP` has three forms in `picoscript.py`, `picoscript_vm.py`, `vm/picovm.c`,
and `vm/picovm.js`:

```text
Rs2 = 0x0  ->  PC = imm16          // ordinary absolute jump
Rs2 = 0x1  ->  PC = Rs1            // indirect jump
Rs2 = 0x3  ->  PC = Rs1 + imm16    // indexed jump
```

That last form unlocked dense jump tables. The bytecode backend emits an indexed
`JUMP` into an inline table of absolute `JUMP` words:

```
  selector in Rk

  JUMP [Rk + table_base]     ; Rs2=0x3, PC = Rk + imm16
table_base:
  JUMP @case_0
  JUMP @case_1
  JUMP @case_2
  ...
```

One addressing-mode bit turns `dispatch` from a compare chain into O(1) control
flow, while old bytecode using plain `JUMP` remains unchanged.

### Constants without `LOADI`

There is intentionally no `LOADI` opcode. Constants lower through the existing ALU:

```text
SUB Rd, Rd, Rd      ; Rd = 0
ADD Rd, Rd, imm16   ; Rd = imm16
```

See `_ConstExpansion` and `lower_to_bytecode_safe()` in `picoscript_il.py`. This is
one of the recurring PicoScript trade-offs: spend an occasional extra word to keep
the decoder at sixteen opcodes.

## 2. PicoIL: one three-address IR

`picoscript_il.py` is the shared middle end. Frontends allocate virtual registers
freely and emit a compact three-address IR:

```python
@dataclass
class Inst:
    op: str
    dst: Optional[VReg] = None
    a: Optional[Operand] = None
    b: Optional[Operand] = None
    cond: Optional[str] = None
    label: Optional[str] = None
    ns: Optional[str] = None
    method: Optional[str] = None
    args: Tuple[Operand, ...] = ()
    targets: Tuple[str, ...] = ()   # jmptab
```

The IL is deliberately simple: integer operations, labels, conditional branches,
calls, host calls, card operations, Net markers, and `jmptab`. It is rich enough for
structured source languages, but close enough to the ISA that lowering is readable.

### Optimizer passes

`optimize()` is conservative and local:

- constant-fold pure arithmetic on immediates;
- fuse `x = x + 1` to `INC`;
- remove redundant `mov x, x`.

There is no global optimizer, no speculative rewrite system, and no dependency on a
large compiler framework. That is why the Python compiler can be mirrored in
`vm/picoc.js` for the browser.

### Loop-aware linear-scan allocation

`allocate()` maps vregs to `R0..R15`. It uses conservative live intervals, then
extends intervals across detected back-edges so loop invariants are not clobbered
inside the loop body. Named/pinned variables, and values spanning a `CALL`, receive
whole-program intervals because subroutines share the bytecode register file.

The allocator also has a card-spill pressure valve: `allocate(spill=True)` reserves
`R14/R15` as shuttle registers and records overflow vregs in scratch-card slots
(`__spilled__`). The normal safe bytecode lowerer currently uses the non-spilling
path and fails loudly if a spilled vreg reaches `_phys()`, which keeps pressure bugs
deterministic rather than silently corrupting registers.

### Two-pass assembly

`lower_to_bytecode_safe()` is the real assembler. It is two-pass because labels are
PCs, but some IL instructions are wider than one word:

| IL op | Bytecode width |
| --- | ---: |
| ordinary op | 1 word |
| `const` / `mov imm` | 2 words (`SUB` + `ADD`) |
| `jmptab` | `1 + N` words (computed jump + inline jump table) |

Pass 1 computes label PCs using expanded widths. Pass 2 emits words, including the
no-`LOADI` constant sequence and the inline jump table.

## 3. Four frontends, one semantic path

PicoScript has four high-level surfaces:

| Surface | File | Notes |
| --- | --- | --- |
| C-syntax | `picoscript_cfront.py` | Own tokens, AST, parser, and `Lowerer` |
| BASIC-like | `picoscript_basic.py` | Block-structured AST and shared `Lowerer` |
| Python-style | `picoscript_python.py` | Parser/tokenizer only; reuses BASIC AST + `Lowerer` |
| Natural English | `picoscript_english.py` | Parser/tokenizer only; reuses BASIC AST + `Lowerer` |

The Python and English frontends are intentionally parser-only layers. Their module
headers say the important part out loud: they import BASIC AST nodes and `Lowerer`
verbatim. That means a Python-style `match`, an English `Choose`, and a BASIC
`SWITCH` lower through the same node classes.

The C frontend is the outlier because its source grammar is expression-heavy and
brace-delimited, so it has its own AST and `Lowerer`. It still emits the same PicoIL
instruction model and uses the same host-hook canonicalization (`canon_host()`).

`vm/picoc.js` is the browser version of this compiler. It mirrors the Python
compiler's optimizer, allocator, bytecode lowerer, and all four frontends so the
playground can compile locally. The invariant is tested in `tests/test_pipeline.py`:
the current run reports **109 passed, 0 failed**, including the in-browser compiler
checks where `picoc.js` bytecode equals Python bytecode byte-for-byte.

## 4. Runtime VMs and source backends

There are three bit-compatible bytecode interpreters:

| Runtime | File | Role |
| --- | --- | --- |
| Python reference | `picoscript_vm.py` | Readable spec VM, host-hook model, profiling |
| Portable C | `vm/picovm.c` | Freestanding-friendly VM for bare metal / embedding |
| JavaScript | `vm/picovm.js` | Browser/Node VM with step-debugging API |

All three decode the same word layout and implement the same `JUMP` modes. The C
and JS files explicitly state that they mirror `picoscript_vm.PicoVM._step`.

PicoIL also lowers directly to source:

- `lower_to_c()` emits portable C using `picovm.h` host helpers. Labels become C
  labels; subroutines become C functions; `jmptab` becomes a native `switch`.
  Native toolchains, including `zig cc`, can lower that to Thumb or AArch64.
- `lower_to_js()` emits a self-contained browser/Node module. Because JavaScript has
  no `goto`, each routine becomes a `while/switch` block state machine; `jmptab`
  becomes a JavaScript `switch`.

The parity suite exercises all routes: Python VM, C VM, JS VM, emitted C, emitted
JS, and browser-compiler bytecode. That makes "add a frontend" or "add a backend"
a testable exercise in preserving the IL contract, not a rewrite of the platform.

## 5. Host-hook ABI: 2 in, 1 out, implicit context

The standard library is not baked into the ISA. Host calls lower to `NOOP` with a
reserved immediate range:

```text
opcode = NOOP
imm16  = HOST_HOOK_BASE | hook_code
Rd     = optional result register
Rs1    = first argument register
Rs2    = second argument register
```

`picoscript_il._emit_word()` maps `Inst("host", ns=..., method=...)` to hook codes
from `picoscript_lang.HOST_HOOK_CODES`. The common hook code is 8-bit (`imm16 &
0x00ff`); the VM also has an extended host-hook range for codes above `0xff`.

The ABI is therefore "two explicit inputs, one explicit output, plus implicit
context." The context lives in the VM/host object:

- Python: `HostApi.call(vm, ns, method, rd, rs1, rs2, imm16)`;
- C VM: `ctx->host(ctx, hook, rd, rs1, rs2, imm16)`;
- JS VM: `_host(code, rd, rs1, rs2, imm)`.

That implicit context is why large features do not need instruction-format changes.
For example, program-level `Storage.*` selects a pack/card in host context; field
names and queries are passed as arena spans built with `Memory.Set` + `Span.Make`;
results come back in `Rd`. The compiler still only sees a host call with up to two
register arguments.

## 6. Metrics and profiling

`picoscript_metrics.py` reports the footprint and estimated cost of a compiled
program:

- raw and optimized IL instruction counts;
- bytecode words/bytes;
- static opcode histogram, splitting host calls and Net markers out of `NOOP`;
- computed jumps as `JUMP*`;
- analytical cycle estimates;
- emitted C/JS source sizes;
- optional profiled dynamic instruction counts from `PicoVM.profile`.

The cycle model is deliberately comparative, not cycle-accurate. It is there to
answer "did this lowering make the program bigger or slower?" before measuring on
real hardware.

## 7. Why so tiny?

Measured on this checkout on 2026-06-10 with Node's `zlib.gzipSync`:

| File | Bytes | Gzipped | Lines |
| --- | ---: | ---: | ---: |
| `vm/picoc.js` | 91,576 | 17,054 | 1,442 |
| `vm/picovm.js` | 27,803 | 7,270 | 559 |
| `vm/picovm.c` | 8,972 | 2,473 | 260 |

These numbers will drift as the implementation changes, but they illustrate the
architecture: the entire in-browser compiler for four languages is about **89.4 KiB
raw / 16.7 KiB gzipped**, and the JS bytecode VM is about **27.2 KiB raw / 7.1 KiB
gzipped**.

The footprint stays small because PicoScript keeps pushing complexity to the same
few places:

1. **A 16-op ISA.** The decoder is tiny. New behavior is normally an addressing mode,
   a `DSP` sub-op, or a host hook, not a new instruction family.
2. **One IL.** Four parsers feed one `Inst` model, one optimizer, one allocator, and
   the same backends.
3. **Integer-only, global-scope semantics.** No object model, closures, GC, dynamic
   module loader, or string pool in bytecode. Strings and structured data live in
   arena spans and host-managed descriptors.
4. **Host-hook stdlib.** Storage, JSON/XML writers, spans, descriptors, context,
   queues, crypto hooks, and HTTP markers extend the platform without changing the
   compiler core.
5. **No npm dependency stack.** `vm/picoc.js` and `vm/picovm.js` are plain JavaScript.
   They can be inlined into the playground or embedded in a host page.
6. **Determinism over cleverness.** Small local optimizer, fixed word format, step
   budgets, explicit host ABI, and parity tests are easier to port than a large
   optimizing compiler.

That is the headline: PicoScript gets four languages, three VM runtimes, two source
backends, jump tables, a card store, spans/descriptors, and an HTTP/descriptor model
not by growing the core, but by keeping the core frozen and making everything else a
lowering convention or host contract.

## Contributor navigation

- ISA constants and encoding: `picoscript.py`, especially `encode_instruction()`.
- IL model and bytecode assembler: `picoscript_il.py`, `Inst`, `optimize()`,
  `allocate()`, `lower_to_bytecode_safe()`.
- Source backends: `picoscript_il.lower_to_c()`, `picoscript_il.lower_to_js()`.
- Python reference runtime: `picoscript_vm.PicoVM._step()` and `HostApi`.
- Bare-metal VM: `vm/picovm.c`, `pv_vm_run()`, `pv_noop()`.
- Browser/Node VM: `vm/picovm.js`, `PicoVM.prototype.step()`.
- Browser compiler: `vm/picoc.js`, `lowerToBytecodeSafe()`.
- Cross-target tests: `tests/test_pipeline.py`.
- Size/cycle reporting: `picoscript_metrics.py`.
