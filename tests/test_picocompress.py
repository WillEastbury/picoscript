#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compress.PicoCompress is the real picocompress codec, vendored byte-identically
into all of picoscript's runtimes. We verify the Python VM, the JS VM and the
native C VM each produce output byte-identical to the picocompress library itself
(picocompress.py == picocompress.mjs == picocompress.c, the user's cross-platform
ports), and that every path round-trips.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402
import picocompress  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")

SAMPLES = [b"", b"a", b"abc", b"The quick brown fox jumps over the lazy dog " * 8,
           bytes(range(64)) * 3, b'{"json":"value","items":[1,2,3,4,5]} ' * 6,
           b"<html><head><title>x</title></head><body>hi</body></html>" * 3]


def _ensure_c_vm():
    if os.path.exists(VM_EXE):
        return True
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def _span_prog(data, method):
    lines = [f"Memory.Set({i}, {b});" for i, b in enumerate(data)]
    lines += [f"int z = Span.Make(0, {len(data)});",
              f"int out = Compress.{method}(z);", "Io.Write(out);"]
    return "\n".join(lines) + "\n"


def _py(words):
    h = HostApi(); vm = PicoVM(host=h); vm.load(words); vm.run()
    return b"".join(vm.output)


def _node(exe_args, words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(exe_args, input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def _js(words):
    return _node(["node", os.path.join(VM_DIR, "picovm_run.js")], words)


def _c(words):
    return _node([VM_EXE], words)


def test_picocompress_py_matches_library():
    for s in SAMPLES:
        words = lower_to_bytecode_safe(compile_c(_span_prog(s, "PicoCompress")))
        assert _py(words) == picocompress.compress(s), f"Python VM != library (len {len(s)})"


def test_picocompress_py_equals_js_equals_c_equals_library():
    have_c = _ensure_c_vm()
    for s in SAMPLES:
        words = lower_to_bytecode_safe(compile_c(_span_prog(s, "PicoCompress")))
        lib = picocompress.compress(s)
        py, js = _py(words), _js(words)
        assert py == lib, f"Python VM != library (len {len(s)})"
        assert js == lib, f"JS VM != library (len {len(s)})"
        if have_c:
            assert _c(words) == lib, f"C VM != library (len {len(s)})"


def test_picocompress_roundtrip_all_vms():
    have_c = _ensure_c_vm()
    for s in SAMPLES:
        rt = _span_prog(s, "PicoCompress").replace(
            "Compress.PicoCompress(z)", "Compress.PicoDecompress(Compress.PicoCompress(z))")
        words = lower_to_bytecode_safe(compile_c(rt))
        assert _py(words) == s, f"Python VM round-trip (len {len(s)})"
        assert _js(words) == s, f"JS VM round-trip (len {len(s)})"
        if have_c:
            assert _c(words) == s, f"C VM round-trip (len {len(s)})"


def test_picocompress_actually_compresses():
    big = b"repeat this phrase many times over and over " * 40
    assert len(picocompress.compress(big)) < len(big) // 3


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS picocompress: Compress.PicoCompress == the picocompress library on "
          "the Python VM == JS VM == C VM (round-trip on all)")


if __name__ == "__main__":
    main()
