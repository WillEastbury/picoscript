# PicoScript Language & Runtime Specification (Draft v0.2)

Status: **Extended with v2 block-structured syntax and library namespaces**

This document defines PicoScript v1 (stable, namespace/method) and v2 (block-structured, case-insensitive) language models. Both target the same bytecode ISA.

PicoScript is a deterministic handler language for userland message processing in the Pico stack (PIOS kernel + picoweb runtime). It is not PIOS-only; a conforming host on any platform may compile and run PicoScript if it satisfies the runtime and ABI contracts defined here.

---

## 1. Scope and non-goals

PicoScript is a deterministic handler language for protocol and application logic. It is designed to run in a host runtime (PIOS/picoweb class host), not as a kernel or network stack replacement.

PicoScript **does not** own:

- socket creation/accept/connect
- TCP/UDP/IP semantics
- direct device I/O
- interrupt controller programming

PicoScript **does** own:

- bounded message/event processing
- deterministic transformation of input descriptors into output descriptors
- explicit state transitions over host-provided state
- arena allocation of its own process memory
- zero-copy descriptor/span shipping where possible
- lease-based access mediation using type hints + span/pointer(offset,length)

---

## 2. Language Versions

### v1: Namespace/Method Syntax (Stable, Frozen Bytecode)

Primary syntax: C#-style method calls on hardware namespaces.

```csharp
Storage.Load(tenant, pack, card, R0);
Math.Add(R1, R0, 42);
Flow.Branch(GT, R1, R0, :done);
```

**Properties:**
- Case-sensitive (`Storage.Load` ≠ `storage.load`)
- Statements end with `;` or newline
- Labels prefixed with `:` (`":done"`)
- No whitespace normalization (parsing is strict)
- Bytecode ISA v1 (stable, frozen at 16 opcodes)

**Namespaces (v1):** `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease`

### v2: Block-Structured Syntax (New, Same Bytecode)

Alternative syntax: case-insensitive, BASIC-like with explicit block delimiters.

```basic
IF R0 EQ 42 THEN
    String.Concat(R1, R2, R3)
    Number.Format(R4, R3, 2)
ELSE
    Maths.Sqrt(R5, R6)
ENDIF

WHILE R9 LT 100
    Maths.Add(R9, R9, 1)
ENDWHILE

FOREACH item AS i IN items
    DateTime.GetNow(R7)
    Locale.Format(R8, R7, "en_US")
ENDFOREACH

SWITCH R0
    CASE 1
        Queue.Dequeue(R1, R2)
    CASE 2
    CASE 3
        Queue.Enqueue(R3, R4)
    ELSE
        Thread.Skip()
ENDSWITCH
```

**Properties:**
- Case-insensitive: `IF`/`if`/`If` all valid; `String.Concat`/`string.concat`/`STRING.CONCAT` all map to same opcode
- Whitespace-ignorant: comments (`//`), indentation, blank lines ignored
- Line endings: CRLF or LF (tracked for diagnostics)
- No semicolons or curly brackets required
- Explicit block delimiters: `IF/THEN/ELSE/ENDIF`, `WHILE/ENDWHILE`, `FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/ELSE/ENDSWITCH`
- Same v1 bytecode ISA (no new opcodes)

**Namespaces (v2 = v1 + Library):**
- **v1 core:** `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease`
- **v2 new:** `String`, `Number`, `Maths`, `DateTime`, `Locale` (compile to host hooks like v1 extended namespaces)

### Interoperability

- v1 and v2 compile to identical bytecode for equivalent logic
- Bytecode does not indicate source language
- Editors can round-trip: load bytecode, display in either v1 or v2 syntax, re-save as bytecode (output matches input)

---

## 3. System contract (normative)

All I/O paths visible to PicoScript-hosted execution are restricted to:

1. kernel-shipped socket data descriptors via FIFO
2. RAM access
3. IPC FIFO
4. storage backend card/pack load

No other I/O classes are valid for conforming hosts.

## 4. Runtime architecture

### 4.1 Roles

- **Kernel (PIOS):** owns IRQ/SW_IRQ routing, sockets, FIFO transport, scheduling, wake/sleep decisions.
- **Host runtime:** drains inbound descriptors, invokes PicoScript entrypoints, enqueues outbound descriptors.
- **PicoScript program:** pure deterministic processing logic with bounded execution budgets.

### 4.2 Wake-drain-sleep lifecycle

1. Kernel signals data/work available (IRQ/SW_IRQ).
2. Worker wakes and drains inbound queue until empty (or budget cap).
3. Each descriptor dispatches into a PicoScript event entrypoint.
6. If inbound queue is empty, worker returns to wait state.

## 5. Bytecode VM contract

The bytecode ISA is stable and frozen. PicoScript compiles to 32-bit fixed instruction words:

- **Opcodes [31:28]:** 4-bit primary operation (OP_NOOP, OP_LOAD, OP_SAVE, OP_PIPE, OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_INC, OP_JUMP, OP_BRANCH, OP_CALL, OP_RETURN, OP_WAIT, OP_RAISE, OP_DSP)
- **Rd [27:24]:** destination register
- **Rs1 [23:20]:** source register 1
- **Rs2 [19:16]:** source register 2 or condition/addressing mode
- **Immediate [15:0]:** 16-bit immediate, card address, imm16, or sub-opcode

Reference opcode groups (v1 stable):

- storage/pipe: `LOAD`, `SAVE`, `PIPE`
- ALU: `ADD`, `SUB`, `MUL`, `DIV`, `INC`
- control flow: `JUMP`, `BRANCH`, `CALL`, `RETURN`
- thread: `WAIT`, `RAISE`
- DSP: `DSP` (with 16 sub-operations: MatMul, Softmax, Dot, etc.)
- hooks: `NOOP` + reserved imm16 range for host hooks (Kernel, Queue, Memory, Span, Descriptor, Lease, String, Number, Maths, DateTime, Locale)

## 6. Kernel/host ABI surface for queue processing

This ABI is the formal processing contract the language targets.

### 6.1 Descriptor-driven entrypoint

Host runtime invokes:

- `dispatch(program, event, context, opts)`

Where `context` contains:

- inbound payload descriptor/buffer
- caller-visible variable map
- optional metadata (connection/session/channel ids)

### 6.1.1 Core runtime constructs

Conforming hosts should provide these base constructs:

- `TypeHint` (logical data type classification)
- `Span` (`ptr`, `length`)
- `Descriptor` (`ptr`, `length`, `flags`)
- `Lease` (`lease_id`, `type_hint`, `span`, state/generation metadata)

Lease-first rule: script-visible access to spans/descriptors is mediated via leases, not raw pointer use.

### 6.2 Queue handling primitives (host ABI)

Host must provide operations equivalent to:

- `Q_DEQUEUE(in_q) -> descriptor|none`
- `Q_ENQUEUE(out_q, descriptor) -> ok|error`
- `Q_DEPTH(q) -> integer`

PicoScript code itself does not mutate kernel queues directly; it emits payloads and host maps emit output to outbound descriptors.

### 6.3 IPC and kernel FIFO calls

For conformance, the host must map script events and emits to kernel IPC/FIFO operations. The exact syscall/API naming is implementation-defined, but semantics are fixed:

- inbound descriptor source is kernel FIFO/IPC FIFO
- outbound descriptor sink is kernel FIFO/IPC FIFO
- ordering is FIFO within each queue
- descriptor ownership transfer is explicit at enqueue/dequeue boundaries

### 6.4 IRQ / SW_IRQ wait-fire semantics

Formal semantics:

- **WAIT_IRQ:** block worker until hardware IRQ indicates work available.
- **WAIT_SW_IRQ:** block worker until software interrupt indicates work available.
- **FIRE_SW_IRQ(target):** request wake/signal of target worker/process after enqueue.
- **SLEEP:** yield execution; host may map to wait-for-IRQ/SW_IRQ policy.

### 6.2 Language exposure and permission model

Language exposure:

- `SLEEP` is an execution opcode and maps to host-yield semantics.
- WAIT/FIRE remain host/kernel control-plane actions and should be exposed to script only through constrained builtins if needed by policy.

Permission model (normative):

- `FIRE_SW_IRQ(pid)` is a **request**, not a direct interrupt operation.
- The kernel/host must authorize the caller against policy (ACL/capability/ownership rules) before issuing the SW_IRQ set call.
- If authorization fails, no wake is fired and a permission error is returned.
- Implementations must not allow unprivileged cross-process wake requests.

### 6.3 Host hook primitives

Recommended host hook primitive surface (all namespaces compile to `NOOP` + reserved imm16 encoding):

- `Kernel.WaitIRQ([Rmask])` / `Kernel.WaitSWIRQ([Rmask])` for controlled wait operations.
- `Kernel.FireSWIRQ(Rpid)` for permission-gated wake requests.
- `Kernel.ProfileStart/ProfileEnd/TracePoint(...)` for profiling and deterministic event tracing.
- `Queue.Dequeue/Enqueue/Depth(...)` for per-item queue operations.
- `Queue.DequeueBatch/EnqueueBatch(...)` for amortized batching (10-100x throughput gain).
- `Random.U32(Rdest)` for host-backed random generation.
- `Memory.ArenaInit/Alloc/Reset/Stats(...)` for arena lifecycle and allocation control.
- `Span.Make/Slice(...)` for zero-copy span construction and slicing.
- `Descriptor.Make/SetFlags/GetPtr/GetLen/GetFlags/CopyBatch(...)` for descriptor flow and bulk transfer.
- `Lease.Acquire/Release/Validate/CachedValidate/GetSpan/GetTypeHint(...)` for capability/lease-mediated access with fast-path validation.
- `Storage.GetSchemaForPack`, `Storage.SetSchemaForPack`, `Storage.AddCard`, `Storage.UpdateCard`, `Storage.DeleteCard`, `Storage.PatchCard`, `Storage.ReadCard`, `Storage.QueryCard` as backend-swappable storage API hooks.
- `String.Concat/Length/Substring/IndexOf/Split/Trim/ToUpper/ToLower/Replace/Format/Parse/Equals(...)` for string manipulation.
- `Number.Parse/Format/Round/Floor/Ceiling/Abs/Min/Max/Clamp/ToInt/ToFloat(...)` for numeric formatting and conversion.
- `Maths.Sqrt/Pow/Sin/Cos/Tan/Log/Exp/Abs/Min/Max/Gcd/Lcm(...)` for mathematical operations.
- `DateTime.GetNow/GetYear/GetMonth/GetDay/GetHour/GetMinute/GetSecond/ToTimestamp/FromTimestamp/AddDays/Format(...)` for datetime manipulation.
- `Locale.GetCurrent/SetCurrent/Format/Parse/GetLanguage/GetRegion/ToLocalTime(...)` for locale-aware formatting.
- `Thread.YieldCounted(iterations)` for preemption hint in tight loops.
- All host hook calls return explicit status/error codes via registers/flags; no silent fallback.

## 7. Performance model

PicoScript includes optional performance hooks for amortization, profiling, and fast-path validation:

### 7.1 Batching & Amortization (Throughput)

- `Queue.DequeueBatch(count) → span` — drain multiple queue descriptors in one host call, amortizing wake-up overhead. Reduces per-item dispatch cost ~100x for bulk processing.
- `Queue.EnqueueBatch(span)` — enqueue multiple descriptors atomically. Preserves ordering and reduces context switches.
- `Descriptor.CopyBatch(src_span, dst_span, count)` — bulk span transfer for zero-copy forwarding.

Rationale: queue drains are hot; batching amortizes context switch cost. Expected throughput gain: 10-100x vs. per-item dispatch.

### 7.2 Fast-Path & Arena Heuristics (Latency)

- `Lease.CachedValidate(lease_id) → bool` — O(1) validation for hot leases (host caches generation on acquire). Typical host cache hit: ~5% of lease checks.
- `Memory.ArenaStats() → (total, free, fragmentation_pct)` — guide allocation policy without scanning arena. Used for heuristic pool rebalance decisions.
- `Thread.YieldCounted(iterations)` — hint that next N loop iterations should run before preemption. Allows tight loops to batch work and reduce preemption overhead.

### 7.3 Profiling & Diagnostics (Observability)

- `Kernel.ProfileStart(slot)` — begin timing bracket in named slot (host buffers timestamps).
- `Kernel.ProfileEnd(slot) → elapsed_ticks` — end bracket, return elapsed time for in-script decisions (e.g., early exit on timeout).
- `Kernel.TracePoint(event_id, data)` — emit tagged event for host trace/replay infrastructure. Deterministic and zero-cost if tracing disabled.

Rationale: identify bottlenecks without guessing. All profiling hooks are optional host bindings; absent implementations are NOOPs.

### 7.4 Determinism & performance tradeoff

- All performance hooks are **optional** and isolated from control flow. Omitting them preserves baseline determinism; using them enables optimization without specifying timing guarantees.
- Profiling is deterministic (same event sequence on replay) but does not guarantee cycle counts match across runs (host scheduler variance).
- Batching preserves queue ordering and descriptor integrity.

## 8. Determinism requirements

A conforming runtime must guarantee:

- bounded execution per dispatch (block/slice/output ceilings)
- deterministic control semantics (instruction behavior and budget enforcement)
- no hidden I/O side channels
- explicit error signaling on budget violation or invalid op

Recommended policy:

- disable unbounded host callbacks from script
- for deterministic-profile deployments, forbid wall-clock-dependent script logic
- allow random number generation where policy permits

Randomness policy:

- PicoScript may use RNG in userland logic.
- Host RNG seed material should combine:
  - system clock entropy
  - a random offset vector generated at host startup
- Conformance does not require deterministic value outputs across dispatches when RNG is enabled.

## 9. Security boundaries

- Kernel remains sole owner of network stack and privilege transitions.
- Script runtime is non-privileged and memory-bounded.
- Runtime access to spans/descriptors is lease-gated; lease validity/type hint checks are enforced by host/kernel policy.
- Queue descriptors are validated by host before script exposure.
- Script outputs are treated as untrusted until host validation passes.
- Profile/trace data is host-directed; scripts cannot read profiling state beyond their own `ProfileEnd` return value.

## 10. Compilation targets

PicoScript supports multiple execution targets:

1. PicoScript bytecode VM (default, deterministic runtime target)
2. C emission (`toC`) for native toolchain builds (Thumb/AArch64 via host toolchains)
3. C# emission (`toCSharp`) for managed-host integration

Target choice must preserve deterministic contract and queue ABI semantics.

Compilation note: Both v1 (namespace/method) and v2 (block-structured) source syntax produce identical bytecode. Editors can round-trip: load bytecode, display in either v1 or v2 view, re-save (output matches input).

## 11. Conformance levels

- **L0 (Core):** parse/compile/disassemble + VM run/dispatch with deterministic budgets.
- **L1 (Queue host):** inbound queue drain + outbound queue emit integration.
- **L2 (Kernel-coupled):** IRQ/SW_IRQ wake-fire lifecycle integrated with FIFO ownership transfer.
- **L3 (Profiling & amortization):** optional performance hooks (batching, profiling, fast-path validation).
- **L4 (v2 syntax):** case-insensitive, block-structured source syntax (IF/THEN/ENDIF, WHILE/ENDWHILE, etc.) + library namespaces (String, Number, Maths, DateTime, Locale).

## 12. Open items for v0.3

- v2 parser completion and round-trip decompiler (bytecode → v2 syntax)
- fixed descriptor binary schema (header fields, endian, size limits)
- host hook namespace hardening and ABI freeze with performance + library hooks
- formal memory model for shared RAM windows
- trace/event format for deterministic replay and audit
- profiling hook payload schema and buffer management
- v2 language completions and diagnostics in editor
