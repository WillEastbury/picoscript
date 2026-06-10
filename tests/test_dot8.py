#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dot8 SIMD primitive: scalar correctness + proof of HW instruction emission.

Verifies the int8 dot-product primitive three ways:
  1. Python VM (scalar reference) runs the int8 matvec kernel bit-exact.
  2. The toC backend's native pv_dot8 (scalar on x86) matches.
  3. Building vm/picovm.c for AArch64 emits NEON SDOT, and for Cortex-M33+dsp
     emits SMLAD -- i.e. Dot8.Of lowers to real hardware MAC instructions.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD = os.path.join(ROOT, ".test_build_native")
PC = os.path.join(ROOT, "examples", "bitnet_int8_matvec.pc")
ZIG = [sys.executable, "-m", "ziglang", "cc"]


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def i32be(v):
    v &= 0xFFFFFFFF
    return bytes(((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255))


def build_arena(rows, cols, weights, acts):
    wbase = 64
    abase = wbase + rows * cols
    obase = abase + cols
    size = obase + rows * 4
    mem = bytearray(size)
    mem[0:4] = i32be(rows)
    mem[4:8] = i32be(cols)
    mem[8:12] = i32be(wbase)
    mem[12:16] = i32be(abase)
    mem[16:20] = i32be(obase)
    for r in range(rows):
        for c in range(cols):
            mem[wbase + r * cols + c] = weights[r][c] & 0xFF
    for c in range(cols):
        mem[abase + c] = acts[c] & 0xFF
    return bytes(mem), obase, size


def run_python_vm(words, image, obase, rows):
    vm = PicoVM(max_steps=10 ** 8)
    vm.mem[:len(image)] = image
    vm.run(words)
    return [s32(int.from_bytes(vm.mem[obase + i * 4:obase + i * 4 + 4], "big")) for i in range(rows)]


def build_native_exe():
    os.makedirs(BUILD, exist_ok=True)
    cfile = os.path.join(BUILD, "int8_entry.c")
    exe = os.path.join(BUILD, "int8_matvec.exe")
    il = compile_c(open(PC, encoding="utf-8").read())
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(lower_to_c(il, func_name="pico_entry", emit_main=False))
    cmd = ZIG + ["-std=c99", "-O2", f"-I{VM_DIR}", cfile,
                 os.path.join(VM_DIR, "pico_arena_run.c"), os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return exe


def run_native(exe, image, obase, size, rows):
    out = subprocess.run([exe, str(size), str(len(image)), str(obase), str(rows * 4)],
                         input=image, capture_output=True)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    raw = out.stdout
    return [s32(int.from_bytes(raw[i:i + 4], "big")) for i in range(0, len(raw), 4)]


def asm_emits(target_args, needle):
    out_s = os.path.join(BUILD, f"picovm_{needle}.s")
    cmd = ZIG + target_args + ["-O2", f"-I{VM_DIR}", "-S",
                               os.path.join(VM_DIR, "picovm.c"), "-o", out_s]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"build failed: {r.stderr}"
    text = open(out_s, encoding="utf-8", errors="replace").read().lower()
    return text.count(needle)


def main():
    import random
    rng = random.Random(99)
    exe = build_native_exe()
    words = lower_to_bytecode_safe(compile_c(open(PC, encoding="utf-8").read()))

    total = 0
    for rows, cols in [(1, 4), (3, 8), (5, 16), (8, 17), (16, 64), (4, 1), (7, 40)]:
        weights = [[rng.randint(-127, 127) for _ in range(cols)] for _ in range(rows)]
        acts = [rng.randint(-127, 127) for _ in range(cols)]
        truth = [sum(weights[r][c] * acts[c] for c in range(cols)) for r in range(rows)]
        image, obase, size = build_arena(rows, cols, weights, acts)
        assert run_python_vm(words, image, obase, rows) == truth, f"PyVM mismatch {rows}x{cols}"
        assert run_native(exe, image, obase, size, rows) == truth, f"native mismatch {rows}x{cols}"
        total += 1

    # Proof of hardware instruction emission from Dot8.Of -> pv_dot8.
    sdot = asm_emits(["-target", "aarch64-linux-gnu", "-mcpu=cortex_a76"], "sdot")
    smlad = asm_emits(["-target", "thumb-freestanding-eabi", "-mcpu=cortex_m33+dsp"], "smlad")
    assert sdot > 0, "expected NEON SDOT in AArch64 build"
    assert smlad > 0, "expected SMLAD in Cortex-M33+dsp build"

    print(f"PASS Dot8: {total} shapes bit-exact (Python VM == native C); "
          f"AArch64 emits SDOT x{sdot}, Cortex-M33 emits SMLAD x{smlad}")


if __name__ == "__main__":
    main()
