#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automatic register spilling on the bytecode backend, byte-identical on all paths.

The frozen ISA has 16 registers; a program with more live values used to throw
RegisterPressureError. Now the allocator auto-spills the overflow to scratch cards
(short-lived shuttle vregs around each use) so real code compiles and runs. The
native toC/toJS backends were never limited (each vreg is a real local), so this
just brings the three bytecode VMs up to the same "it always works" behaviour --
verified identical across Python VM, JS VM, C interpreter, toC-native, toJS-native.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_spill")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def parse_out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    return parse_out_bytes(subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout)


def js_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    return parse_out_bytes(subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                                          input=inp, capture_output=True, text=True).stdout)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c"); exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
                        f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js"); runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js'); const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2,'0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    words = lower_to_bytecode_safe(compile_c(prog))
    runs = {
        "Python VM": b"".join(PicoVM().run(words).output),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_c(prog), slot),
        "toJS native": js_native_out(compile_c(prog), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{slot}] {label} {got!r} != {expected!r}"


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js"):
        s = os.path.join(VM_DIR, dep)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(BUILD, dep))
    try:
        # 20 named vars (all pinned -> all whole-program-live) -> 4 must spill.
        decls = "".join(f"int a{i} = {i};" for i in range(20))
        summ = "int s = " + " + ".join(f"a{i}" for i in range(20)) + ";"
        check(decls + summ + "Io.WriteByte(s);", bytes([190]), "sum20")   # 0+..+19 = 190

        # Spilled variable read repeatedly inside a loop, with loop control flow.
        loop = ("".join(f"int a{i} = {i};" for i in range(18)) +
                "int acc = 0; int n = 0;"
                "while (n < 5) { acc = acc + a3; n = n + 1; }"   # 5 * 3 = 15
                "Io.WriteByte(acc);")
        check(loop, bytes([15]), "loop_spill")

        # Spilled values feeding a conditional.
        cond = ("".join(f"int a{i} = {i + 1};" for i in range(17)) +
                "int m = 0; if (a16 > a2) { m = a16 - a2; } Io.WriteByte(m);")  # 17 - 3 = 14
        check(cond, bytes([14]), "cond_spill")

        print("PASS automatic spilling: programs exceeding 16 live values compile via scratch-card "
              "spilling and run byte-identically on all five runtimes (sum-of-20, loop, conditional)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
