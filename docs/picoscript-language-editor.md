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

`picoscript_lang.py` currently supports these decompiler views:

| Mode | Extension | Example |
|------|-----------|---------|
| C# style | `.pico` | `Storage.Load(0, 1, 42, R0);` |
| BASIC style | `.bas` | `10 STORAGE LOAD, 0, 1, 42, R0` |
| Python style | `.py` | `storage.load(0, 1, 42, r0)` |
| Hex | `.hex` | `1040002A` |

The checked-in compiler currently accepts the C#-style namespace/method syntax as the primary input syntax.

## Primary Source Syntax

The current primary syntax is:

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

## Namespaces

Namespaces are language-facing names for hardware capabilities:

| Namespace | Purpose |
|-----------|---------|
| `Storage` | Card load, save, pipe |
| `Thread` | Skip, wait, raise |
| `Math` | Integer arithmetic |
| `Flow` | Jump, branch, call, return |
| `Dsp` | DSP envelope operations |
| `Net` | HTTP response metadata |
| `Kernel` | Host IRQ/SW_IRQ control hooks |
| `Queue` | Host queue descriptor hooks |
| `Random` | Host RNG hooks |
| `Memory` | Host arena allocator hooks |
| `Span` | Host pointer/span hooks |
| `Descriptor` | Host descriptor hooks |
| `Lease` | Lease/type-hint access hooks |

These names are editor-facing. The compiler maps them to opcode fields described in `docs/picoscript-hardware.md`.

## Editor Model

The editor should treat PicoScript as a structured bytecode view:

1. Parse source into statements and labels.
2. Resolve labels.
3. Emit 32-bit instruction words.
4. Store only bytecode in cards.
5. Decompile bytecode back into the selected display syntax.

Round-trip invariant:

```text
source -> bytecode -> selected source view -> bytecode
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
| Immediate out of range | `Immediate must fit imm16.` |

Avoid hardware-centric errors like "bad Rs2" in the editor unless the user is in hex/assembly mode.

## Completion Metadata

The editor can derive completions from the language namespace table:

| Trigger | Suggestions |
|---------|-------------|
| start of statement | `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease` |
| `Storage.` | `Load`, `Save`, `Pipe`, `GetSchemaForPack`, `SetSchemaForPack`, `AddCard`, `UpdateCard`, `DeleteCard`, `PatchCard`, `ReadCard`, `QueryCard` |
| `Thread.` | `Skip`, `Wait`, `Raise` |
| `Math.` | `Add`, `Sub`, `Mul`, `Div`, `Inc` |
| `Flow.` | `Jump`, `Branch`, `Call`, `Return` |
| `Dsp.` | `MatMul`, `Softmax`, `Dot`, `Scale`, `Relu`, `Norm`, `TopK`, `Gelu`, `Transpose`, `VAdd`, `Embed`, `Quant`, `Dequant`, `Mask`, `Concat`, `Split` |
| `Net.` | `Status`, `Header`, `Type`, `Body`, `Close` |
| `Kernel.` | `WaitIRQ`, `WaitSWIRQ`, `FireSWIRQ` |
| `Queue.` | `Dequeue`, `Enqueue`, `Depth` |
| `Random.` | `U32` |
| `Memory.` | `ArenaInit`, `ArenaAlloc`, `ArenaReset`, `ArenaStats` |
| `Span.` | `Make`, `Slice` |
| `Descriptor.` | `Make`, `SetFlags`, `GetPtr`, `GetLen`, `GetFlags` |
| `Lease.` | `Acquire`, `Release`, `Validate`, `GetSpan`, `GetTypeHint` |

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

- `Kernel.WaitIRQ([Rmask]);`
- `Kernel.WaitSWIRQ([Rmask]);`
- `Kernel.FireSWIRQ(Rpid);` (permission-gated in host/kernel policy)
- `Queue.Dequeue(queueId, Rdest);`
- `Queue.Enqueue(queueId, Rsrc);`
- `Queue.Depth(queueId, Rdest);`
- `Random.U32(Rdest);`
- `Memory.ArenaInit(Rbase, Rsize, Rarena);`
- `Memory.ArenaAlloc(Rarena, Rbytes, RptrOut);`
- `Memory.ArenaReset(Rarena);`
- `Memory.ArenaStats(Rarena, Rout);`
- `Span.Make(Rptr, Rlen, RspanOut);`
- `Span.Slice(Rspan, Roff, Rout);`
- `Descriptor.Make(Rptr, Rlen, RdescOut);`
- `Descriptor.SetFlags(Rdesc, Rflags);`
- `Descriptor.GetPtr(Rdesc, Rout);`
- `Descriptor.GetLen(Rdesc, Rout);`
- `Descriptor.GetFlags(Rdesc, Rout);`
- `Lease.Acquire(Rtype, Rspan, RleaseOut);`
- `Lease.Release(Rlease);`
- `Lease.Validate(Rlease, Rout);`
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

These are language-stable and host-fillable. They preserve bytecode compatibility while allowing runtime-specific implementation behind the contract.

Access model rule: all runtime data access should be lease-mediated (`Lease.*`) using a type hint plus span/pointer `(offset,length)`.

## Parser Boundaries

The compiler should keep language parsing separate from bytecode emission:

```text
source text -> AST/statements -> resolved IR -> 32-bit words
```

The current implementation is a compact direct parser in `picoscript_lang.py`. As the editor grows, it should be split so autocomplete, formatting, diagnostics, and compilation reuse the same parse result.

## Files

Language/editor files:

- `picoscript_lang.py` - primary compiler, decompilers, examples
- `docs/picoscript-language-editor.md` - language/editor contract

Hardware contract files consumed by language tooling:

- `docs/picoscript-hardware.md`
- `picoscript.py`
- `picoscript_opcodes.py`
- `picowal_hx_cu/picoscript_decode.v`
