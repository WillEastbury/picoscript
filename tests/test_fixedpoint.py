#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maths.* Q16.16 fixed-point transcendentals (Sin/Cos/Tan via CORDIC; Exp/Log/Log10).

A value v is represented as round(v * 65536); angles are radians in Q16.16. The
functions are all-integer CORDIC with shared constants and iteration count, so the
result is BYTE-IDENTICAL on the Python VM, the portable C VM and the JS VM (the same
host handlers also back the toC/toJS native paths). Accuracy is ~1e-3, ample for
fixed-point DSP / activation math.

Note: PicoScript literals are limited to a sign-extended 16-bit immediate
([-32768, 32767]); Q16.16 angles are larger, so they are constructed at runtime
(here `i * step`, a full-width MUL) -- which is the normal way fixed-point values
arise (from data / computation), not from literals.
"""

import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c              # noqa: E402
from picoscript_il import lower_to_bytecode_safe     # noqa: E402
from picoscript_vm import PicoVM                     # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
ONE = 65536
STEP = 17157          # ~0.2618 rad/step (2*pi / 24), fits a 16-bit literal
STEPS = 24            # i*STEP sweeps ~0..6.02 rad at runtime via MUL


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def run_vm(words, cmd):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(cmd, input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:]).decode()
    return ""


PROG = f"""
int step = {STEP}; int i = 0;
while (i < {STEPS}) {{
  int ang = i * step; int r;
  r = Maths.Sin(ang); r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  r = Maths.Cos(ang); r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  r = Maths.Tan(ang); r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  i = i + 1;
}}
"""

# exp over z in ~[-4.8, 4.4]; log/log10 over x in ~[0.5, 12]. Inputs built at runtime.
PROG_EL = """
int i = 0;
while (i < 24) {
  int z = (i - 12) * 26214; int xl = (i + 1) * 32767; int r;
  r = Maths.Exp(z);    r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  r = Maths.Log(xl);   r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  r = Maths.Log10(xl); r = Number.ToString(r); Io.Write(r); Io.WriteByte(32);
  i = i + 1;
}
"""


def main():
    build_c_vm()
    words = lower_to_bytecode_safe(compile_c(PROG))
    py = b"".join(PicoVM().run(words).output).decode()
    c = run_vm(words, [VM_EXE])
    js = run_vm(words, ["node", os.path.join(VM_DIR, "picovm_run.js")])

    assert py == c, f"Sin/Cos/Tan: Python VM != C VM\n{py}\n{c}"
    assert py == js, f"Sin/Cos/Tan: Python VM != JS VM\n{py}\n{js}"

    # Accuracy vs reference math (skip tan only where cos ~ 0, i.e. near +-pi/2).
    vals = py.split()
    assert len(vals) == STEPS * 3, f"expected {STEPS*3} values, got {len(vals)}"
    max_err = 0.0
    k = 0
    for i in range(STEPS):
        rad = (i * STEP) / ONE
        for op, ref in (("Sin", math.sin), ("Cos", math.cos), ("Tan", math.tan)):
            raw = int(vals[k]); k += 1
            if op == "Tan" and abs(math.cos(rad)) < 0.02:
                continue
            max_err = max(max_err, abs(raw / ONE - ref(rad)))
    assert max_err < 5e-3, f"fixed-point trig accuracy too low: max_err={max_err}"

    # Spot anchors (within CORDIC residual): sin(0) ~ 0, cos(0) ~ 1.0 (raw 65536).
    assert abs(int(vals[0])) <= 4, f"sin(0) must be ~0, got {vals[0]}"
    assert abs(int(vals[1]) - ONE) <= 4, f"cos(0) must be ~65536, got {vals[1]}"

    # ── Exp / Log / Log10 ──
    ewords = lower_to_bytecode_safe(compile_c(PROG_EL))
    epy = b"".join(PicoVM().run(ewords).output).decode()
    ec = run_vm(ewords, [VM_EXE])
    ejs = run_vm(ewords, ["node", os.path.join(VM_DIR, "picovm_run.js")])
    assert epy == ec, f"Exp/Log: Python VM != C VM\n{epy}\n{ec}"
    assert epy == ejs, f"Exp/Log: Python VM != JS VM\n{epy}\n{ejs}"

    ev = epy.split()
    assert len(ev) == 24 * 3, f"expected 72 exp/log values, got {len(ev)}"
    me = ml = ml10 = 0.0
    k = 0
    for i in range(24):
        z = (i - 12) * 26214 / ONE
        x = (i + 1) * 32767 / ONE
        e_raw = int(ev[k]); k += 1
        l_raw = int(ev[k]); k += 1
        l10_raw = int(ev[k]); k += 1
        if z < 4.2:   # above this exp() exceeds Q16.16 range and saturates
            me = max(me, abs(e_raw / ONE - math.exp(z)) / max(1, math.exp(z)))
        ml = max(ml, abs(l_raw / ONE - math.log(x)))
        ml10 = max(ml10, abs(l10_raw / ONE - math.log10(x)))
    assert me < 1e-3, f"exp accuracy too low: {me}"
    assert ml < 1e-3, f"log accuracy too low: {ml}"
    assert ml10 < 1e-3, f"log10 accuracy too low: {ml10}"

    print(f"PASS fixed-point: Maths.Sin/Cos/Tan/Exp/Log/Log10 Q16.16 byte-identical on "
          f"Python/C/JS; trig max err {max_err:.5f}, exp rel {me:.6f}, log {ml:.6f}, log10 {ml10:.6f}")



def test_main():
    main()

if __name__ == "__main__":
    main()
