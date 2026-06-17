#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tokenizer/Model/KV/Sampling primitives for a PicoScript BitNet harness."""

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
    # Tokenizer byte fallback round-trip over "Hi"
    setbytes(1000, b"Hi") +
    'int text = Span.Make(1000, 2);'
    'print(Tokenizer.EncodeBytes(text));'
    'print(Tokenizer.Token(0));'
    'Io.Write(Tokenizer.DecodeBytes());'
    'Io.WriteByte(124);' +
    # Model metadata and tensor view.
    'Model.SetConfig(1, 128); print(Model.GetConfig(1));'
    'int spec = "4096|2|4|15"; Model.TensorView(3, spec);'
    'print(Model.TensorOffset(3)); print(Model.TensorRows(3)); print(Model.TensorCols(3)); print(Model.TensorFormat(3));' +
    # KV cache.
    setbytes(1100, b"KEY") +
    setbytes(1110, b"VAL") +
    'int k = Span.Make(1100, 3); int v = Span.Make(1110, 3);'
    'int pos = 65537;'
    'Kv.WriteK(pos, k); Kv.WriteV(pos, v);'
    'print(Kv.Len()); Io.Write(Kv.ReadK(pos)); Io.Write(Kv.ReadV(pos)); Io.WriteByte(124);' +
    # Sampling top-k over [5, 9, 1].
    setbytes(1200, i32s([5, 9, 1])) +
    'int logits = Span.Make(1200, 12);'
    'print(Sampling.ArgMax(logits));'
    'Io.Write(Sampling.TopK(logits, 2));' +
    # BitLinear bitmap/base3 formats on one row: [1, 0, -1, 1] dot [2,3,4,5] = 3.
    setbytes(1300, bytes([0b00000010, 0b00000100])) +
    setbytes(1310, bytes([0x22])) +   # base3 encode [1,0,-1,1,0] = 34
    setbytes(1320, bytes([2, 3, 4, 5])) +
    'BitLinear.SetShape(1, 4);'
    'int bm = Span.Make(1300, 2); int b3 = Span.Make(1310, 4); int act = Span.Make(1320, 4);'
    'Io.Write(BitLinear.MatVecBitmap(bm, act));'
    'Io.Write(BitLinear.MatVecBase3(b3, act));'
)

EXPECTED = (
    (2).to_bytes(4, "big") +
    (75).to_bytes(4, "big") +
    b"Hi|" +
    (128).to_bytes(4, "big") +
    (4096).to_bytes(4, "big") +
    (2).to_bytes(4, "big") +
    (4).to_bytes(4, "big") +
    (15).to_bytes(4, "big") +
    (2).to_bytes(4, "big") +
    b"KEYVAL|" +
    (1).to_bytes(4, "big") +
    i32s([1, 0]) +
    i32s([3]) +
    i32s([-2])
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


def test_bitnet_harness_primitives():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py == EXPECTED, py.hex()


def main():
    test_bitnet_harness_primitives()
    print("PASS BitNet harness primitives: tokenizer/model/KV/sampling/bitmap/base3 (Python VM == JS VM)")


if __name__ == "__main__":
    main()
