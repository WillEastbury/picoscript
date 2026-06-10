#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-process native engine: run the BitNet forward pass with the ternary
matvec compiled to native C (toC bridge) over a shared arena.

The dominant compute (the ternary mat-vecs) runs as the PicoScript-compiled
`bitnet_k_matvec.pc` kernel via ctypes against a shared arena holding the
model; the surrounding orchestration (norm / attention / FFN / residual)
matches tools/bitnet_forward_ref.py exactly. This is how a host (or the PIOS
scheduler) drives PicoScript-compiled kernels over a model in arena memory.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_c  # noqa: E402
import bitnet_forward_ref as ref  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD = os.path.join(ROOT, ".test_build_native")
MATVEC_PC = os.path.join(ROOT, "examples", "bitnet_k_matvec.pc")

SHIM = r"""
#include "picovm.h"
#include <stdlib.h>
int64_t pico_matvec(pv_ctx *ctx);
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
EXPORT pv_ctx *pv_make(uint8_t *mem, long n) {
    pv_ctx *c = (pv_ctx *)calloc(1, sizeof(pv_ctx));
    pv_init(c); c->mem = mem; c->mem_size = n; c->max_steps = 0;
    return c;
}
EXPORT void run_matvec(pv_ctx *c) { pico_matvec(c); }
"""


_DLL_PATH = None


def _build_dll():
    global _DLL_PATH
    if _DLL_PATH is not None:
        return _DLL_PATH
    os.makedirs(BUILD, exist_ok=True)
    entry = os.path.join(BUILD, "matvec_entry.c")
    shim = os.path.join(BUILD, "engine_shim.c")
    dll = os.path.join(BUILD, "bitnet_engine.dll")
    il = compile_c(open(MATVEC_PC, encoding="utf-8").read())
    with open(entry, "w", encoding="utf-8") as f:
        f.write(lower_to_c(il, func_name="pico_matvec", emit_main=False))
    with open(shim, "w", encoding="utf-8") as f:
        f.write(SHIM)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-O2", "-shared", f"-I{VM_DIR}",
           entry, shim, os.path.join(VM_DIR, "picovm.c"), "-o", dll]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    _DLL_PATH = dll
    return dll


def _i32be(v):
    v &= 0xFFFFFFFF
    return bytes(((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255))


def _s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


class NativeEngine:
    """Holds the model in a shared arena; runs ternary mat-vecs natively."""

    PARAM = 0
    ACT = 64                       # int8 activation scratch
    OUT = 64 + 8192                # int32 output scratch
    WEIGHTS = 64 + 8192 + 32004 * 4  # weights start after scratch

    def __init__(self, arena_bytes):
        self.lib = ctypes.CDLL(_build_dll())
        self.lib.pv_make.restype = ctypes.c_void_p
        self.lib.pv_make.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_long]
        self.lib.run_matvec.argtypes = [ctypes.c_void_p]
        self.arena = (ctypes.c_uint8 * arena_bytes)()
        self.ctx = self.lib.pv_make(self.arena, arena_bytes)
        self._top = self.WEIGHTS

    def load_matrix(self, mat):
        """Place a packed ternary matrix in the arena; return a descriptor."""
        off = self._top
        data = mat["packed"]
        ctypes.memmove(ctypes.byref(self.arena, off), data, len(data))
        self._top = off + len(data)
        stride = ref.trit_row_bytes(mat["cols"])
        return {"rows": mat["rows"], "cols": mat["cols"], "stride": stride, "off": off}

    def _put(self, off, data):
        self.arena[off:off + len(data)] = data

    def matvec(self, desc, act):
        for i, a in enumerate(act):
            self.arena[self.ACT + i] = a & 0xFF
        self._put(self.PARAM + 0, _i32be(desc["rows"]))
        self._put(self.PARAM + 4, _i32be(desc["cols"]))
        self._put(self.PARAM + 8, _i32be(desc["stride"]))
        self._put(self.PARAM + 12, _i32be(desc["off"]))
        self._put(self.PARAM + 16, _i32be(self.ACT))
        self._put(self.PARAM + 20, _i32be(self.OUT))
        self.lib.run_matvec(self.ctx)
        out = bytes(self.arena[self.OUT:self.OUT + desc["rows"] * 4])
        return [_s32(int.from_bytes(out[i * 4:i * 4 + 4], "big")) for i in range(desc["rows"])]


def forward_native(model, tokens, engine):
    """Mirror ref.forward, but every mat-vec runs on the native kernel."""
    descs = {
        "head": engine.load_matrix(model["head"]),
        "layers": [{k: engine.load_matrix(v) for k, v in layer.items()} for layer in model["layers"]],
    }

    def mv(desc, act):
        return engine.matvec(desc, act)

    hidden = [list(model["embed"][t]) for t in tokens]
    n = len(tokens)
    for li, layer in enumerate(model["layers"]):
        d = descs["layers"][li]
        q = [None] * n
        k = [None] * n
        v = [None] * n
        for p in range(n):
            a = ref.shiftnorm(hidden[p])
            q[p] = mv(d["Wq"], a)
            k[p] = mv(d["Wk"], a)
            v[p] = mv(d["Wv"], a)
        for p in range(n):
            attn = ref.attention(q, k, v, p)
            o = mv(d["Wo"], ref.shiftnorm(attn))
            hidden[p] = [hidden[p][i] + o[i] for i in range(ref.D)]
        for p in range(n):
            a = ref.shiftnorm(hidden[p])
            gate = ref.relu2(mv(d["gate"], a))
            up = mv(d["up"], a)
            fused = ref.shiftnorm([gate[i] + up[i] for i in range(ref.F)])
            down = mv(d["down"], fused)
            hidden[p] = [hidden[p][i] + down[i] for i in range(ref.D)]
    return mv(descs["head"], ref.shiftnorm(hidden[n - 1]))


if __name__ == "__main__":
    model = ref.build_model()
    tokens = [3, 7, 1][:ref.T]
    eng = NativeEngine(8 * 1024 * 1024)
    logits = forward_native(model, tokens, eng)
    print("native logits:", logits)
    print("ref    logits:", ref.forward(model, tokens))
