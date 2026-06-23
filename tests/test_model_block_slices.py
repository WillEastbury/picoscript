#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Storage-backed tensor blocks for LLM/BitNet model cards."""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def i32s(vals):
    out = bytearray()
    for v in vals:
        out += int(v).to_bytes(4, "big", signed=True)
    return bytes(out)


def base3_row(trits):
    code = 0
    for t in trits:
        digit = 0 if t == 0 else (1 if t > 0 else 2)
        code = code * 3 + digit
    return code


I8_ROWS = bytes([
    1, 2, 3,
    4, 5, 6,
    7, 8, 9,
    10, 11, 12,
])
ACT3 = bytes([1, 1, 1])

BITMAP_ROWS = bytes([
    0b00000010, 0b00000100,  # [ 1,  0, -1,  1]
    0b00000001, 0b00001000,  # [ 0,  1,  1, -1]
    0b00000100, 0b00000011,  # [-1, -1,  0,  1]
    0b00000000, 0b00000000,  # [ 1,  1,  1,  1]
])
BASE3_ROWS = bytes([
    base3_row([1, 0, -1, 1, 0]), 0, 0, 0,
    base3_row([0, 1, 1, -1, 0]), 0, 0, 0,
    base3_row([-1, -1, 0, 1, 0]), 0, 0, 0,
    base3_row([1, 1, 1, 1, 0]), 0, 0, 0,
])
TERNARY_ROWS = bytes([
    0x91,  # [1,0,-1,1] with 2-bit trits, low bits first
    0x64,  # [0,1,1,-1]
    0x0A,  # [-1,-1,0,0] first three cols used for row2 if global-packed by trit
    0x55,  # padding-ish; test uses bitmap/base3 for row-aligned formats
])
ACT4 = bytes([2, 3, 4, 5])


SRC = (
    "Storage.UsePack(2);"
    + setbytes(1000, I8_ROWS)
    + "int i8blob = Span.Make(1000, 12); Storage.SetSlice(0, Span.Len(i8blob)); Storage.WriteSlice(7, i8blob);"
    + setbytes(1100, ACT3)
    + "int act3 = Span.Make(1100, 3);"
    + 'Model.TensorView(1, "2|7|0|4|3|1");'
    + "Model.SetBlock(1, 2);"
    + "Io.Write(Model.ReadTensorBlock(1, 0)); Io.WriteByte(124);"
    + "Io.Write(Model.MatVecI8Block(1, act3)); Io.WriteByte(124);"
    + setbytes(1200, BITMAP_ROWS)
    + "int bm = Span.Make(1200, 8); Storage.SetSlice(0, Span.Len(bm)); Storage.WriteSlice(8, bm);"
    + setbytes(1300, BASE3_ROWS)
    + "int b3 = Span.Make(1300, 16); Storage.SetSlice(0, Span.Len(b3)); Storage.WriteSlice(9, b3);"
    + setbytes(1400, TERNARY_ROWS)
    + "int tr = Span.Make(1400, 4); Storage.SetSlice(0, Span.Len(tr)); Storage.WriteSlice(10, tr);"
    + setbytes(1500, ACT4)
    + "int act4 = Span.Make(1500, 4);"
    + 'Model.TensorView(2, "2|8|0|4|4|2");'
    + 'Model.TensorView(3, "2|9|0|4|4|3");'
    + 'Model.TensorView(4, "2|10|0|4|4|1");'
    + "Model.SetBlock(1, 2);"
    + "Io.Write(BitLinear.MatVecBitmapBlock(2, act4)); Io.WriteByte(124);"
    + "Io.Write(BitLinear.MatVecBase3Block(3, act4));"
)

EXPECTED = (
    I8_ROWS[3:9] + b"|" +
    i32s([15, 24]) + b"|" +
    i32s([2, 0]) + b"|" +
    i32s([2, 0])
)


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _run_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_model_block_slices():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py == EXPECTED, py.hex()


def main():
    test_model_block_slices()
    print("PASS model block slices: ReadTensorBlock + card-backed MatVecI8/BitLinear block matvec (Python VM == JS VM)")


if __name__ == "__main__":
    main()
