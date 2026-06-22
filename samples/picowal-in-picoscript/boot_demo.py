#!/usr/bin/env python3
"""boot_demo.py -- durable PicoWAL across simulated reboots.

The store engine (boot_store.ppy) lives in CODE (compiled to bytecode, loaded by
the host -- the immutable boot image). The DATA lives in an append-only WAL that
the host injects into the VM arena at startup from picowal.img and snapshots back
after -- standing in for the missing Block.*/NVMe binding (see ../FINDINGS.md).

Each "reboot" builds a FRESH VM; only picowal.img carries state between them. The
record count therefore proves the WAL survived: 1, 2, 3, ...

    python boot_demo.py           # fresh image, simulate 3 reboots -> 1,2,3
    python boot_demo.py --keep    # keep existing image -> continues 4,5,6,...
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from picoscript_python import compile_python          # noqa: E402
from picoscript_il import lower_to_bytecode_safe       # noqa: E402
from picoscript_vm import PicoVM, HostApi              # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "boot_store.ppy")
IMG = os.path.join(os.path.dirname(__file__), "picowal.img")

# must match the addresses in boot_store.ppy
SUPER = 0xB000
WAL_BASE = 0xD000


def boot_once(words):
    """One reboot: inject image -> run engine -> snapshot image. Fresh VM."""
    host = HostApi(); host.caps = 0xFFFFFFFF
    vm = PicoVM(host=host); vm.load(words)

    # --- inject the durable image into the arena BEFORE execution ---
    if os.path.exists(IMG):
        data = open(IMG, "rb").read()
        sup, wal = data[:8], data[8:]
        vm.mem[SUPER:SUPER + len(sup)] = sup
        vm.mem[WAL_BASE:WAL_BASE + len(wal)] = wal

    vm.run()
    out = vm.output_text()

    # --- snapshot superblock + WAL back to disk AFTER execution ---
    wal_top = int.from_bytes(bytes(vm.mem[SUPER + 1:SUPER + 5]), "big")
    sup = bytes(vm.mem[SUPER:SUPER + 8])
    wal = bytes(vm.mem[WAL_BASE:wal_top]) if wal_top > WAL_BASE else b""
    open(IMG, "wb").write(sup + wal)
    return out


def main():
    keep = "--keep" in sys.argv
    if not keep and os.path.exists(IMG):
        os.remove(IMG)

    words = lower_to_bytecode_safe(compile_python(open(SRC, encoding="utf-8").read()))
    print(f"compiled boot_store.ppy -> {len(words)} bytecode words")
    print(f"image: {IMG} ({'exists' if os.path.exists(IMG) else 'absent -- will format'})\n")

    for boot in range(1, 4):
        print(f"--- reboot {boot} (fresh VM; only picowal.img carries state) ---")
        print(boot_once(words).rstrip())
        print()

    size = os.path.getsize(IMG)
    print(f"picowal.img on disk: {size} bytes  <- the durable WAL (code is NOT in it)")
    print("re-run with --keep to continue 4,5,6 across separate processes.")


if __name__ == "__main__":
    main()
