# Self-hosting PicoScript — feasibility exploration

> **Can PicoScript compile PicoScript?** Short answer: the **code-emission half is
> already possible today** (proven below), the **parsing half is possible but needs
> an explicit-stack rewrite**, and a *full* self-hosted port of the current
> compiler is not worth it without modest language growth. A **staged bootstrap**
> is the sensible path.

This is a design exploration, not a committed roadmap. Everything labelled
"works today" is backed by a runnable artifact in this repo.

## The pipeline, stage by stage

The native toolchain is: `source → lex → parse → AST → PicoIL → (optimise +
register-allocate) → bytecode`. To self-host, a PicoScript program must perform
some or all of these stages over a source span and emit bytecode bytes.

| Stage | Self-host feasibility today | Why |
|-------|-----------------------------|-----|
| **Lex** | Feasible | `Utf8Reader.*` scans a span; `Utf8Reader.Int` parses decimals; `Memory.*` holds the token stream. |
| **Parse** | Feasible *with rework* | PicoScript subroutines take **no parameters** and don't recurse safely (one global scope). A recursive-descent/Pratt parser must be rewritten as an **explicit-stack state machine** in `Memory`/spans. |
| **IL + optimise + regalloc** | Hard | Needs dynamic arrays and richer state than 16 globals comfortably allow. A minimal subset can **skip PicoIL entirely** and emit bytecode directly from the parse. |
| **Emit (codegen)** | **Works today** | Instruction words are pure integer arithmetic; bytes are emitted with `Io.WriteByte`. See the PoC. |

## Stage 0 — emit stage, proven today

`examples/selfhost_emit.pc` is a PicoScript program that **generates a runnable
PicoScript binary**: it emits the five bytecode words for `print(K)` as
big-endian bytes on the output buffer. Feed that output into any PicoVM and it
prints `K`.

Key trick: a bytecode word is `(op<<28)|(rd<<24)|(rs1<<20)|(rs2<<16)|imm16`.
PicoScript has no shift operator and `int` is **signed 32-bit** (so a word like
`RETURN = 0xC0000000` is negative and unsafe to divide). We therefore never build
the 32-bit value — we emit the four bytes straight from the instruction fields,
where nothing exceeds 255:

```
byte0 = op*16 + rd     byte1 = rs1*16 + rs2     byte2 = imm/256     byte3 = imm%256
```

Verified round-trip (both VMs):

```
emitter (Python VM) → 50 01 00 00 40 00 04 d2 20 00 ff fe 30 00 ff fe c0 00 00 00
emitter (JS VM)     → 50 01 00 00 40 00 04 d2 20 00 ff fe 30 00 ff fe c0 00 00 00
generated program runs → prints 1234
```

…and that byte string is **identical** to what the native compiler emits for
`print(1234)`. So the back end of self-hosting is a solved problem in the
language as it stands.

Reproduce:

```bash
python picoscript_build.py run examples/selfhost_emit.pc          # emits the bytes
# tests/test_pipeline.py :: check_selfhost reassembles + runs them → 1234,
# and asserts they equal the native compiler's bytecode for print(1234).
```

Note the constant-build idiom the native compiler uses (and that a self-hosted
emitter must reproduce): there is **no load-immediate opcode**, so a constant is
`SUB Rx,Rx,Rx` (zero) followed by `ADD Rx,Rx,#imm` for `imm` in `0..65535`.

## The two real gaps (and how to close them)

1. **No arrays/lists as values.** PicoScript values are scalar ints. *But* the
   runtime already exposes a 64 KB byte `Memory`, zero-copy `Span`s, and
   `Storage` cards — a perfectly good data plane for token streams, a symbol
   table, an operand stack, and the output word buffer. A self-hosted compiler
   stores its structures there, not in variables.
2. **No recursion / no parameters.** Subroutines share globals and return once.
   Tree-walking a grammar therefore needs an **explicit stack** (a region of
   `Memory` with a stack-pointer variable) and a hand-written shunting-yard /
   table-driven parser instead of recursive descent.

Neither requires changing the frozen ISA. Two optional language conveniences
would make a fuller self-host pleasant rather than painful: first-class
fixed-size arrays (sugar over `Memory`/`Span`) and value parameters / a real call
stack (sugar over an explicit `Memory` stack). Both are pure front-end/IL work;
the bytecode and VMs stay frozen.

## Suggested staged bootstrap

- **Stage 0 — emit (done):** `examples/selfhost_emit.pc` proves codegen.
- **Stage 1 — self-hosted assembler:** a PicoScript program that reads a tiny
  textual listing (`ADD R0 R0 42`, one instruction per line) via `Utf8Reader`
  and emits bytecode with the Stage-0 idiom. No grammar, no stack — just lex +
  encode. This is the smallest *useful* self-hosted tool.
- **Stage 2 — tiny expression compiler:** add an explicit-stack expression parser
  (numbers, `+ - * /`, `print`) emitting bytecode directly (skip PicoIL). Proves
  the parse half works under the no-recursion constraint.
- **Stage 3 — grow the subset:** assignments, `if`, `while`. Decide here whether
  to add array/param sugar to the language to keep the self-hosted compiler
  readable, or to keep grinding with `Memory`-backed structures.
- **Stage 4 — fixed point:** the self-hosted compiler compiles its own source.
  This only needs to cover the subset the compiler itself is written in, not all
  four surface styles.

## Recommendation

Pursue **Stage 1 (self-hosted assembler)** next — it is small, immediately
demonstrable, and reuses the proven Stage-0 emitter. Treat full self-hosting as a
**subset** goal (a "PicoScript-0" the compiler is written in), not a port of the
1,300-line four-frontend toolchain. Add array/param sugar only if Stage 3 proves
the `Memory`-backed style too unwieldy — and keep the 16-opcode ISA and both VMs
frozen throughout, so every self-hosted output remains verifiable against the
native compiler byte-for-byte.
