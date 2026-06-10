#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bit-exact verification of examples/bitnet_ternary_matvec.pc against the
BitNet b1.58 C engine (bitnet_c/ternary_matrix.c).

The C reference (trit5 base-3 packing + 243-entry LUT decode + int8 MAC) is
reproduced exactly here, cross-checked against a direct integer dot product,
then the PicoScript kernel is run on the PicoVM and compared byte-for-byte.
"""

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

ACT_BASE = 256
W_BASE = 4096
PC_PATH = os.path.join(ROOT, "examples", "bitnet_ternary_matvec.pc")


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


# ── C engine packing, byte-for-byte (ternary_matrix.c) ──────────────────────
def trit_row_bytes(cols):
    """TRIT_ROW_BYTES(c) from bitnet.h: ceil(c/5) padded to a uint32 boundary."""
    return ((cols + 4) // 5 + 3) & ~3


def trit5_encode(chunk):
    """trit5_encode(): 5 trits {-1,0,+1} -> base-3 byte (0->0,+1->1,-1->2)."""
    v = 0
    for t in chunk:
        e = 0 if t == 0 else (1 if t > 0 else 2)
        v = v * 3 + e
    return v


def pack_row(row):
    """trit_pack_row(): one ternary row -> zero-padded base-3 bytes."""
    cols = len(row)
    out = bytearray(trit_row_bytes(cols))
    bi = 0
    for i in range(0, cols, 5):
        chunk = [0, 0, 0, 0, 0]
        for j in range(min(5, cols - i)):
            chunk[j] = row[i + j]
        out[bi] = trit5_encode(chunk)
        bi += 1
    return bytes(out)


def build_trit_lut():
    """trit_lut_init(): g_trit_lut[243][5]."""
    lut = []
    for v in range(243):
        tmp, w = v, [0, 0, 0, 0, 0]
        for j in range(4, -1, -1):
            t = tmp % 3
            tmp //= 3
            w[j] = 0 if t == 0 else (1 if t == 1 else -1)
        lut.append(w)
    return lut


_LUT = build_trit_lut()


def c_trit_dot(packed_row, cols, act):
    """trit_dot_row(): LUT-decode the packed bytes and MAC with int8 acts."""
    acc, xi = 0, 0
    for b in range((cols + 4) // 5):
        w = _LUT[packed_row[b]]
        for j in range(5):
            if xi < cols:
                acc += w[j] * act[xi]
                xi += 1
    return acc


# ── PicoScript run ──────────────────────────────────────────────────────────
def run_picoscript(words, rows, cols, weights, acts):
    vm = PicoVM()
    stride = trit_row_bytes(cols)
    vm.mem[0], vm.mem[1] = rows >> 8, rows & 0xFF
    vm.mem[2], vm.mem[3] = cols >> 8, cols & 0xFF
    vm.mem[4], vm.mem[5] = stride >> 8, stride & 0xFF
    for c in range(cols):
        vm.mem[ACT_BASE + c] = acts[c] & 0xFF
    off = W_BASE
    for r in range(rows):
        pr = pack_row(weights[r])
        vm.mem[off:off + len(pr)] = pr
        off += stride
    vm.run(words)
    out = b"".join(vm.output)
    assert len(out) == rows * 4, f"expected {rows*4} bytes, got {len(out)}"
    return [s32(int.from_bytes(out[i:i + 4], "big")) for i in range(0, len(out), 4)]


def make_case(rng, rows, cols, force=None):
    weights = [[rng.choice((-1, 0, 0, 1)) for _ in range(cols)] for _ in range(rows)]
    acts = [rng.randint(-127, 127) for _ in range(cols)]
    if force == "zero":
        weights = [[0] * cols for _ in range(rows)]
    if force == "neg":  # drive a strongly negative accumulator
        weights = [[-1] * cols for _ in range(rows)]
        acts = [rng.randint(40, 127) for _ in range(cols)]
    return weights, acts


def main():
    rng = random.Random(20260610)

    # Compile once; assert the toC / toJS bridges accept the kernel too.
    src = open(PC_PATH, encoding="utf-8").read()
    il = compile_c(src)
    words = lower_to_bytecode_safe(il)
    c_text = lower_to_c(il, func_name="pico_bitnet_matvec", emit_main=True)
    js_text = lower_to_js(il, module_name="pico_bitnet_matvec")
    # Bits.* must lower to NATIVE C/JS (real shifts/masks), not pv_host calls.
    assert ">>" in c_text and "0xFFFFFFFFu" in c_text, "Bits.* did not lower natively in C"
    assert ">>>" in js_text, "Bits.Shr did not lower to a native unsigned shift in JS"
    assert len(c_text) > 0 and len(js_text) > 0

    cases = [
        (1, 5), (1, 4), (2, 5), (3, 7), (4, 10), (5, 37),
        (8, 40), (8, 41), (16, 128), (24, 153), (6, 1),
    ]
    total = 0
    for rows, cols in cases:
        for force in (None, "zero", "neg"):
            weights, acts = make_case(rng, rows, cols, force)
            truth = [sum(weights[r][c] * acts[c] for c in range(cols)) for r in range(rows)]
            # C engine reference must reproduce the integer dot exactly.
            cref = [c_trit_dot(pack_row(weights[r]), cols, acts) for r in range(rows)]
            assert cref == truth, f"C-ref mismatch rows={rows} cols={cols} force={force}"
            # PicoScript kernel must match the C engine bit-for-bit.
            got = run_picoscript(words, rows, cols, weights, acts)
            assert got == truth, (
                f"PicoScript mismatch rows={rows} cols={cols} force={force}\n"
                f"  got  ={got}\n  truth={truth}"
            )
            total += 1

    print(f"PASS bitnet ternary kernel: {total} cases, PicoScript == C engine (bit-exact)")


if __name__ == "__main__":
    main()
