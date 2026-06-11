# PIOS host-binding contracts — work for the PIOS build agent

**Audience:** the agent that builds the PIOS kernel / EL1 runtime. The PicoScript VMs
(`picoscript_vm.py`, `vm/picovm.c`, `vm/picovm.js`) are pure and deterministic; the
bindings below are **genuinely host-injected** and cannot be implemented in the VM. This
spec defines their contracts so the kernel can provide them without breaking determinism,
parity, or the invariants.

Guiding rules (from `docs/INVARIANTS.md` and the binding invariants):
- **"Bindings are not ambient"** (INV-17): every hook here is gated by a capability class;
  a capsule without the grant faults `PV_FAULT_CAPABILITY`=8 *before* dispatch (already
  enforced by the VM classifier — the kernel only supplies the implementation).
- **Hooks are the only outside world** (INV-3) and **every hook has a typed contract**
  (INV-4) and **typed failures** (INV-18, via the `Status.Last` channel).
- **Async message ABI**: a binding call is a post to the kernel IPC mailbox/FIFO with an
  optional sleep on the return FIFO — no syscalls / privilege transitions in the worker.
- **Deterministic mode** (INV-15): when the capsule runs with an injected seed / frozen
  clock, these bindings must be replaced by the deterministic providers (seeded RNG,
  fixed clock, recorded IO) so replay is byte-identical.
- Every shared object a binding hands back **must declare OWNER, CACHEABILITY, LIFETIME,
  SYNCHRONIZATION MODEL** (see the per-binding tables).

The VM ships **deterministic stubs** for these today (so tests and replay work); the kernel
provides the real, capability-gated implementations.

---

## 1. Time — `DateTime.*` (capability: `TIME` = 1<<4)

| Hook (example) | in → out | contract |
|----------------|----------|----------|
| `DateTime.NowUnix()` | () → int (seconds) | monotone within a request; **frozen** in deterministic mode |
| `DateTime.NowMillis()` | () → int (ms, Q… raw int) | same source as NowUnix |
| `DateTime.Format(spanFmt, tsInt)` | (span, int) → span | pure given inputs; no clock read |

- OWNER: kernel clock service. CACHEABILITY: value is a snapshot copy (not a live page).
  LIFETIME: the returned int is owned by the caller (immutable). SYNC: read-only snapshot,
  no shared mutable state.
- Failure: if the clock is unavailable, set `Status.Last = 1` and return 0 (no trap).
- Determinism: in seeded/replay mode the kernel returns the recorded/frozen value.

## 2. Randomness — `Maths.Random`/`Maths.RandomRange`, `Crypto.RandomBytes` (capability: `RANDOM` = 1<<2)

| Hook | in → out | contract |
|------|----------|----------|
| `Maths.Random()` | () → int (Q16.16 in [0,1)) | from the capsule's RNG stream |
| `Maths.RandomRange(lo, hi)` | (int, int) → int | uniform in [lo,hi]; lo,hi are register ints |
| `Crypto.RandomBytes(nInt, dstSpan)` | (int, span) → span | fills a leased span with CSPRNG bytes |

- OWNER: the capsule's RNG stream (per-capsule, seeded at spawn). CACHEABILITY: N/A (values
  copied out). LIFETIME: returned span is caller-owned (arena). SYNC: the RNG state is
  per-capsule, single-owner — never shared across cores.
- Determinism (INV-15): the seed is injectable (`PICOVM_SEED` mirrors this); replay must
  reproduce the exact stream. The CSPRNG used for `Crypto.RandomBytes` MUST also be seedable
  in deterministic mode (a deterministic DRBG) so traces replay.
- **Security**: `Crypto.RandomBytes` must be a CSPRNG in production (not the Maths PRNG).

## 3. Files / persistent storage — `Storage.*` beyond the in-VM card store (capability: `STORAGE` = 1<<3)

The VM has an in-memory card store for tests. Real persistence is the kernel's.

| Hook (example) | in → out | contract |
|----------------|----------|----------|
| `Storage.AddCard(packSpan, dataSpan)` | (span, span) → int handle | persist; returns a card id |
| `Storage.GetCard(idInt)` | (int) → span (leased) | read; span is a **lease** (INV-4) |
| `Storage.Query(...)` | … | bounded result set |

- OWNER: kernel storage service holds the backing pages; the worker gets a **validated
  lease** (`pooldesc`), never a raw pointer. CACHEABILITY: must match the kernel's mapping
  attributes for that page — *if the kernel maps it non-cacheable, the worker mapping is
  non-cacheable too* (no conflicting attributes, ever). LIFETIME: lease is scope-bound —
  auto-released at handler scope exit or on kernel revoke (INV-8). SYNC: copy-in/copy-out
  or single-writer lease; no shared mutable file buffer across capsules.
- Failure: missing card / revoked lease → `Status.Last` typed code, not a magic value.

## 4. Sockets / network — `Net.*` and the `Req`/`Resp` binding (capability: `NET` = 1<<5)

Inbound/outbound bytes are the kernel's message-boundary authority (see
`docs/PIOS_IO_BINDING.md`, I1). `Net.*` for client sockets:

| Hook (example) | in → out | contract |
|----------------|----------|----------|
| `Net.Connect(hostSpan, portInt)` | (span, int) → int conn-id | async: posts CONNECT, sleeps on return FIFO |
| `Net.Send(connInt, dataSpan)` | (int, span) → int | enqueues a body descriptor; may flush |
| `Net.Recv(connInt, maxInt)` | (int, int) → span (leased) | pulls a pooldesc; blocks via FIFO |
| `Net.Close(connInt)` | (int) → () | releases the connection descriptor |

- OWNER: kernel network stack owns sockets + buffers; the worker holds a connection
  **descriptor** with linear ownership (INV-13 — one owner at a time, moves via FIFO).
  CACHEABILITY: leased payload spans mirror the kernel's DMA buffer attributes exactly.
  LIFETIME: connection descriptor released on `Close` or scope exit; **poison + generation
  bump on release** (any later use faults). SYNC: all socket state changes are **messages**
  to the kernel (no direct poking of kernel socket fields); barriers are part of the FIFO
  ABI (INV-17 of the binding spec).
- No request smuggling: the worker gets a length-bounded body it physically cannot read
  past (I1). Reorder/seal/phase rules are the kernel's (see `PIOS_IO_BINDING.md`).

---

## Cross-cutting requirements

1. **Capability gating is already in the VM** — the kernel must honour the same class bits
   (`PV_CAP_*` in `vm/picovm.h`, mirrored in Python/JS) so a denied binding faults
   identically on every path. Adding a new binding ⇒ add its class to the classifier in all
   three VMs (and bump nothing else).
2. **Typed failures** — every fallible binding sets the `Status.Last` channel (0=OK and a
   typed non-zero on failure); never return a magic value the script can't distinguish.
3. **No hidden allocation in hot bindings** (INV-5) — request-path bindings declare arena
   use or are forbidden; honour `no_alloc` mode.
4. **Deterministic providers** — in seeded/replay mode every binding here is swapped for a
   deterministic implementation so a recorded trace replays byte-for-byte.
5. **Structured traps** — a fault in a binding carries `code/pc/detail` (INV-25) and the
   kernel adds `capsule_id`/`binding_id` (see `docs/INV25_PIOS_TRACE.md`).

Until these land, the namespaces above remain VM-side **deterministic stubs**; production
behaviour is the kernel's to provide under the contracts here.
