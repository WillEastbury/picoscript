#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""String literals -> arena spans, on all five runtimes.

A string literal in any expression position lowers to a span over its interned
constant-pool bytes (no Memory.Set ritual). The pool deduplicates identical
literals and gives each distinct literal its own stable address, so any number
can be live at once -- e.g. three literals stored in variables no longer clobber
each other. Verified byte-identical across Python VM, JS VM, C interpreter,
toC-native and toJS-native.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_strlit")


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


def check_c(prog, expected, slot):
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
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picocompress.js"):
        s = os.path.join(VM_DIR, dep)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(BUILD, dep))
    try:
        # Three literals stored in variables stay live simultaneously (the old
        # 2-slot scheme clobbered the third -> "cc|bb|cc"; the const pool fixes it).
        check_c('int a = "aa"; int b = "bb"; int cc = "cc"; '
                'Io.Write(a); Io.WriteByte(124); Io.Write(b); Io.WriteByte(124); Io.Write(cc);',
                b"aa|bb|cc", "three_live")

        # Literals flow as ordinary expression values into namespace ops.
        check_c('int u = String.ToUpper(String.Concat("ab", "cd")); Io.Write(u);',
                b"ABCD", "expr_value")

        # Identical literals dedup to one stable address (both render correctly).
        check_c('int a = "xy"; int b = "xy"; Io.Write(a); Io.Write(b);', b"xyxy", "dedup")

        # A literal used inside a loop body reuses its stable address each iteration.
        check_c('int n = 0; while (n < 3) { Io.Write("ab"); n = n + 1; }', b"ababab", "loop")

        # BASIC frontend literals still work after the same const-pool fix.
        bwords = lower_to_bytecode_safe(compile_basic('PRINT "hi"\n'))
        bpy = b"".join(PicoVM().run(bwords).output)
        bjs = js_interp_out(bwords)
        assert bpy == bjs and b"hi" in bpy, f"BASIC literal: py={bpy!r} js={bjs!r}"

        print("PASS string literals: \"...\" -> arena span on all five runtimes; const pool keeps "
              "multiple literals live (no clobber), dedups, and survives loops (no Memory.Set ritual)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
