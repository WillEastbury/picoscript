#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Number.* integer/format library: Python VM == JS VM, byte-exact."""

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
r = Number.Abs(0 - 7); Io.WriteByte(r);     // 7
r = Number.Min(5, 3);  Io.WriteByte(r);     // 3
r = Number.Max(5, 3);  Io.WriteByte(r);     // 5
r = Number.ToString(42); Io.Write(r);       // "42"
r = Number.ToHex(255);   Io.Write(r);       // "ff"
Memory.Set(2000, 49); Memory.Set(2001, 50); Memory.Set(2002, 51);  // "123"
r = Number.Parse(Span.Make(2000, 3)); Io.WriteByte(r);   // 123
"""

EXPECTED = [7, 3, 5] + [52, 50] + [102, 102] + [123]


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return [int(x, 16) for x in p[1:]]
    return []


def main():
    words = lower_to_bytecode_safe(compile_c(PROG))
    py_out = list(b"".join(PicoVM().run(words).output))
    js_out = run_js_vm(words)
    assert py_out == EXPECTED, f"Python Number.* {py_out} != {EXPECTED}"
    assert js_out == EXPECTED, f"JS Number.* {js_out} != {EXPECTED}"
    print(f"PASS Number.*: Python VM == JS VM byte-exact "
          f"(Abs/Min/Max/ToString/ToHex/Parse)")



def test_main():
    main()

if __name__ == "__main__":
    main()
