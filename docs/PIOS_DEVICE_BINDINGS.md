# PIOS device & streaming bindings — architecture spec

**Status:** design (language + compiler surface defined here; OS implementation deferred to
the PIOS build agent). No VM/driver code is changed by this document.

**Layering principle (the contract this doc encodes):**
- **PicoScript owns** the *language features* (the `Device`/`Mmio`/`Stream` namespaces),
  the *compiler components* (hook lowering, capability gating, binding lifecycle,
  source-span/INV-25), and the *declared hook surface* OS code plugs into.
- **PIOS (EL1) owns** the *security surface* (capability enforcement, register-window
  whitelisting, lease validation/revoke, DMA-buffer ownership) and the *actual drivers*.

This is the same split as `docs/PIOS_HOST_BINDINGS.md` and `docs/INV25_PIOS_TRACE.md`:
PicoScript stays pure, deterministic and 5-path byte-identical; the device is an injected,
capability-gated binding whose real behaviour lives in the kernel.

---

## 1. The unifying model: a device is a binding over `pooldesc` + lease + FIFO

A streaming hardware device is **structurally identical to the HTTP `Req`/`Resp` binding**
(`docs/PIOS_IO_BINDING.md`): the same `pooldesc` descriptors, the same validated leases,
the same async FIFO ABI, the same I1–I8 invariants — only the *producer/consumer* is
hardware (a DMA ring, a register block) instead of a TCP socket. The building blocks
already exist in the language:

| Need | Existing PicoScript surface |
|------|-----------------------------|
| Zero-copy DMA buffer handle | `Descriptor.*` (0x50–0x55) = `pooldesc` |
| Validated / revocable / lifetime-bound access | `Lease.*` (0x58–0x5D) — I4/I8 |
| "Buffer ready" / "transfer complete" event | `Kernel.WaitIRQ` (+ the RP1-ETH IACK-gated drain pattern) |
| Async producer/consumer, backpressure | `Queue.*`, `Thread.Wait/Raise`, FIFO `LEASE_REVOKE` |
| Streaming lifecycle | binding kinds `stream` / `duplex` (`PIOS_IO_BINDING.md` §5) |
| Register field extract/insert | `Bits.*` (And/Or/Xor/Shl/Shr/Sar) |

So the new surface is small: an **enumeration/open** layer (`Device.*`), a **streaming
ring** layer (`Stream.*`, thin sugar over `Lease`+`Descriptor`+`WaitIRQ`), and a
**register-window** layer (`Mmio.*`, genuinely new).

---

## 2. Two device classes (one coherent spec, two APIs)

### 2a. Streaming / DMA-ring devices — `Device.*` + `Stream.*`
High-bandwidth producer/consumer ring of DMA buffers: **camera frames, HDMI scanout,
PCIe/NVMe blocks, SDIO/QSPI blocks, Ethernet RX/TX.** Maps almost verbatim onto
`Descriptor` + `Lease` + a FIFO of ready-events. Zero-copy: the capsule leases a buffer
the DMA engine filled, reads it through the lease span, releases it back to the ring.

### 2b. Register / MMIO control devices — `Mmio.*`
Low-bandwidth, synchronous, latency-sensitive register pokes: **GPIO, I²C, SPI config,
PWM, clock/reset.** Needs a new model: a **kernel-validated register window** (a lease
over a *whitelisted* MMIO range) plus typed `Peek`/`Poke`; `Bits.*` does the field work.
This is the security-sensitive class — see §6.

Both classes flow through the same `pooldesc`/lease/FIFO substrate and the same I1–I8
invariants; only the lifecycle contract differs.

---

## 3. Language surface (proposed)

All host hooks are **2-in/1-out** (`rd`, `rs1`, `rs2`) — a hard ABI constraint. Ops that
need >2 inputs (open with mode+config, poke with window+offset+value) take a **config
descriptor span** built by the program and pass its handle, exactly as AES packs
`IV||payload` into one span. This keeps the generic `Ns.Method` lowering unchanged (no
frontend edits).

### `Device.*` — enumeration & lifecycle
```
Device.Open(idSpan, cfgDesc) -> devHandle      // idSpan = "gpio0"/"csi0"/"eth0"/"nvme0"; cfgDesc packs mode/flags
Device.Caps(devHandle)       -> capsBitsInt    // class bits the device exposes (stream/mmio/duplex)
Device.Close(devHandle)      -> ()             // releases all leases + the device binding
Device.Status(devHandle)     -> statusInt      // typed status (errno-style; see Status.Last, INV-18)
```

### `Stream.*` — DMA-ring streaming (sugar over Lease+Descriptor+WaitIRQ)
```
Stream.Open(devHandle, ringCfgDesc) -> streamHandle   // ringCfgDesc: depth, buf size, direction, policy(block|drop-oldest)
Stream.Next(streamHandle)           -> leaseHandle     // block (yield CPU) until a filled/free buffer; 0 + Status on EOF/timeout
Stream.Span(leaseHandle)            -> span            // zero-copy view of the buffer (read for RX, write for TX)
Stream.Submit(streamHandle, leaseHandle) -> ()         // TX: hand a filled buffer to the device
Stream.Release(leaseHandle)         -> ()              // RX: return a consumed buffer to the ring (I8)
Stream.Close(streamHandle)          -> ()
```
`Stream.Next` is the canonical **"IRQ → drain ring → re-arm"** loop (the RP1-ETH pattern):
it posts a pull to the kernel and sleeps on the return FIFO, yielding the CPU — **no
syscall/transition** (the async message-ABI rule). Backpressure is the existing
`LEASE_REVOKE`: under pressure the kernel reclaims the oldest lease; `ringCfgDesc.policy`
chooses block-vs-drop.

### `Mmio.*` — validated register window
```
Mmio.Open(devHandle, windowCfgDesc) -> windowHandle    // windowCfgDesc: which named window + RO/RW; kernel validates against whitelist
Mmio.Peek(windowHandle, offsetInt)  -> wordInt          // read reg at window+offset (offset bounds-checked)
Mmio.Poke(pokeDesc)                 -> ()               // pokeDesc packs (windowHandle, offset, value) -- 3 inputs -> descriptor
Mmio.Barrier(windowHandle)          -> ()               // memory/ordering barrier (DSB/DMB equivalent)
Mmio.Close(windowHandle)            -> ()
```
A window is a **lease over a kernel-whitelisted physical range**; `Peek`/`Poke` are
offset-bounded *into that window only*. There are no raw pointers (I4). `Bits.*` composes
field extract/insert on the read-modify-write.

---

## 4. Hook codes & capability classes (proposed reservations)

- **Hook range:** `0x130–0x16F` is free (current table tops out at Auth `0x129`; the byte
  range is dense). Suballocate e.g. `Device 0x130–0x137`, `Stream 0x138–0x147`,
  `Mmio 0x148–0x14F`, with `0x150–0x16F` reserved for growth. *Confirm against the live
  table + `EXT_HOST_HOOK_BASE` at reservation time; adding hooks bumps
  `PV_HOOK_TABLE_VERSION` (INV-23), which the module check adapts to automatically.*
- **Capability classes** (continue from `CAP_CRYPTO = 1<<9`; security-first ⇒ fine-grained):
  - `CAP_DEVICE = 1<<10` — enumerate/open any device (coarse gate).
  - `CAP_DMA    = 1<<11` — `Stream.*` (DMA-ring buffers).
  - `CAP_MMIO   = 1<<12` — `Mmio.*` (register windows — the dangerous one).
  - Per-bus refinement (`CAP_GPIO`, `CAP_PCIE`, …) is left to a **per-device allow-list in
    the open path** rather than burning a capability bit each — the kernel checks the
    capsule's grant table for `idSpan` at `Device.Open`. This keeps the bitmask small while
    still gating per device instance (the finer-grained security control).
  - `CAP_ALL` widens accordingly (e.g. `0x3FF -> 0x1FFF`); default grant stays "all" so
    existing programs are unaffected, and the harness/`PICOVM_CAPS` restricts to gate.

---

## 5. Compiler components (PicoScript side — what we build when implementing)

1. **Namespaces + hook codes** in `picoscript_lang.py` (`HOST_HOOK_CODES` + the namespace
   tables). No new opcodes — everything lowers through the existing generic `Ns.Method`
   host-call path, so **the four frontends and `vm/picoc.js` need no parser/lowerer
   changes** (only the hook table regen via `gen_hooks_js.py`).
2. **Capability classifier** entries in all three VMs (`hook_cap`/`pv_hook_cap`/`hookCap`)
   — identical bit values, so a denied device binding faults `PV_FAULT_CAPABILITY`=8
   byte-identically on every path (INV-17).
3. **Config-descriptor helpers**: a small in-language convention (or `Descriptor.Make` +
   `Memory.Set`) to pack open/poke args into a span, since hooks are 2-in/1-out.
4. **Typed failures** via the existing `Status.Last` channel (INV-18): `Device.Open`
   failure, lease timeout, bad MMIO offset, etc. set a typed code; the primary return is a
   null handle / 0.
5. **Source-span / structured traps** come for free (INV-25) — a fault in a device hook
   already carries `code/pc/detail`; PIOS adds `capsule_id`/`binding_id`.

---

## 6. Security surface (OS contract — what PIOS implements underneath)

Security is the first priority; this is where it bites. PIOS MUST:

1. **Capability check before dispatch** (INV-17) — honour the `CAP_DEVICE/DMA/MMIO` bits
   *and* the per-`idSpan` allow-list in the capsule's grant table. Hook existence is not
   permission.
2. **MMIO window whitelisting** — `Mmio.Open` resolves a *named* window to a physical
   range only if that capsule is granted it; `Peek/Poke` are bounds-checked into the
   window. **Never a raw address from the script.** A poke outside the window faults. This
   is the control that prevents isolation breach / SoC brick.
3. **DMA-buffer ownership** (I2/I3, the iso-lease model, `PIOS_IO_BINDING.md` D6) — a ring
   buffer has exactly one owner at a time; ownership *moves* capsule↔device at
   `Stream.Next`/`Submit`/`Release`. Use-after-release faults (poison + generation bump,
   like the response descriptors). The compile-time iso-lease (INV-7) extends to
   `Stream.*` handles.
4. **Validated leases + eventual release** (I4/I8) — every `Stream.Span` read goes through
   a validated lease; `LEASE_REVOKE` reclaims under pressure; scope exit auto-releases.
5. **Deterministic providers** (INV-15) — in seeded/replay mode every device hook is a
   deterministic provider (recorded camera frames, a fake register window, a canned block
   stream) so a recorded trace replays byte-for-byte. **This is what lets a device-using
   capsule still run on the Python/JS VMs and in tests.**
6. **No hidden allocation in hot device hooks** (INV-5) — `Stream.Next`/`Span`/`Release`
   are arena-free or declare arena use; honour `no_alloc`.
7. **DoS/quotas** — ring depth, MMIO poke rate, and per-capsule device count are bounded
   (a streaming capsule cannot starve the kernel).

---

## 7. Determinism & 5-path parity (the core tension, resolved)

Hardware is nondeterministic and non-portable; PicoScript is 5-path byte-identical +
deterministically replayable. The resolution is the existing binding doctrine
(INV-3, "host hooks are the only outside world"; the killer rule "bindings are not
ambient"):

- The capsule **logic stays pure and portable** — it manipulates handles/spans, never
  hardware directly.
- Device hooks exist on **all five paths**, so the **bytecode is byte-identical** and the
  parity gate (INV-24) still covers every program.
- **Observable behaviour** of a device hook is allowed to differ between the PIOS driver
  and the Python/JS deterministic provider — that divergence lives *only inside the
  binding*, which is exactly what "bindings are not ambient/portable" permits.
- Tests + replay use the deterministic provider; production uses the driver. Same bytecode,
  same hooks, same capability gates.

---

## 8. Invariant mapping

| Invariant | How device bindings satisfy it |
|-----------|--------------------------------|
| INV-3 outside world only via hooks | all hardware access is `Device/Stream/Mmio` hooks |
| INV-4 every hook has a contract | signatures + ownership/lifetime declared here |
| INV-5 no hidden alloc in hot hooks | `Stream.Next/Span/Release` arena-free / declared |
| INV-7 seal consumes ownership | `Stream`/`Mmio` handles use the iso-lease (move) model |
| INV-15 deterministic mode | deterministic providers for replay/test |
| INV-17 capability before dispatch | `CAP_DEVICE/DMA/MMIO` + per-`idSpan` allow-list |
| INV-18 typed failures | `Status.Last` for open/lease/offset errors |
| INV-24 parity gate | hooks present on all 5 paths; deterministic providers tested |
| INV-25 structured traps | `code/pc/detail` + PIOS `capsule_id/binding_id` |
| I1–I8 (binding) | reuse `pooldesc`+lease+FIFO; one-owner, validated, eventual release |

No invariant is weakened; the only *new* surface is the MMIO window, governed by §6.2.

---

## 9. Worked bring-up examples (mapping to existing PIOS drivers)

- **RP1 Ethernet RX (stream)** — `Device.Open("eth0")` → `Stream.Open(dir=RX, depth=…,
  policy=drop-oldest)` → loop `l = Stream.Next; s = Stream.Span(l); …consume…;
  Stream.Release(l)`. This *is* the RP1 "MIP-edge IRQ → drain RX ring → IACK re-arm"
  flow, wrapped: `Stream.Next` hides the `WaitIRQ` + drain + re-arm.
- **SD2 / QSPI block device (stream, bidir)** — `Device.Open("sd2"/"qspi0")` →
  `Stream.Open(dir=RW)`; read = `Next`/`Span`/`Release`, write = lease a free buffer,
  `Stream.Span` (write), `Stream.Submit`. (SD2 wiring/clock and QSPI pinout are kernel
  facts; the capsule sees only the ring.)
- **GPIO/I²C control (mmio)** — `Device.Open("gpio0")` → `Mmio.Open(window="gpio", RW)` →
  `Mmio.Poke(pack(w, SET_OFFSET, mask))`, `v = Mmio.Peek(w, LEVEL_OFFSET)`; `Bits.*` builds
  the field masks.
- **HDMI scanout (stream, TX)** — `Stream.Open(dir=TX)`; the capsule fills framebuffer
  leases and `Submit`s them to the scanout ring.

---

## 10. Open decisions for PIOS / future implementation

1. **Capability granularity** — confirm coarse `CAP_DEVICE/DMA/MMIO` + per-`idSpan`
   allow-list (this spec's recommendation) vs a bit per bus. (Security-first: the
   per-`idSpan` allow-list gives instance-level gating without bit exhaustion.)
2. **Window naming** — the registry of named MMIO windows per SoC (Pi5 RP1, Pico2 RP2350)
   and who owns the whitelist (board config vs kernel build).
3. **Backpressure policy default** — block vs drop-oldest per device class.
4. **Hook range confirmation** — `0x130–0x16F` vs the `EXT_HOST_HOOK_BASE` block.

## 11. Next implementation step (deferred until requested)

When green-lit, the PicoScript-side work is small and parity-safe:
1. Reserve the namespaces + hook codes in `picoscript_lang.py`; regen `vm/pico_hooks.*`.
2. Add the capability bits + classifier entries (3 VMs) and widen `CAP_ALL`.
3. Implement **deterministic providers** in the three VMs (recorded ring / fake window) so
   the hooks run + replay byte-identically; add a `tests/test_device.py` parity test.
4. Hand the driver + security surface to the PIOS build agent against §6.

No driver, security enforcement, or VM device behaviour is built until then; this document
is the contract both sides implement against.
