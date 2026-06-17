#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Higher-level transformer helpers: Quant, Attention, head-aware KV, row argmax."""

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


SRC = (
    setbytes(1000, i32s([10, -20, 30])) +
    setbytes(1100, i32s([256, 256, 256])) +
    'int x = Span.Make(1000, 12); int gamma = Span.Make(1100, 12);'
    'print(Quant.AbsMax(x));'
    'int qi8 = Quant.QuantI8(x, 10); Io.Write(qi8); Io.WriteByte(124);'
    'Io.Write(Quant.DequantI8(qi8, 10));'
    'Io.Write(Quant.ApplyScale(x, 2));'
    'Io.Write(Quant.GroupScale(0x00090004));'
    'Io.Write(Tensor.RmsNormI32(x, gamma));' +
    # q=[1,2], K rows [[1,0],[0,1]], V rows [[10,20],[30,40]]
    setbytes(1200, bytes([1, 2])) +
    setbytes(1210, bytes([1, 0, 0, 1])) +
    setbytes(1220, bytes([10, 20, 30, 40])) +
    'Attention.SetShape(1, 2);'
    'int q = Span.Make(1200, 2); int k = Span.Make(1210, 4); int v = Span.Make(1220, 4);'
    'int scores = Attention.Scores(q, k); Io.Write(scores);'
    'int weights = Tensor.SoftmaxI32(scores); Io.Write(Attention.Mix(weights, v));'
    # Head-aware KV.
    'Kv.SetHead(2); Kv.WriteKH(1, q); Kv.WriteVH(1, v);'
    'Io.Write(Kv.ReadKH(1)); Io.Write(Kv.ReadVH(1));' +
    # ArgMaxRows over matrix [[1,1], [3,4]] dot [1,1] => [2,7] => row 1.
    setbytes(1300, bytes([1, 1, 3, 4])) +
    setbytes(1310, bytes([1, 1])) +
    'Tensor.SetShape(2, 2); int mat = Span.Make(1300, 4); int act = Span.Make(1310, 2);'
    'print(Sampling.ArgMaxRows(mat, act));'
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


def test_transformer_helpers_py_js_parity():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py[:4] == (30).to_bytes(4, "big")
    assert py[-4:] == (1).to_bytes(4, "big")
    assert b"\x01\xfe\x03|" in py


def main():
    test_transformer_helpers_py_js_parity()
    print("PASS transformer helpers: Quant/Attention/head-KV/ArgMaxRows (Python VM == JS VM)")


if __name__ == "__main__":
    main()
