#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tensor / BitLinear primitives for small AI inference harnesses.

These primitives are intentionally span-based so a PicoScript transformer harness
can run on the reference VM, the browser VM, or a host-accelerated runtime with
the same source.
"""

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


def pack_ternary(trits):
    out = []
    for i in range(0, len(trits), 4):
        b = 0
        for j, t in enumerate(trits[i:i+4]):
            code = 1 if t > 0 else (2 if t < 0 else 0)
            b |= code << (j * 2)
        out.append(b)
    return bytes(out)


TENSOR_SRC = (
    setbytes(1000, bytes([1, 2, 253, 4])) +
    setbytes(1100, bytes([5, 254, 3, 1])) +
    setbytes(1200, bytes([1, 2, 3, 4, 255, 0, 2, 254])) +
    setbytes(1300, bytes([1, 1, 1, 1])) +
    setbytes(1400, i32s([10, -1])) +
    setbytes(1500, i32s([2, 3])) +
    setbytes(1600, i32s([3, 4])) +
    setbytes(1700, i32s([32768, 0, 0, 32768])) +
    'int a = Span.Make(1000, 4);'
    'int b = Span.Make(1100, 4);'
    'Tensor.SetShape(2, 4);'
    'print(Tensor.DotI8(a, b));'
    'int mat = Span.Make(1200, 8);'
    'int vec = Span.Make(1300, 4);'
    'int mv = Tensor.MatVecI8(mat, vec); Io.Write(mv);'
    'int x = Span.Make(1400, 8);'
    'int y = Span.Make(1500, 8);'
    'Io.Write(Tensor.AddI32(x, y));'
    'Io.Write(Tensor.MulI32(x, y));'
    'Io.Write(Tensor.ScaleI32(x, 2));'
    'Io.Write(Tensor.ReluI32(x));'
    'Io.Write(Tensor.RmsNormI32(x, y));'
    'int rot = Span.Make(1600, 8); int cs = Span.Make(1700, 16); Io.Write(Tensor.RoPEI32(rot, cs));'
    'Io.Write(Tensor.SoftmaxI32(x));'
    'print(Tensor.ArgMaxI32(x));'
)

BITLINEAR_SRC = (
    setbytes(2000, pack_ternary([1, 0, -1, 1, -1, -1, 0, 1])) +
    setbytes(2100, bytes([2, 3, 4, 5])) +
    'BitLinear.SetShape(2, 4);'
    'int w = Span.Make(2000, 2); int v = Span.Make(2100, 4);'
    'int out = BitLinear.MatVecTernary(w, v); Io.Write(out);'
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


def _check(src, expected):
    words = lower_to_bytecode_safe(compile_c(src))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py == expected, py.hex()


def test_tensor_i8_i32_transformer_primitives():
    expected = (
        int(-4 & 0xFFFFFFFF).to_bytes(4, "big") +
        i32s([10, -1]) +
        i32s([12, 2]) +
        i32s([0, -1]) +
        i32s([20, -2]) +
        i32s([10, 0]) +
        i32s([2, 0]) +
        i32s([3, 4]) +
        i32s([16383, 16383]) +
        int(0).to_bytes(4, "big")
    )
    _check(TENSOR_SRC, expected)


def test_bitlinear_ternary_matvec():
    _check(BITLINEAR_SRC, i32s([3, 0]))


def main():
    test_tensor_i8_i32_transformer_primitives()
    test_bitlinear_ternary_matvec()
    print("PASS Tensor/BitLinear primitives: int8 matvec, ternary matvec, i32 ops, norm/rope/softmax/argmax")


if __name__ == "__main__":
    main()
