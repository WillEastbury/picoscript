#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-runtime tests for Bits.* host hooks and native lowerings."""

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
BUILD_DIR = os.path.join(ROOT, ".test_build_bits")
MASK32 = 0xFFFFFFFF


def s32(v):
    v &= MASK32
    return v - 0x100000000 if v & 0x80000000 else v


def u32(v):
    return v & MASK32


def ref(op, a, b=0):
    a = u32(a)
    b = u32(b)
    sh = b & 31
    if op == "And":
        return s32((a & b) & MASK32)
    if op == "Or":
        return s32((a | b) & MASK32)
    if op == "Xor":
        return s32((a ^ b) & MASK32)
    if op == "Not":
        return s32((~a) & MASK32)
    if op == "Shl":
        return s32((a << sh) & MASK32)
    if op == "Shr":
        return s32(a >> sh)
    if op == "Sar":
        return s32(s32(a) >> sh)
    raise AssertionError(op)


def build_c_vm():
    if os.path.exists(VM_EXE):
        os.remove(VM_EXE)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"),
           "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def run_c_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out(out)


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out(out.stdout)


def run_py_vm(words):
    vm = PicoVM().run(words)
    return [s32(int.from_bytes(b, "big")) for b in vm.output]


def parse_out(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            vals = []
            hx = p[1:]
            for i in range(0, len(hx), 4):
                vals.append(s32(int("".join(hx[i:i + 4]), 16)))
            return vals
    return []


def var_set(name, value):
    bs = [(u32(value) >> shift) & 0xFF for shift in (24, 16, 8, 0)]
    lines = [f"{name} = {bs[0]};"]
    for b in bs[1:]:
        lines.append(f"{name} = {name} * 256;")
        if b:
            lines.append(f"{name} = {name} + {b};")
    return lines


def program_for(op, cases):
    lines = ["int a = 0;", "int b = 0;"]
    for a, b in cases:
        lines.extend(var_set("a", a))
        if op != "Not":
            lines.extend(var_set("b", b))
            lines.append(f"print(Bits.{op}(a, b));")
        else:
            lines.append("print(Bits.Not(a));")
    return "\n".join(lines) + "\n"


def run_native_c(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_bits_{slot}", emit_main=True)
    cfile = os.path.join(BUILD_DIR, f"bits_{slot}.c")
    exe = os.path.join(BUILD_DIR, f"bits_{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out(out.stdout)


def run_native_js(il, slot):
    jsfile = os.path.join(BUILD_DIR, f"bits_{slot}.js")
    runner = os.path.join(BUILD_DIR, f"run_bits_{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_bits_{slot}"))
    with open(runner, "w", encoding="utf-8") as f:
        f.write(
            f"const p = require('./bits_{slot}.js');\n"
            "const rt = p.run();\n"
            "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n"
        )
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out(out.stdout)


def assert_all_equal(label, got, want):
    assert got == want, f"{label}: got {got}, want {want}"


def main():
    build_c_vm()
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)
    try:
        cases = [
            (0, 0), (1, 1), (-1, 1), (-1, 31), (0x7FFFFFFF, 4),
            (0x80000000, 1), (0x12345678, 36), (0x87654321, 63),
            (0xFFFFFFFF, 0xAAAAAAAA), (0x13579BDF, 0x2468ACE0),
        ]
        for op in ("And", "Or", "Xor", "Shl", "Shr", "Sar", "Not"):
            op_cases = cases if op != "Not" else [(a, 0) for a, _ in cases]
            src = program_for(op, op_cases)
            il = compile_c(src)
            words = lower_to_bytecode_safe(il)
            want = [ref(op, a, b) for a, b in op_cases]
            assert_all_equal(f"{op} Python VM", run_py_vm(words), want)
            assert_all_equal(f"{op} C VM", run_c_vm(words), want)
            assert_all_equal(f"{op} JS VM", run_js_vm(words), want)
            assert_all_equal(f"{op} native C", run_native_c(il, op.lower()), want)
            assert_all_equal(f"{op} native JS", run_native_js(il, op.lower()), want)

        pack_src = """
int op = 7;
int imm = 1;
imm = imm * 256 + 35;
imm = imm * 256 + 69;
imm = imm * 256 + 103;
print(Bits.Or(Bits.Shl(op, 28), imm));
"""
        il = compile_c(pack_src)
        words = lower_to_bytecode_safe(il)
        want = [s32((7 << 28) | 0x01234567)]
        assert_all_equal("pack Python VM", run_py_vm(words), want)
        assert_all_equal("pack C VM", run_c_vm(words), want)
        assert_all_equal("pack JS VM", run_js_vm(words), want)
        assert_all_equal("pack native C", run_native_c(il, "pack"), want)
        assert_all_equal("pack native JS", run_native_js(il, "pack"), want)
    finally:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    print("Bits tests passed")


if __name__ == "__main__":
    main()
