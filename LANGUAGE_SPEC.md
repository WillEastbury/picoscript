# PicoScript Language & Runtime Specification (Draft v0.1)

Status: **Draft for client+server convergence**

This document defines the formal PicoScript model for deterministic userland processing in the Pico stack, with kernel-owned I/O and interrupt delivery.

PIOS is the reference host implementation, but PicoScript is not PIOS-only. A conforming host on any platform may compile and run PicoScript if it satisfies the runtime and ABI contract defined here.

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

## 2. System contract (normative)

All I/O paths visible to PicoScript-hosted execution are restricted to:

1. kernel-shipped socket data descriptors via FIFO
2. RAM access
3. IPC FIFO
4. storage backend card/pack load

No other I/O classes are valid for conforming hosts.

## 3. Runtime architecture

### 3.1 Roles

- **Kernel (PIOS):** owns IRQ/SW_IRQ routing, sockets, FIFO transport, scheduling, wake/sleep decisions.
- **Host runtime:** drains inbound descriptors, invokes PicoScript entrypoints, enqueues outbound descriptors.
- **PicoScript program:** pure deterministic processing logic with bounded execution budgets.

### 3.2 Wake-drain-sleep lifecycle

1. Kernel signals data/work available (IRQ/SW_IRQ).
2. Worker wakes and drains inbound queue until empty (or budget cap).
3. Each descriptor dispatches into a PicoScript event entrypoint.
4. Program emits response payload bytes/metadata to host buffer.
5. Host enqueues outbound response descriptors.
6. If inbound queue is empty, worker returns to wait state.

## 4. Language model

Current source language is BASIC-like and compiles to PicoScript bytecode.

Implemented core forms include:

- assignment (`LET`, implicit assignment)
- conditionals (`IF ... THEN ... ELSE ... END IF`)
- loops (`FOR/NEXT`, `WHILE/WEND`, `DO/LOOP`)
- data statements (`DATA`, `READ`, `RESTORE`, `DIM`)
- labels and jumps (`GOTO`, `GOSUB`, `RETURN`)
- event handlers (`ON <event>`)

Core builtins include numeric/string ops plus protocol helpers:

- emit family: `EMIT`, `EMIT_U8`, `EMIT_U16`, `EMIT_U32`, `EMIT_STR`, `EMIT_CRLF`
- inspect family: `PEEK`, `PEEK_U16`, `PEEK_U32`, `SLICE`, `BUF_LEN`

## 5. Bytecode VM contract

The VM executes stack-based bytecode with fixed opcodes, including arithmetic, control-flow, builtins, and sleep.

Reference opcode groups:

- stack/load/store: `PUSH_*`, `LOAD`, `STORE`, `LOAD_ARR`, `STORE_ARR`
- ALU/logic: `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `POW`, `NEG`, `NOT`, comparisons
- control flow: `JMP`, `JZ`, `JNZ`, `CALL`, `RET`
- runtime/builtins: `BUILTIN`, `SLEEP`, `HALT`

Event dispatch model:

- host selects event entrypoint (`main` or named event)
- input buffer is exposed to script context (`DATA$` / `_BUFFER` style binding)
- output is collected as emit buffer and returned to host

Recommended strict default budgets per dispatch:

- `maxBlocksPerEvent = 256`
- `maxEmitBytes = 65536`
- `maxSlices = 16`

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

Language exposure:

- `SLEEP` is an execution opcode and maps to host-yield semantics.
- WAIT/FIRE remain host/kernel control-plane actions and should be exposed to script only through constrained builtins if needed by policy.

Permission model (normative):

- `FIRE_SW_IRQ(pid)` is a **request**, not a direct interrupt operation.
- The kernel/host must authorize the caller against policy (ACL/capability/ownership rules) before issuing the SW_IRQ set call.
- If authorization fails, no wake is fired and a permission error is returned.
- Implementations must not allow unprivileged cross-process wake requests.

Recommended host hook primitive surface:

- `Kernel.WaitIRQ([Rmask])` / `Kernel.WaitSWIRQ([Rmask])` for controlled wait operations.
- `Kernel.FireSWIRQ(Rpid)` for permission-gated wake requests.
- `Queue.Dequeue(queueId, Rdest)`, `Queue.Enqueue(queueId, Rsrc)`, `Queue.Depth(queueId, Rdest)`.
- `Random.U32(Rdest)` for host-backed random generation.
- `Memory.ArenaInit/Alloc/Reset/Stats(...)` for arena lifecycle and allocation control.
- `Span.Make/Slice(...)` and `Descriptor.Make/SetFlags/GetPtr/GetLen/GetFlags(...)` for zero-copy pointer/span descriptor flow.
- `Lease.Acquire/Release/Validate/GetSpan/GetTypeHint(...)` for capability/lease-mediated access.
- `Storage.GetSchemaForPack`, `Storage.SetSchemaForPack`, `Storage.AddCard`, `Storage.UpdateCard`, `Storage.DeleteCard`, `Storage.PatchCard`, `Storage.ReadCard`, `Storage.QueryCard` as backend-swappable storage API hooks.
- All host hook calls return explicit status/error codes via registers/flags; no silent fallback.

## 7. Determinism requirements

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

## 8. Security boundaries

- Kernel remains sole owner of network stack and privilege transitions.
- Script runtime is non-privileged and memory-bounded.
- Runtime access to spans/descriptors is lease-gated; lease validity/type hint checks are enforced by host/kernel policy.
- Queue descriptors are validated by host before script exposure.
- Script outputs are treated as untrusted until host validation passes.

## 9. Compilation targets

PicoScript supports multiple execution targets:

1. PicoScript bytecode VM (default, deterministic runtime target)
2. C emission (`toC`) for native toolchain builds (Thumb/AArch64 via host toolchains)
3. C# emission (`toCSharp`) for managed-host integration

Target choice must preserve deterministic contract and queue ABI semantics.

## 10. Conformance levels

- **L0 (Core):** parse/compile/disassemble + VM run/dispatch with deterministic budgets.
- **L1 (Queue host):** inbound queue drain + outbound queue emit integration.
- **L2 (Kernel-coupled):** IRQ/SW_IRQ wake-fire lifecycle integrated with FIFO ownership transfer.

## 11. Open items for v0.2

- fixed descriptor binary schema (header fields, endian, size limits)
- host hook namespace hardening and ABI freeze (`Kernel.*`, `Queue.*`, `Random.*`, `Memory.*`, `Span.*`, `Descriptor.*`, extended `Storage.*`)
- formal memory model for shared RAM windows
- trace/event format for deterministic replay and audit
