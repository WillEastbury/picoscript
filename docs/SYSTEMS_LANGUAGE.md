# PicoScript as a systems language — self-hosting & compiling PIOS on itself

Can PicoScript become powerful enough to **write the PIOS kernel itself**, so the
hand-rolled assembly shrinks to almost nothing? Short answer: **mostly yes, with one
real language decision (64-bit pointers) and a small irreducible C/asm nucleus.**
This is the grounded inventory + staged plan.

Two self-hosting axes — keep them separate:

1. **Self-hosting the compiler** — PicoScript compiling PicoScript (Stages 0–4 in
   `SELF_HOSTING.md`; Stage 0 emit + Stage 1 assembler are done).
2. **Self-hosting the kernel** — writing PIOS *itself* in PicoScript. **This doc.**

## The bridge that makes it plausible: `toC`

PicoScript already lowers to **portable C** (`lower_to_c` → `zig cc` →
Thumb/AArch64) — see `COMPILER_ARCHITECTURE.md` / `INTERNALS.md`. So
"kernel code in PicoScript" is really **PicoScript → C → native object**, linked
into the kernel. The bytecode VM is for the *simulator*; the kernel target is the C
backend. That means the question isn't "can the 16-op VM run a kernel" — it's
"can the language *express* kernel logic such that the emitted C is real kernel
code." Most of the gap is **primitives**, and primitives are cheap to add (host
hooks with native `toC` lowering — no ISA or frontend change, byte-identical
preserved).

## What PIOS already is (Compute / IO / State)

| Plane | PIOS has | PicoScript expresses it with |
|-------|----------|------------------------------|
| **Compute** | preemptive scheduling, capsules/threads | jump-table **`dispatch`** (IRQ / syscall / driver / scheduler-state dispatch), control flow |
| **IO** | kernel-owned TCP/UDP/TLS/FIFO **descriptors** | the binding + **lease/`pooldesc`** model (`PIOS_IO_BINDING.md`, `include/pios_io_binding.h`) |
| **State** | Picowal cards, indexes, queries | the `Storage.*` card store + query language (a real data plane, not a demo) |

These are the three legs of a real platform — and each already has a PicoScript
surface. That's why this is more than "hello world with interrupts."

## Primitive inventory

**Have today**
- Integer arithmetic, comparisons, full control flow, **jump-table dispatch**
  (ideal for interrupt vectors / syscall tables / driver state machines).
- `Memory.*` (byte memory), `Span.*` (zero-copy views), arena allocation,
  `Descriptor.*` / `Lease.*` hooks, the host-hook ABI (extensible at will).
- The **`toC` backend** (the native bridge) and cross-target parity testing.
- The card store (State) and the descriptor/FIFO IO model (IO).

**Landing now**
- **Bitwise + shift** (`Bits.And/Or/Xor/Not/Shl/Shr/Sar`) — register/flag
  manipulation, bit-packing, MMIO field extract/insert. Native in `toC`. (The
  self-hosted assembler's `*256 / %256` workaround becomes real `<<`/`>>`.)

**The dragons (what's still missing, and how to close each)**

1. **64-bit / pointers — the one structural decision.** PicoScript is signed-32;
   AArch64 pointers and many registers are 64-bit (MMIO at `0xFC……`, page tables,
   DMA addresses). Options:
   - **(a) Handle model (recommended near-term):** the kernel C/nucleus owns the
     real 64-bit pointers; PicoScript manipulates *opaque handles + 32-bit offsets*
     and does the field/bit logic. No language change.
   - **(b) Widen to 64-bit** (long-term, if PicoScript becomes the primary kernel
     language): a larger ISA/VM change.
   - **(c) hi/lo split** (stopgap).
2. **Volatile MMIO load/store.** Add `Mmio.R32/W32/R64/W64(handle, offset[, val])`
   host hooks → native `*(volatile uintN_t*)(base+off)` in `toC`, simulated
   (sparse map) in the VM. Pairs with the handle model (#1).
3. **Typed memory / structs.** Kernel structs (`pooldesc`, `ctx_desc`, register
   blocks). Model as **byte-offset field accessors** over a span/`Memory`
   (`Field.GetU32(span, off)` / `SetU32`) — the structs already exist in
   `include/pios_io_binding.h`, so PicoScript reads/writes their fields by offset.
4. **Irreducible asm/intrinsics.** Barriers, `MSR`/`MRS`, cache maintenance,
   exception-vector install, MMU enable, atomic CAS, context-switch register
   save/restore, boot. These stay in a **small C/asm nucleus** — and that's normal:
   even Rust and C kernels keep an asm trap/boot layer.

## The realistic architecture

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  PicoScript → C  kernel BODY  (the bulk)                            │
  │   scheduler policy · FIFO/IPC · descriptor IO · capsule lifecycle   │
  │   driver state machines (dispatch) · Picowal data plane · HTTP/TLS  │
  │   orchestration · syscall/IRQ dispatch tables                       │
  └───────────────▲───────────────────────────────▲────────────────────┘
                  │ host hooks (Mmio/Bits/Field/Lease/Descriptor)         │
  ┌───────────────┴───────────────────────────────┴────────────────────┐
  │  C / asm NUCLEUS  (irreducible, ~hundreds of lines)                  │
  │   boot · exception vectors · MMU · barriers · atomics · ctx switch  │
  │   raw 64-bit MMIO · the host-hook runtime that backs the above       │
  └─────────────────────────────────────────────────────────────────────┘
```

The nucleus is small and stable; everything above it is PicoScript→C — which buys:
**determinism**, the **verified-compilation** property (every kernel function is
machine-checked to compile, and the bytecode rendering runs in the simulator),
jump-table dispatch for the hot paths, and even *readable* kernel logic (the
English dialect for policy code). The asm residue is a few hundred lines, not the
kernel.

## Staged plan

| Stage | Deliverable | Unlocks |
|------:|-------------|---------|
| **S0** | `Bits.*` bitwise/shift (in progress) | register/flag/bit-packing logic |
| **S1** | `Mmio.*` + pointer-handle model (native volatile in `toC`) | device-register access |
| **S2** | `Field.*` struct-offset accessors over spans | read/write `pooldesc`/`ctx_desc` fields from PicoScript |
| **S3** | **first PicoScript driver** (e.g. UART/GPIO) as a `dispatch` state machine → `toC` → linked into the nucleus, running on Pi5 | proof: real kernel code in PicoScript |
| **S4** | port a subsystem — the FIFO/descriptor IO path or the scheduler policy | a kernel *plane* in PicoScript |
| **S5** | stabilise the nucleus; measure the asm/C residue | the "PIOS body builds from PicoScript" milestone |
| ∥ | compiler self-host Stages 2–4 (`SELF_HOSTING.md`) | eventually the PicoScript→C step runs *on* PIOS |

"Compile PIOS's kernel **on itself**" (the compiler running on PIOS, compiling the
kernel) is S5 + compiler-Stage-4 territory. But the **cross-compiled** version —
kernel body authored in PicoScript, lowered to C, built with the host toolchain —
is reachable as soon as S1–S3 land.

## Verdict

Yes — the *kernel body* can become PicoScript→C after the systems primitives
(S0–S2) land, with a small irreducible C/asm nucleus for boot/traps/MMU/atomics.
The single real language decision is **64-bit pointers** (handle model now, widen
later). Hand-rolled asm doesn't disappear, but it shrinks from "the kernel" to "the
nucleus" — and everything with a data plane (scheduling, IPC, descriptor IO,
Picowal, HTTP/TLS orchestration) moves up into deterministic, verified PicoScript.
