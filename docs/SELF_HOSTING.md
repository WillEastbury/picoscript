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
- **Stage 1 — self-hosted assembler (done):** `examples/selfhost_asm.pc` reads
  a numeric-field textual listing (`op rd rs1 rs2 imm`, one instruction per
  line) via `Utf8Reader` and emits bytecode with the Stage-0 idiom. Verified by
  `tests/test_selfhost_asm.py`, which assembles native bytecode, runs the
  generated program, and asserts byte-for-byte equality with the native compiler.
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

## On-device editor and live program cards on PIOS

This is a feasibility sketch for a stronger target than "PicoScript can emit
bytecode": the **editor itself** runs as an EL0 PicoScript/PIOS capsule, edits
source stored on the device, compiles it to bytecode cards, and hot-reloads a
running capsule without a host. It is plausible, but it depends on the staged
bootstrap above maturing past the current Stage 1 assembler. Today the reliable
path is still host-assisted; the fully self-hosted loop starts becoming useful at
Stage 2 and becomes a real on-device development environment around Stage 3.

### Editor as an EL0 capsule

Treat the editor as an ordinary capsule bound to a PIOS binding, not as a kernel
special case. The binding can be one of:

- a `duplex` terminal/WebSocket-like port for an interactive screen editor;
- a `stream`/`unary` console binding for a line editor or batch edit command;
- an `ipc` binding where a separate shell capsule sends edit operations.

The kernel-owned pieces are the same ones described in `PIOS_IO_BINDING.md`:

- **Input FIFO:** keystrokes, paste chunks, terminal resize events, or network
  frames arrive as `CTX_READY`/`BODY_CHUNK`-style messages. The editor reads only
  through lease-validated `pooldesc` spans, so input is length-bounded and
  revocable (I4/I8).
- **Output binding:** the editor renders either a framebuffer/console descriptor
  stream or a network response/body stream. The editor expresses screen deltas as
  descriptor bodies; the binding owns the transport details and ordering legality
  (I6/I7).
- **Lease/pooldesc memory:** the editor's text buffer, token cache, dirty ranges,
  and output spans live in the capsule's micro-pool or in `Memory`/`Span`
  structures. Descriptors stay single-owner (I2) and never cross to the compiler
  or runner except through kernel-mediated FIFOs (I5).

The important constraint is that the editor is not allowed to "borrow" another
capsule's source buffer directly. It can send an edit/save request over an `ipc`
FIFO, or persist a new source card and hand over the card id. That keeps the
capsule boundary honest.

### Compile path: self-hosted vs host-assisted

There are two viable compile paths, with different readiness:

1. **Host-assisted now.** A host cross-compiles source to PicoScript bytecode and
   ships both source and compiled module cards to PicoStore/PicoWAL. PIOS can then
   load and run the module from cards. This is feasible before the compiler is
   self-hosted because the on-device side only needs card storage, capsule spawn,
   and bytecode validation/loading.
2. **Self-hosted later.** The on-device compiler should extend the completed
   Stage 1 numeric assembler toward the staged plan above:
   - Stage 2 gives the first useful on-device compiler: a tiny expression/parser
     subset (`print`, numbers, arithmetic) that emits bytecode directly.
   - Stage 3 unlocks practical edit/compile/run demos: assignments, `if`, and
     `while`, still likely skipping PicoIL and using `Memory`-backed token,
     symbol, and operand stacks.
   - Stage 4 is the fixed point: the compiler compiles the subset it is written
     in.

Be explicit about the tradeoff: a self-hosted compiler for the full current
Python toolchain is still the wrong target. A "PicoScript-0" compiler that emits
the frozen 16-opcode bytecode directly is the right target. Until Stage 2 exists,
the editor can save and launch host-compiled cards, but it cannot honestly claim
to compile arbitrary source on-device.

### Program-as-card storage

Use PicoStore/PicoWAL as a **program pack**. A program is not one opaque blob; it
is a set of deterministic cards:

- one source card per source file;
- one compiled-module card per emitted bytecode module;
- optional manifest, build log, debug map, and history cards.

PicoBinarySerializer cards are self-describing `int32`/UTF-8-string records, so
binary bytecode should be stored as a hex string or as fixed-size chunk cards
rather than as a raw byte array. A concrete schema could be:

```text
pack: "program:<app>"

manifest card
  kind="manifest", app="hello", main="main.pc", version=7,
  active_build=42, created_at=..., updated_at=...

source card
  kind="source", path="main.pc", version=7, lang="picoscript",
  text="<UTF-8 source>", parent=6, hash="..."

module card
  kind="module", path="main.pc", version=7, isa=1,
  bytecode_hex="50010000...", source_card=17, hash="..."

history card
  kind="history", path="main.pc", from=6, to=7,
  source_card=17, module_card=42, reason="editor-save"
```

For larger modules, replace `bytecode_hex` with `chunk_count` and store
`kind="module-chunk", module=42, index=N, data_hex="..."` cards. Keeping source
and module cards separate lets the editor preserve history even when compilation
fails; a failed build writes a build-log card but does not advance
`manifest.active_build`.

### Hot-reload lifecycle

Hot-reload should be "load a new capsule from cards", not "patch a live capsule's
memory". The safe flow is:

1. The editor/compiler writes new source/module cards and updates the manifest
   only after the module card validates.
2. The supervisor asks the old capsule to quiesce, or the kernel stops accepting
   new contexts for its binding.
3. The old capsule exits or is killed; scope-bound inbound leases auto-release
   and outbound descriptors are ACKed/released through `RESP_SENT` (I8).
4. The kernel spawns a new capsule from the module card, with a fresh micro-pool
   and binding attachment.
5. Traffic resumes against the new capsule.

This preserves the binding invariants: bytecode/module descriptors have one owner
at a time (I2), no live descriptor graph is mutated under another capsule (I3),
the old and new capsules do not share descriptors except through kernel FIFOs
(I5), and teardown has a bounded release path (I8). The cost is that hot-reload is
a short capsule replacement event, not in-place code swapping. That is a good
trade for a micro-OS.

### Full on-device development loop

The eventual no-host loop looks like this:

1. **Edit:** the editor capsule receives input over a terminal/network binding and
   maintains the working buffer in `Memory`/cards.
2. **Compile:** it invokes a compiler capsule over `ipc` or runs a compiler
   subroutine over a source span. At Stage 2 this is a tiny expression compiler;
   at Stage 3 it is a useful PicoScript-0 subset.
3. **Store:** the compiler writes source, module, build-log, and manifest cards to
   the program pack. Successful builds advance `active_build`; failed builds leave
   the last runnable module intact.
4. **Run:** a supervisor loads `manifest.active_build`, validates the module card,
   and spawns a capsule bound to the requested console/network port.
5. **Debug:** traces, registers, build errors, and crash reports are just more
   cards or streams. The editor can query them without privileged access.

That loop is entirely compatible with PIOS because all communication is by
bindings, FIFOs, leases, and cards. There is no need for the editor to become a
kernel feature.

### Risks, gaps, and next step

The main gaps are concrete:

- **Compiler maturity:** Stage 1 assembles numeric listings; it does not parse the
  source users want to edit. Stage 2 is the next real unlock.
- **Kernel services:** PIOS needs a capsule loader that can validate and spawn
  bytecode from a module card, a supervisor API for quiesce/replace, and at least
  one interactive binding (`duplex` terminal/WebSocket or a simpler console
  stream).
- **Storage shape:** PicoBinarySerializer is record-oriented and string/int-only.
  That is fine for manifests, source, and small modules, but large bytecode wants
  chunk cards or a future typed-byte field.
- **Memory pressure:** an editor plus compiler uses token streams, source buffers,
  bytecode buffers, and screen state. All of that must fit in per-capsule
  `Memory`/micro-pools or be paged through cards.
- **Debug ergonomics:** traces and source maps need a card schema early, otherwise
  "on-device" will mean "opaque failures on-device".

Recommended next concrete step: build **Stage 2 as a compiler capsule contract**,
not just as a command-line demo. It should accept a source span/card id, emit a
module card plus build-log card, and prove the supervisor can spawn that module
from PicoStore/PicoWAL. In parallel, specify the minimal PIOS loader/supervisor
verbs for `spawn_from_card`, `quiesce`, and `replace_binding`. That combination is
the smallest milestone that turns self-hosting from byte emission into a credible
on-device edit → compile → store → run loop.
