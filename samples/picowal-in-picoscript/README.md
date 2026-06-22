# picowal-in-picoscript

A write-ahead-logged pack/card key-value store written **entirely in
PicoScript**. Unlike `picoweb`, this calls **no** `Storage.*` host hooks — it
*implements the store logic itself* (byte-packed record slots, an append-only
WAL, an in-memory index scan, and crash-recovery replay) directly on the raw
`Memory.*` / `Span.*` primitives.

Key model mirrors real PicoWAL: a value is addressed by `(pack:u16, card:u32)`.

## Run

```
python store_demo.py
```

`store.ppy` is self-contained: it defines the engine and runs a self-test:

```
get(1,100)      = hello
get(1,200)      = world
get(2,100)      = other-pack
list(pack=1)    = 2
after delete(1,100) list(pack=1) = 1
get(1,100)      = <none>
after crash list(pack=1) = 0          <- table wiped to simulate a crash
after replay list(pack=1) = 1         <- rebuilt from the WAL alone
get(1,200) post-replay = world        <- value recovered
get(1,100) post-replay = <none>       <- the delete was replayed too
```

The **crash + replay** block is the interesting part: it wipes the slot table
and rebuilds it purely by walking the append-only WAL, proving write-ahead /
recovery semantics — implemented in PicoScript, not the host.

## Durability across reboots (boot-tier simulation)

`store_demo.py` is in-memory only. `boot_demo.py` makes it **durable** by
simulating the immutable boot/NVMe tier with a file the host injects into the VM
arena at startup — like a bootloader reading a boot partition, or Python loading
a module image:

```
python boot_demo.py          # fresh image, 3 simulated reboots -> 1, 2, 3
python boot_demo.py --keep    # keep the image -> continues 4, 5, 6
```

```
--- reboot 1 ---  format: no image -- initializing fresh   records=1
--- reboot 2 ---  mount: existing image found -- replaying WAL   records=2
--- reboot 3 ---  mount: existing image found -- replaying WAL   records=3
picowal.img on disk: 44 bytes  <- the durable WAL (code is NOT in it)
```

Each reboot builds a **fresh VM**; only `picowal.img` carries state between them
(and across separate processes), so the growing record count proves the WAL
survived. Two tiers, kept separate to avoid the chicken-and-egg:

- **CODE** = the engine's bytecode, compiled and loaded by the host — the
  immutable boot image. *Never stored in the WAL.*
- **DATA** = an append-only WAL persisted to `picowal.img`, injected into the
  arena before `run()` and snapshotted back after.

Only the WAL is durable; the slot table is a **volatile projection** rebuilt by
replaying the log on boot — real write-ahead-log semantics. The host injection
stands in for the missing `Block.*`/NVMe binding: swap it for a real device and
nothing in `boot_store.ppy` changes.

## Gaps surfaced (see ../FINDINGS.md)

- **No durable persistence primitive** — the headline finding. Everything lives
  in volatile arena memory; there is no pure-PicoScript way to write a byte to a
  device. A real store needs a host **block-device binding** (`Block.Read/
  Write/Flush` over NVMe/PCIe, or SD/flash). The store *logic* here is already
  written against that future contract — swap the `Memory.*` backing for
  `Block.*` and it becomes durable.
- **No map/dict** → the index is an O(n) linear slot scan; a hash/B-tree index
  must be hand-built in raw memory.
- **No bitwise operators** → multi-byte integers are packed/unpacked with
  `*256 / /256 / %256` arithmetic instead of shifts/masks.
- **`Span.Make` truncates its pointer to 16 bits** → the store must live in the
  low 64 KB (shared with the span bump-allocator), so `VALUE_MAX` is capped at
  64 here even though real PicoWAL allows 508. The *logic* is identical; only
  the window is cramped.
- **`label` is a reserved word** → a helper parameter had to be renamed `tag`.
- **Argument/return clobber bug** (composing `Number.ToString(list_count(1))`
  inside another call) — **fixed** in this branch; before the fix the report
  labels silently vanished.

## Hardware bindings this needs to become real

A self-hosting database server is gated on driver bindings that don't exist yet:
**UART** (console/log), **SPI/I²C** (SD/flash/sensors), **PCIe** (bus
enumeration), and **M.2/NVMe block storage** (the actual durable target the WAL
must fsync to). See the hardware-driver section of `../FINDINGS.md`.
