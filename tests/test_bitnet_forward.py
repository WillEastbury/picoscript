#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full BitNet forward pass, native ternary matmuls, verified vs the reference.

Runs the complete decoder-only forward (embed -> L x [shiftnorm -> ternary
Q/K/V -> multi-head attention w/ integer LUT softmax -> ternary Wo -> residual
-> shiftnorm -> ternary gate/up -> ReLU^2 -> ternary down -> residual] ->
shiftnorm -> ternary LM head) with every mat-vec compiled to native C and run
over the model held in arena memory. Asserts bit-exact match with the pure
integer reference across several model shapes.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bitnet_forward_ref as ref  # noqa: E402
from bitnet_forward_native import NativeEngine, forward_native  # noqa: E402


def reconfigure(D, H, F, L, V, T):
    ref.D, ref.H, ref.DH, ref.F, ref.L, ref.V, ref.T = D, H, D // H, F, L, V, T


def arena_for(model):
    total = sum(m["rows"] * ref.trit_row_bytes(m["cols"])
                for layer in model["layers"] for m in layer.values())
    total += model["head"]["rows"] * ref.trit_row_bytes(model["head"]["cols"])
    return NativeEngine.WEIGHTS + total + 8192


def run_case(D, H, F, L, V, T, compare=True):
    reconfigure(D, H, F, L, V, T)
    model = ref.build_model(seed=12345)
    tokens = [3, 7, 1, 5, 2, 11, 4, 9][:T]
    eng = NativeEngine(arena_for(model))
    t0 = time.time()
    got = forward_native(model, tokens, eng)
    dt = time.time() - t0
    assert len(got) == V
    if compare:
        assert got == ref.forward(model, tokens), f"mismatch at D={D} H={H} F={F} L={L} V={V}"
    return got, dt


def main():
    # default tiny shape, and two larger shapes with different head/ffn dims
    run_case(8, 2, 16, 2, 16, 3)
    run_case(32, 4, 64, 3, 64, 4)
    got, dt = run_case(128, 8, 256, 4, 256, 5)
    pred = ref.argmax(got)
    print(f"PASS bitnet forward: native matvecs == integer reference (bit-exact) "
          f"at dims 8/32/128; 128-dim 4-layer prefill(5) in {dt*1000:.0f} ms, argmax={pred}")



def test_main():
    main()

if __name__ == "__main__":
    main()
