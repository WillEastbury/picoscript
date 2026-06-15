#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runs the namespace demo programs (examples/*.pc) on all five PicoScript runtimes
and asserts byte-identical output + the known answer. Makes the demos executable,
parity-checked documentation: Python VM == JS VM == C interpreter == toC-native ==
toJS-native.
"""

import hashlib
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
EX_DIR = os.path.join(ROOT, "examples")
BUILD = os.path.join(ROOT, ".test_build_examples")


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


def py_out(words):
    return b"".join(PicoVM().run(words).output)


def js_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js');\n"
                "const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def run_demo(name, expected):
    slot = name.replace(".pc", "")
    with open(os.path.join(EX_DIR, name), encoding="utf-8") as f:
        src = f.read()
    words = lower_to_bytecode_safe(compile_c(src))
    runs = {
        "Python VM": py_out(words),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_c(src), slot),
        "toJS native": js_native_out(compile_c(src), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{name}] {label} {got!r} != {expected!r}"
    return slot


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
        run_demo("text_tools.pc",
                 b"HELLO" + b"|" + bytes([5]) + b"|" + b"ff" + b"|" + b"&lt;b&gt;")
        run_demo("web_template.pc", b"Hi Ada (admin)")
        run_demo("hashing.pc",
                 hashlib.sha256(b"abc").digest() + b"|" + b"aaabbbbc")
        print("PASS examples/*.pc demos: text_tools / web_template / hashing run byte-identically "
              "on all five runtimes (Python VM == JS VM == C interp == toC-native == toJS-native)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
