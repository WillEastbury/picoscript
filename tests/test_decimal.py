#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decimal.* Q16.16 fixed-point library: Python VM == JS VM, byte-exact.

Decimal.* (see PROVENANCE/HostApi._decimallib in picoscript_vm.py) preserves
the fractional part of a decimal value as a Q16.16 fixed-point 32-bit int --
unlike Number.Parse (32-bit-integer only, truncates any fraction). Uses the
same Q16.16 encoding already proven byte-identical across the Python/JS VMs
for Maths.Sin/Cos/Exp/Log.
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
var r = Decimal.ToString(Decimal.Parse("19.99")); Io.Write(r); Io.WriteByte(124);   // "19.99|" (no binary-fraction noise)
r = Decimal.ToString(Decimal.Parse("1000.00")); Io.Write(r); Io.WriteByte(124);     // "1000|"
r = Decimal.ToString(Decimal.Add(Decimal.Parse("1000.25"), Decimal.Parse("0.10"))); Io.Write(r); Io.WriteByte(124);  // "1000.35|"
r = Decimal.ToString(Decimal.Sub(Decimal.Parse("1000.25"), Decimal.Parse("0.10"))); Io.Write(r); Io.WriteByte(124);  // "1000.15|"
r = Decimal.ToString(Decimal.Mul(Decimal.Parse("2.5"), Decimal.Parse("4"))); Io.Write(r); Io.WriteByte(124);         // "10|"
r = Decimal.ToString(Decimal.Div(Decimal.Parse("10"), Decimal.Parse("4"))); Io.Write(r); Io.WriteByte(124);          // "2.5|"
int c1 = Decimal.Compare(Decimal.Parse("5000.0"), Decimal.Parse("1000.0")); Io.WriteByte(c1 + 1);  // 2 (1+1, >)
int c2 = Decimal.Compare(Decimal.Parse("1000.0"), Decimal.Parse("1000.0")); Io.WriteByte(c2 + 1);  // 1 (0+1, ==)
int c3 = Decimal.Compare(Decimal.Parse("1.0"), Decimal.Parse("5000.0")); Io.WriteByte(c3 + 1);      // 0 (-1+1, <)
int t1 = Decimal.ToInt(Decimal.Parse("-3.75")); Io.WriteByte(t1);   // -3 truncate towards zero (253 as byte)
"""

EXPECTED = (
    list(b"19.99|") + list(b"1000|") + list(b"1000.35|") + list(b"1000.15|") +
    list(b"10|") + list(b"2.5|") + [2, 1, 0] + [253]
)


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
    assert py_out == EXPECTED, f"Python Decimal.* {py_out} != {EXPECTED}"
    assert js_out == EXPECTED, f"JS Decimal.* {js_out} != {EXPECTED}"
    print("PASS Decimal.*: Python VM == JS VM byte-exact "
          "(Parse/ToString/Add/Sub/Mul/Div/Compare/ToInt)")


def test_main():
    main()


if __name__ == "__main__":
    main()
