# PicoScript Method Reference

**Version:** v0.3 (Lease-first, Case-insensitive v2 Language)

## Overview

This document provides a comprehensive reference for all PicoScript methods, organized by namespace. Each method shows:

- **Opcode**: Internal bytecode instruction
- **Hook Code**: Hexadecimal encoding for host hooks (reserved imm16 range)
- **v2 Syntax**: Case-insensitive, block-structured syntax example
- **Conformance Level**: L0 (minimal) through L6 (full security/crypto)

## Table of Contents

- [Descriptor](#descriptor)
- [Dsp](#dsp)
- [Flow](#flow)
- [Kernel](#kernel)
- [Lease](#lease)
- [Math](#math)
- [Memory](#memory)
- [Net](#net)
- [Queue](#queue)
- [Random](#random)
- [Span](#span)
- [Storage](#storage)
- [Thread](#thread)

---

## Descriptor

**Conformance Level:** L4  
**Methods:** 6

Data descriptor with flags, TTL, reference counting.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| CopyBatch | 0x00 | 0x7055 | `Descriptor.CopyBatch(...)` |
| GetFlags | 0x00 | 0x7054 | `Descriptor.GetFlags(...)` |
| GetLen | 0x00 | 0x7053 | `Descriptor.GetLen(...)` |
| GetPtr | 0x00 | 0x7052 | `Descriptor.GetPtr(...)` |
| Make | 0x00 | 0x7050 | `Descriptor.Make(...)` |
| SetFlags | 0x00 | 0x7051 | `Descriptor.SetFlags(...)` |

## Dsp

**Conformance Level:** L0  
**Methods:** 16

Digital signal processing: neural network ops, matrix operations.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Concat | 0x0F+0x0E | - | `Dsp.Concat(...)` |
| Dequant | 0x0F+0x0C | - | `Dsp.Dequant(...)` |
| Dot | 0x0F+0x02 | - | `Dsp.Dot(...)` |
| Embed | 0x0F+0x0A | - | `Dsp.Embed(...)` |
| Gelu | 0x0F+0x07 | - | `Dsp.Gelu(...)` |
| Mask | 0x0F+0x0D | - | `Dsp.Mask(...)` |
| MatMul | 0x0F+0x00 | - | `Dsp.MatMul(...)` |
| Norm | 0x0F+0x05 | - | `Dsp.Norm(...)` |
| Quant | 0x0F+0x0B | - | `Dsp.Quant(...)` |
| Relu | 0x0F+0x04 | - | `Dsp.Relu(...)` |
| Scale | 0x0F+0x03 | - | `Dsp.Scale(...)` |
| Softmax | 0x0F+0x01 | - | `Dsp.Softmax(...)` |
| Split | 0x0F+0x0F | - | `Dsp.Split(...)` |
| TopK | 0x0F+0x06 | - | `Dsp.TopK(...)` |
| Transpose | 0x0F+0x08 | - | `Dsp.Transpose(...)` |
| VAdd | 0x0F+0x09 | - | `Dsp.VAdd(...)` |

## Flow

**Conformance Level:** L1  
**Methods:** 4

Control flow: jumps, branches, function calls, returns.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Branch | 0x0A | - | `Flow.Branch(...)` |
| Call | 0x0B | - | `Flow.Call(...)` |
| Jump | 0x09 | - | `Flow.Jump(...)` |
| Return | 0x0C | - | `Flow.Return(...)` |

## Kernel

**Conformance Level:** L6  
**Methods:** 6

Core kernel interaction: process management, IPC, system control.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| FireSWIRQ | 0x00 | 0x7003 | `Kernel.FireSWIRQ(...)` |
| ProfileEnd | 0x00 | 0x7005 | `Kernel.ProfileEnd(...)` |
| ProfileStart | 0x00 | 0x7004 | `Kernel.ProfileStart(...)` |
| TracePoint | 0x00 | 0x7006 | `Kernel.TracePoint(...)` |
| WaitIRQ | 0x00 | 0x7001 | `Kernel.WaitIRQ(...)` |
| WaitSWIRQ | 0x00 | 0x7002 | `Kernel.WaitSWIRQ(...)` |

## Lease

**Conformance Level:** L4  
**Methods:** 6

Lease lifecycle: acquire, validate, release, stats.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Acquire | 0x00 | 0x7058 | `Lease.Acquire(...)` |
| CachedValidate | 0x00 | 0x705B | `Lease.CachedValidate(...)` |
| GetSpan | 0x00 | 0x705C | `Lease.GetSpan(...)` |
| GetTypeHint | 0x00 | 0x705D | `Lease.GetTypeHint(...)` |
| Release | 0x00 | 0x7059 | `Lease.Release(...)` |
| Validate | 0x00 | 0x705A | `Lease.Validate(...)` |

## Math

**Conformance Level:** L1  
**Methods:** 5

Mathematical ALU operations: add, subtract, multiply, divide.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Add | 0x04 | - | `Math.Add(...)` |
| Div | 0x07 | - | `Math.Div(...)` |
| Inc | 0x08 | - | `Math.Inc(...)` |
| Mul | 0x06 | - | `Math.Mul(...)` |
| Sub | 0x05 | - | `Math.Sub(...)` |

## Memory

**Conformance Level:** L4  
**Methods:** 6

Arena allocation and lease-based typed access primitives.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| ArenaAlloc | 0x00 | 0x7031 | `Memory.ArenaAlloc(...)` |
| ArenaInit | 0x00 | 0x7030 | `Memory.ArenaInit(...)` |
| ArenaReset | 0x00 | 0x7032 | `Memory.ArenaReset(...)` |
| ArenaStats | 0x00 | 0x7033 | `Memory.ArenaStats(...)` |
| Peek | 0x00 | 0x7034 | `Memory.Peek(...)` |
| Poke | 0x00 | 0x7035 | `Memory.Poke(...)` |

## Net

**Conformance Level:** L1  
**Methods:** 5

HTTP response framing: status, headers, body, close.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Body | 0x00 | - | `Net.Body(...)` |
| Close | 0x00 | - | `Net.Close(...)` |
| Header | 0x00 | - | `Net.Header(...)` |
| Status | 0x00 | - | `Net.Status(...)` |
| Type | 0x00 | - | `Net.Type(...)` |

## Queue

**Conformance Level:** L5  
**Methods:** 5

Queue operations: async task enqueue/dequeue, batch operations.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Depth | 0x00 | 0x7012 | `Queue.Depth(...)` |
| Dequeue | 0x00 | 0x7010 | `Queue.Dequeue(...)` |
| DequeueBatch | 0x00 | 0x7013 | `Queue.DequeueBatch(...)` |
| Enqueue | 0x00 | 0x7011 | `Queue.Enqueue(...)` |
| EnqueueBatch | 0x00 | 0x7014 | `Queue.EnqueueBatch(...)` |

## Random

**Conformance Level:** L4  
**Methods:** 1

Cryptographically-seeded randomness from host startup.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| U32 | 0x00 | 0x7020 | `Random.U32(...)` |

## Span

**Conformance Level:** L4  
**Methods:** 2

Span descriptor (offset + length) for zero-copy access.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Make | 0x00 | 0x7040 | `Span.Make(...)` |
| Slice | 0x00 | 0x7041 | `Span.Slice(...)` |

## Storage

**Conformance Level:** L5  
**Methods:** 11

Persistent storage: pack/card schema, CRUD, query.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AddCard | 0x00 | 0x7062 | `Storage.AddCard(...)` |
| DeleteCard | 0x00 | 0x7064 | `Storage.DeleteCard(...)` |
| GetSchemaForPack | 0x00 | 0x7060 | `Storage.GetSchemaForPack(...)` |
| Load | 0x01 | - | `Storage.Load(...)` |
| PatchCard | 0x00 | 0x7065 | `Storage.PatchCard(...)` |
| Pipe | 0x03 | - | `Storage.Pipe(...)` |
| QueryCard | 0x00 | 0x7067 | `Storage.QueryCard(...)` |
| ReadCard | 0x00 | 0x7066 | `Storage.ReadCard(...)` |
| Save | 0x02 | - | `Storage.Save(...)` |
| SetSchemaForPack | 0x00 | 0x7061 | `Storage.SetSchemaForPack(...)` |
| UpdateCard | 0x00 | 0x7063 | `Storage.UpdateCard(...)` |

## Thread

**Conformance Level:** L5  
**Methods:** 4

Thread preemption hints and cooperative yielding.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Raise | 0x0E | - | `Thread.Raise(...)` |
| Skip | 0x00 | - | `Thread.Skip(...)` |
| Wait | 0x0D | - | `Thread.Wait(...)` |
| YieldCounted | 0x00 | 0x7070 | `Thread.YieldCounted(...)` |

---

## Summary by Conformance Level

### L0: 16 methods

- Dsp.Concat (core)
- Dsp.Dequant (core)
- Dsp.Dot (core)
- Dsp.Embed (core)
- Dsp.Gelu (core)
- Dsp.Mask (core)
- Dsp.MatMul (core)
- Dsp.Norm (core)
- Dsp.Quant (core)
- Dsp.Relu (core)
- Dsp.Scale (core)
- Dsp.Softmax (core)
- Dsp.Split (core)
- Dsp.TopK (core)
- Dsp.Transpose (core)
- Dsp.VAdd (core)

### L1: 14 methods

- Flow.Branch (core)
- Flow.Call (core)
- Flow.Jump (core)
- Flow.Return (core)
- Math.Add (core)
- Math.Div (core)
- Math.Inc (core)
- Math.Mul (core)
- Math.Sub (core)
- Net.Body (core)
- Net.Close (core)
- Net.Header (core)
- Net.Status (core)
- Net.Type (core)

### L4: 21 methods

- Descriptor.CopyBatch (0x7055)
- Descriptor.GetFlags (0x7054)
- Descriptor.GetLen (0x7053)
- Descriptor.GetPtr (0x7052)
- Descriptor.Make (0x7050)
- Descriptor.SetFlags (0x7051)
- Lease.Acquire (0x7058)
- Lease.CachedValidate (0x705B)
- Lease.GetSpan (0x705C)
- Lease.GetTypeHint (0x705D)
- Lease.Release (0x7059)
- Lease.Validate (0x705A)
- Memory.ArenaAlloc (0x7031)
- Memory.ArenaInit (0x7030)
- Memory.ArenaReset (0x7032)
- Memory.ArenaStats (0x7033)
- Memory.Peek (0x7034)
- Memory.Poke (0x7035)
- Random.U32 (0x7020)
- Span.Make (0x7040)
- Span.Slice (0x7041)

### L5: 20 methods

- Queue.Depth (0x7012)
- Queue.Dequeue (0x7010)
- Queue.DequeueBatch (0x7013)
- Queue.Enqueue (0x7011)
- Queue.EnqueueBatch (0x7014)
- Storage.AddCard (0x7062)
- Storage.DeleteCard (0x7064)
- Storage.GetSchemaForPack (0x7060)
- Storage.Load (core)
- Storage.PatchCard (0x7065)
- Storage.Pipe (core)
- Storage.QueryCard (0x7067)
- Storage.ReadCard (0x7066)
- Storage.Save (core)
- Storage.SetSchemaForPack (0x7061)
- Storage.UpdateCard (0x7063)
- Thread.Raise (core)
- Thread.Skip (core)
- Thread.Wait (core)
- Thread.YieldCounted (0x7070)

### L6: 6 methods

- Kernel.FireSWIRQ (0x7003)
- Kernel.ProfileEnd (0x7005)
- Kernel.ProfileStart (0x7004)
- Kernel.TracePoint (0x7006)
- Kernel.WaitIRQ (0x7001)
- Kernel.WaitSWIRQ (0x7002)

---

## Hook Code Allocation

Host hooks use reserved imm16 range 0x7000-0x7FFF:

| Range | Namespace | Count | Purpose |
|-------|-----------|-------|---------|
| 0x7001-0x7006 | Kernel | 6 | Process, IPC |
| 0x7010-0x7014 | Queue | 5 | Task queue |
| 0x7020 | Random | 1 | RNG |
| 0x7030-0x7033 | Memory | 4 | Arena |
| 0x7040-0x7041 | Span | 2 | Spans |
| 0x7050-0x7055 | Descriptor | 6 | Descriptors |
| 0x7058-0x705D | Lease | 6 | Leases |
| 0x7060-0x7067 | Storage | 8 | Cards/packs |
| 0x7070 | Thread | 1 | Preemption |
| 0x7080-0x708B | String | 12 | Strings |
| 0x7090-0x709A | Number | 11 | Numbers |
| 0x70A0-0x70AB | Maths | 12 | Math |
| 0x70B0-0x70BA | DateTime | 11 | Date/time |
| 0x70C0-0x70C6 | Locale | 7 | Locale |
| 0x70D0-0x70D8 | Environment | 9 | Environment |
| 0x70E0-0x70EE | Context | 15 | Context |
| 0x70F0-0x70FE | Crypto | 15 | Crypto |

**Total:** 77 methods across 13 namespaces.

## IDE Code Completion

### When User Types Namespace Dot:

```
String.<COMPLETIONS>
  .Concat(s1, s2) -> string
  .Length(s) -> int
  .Substring(s, start, len) -> string
  .IndexOf(s, substr) -> int
  .Split(s, delim) -> array
  ...
```

### Syntax Highlighting

```
KEYWORDS:        IF THEN ELSE ENDIF WHILE ENDWHILE FOREACH AS IN 
                 ENDFOREACH SWITCH CASE ENDSWITCH LET RETURN
NAMESPACES:      String Number Maths DateTime Locale Environment 
                 Context Crypto Kernel Queue Memory Span Descriptor Lease Storage
METHODS:         .MethodName(...) via dot notation
IDENTIFIERS:     Case-insensitive (all normalized to lowercase)
LITERALS:        "string" 123 3.14 true false
OPERATORS:       = + - * / % < > <= >= == != AND OR NOT
COMMENTS:        // rest of line
```

### Performance Annotations

Editors may color-code methods:

- **Green (FAST)**: O(1) userland - String.Length, Number.Parse
- **Yellow (LAZY)**: Cached on first call - Context.GetHeaders
- **Red (FIFO)**: Kernel IPC needed - Crypto.Sign, Kernel operations

## v2 Syntax Examples

### String Operations

```
LET s1 = "Hello"
LET s2 = "World"
LET combined = String.Concat(s1, " ", s2)
LET len = String.Length(combined)
IF len > 10 THEN
  LET upper = String.ToUpper(combined)
ENDIF
```

### Context Access (Lazy-Decoded)

```
LET user = Context.GetUser()        -- Expensive on first call
LET verb = Context.GetVerb()        -- Fast: pre-cached
LET headers = Context.GetHeaders()  -- Expensive on first call
```

### Control Flow

```
FOREACH item AS x IN items THEN
  IF String.Length(x) > 0 THEN
    Queue.Enqueue(x)
  ENDIF
ENDFOREACH
```

### Memory/Lease

```
LET lease = Memory.ArenaAlloc(1024)
LET handle = Lease.Acquire(lease)
Lease.Validate(handle)
Lease.Release(handle)
```

### Cryptography

```
-- Userland key (fast, no kernel mediation)
LET key = "my-session-token"
LET sig = Crypto.HmacSha256(key, "data")

-- System key (kernel FIFO, audit-logged)
LET sys_sig = Crypto.Sign(system_handle, "data")
```

---

Generated from `picoscript_lang.py` (v0.3)
