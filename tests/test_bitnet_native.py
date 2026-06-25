#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native-C execution of the BitNet ternary kernel via the toC bridge.

Proves the new native Memory.* / Io.WriteByte lowering: the same PicoScript
matvec compiles to C that touches a real arena (ctx->mem), runs ~1000x faster
than the bytecode interpreter, and stays bit-exact with the C engine reference.
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_c  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD = os.path.join(ROOT, ".test_build_native")
PC = os.path.join(ROOT, "examples", "bitnet_ternary_matvec.pc")
ACT_BASE, W_BASE = 256, 4096


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def trit_row_bytes(c):
    return ((c + 4) // 5 + 3) & ~3


def pack_row(row):
    out = bytearray(trit_row_bytes(len(row)))
    bi = 0
    for i in range(0, len(row), 5):
        v = 0
        for j in range(5):
            t = row[i + j] if i + j < len(row) else 0
            v = v * 3 + (0 if t == 0 else 1 if t > 0 else 2)
        out[bi] = v
        bi += 1
    return bytes(out)


def build_arena(rows, cols, weights, acts):
    stride = trit_row_bytes(cols)
    size = W_BASE + rows * stride
    mem = bytearray(size)
    mem[0], mem[1] = rows >> 8, rows & 0xFF
    mem[2], mem[3] = cols >> 8, cols & 0xFF
    mem[4], mem[5] = stride >> 8, stride & 0xFF
    for c in range(cols):
        mem[ACT_BASE + c] = acts[c] & 0xFF
    off = W_BASE
    for r in range(rows):
        pr = pack_row(weights[r])
        mem[off:off + len(pr)] = pr
        off += stride
    return bytes(mem)


def build_exe():
    os.makedirs(BUILD, exist_ok=True)
    cfile = os.path.join(BUILD, "bitnet_entry.c")
    exe = os.path.join(BUILD, "arena_matvec.exe")
    il = compile_c(open(PC, encoding="utf-8").read())
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(lower_to_c(il, func_name="pico_entry", emit_main=False))
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O3", f"-I{VM_DIR}",
           cfile, os.path.join(VM_DIR, "pico_arena_run.c"),
           os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return exe


def run_native(exe, arena_image, arena_bytes=None):
    arena_bytes = arena_bytes or len(arena_image)
    out = subprocess.run([exe, str(arena_bytes), str(len(arena_image))],
                         input=arena_image, capture_output=True)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    raw = out.stdout
    return [s32(int.from_bytes(raw[i:i + 4], "big")) for i in range(0, len(raw), 4)]


def main():
    import random
    rng = random.Random(7)
    exe = build_exe()

    # correctness: native C matvec == direct integer dot, many shapes
    total = 0
    for rows, cols in [(1, 5), (3, 7), (8, 40), (8, 41), (16, 128), (24, 153), (5, 37)]:
        weights = [[rng.choice((-1, 0, 0, 1)) for _ in range(cols)] for _ in range(rows)]
        acts = [rng.randint(-127, 127) for _ in range(cols)]
        truth = [sum(weights[r][c] * acts[c] for c in range(cols)) for r in range(rows)]
        got = run_native(exe, build_arena(rows, cols, weights, acts))
        assert got == truth, f"native mismatch rows={rows} cols={cols}\n got={got}\n exp={truth}"
        total += 1

    # speed: a real-width 1536x1536 matvec (Python VM took ~79 s for this)
    rows = cols = 1536
    weights = [[rng.choice((-1, 0, 0, 1)) for _ in range(cols)] for _ in range(rows)]
    acts = [rng.randint(-127, 127) for _ in range(cols)]
    image = build_arena(rows, cols, weights, acts)
    t0 = time.time()
    got = run_native(exe, image)
    dt = time.time() - t0
    truth = [sum(weights[r][c] * acts[c] for c in range(cols)) for r in range(rows)]
    assert got == truth, "native 1536 mismatch"
    print(f"PASS bitnet native kernel: {total} shapes bit-exact; "
          f"1536x1536 matvec in {dt*1000:.1f} ms (Python VM: ~79000 ms)")



def test_main():
    main()

if __name__ == "__main__":
    main()
