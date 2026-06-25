#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maths.* pure-integer ops (Power, Sqrt): Python VM == JS VM, byte-exact.

Power uses a 32-bit modular multiply (Math.imul in JS) so it matches Python's
& MASK32 exactly; Sqrt is an integer floor sqrt (bit method) identical across VMs.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

PROG = """
int r = 0;
r = Maths.Power(2, 10); r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);   // 1024
r = Maths.Power(3, 4);  r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);   // 81
r = Maths.Power(5, 0);  r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);   // 1
r = Maths.Sqrt(144);    r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);   // 12
r = Maths.Sqrt(1000);   r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);   // 31
r = Maths.Sqrt(0);      r = Number.ToString(r); Io.Write(r);                     // 0
"""

EXPECTED = b"1024 81 1 12 31 0"


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def main():
    words = lower_to_bytecode_safe(compile_c(PROG))
    py = b"".join(PicoVM().run(words).output)
    js = run_js_vm(words)
    assert py == EXPECTED, f"Python Maths {py!r} != {EXPECTED!r}"
    assert js == EXPECTED, f"JS Maths {js!r} != {EXPECTED!r}"
    print("PASS Maths.*: Python VM == JS VM byte-exact (Power, integer Sqrt)")



def test_main():
    main()

if __name__ == "__main__":
    main()
