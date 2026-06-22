#!/usr/bin/env python3
"""store_demo.py -- run the PicoScript WAL store (store.ppy) on the bytecode VM.

store.ppy is self-contained: it defines the engine (byte-packed slots, an
append-only WAL, crash-recovery replay) and then runs a self-test that exercises
put / get / list / delete and a simulated crash + WAL replay. This harness just
compiles it, runs it warm, prints the report, and times a put+get cycle.

    python store_demo.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from picoscript_python import compile_python          # noqa: E402
from picoscript_il import lower_to_bytecode_safe       # noqa: E402
from picoscript_vm import PicoVM, HostApi              # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "store.ppy")


def main():
    words = lower_to_bytecode_safe(compile_python(open(SRC, encoding="utf-8").read()))
    print(f"compiled store.ppy -> {len(words)} bytecode words\n")

    host = HostApi(); host.caps = 0xFFFFFFFF
    vm = PicoVM(host=host); vm.load(words)
    vm.run()
    print(vm.output_text())

    # crude timing: a full store.ppy self-test run (3 puts + gets + delete + replay)
    iters = 3000
    t0 = time.perf_counter()
    for _ in range(iters):
        vm.reset_for_request()
        vm.run()
    dt = (time.perf_counter() - t0) / iters * 1e6
    print(f"\nVM full self-test cycle: {dt:.1f} us  "
          f"(put*3 + get*5 + delete + crash + WAL replay), warm VM")
    print("(native lower_to_c removes the interpreter; the real persistence")
    print(" target -- an M.2/NVMe block device -- is a missing binding; see README.)")


if __name__ == "__main__":
    main()
