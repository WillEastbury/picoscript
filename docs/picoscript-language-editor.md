# PicoScript Language and Editor Specification

This document is the client-facing PicoScript language and editor contract. It is intentionally separate from the hardware specification so the source language, display syntaxes, diagnostics, and editing experience can be tuned without changing the FPGA bytecode contract.

The canonical hardware bytecode contract is `docs/picoscript-hardware.md`.

## Scope

Language/editor owns:

- Source syntax and aliases
- Namespace and method naming
- Label syntax
- Formatting and CRLF/LF handling
- Decompiler views
- Diagnostics
- Autocomplete metadata
- Refactoring rules
- Editor round-trip guarantees

Language/editor does not own:

- Opcode numbers
- Bit layout
- Register width
- Hardware cycle counts
- RTL module boundaries

## Core Principle

Cards store bytecode, not source text.

Source files are views over bytecode. The editor may let one user write C#-style PicoScript, another view the same card as BASIC, and another view it as Python-style calls. Save compiles source to bytecode. Load decompiles bytecode to the selected view.

## Current Syntax Views

`picoscript_lang.py` currently accepts register-level C-style namespace calls and BASIC-style input, and supports these decompiler views:

| Mode | Extension | Example |
|------|-----------|---------|--------|
| C style | `.pico` | `Storage.Load(0, 1, 42, R0);` | Input + output |
| BASIC style | `.bas` | `10 STORAGE LOAD, 0, 1, 42, R0` | Input + output |
| Python style | `.py` | `storage.load(0, 1, 42, r0)` | Output view only |
| Hex | `.hex` | `1040002A` | Output view only |

The register-level compiler must preserve bytecode across C/BASIC views: C-style source can be decompiled to BASIC and recompiled to the same bytecode, and BASIC can be decompiled to C-style and recompiled to the same bytecode.

## High-Level Source Frontends

Beyond the register-level v1 syntax above, two **high-level imperative frontends**
compile through a shared intermediate language (PicoIL) to the same frozen
bytecode — and also to C and JavaScript. They add named variables (auto-allocated
to `R0`–`R15`) and integer expressions, so authors write ordinary code instead of
hand-managing registers. Both are **case-insensitive for keywords and variable
names**; `Namespace.Method` resolves case-insensitively to the canonical host name.

| Frontend | File | Extension | Style |
|----------|------|-----------|-------|
| C-syntax | `picoscript_cfront.py` | `.pc` | curly-brace, C-like |
| BASIC-like | `picoscript_basic.py` | `.pbas` | block-structured |
| Python-style | `picoscript_python.py` | `.ppy` | significant indentation, colon blocks |
| Natural-English | `picoscript_english.py` | `.eng` | plain imperative sentences |

All four frontends build the **same AST and reuse the same `Lowerer`**, so the same
program in any style lowers to **byte-for-byte identical bytecode** (the test suite
asserts `python == basic` and `english == basic` for matching programs). The
Python-style and English-style frontends are intentionally structured (they have
no `goto`/`switch`/`do-loop`); everything else maps across all four surfaces.

**C-syntax constructs:** `int`/`var` declarations, assignment and compound
assignment (`+= -= *= /= %=`), arithmetic `+ - * / %`, comparisons, logical
`&& || !`, the ternary `?:`, pre/post `++`/`--`, `if/else`, `while`, `for`,
`break`, `continue`, `return`, `void` subroutines and calls, `print(expr)`, and
`Namespace.Method(...)` (Net/Storage/host) calls. Line comments `//`, block `/* */`.

**BASIC-like constructs:** `DIM`/`LET` declaration and `x = expr` assignment (plus
compound `+= -= *= /=`), `INC`/`DEC`, arithmetic `+ - * / MOD`, comparisons by
symbol (`= <> < > <= >=`) or word (`EQ NE LT GT LE GE`) where `=` means equality
inside a test, logical `AND OR NOT`, the `IIF(cond, a, b)` ternary,
`IF/THEN/ELSEIF/ELSE/ENDIF`, `WHILE/ENDWHILE`, `DO/LOOP` (with a `WHILE`/`UNTIL`
guard at the `DO` for pre-test or at the `LOOP` for post-test), `FOR/TO/STEP/NEXT`,
`FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/DEFAULT/ENDSWITCH`, `GOTO` + `name:` labels,
`GOSUB`/`SUB`/`ENDSUB`, `RETURN`, `BREAK` (exit nearest loop or `SWITCH`), `SKIP`
(continue nearest loop), `PRINT expr`. Line comments `'` or `//`.

**Python-style constructs:** first assignment declares (`x = expr`), augmented
assignment (`+= -= *= /= %=`), arithmetic `+ - * / %`, comparisons `== != < > <= >=`,
logical `and`/`or`/`not`, the conditional expression `a if c else b`, `if:` /
`elif:` / `else:` indentation blocks, `while:`, `for i in range(n):` (0..n-1) and
`for i in range(a, b[, step]):` (a..b-1), `def name():` subroutines and `name()`
calls, `return` / `break` / `continue` / `pass`, `print(expr)`, and
`Namespace.Method(...)` host calls. Line comments `#`. Example:

```python
total = 0
for i in range(1, 11):
    total += i
if total > 50:
    print(total)
```

**Natural-English constructs:** the pièce de résistance — programs read like plain
imperative sentences and still compile to the identical bytecode. Compound
statements use a colon + indented block; simple statements end at the line (a
trailing `.` is allowed and idiomatic).

| English | Meaning |
|---------|---------|
| `Set X to <expr>.` / `Let X be <expr>.` | assign (first use declares) |
| `Add <e> to X.` / `Subtract <e> from X.` | `X = X + e` / `X = X - e` |
| `Increase X by <e>.` / `Decrease X by <e>.` | `+= ` / `-=` |
| `Multiply X by <e>.` / `Divide X by <e>.` | `*=` / `/=` |
| `Print <expr>.` / `Show <expr>.` / `Display <expr>.` | emit a value |
| `If <cond>:` … `Otherwise if <cond>:` … `Otherwise:` | conditional block |
| `While <cond>:` / `Repeat while <cond>:` / `As long as <cond>:` | pre-test loop |
| `Repeat <n> times with X:` | `X` counts `0..n-1` |
| `For each X from <a> to <b>:` | `X` counts `a..b` inclusive |
| `Define <name>:` / `To <name>:` | subroutine (globals; no params) |
| `Do <name>.` / `Call <name>.` | invoke a subroutine |
| `Return.` / `Stop.` (break) / `Skip.` (continue) | control flow |
| `Ns.Method(a, b).` | host hook call |

Comparisons are written in words: `is greater than`, `is less than`, `is at least`
(`>=`), `is at most` (`<=`), `is greater than or equal to`, `is less than or equal
to`, `is` / `equals` / `is equal to` (`==`), `is not` / `is not equal to` (`!=`),
`exceeds` (`>`), combined with `and` / `or` / `not`. Arithmetic may be written in
words too — `plus`, `minus`, `times`, `divided by`, `modulo` / `mod` — or with the
usual symbols. Articles `a`/`an`/`the` before a value are ignored. Line comments `#`.
Example (identical bytecode to the Python and BASIC versions above):

```text
Set total to 0.
For each i from 1 to 10:
    Increase total by i.
If total is greater than 50:
    Print total.
```

The build driver `picoscript_build.py` runs or emits any stage:
`run`, `emit --as il|bytecode|c|js`, and `native` (zig cc). All four frontends are
also ported to JavaScript (`vm/picoc.js`) so they compile **in the browser**,
byte-for-byte identical to Python — see the live playground `docs/playground.html`
and the pipeline overview `docs/COMPILER_ARCHITECTURE.md`. Force a frontend with
`--lang c|basic|python|english`, or let the extension choose
(`.pc`/`.pbas`/`.ppy`/`.eng`).

## Register-Level C-style Source Syntax

The register-level C-style syntax is:

```csharp
Namespace.Method(arg0, arg1, ...);
```

Example:

```csharp
Net.Status(200);
Net.Type("text/html");
Net.Body();
Storage.Load(0, 1, 42, R0);
Flow.Branch(Z, R0, R0, :notfound);
Storage.Pipe(0, 1, 42, Stream.Out);
Flow.Return();
```

Labels start with `:` and bind to instruction indices:

```csharp
:loop
Math.Inc(R0);
Flow.Branch(LT, R0, R1, :loop);
```

Comments currently use `//`.

## Register-Level BASIC-style Source Syntax

BASIC-style input uses optional ascending line numbers, uppercase command groups, and comma-separated operands:

```basic
10 NET STATUS, 200
20 NET TYPE, TEXT/HTML
30 NET BODY
40 STORAGE LOAD, 0, 1, 42, R0
50 FLOW BRANCH, NZ, R0, R0, 40
60 FLOW RETURN
```

Numeric flow targets are BASIC line numbers. The compiler maps line numbers to instruction indices before emitting bytecode.

## Namespaces

Namespaces are language-facing names for hardware capabilities:

| Namespace | Purpose |
|-----------|---------|
| `Storage` | Card load, save, pipe, and storage backend ops |
| `Thread` | Skip, wait, raise, and performance hints |
| `Math` | Integer arithmetic |
| `Flow` | Jump, branch, call, return |
| `Dsp` | DSP envelope operations |
| `Net` | HTTP response metadata |
| `Kernel` | Host IRQ/SW_IRQ control and profiling hooks |
| `Queue` | Host queue descriptor and batching hooks |
| `Random` | Host RNG hooks |
| `Memory` | Host arena allocator hooks |
| `Span` | Host pointer/span hooks |
| `Descriptor` | Host descriptor and bulk transfer hooks |
| `Lease` | Lease/type-hint access and validation hooks |

These names are editor-facing. The compiler maps them to opcode fields described in `docs/picoscript-hardware.md`.

## Editor Model

The editor should treat PicoScript as a structured bytecode view:

1. Parse source into statements and labels.
2. Resolve labels.
3. Emit 32-bit instruction words.
4. Store only bytecode in cards.
5. Decompile bytecode back into the selected display syntax.

Round-trip invariants:

```text
C-style source -> bytecode -> BASIC source view -> bytecode
BASIC source -> bytecode -> C-style source view -> bytecode
C-style source -> bytecode -> C-style source view -> bytecode
BASIC source -> bytecode -> BASIC source view -> bytecode
```

The final bytecode should match unless the user edits semantics.

## Diagnostics

Diagnostics should be source-level and explain the hardware constraint when relevant:

| Error | Preferred diagnostic |
|-------|----------------------|
| Unknown namespace | `Unknown namespace 'X'. Expected Storage, Thread, Math, Flow, Dsp, Net, Kernel, Queue, Random, Memory, Span, Descriptor, or Lease.` |
| Unknown method | `Unknown method 'Storage.X'.` |
| Bad register | `Expected register R0-R15.` |
| Card address out of range | `Card address fields must fit tenant=0-31, pack=0-63, card=0-31.` |
| Unknown label | `Unknown label ':name'. Define it with ':name' on its own line.` |
| Unknown BASIC line | `Unknown BASIC line N.` |
| Non-ascending BASIC line numbers | `BASIC line numbers must be unique and ascending.` |
| Immediate out of range | `Immediate must fit imm16.` |

Avoid hardware-centric errors like "bad Rs2" in the editor unless the user is in hex/assembly mode.

## Completion Metadata

The editor can derive completions from the language namespace table:

| Trigger | Suggestions |
|---------|-------------|
| start of statement | `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease` |
| `Storage.` | `Load`, `Save`, `Pipe`, `GetSchemaForPack`, `SetSchemaForPack`, `AddCard`, `UpdateCard`, `DeleteCard`, `PatchCard`, `ReadCard`, `QueryCard`, `UsePack`, `EditCard`, `GetField`, `SetField`, `SetFieldStr`, `GetFieldStr`, `QueryResult` |
| `Thread.` | `Skip`, `Wait`, `Raise`, `YieldCounted` |
| `Math.` | `Add`, `Sub`, `Mul`, `Div`, `Inc` |
| `Flow.` | `Jump`, `Branch`, `Call`, `Return` |
| `Dsp.` | `MatMul`, `Softmax`, `Dot`, `Scale`, `Relu`, `Norm`, `TopK`, `Gelu`, `Transpose`, `VAdd`, `Embed`, `Quant`, `Dequant`, `Mask`, `Concat`, `Split` |
| `Net.` | `Status`, `Header`, `Type`, `Body`, `Close` |
| `Kernel.` | `WaitIRQ`, `WaitSWIRQ`, `FireSWIRQ`, `ProfileStart`, `ProfileEnd`, `TracePoint` |
| `Queue.` | `Dequeue`, `Enqueue`, `Depth`, `DequeueBatch`, `EnqueueBatch` |
| `Random.` | `U32` |
| `Memory.` | `ArenaInit`, `ArenaAlloc`, `ArenaReset`, `ArenaStats`, `Set`, `Get` |
| `Span.` | `Make`, `Slice`, `Materialize`, `Len`, `Get` |
| `Descriptor.` | `Make`, `SetFlags`, `GetPtr`, `GetLen`, `GetFlags`, `CopyBatch` |
| `Lease.` | `Acquire`, `Release`, `Validate`, `CachedValidate`, `GetSpan`, `GetTypeHint` |

Register completions should offer `R0` through `R15`, with `R15` marked read-only/context.

## Formatting

Recommended C#-style formatting:

- One statement per line
- Labels on their own line
- Four-space indentation for statements under labels when displayed in examples
- Semicolon required in C# style
- Preserve comments where source text is available

Decompiler output currently uses CRLF so generated source views remain easy to consume across editors and terminals.

## Language Tuning Guidelines

Language changes are encouraged here as long as emitted bytecode remains stable. Good candidates:

- Friendlier aliases, such as `return;` mapping to `Flow.Return();`
- Safer high-level forms, such as `if R0 == R1 goto :done`
- Editor-only macros that expand deterministically to bytecode
- Better field/schema names that compile to numeric card fields
- Snippets for HTTP handlers, filters, scans, and template responders

Avoid adding features that hide unpredictable work from the hardware. PicoScript should remain a transparent view over finite bytecode.

## Host Hook Primitives (fillable runtime surface)

To support queue-driven runtimes across non-identical hosts, PicoScript exposes a reserved hook surface compiled as `NOOP` with reserved metadata encodings:

### Control & IRQ

- `Kernel.WaitIRQ([Rmask]);`
- `Kernel.WaitSWIRQ([Rmask]);`
- `Kernel.FireSWIRQ(Rpid);` (permission-gated in host/kernel policy)

### Batching & Amortization (Performance)

- `Queue.Dequeue(queueId, Rdest);`
- `Queue.Enqueue(queueId, Rsrc);`
- `Queue.Depth(queueId, Rdest);`
- `Queue.DequeueBatch(queueId, Rcount, RspanOut);` — drain N items, return span of descriptors
- `Queue.EnqueueBatch(queueId, Rspan);` — enqueue from span atomically

### Profiling & Tracing (Performance)

- `Kernel.ProfileStart(Rslot);`
- `Kernel.ProfileEnd(Rslot, RtickOut);` — return elapsed ticks in Rslot
- `Kernel.TracePoint(ReventId, Rdata);`

### Other hooks

- `Thread.YieldCounted(Riterations);` — hint for batch preemption
- `Random.U32(Rdest);`
- `Memory.ArenaInit(Rbase, Rsize, Rarena);`
- `Memory.ArenaAlloc(Rarena, Rbytes, RptrOut);`
- `Memory.ArenaReset(Rarena);`
- `Memory.ArenaStats(Rarena, Rout);`
- `Memory.Set(Rspan, Ridx, Rval);` — write one byte into a span's backing arena.
- `Memory.Get(Rspan, Ridx, Rout);` — read one byte from a span.
- `Span.Make(Rptr, Rlen, RspanOut);`
- `Span.Slice(Rspan, Roff, Rout);` — zero-copy view (shares backing bytes).
- `Span.Materialize(Rspan, Rout);` — memcpy to a fresh contiguous region (independent copy).
- `Span.Len(Rspan, Rout);` — element count of a span.
- `Span.Get(Rspan, Ridx, Rout);` — read element `idx` of a span.
- `Descriptor.Make(Rptr, Rlen, RdescOut);`
- `Descriptor.SetFlags(Rdesc, Rflags);`
- `Descriptor.GetPtr(Rdesc, Rout);`
- `Descriptor.GetLen(Rdesc, Rout);`
- `Descriptor.GetFlags(Rdesc, Rout);`
- `Descriptor.CopyBatch(RsrcSpan, RdstSpan, Rcount);` — bulk span transfer
- `Lease.Acquire(Rtype, Rspan, RleaseOut);`
- `Lease.Release(Rlease);`
- `Lease.Validate(Rlease, Rout);`
- `Lease.CachedValidate(Rlease, Rout);` — O(1) fast-path for hot leases
- `Lease.GetSpan(Rlease, RoutSpan);`
- `Lease.GetTypeHint(Rlease, RoutType);`
- `Storage.GetSchemaForPack(RpackCtx, Rout);`
- `Storage.SetSchemaForPack(RpackCtx, Rschema);`
- `Storage.AddCard(RpackCtx, Rcard, RoutId);`
- `Storage.UpdateCard(RpackCtx, RcardId, Rcard);`
- `Storage.DeleteCard(RpackCtx, RcardId);`
- `Storage.PatchCard(RpackCtx, RcardId, Rpatch);`
- `Storage.ReadCard(RpackCtx, RcardId, Rout);`
- `Storage.QueryCard(RpackCtx, Rquery, RoutCursor);`

Program-level card CRUD/query executable in the reference and JS VMs (PicoStore-backed; a `cur pack` + `cur card` context keeps every op within the 2-in/1-out host ABI). Field names and queries are UTF-8 byte-spans built in arena memory (`Memory.Set` + `Span.Make`):

- `Storage.UsePack(RpackId, Rout);` — select the active pack.
- `Storage.AddCard(RoutId);` — create an empty card, select it, return its id.
- `Storage.EditCard(RcardId, Rout);` — select an existing card (0 if missing).
- `Storage.SetField(RnameSpan, Rval, Rok);` — write an integer field on the current card.
- `Storage.SetFieldStr(RnameSpan, RvalSpan, Rok);` — write a string field.
- `Storage.GetField(RnameSpan, Rout);` — read an integer field (0 if missing).
- `Storage.GetFieldStr(RnameSpan, RoutSpan);` — read a string field into a new span.
- `Storage.DeleteCard(RcardId, Rok);` — delete a card from the current pack.
- `Storage.QueryCard(RquerySpan, Rcount);` — run the query language, return match count.
- `Storage.QueryResult(Ridx, RoutId);` — read the i-th matching card id.

These are language-stable and host-fillable. They preserve bytecode compatibility while allowing runtime-specific implementation behind the contract.

Access model rule: all runtime data access should be lease-mediated (`Lease.*`) using a type hint plus span/pointer `(offset,length)`.

Performance hooks (`Queue.*Batch`, `Descriptor.CopyBatch`, `Lease.CachedValidate`, `Kernel.Profile*`, `Kernel.TracePoint`, `Thread.YieldCounted`) are optional and do not affect determinism when omitted.

## Parser Boundaries

The compiler should keep language parsing separate from bytecode emission:

```text
source text -> AST/statements -> resolved IR -> 32-bit words
```

The current implementation is a compact direct parser in `picoscript_lang.py`. As the editor grows, it should be split so autocomplete, formatting, diagnostics, and compilation reuse the same parse result.

## Files

Language/editor files:

- `picoscript_lang.py` - v1 compiler, decompilers, examples
- `picoscript_cfront.py` - C-syntax frontend (.pc) → PicoIL
- `picoscript_basic.py` - BASIC-like frontend (.pbas) → PicoIL
- `picoscript_il.py` - PicoIL: optimizer, register allocator, `lower_to_bytecode`/`lower_to_c`/`lower_to_js`
- `picoscript_vm.py` - Python reference VM (runtime)
- `picoscript_build.py` - unified driver (run / emit il|bytecode|c|js / native)
- `vm/picovm.c`, `vm/picovm.js` - portable C and JS VMs (bare metal / browser)
- `vm/picoc.js` - in-browser compiler (byte-identical to Python)
- `docs/playground.html` - side-by-side language guide + live compile/run/step debugger
- `docs/COMPILER_ARCHITECTURE.md` - frontend/IL/backend pipeline
- `docs/picoscript-language-editor.md` - language/editor contract

Hardware contract files consumed by language tooling:

- `docs/picoscript-hardware.md`
- `picoscript.py`
- `picoscript_opcodes.py`
- `picowal_hx_cu/picoscript_decode.v`
