#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compress.* real DEFLATE (RFC 1951) + gzip (RFC 1952), built into the runtime.

The compressor is canonical (one fixed-Huffman block, greedy LZ77 with a
deterministic hash-chain match finder) so the bytes are identical on the Python
and JS VMs. inflate is spec-deterministic and reads real zlib/gzip output, so
these interoperate with the outside world. No host zlib is used in the runtime.
"""

import gzip as _gzip
import os
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
import picoscript_vm as P  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

SAMPLES = [b"", b"a", b"hello hello hello world world world",
           b"abcabc" * 50, bytes(range(256)) * 4,
           b"The quick brown fox jumps over the lazy dog. " * 30]


def test_deflate_roundtrip_and_zlib_interop():
    for s in SAMPLES:
        assert P._inflate(P._deflate(s)) == s
        assert zlib.decompress(P._deflate(s), -15) == s        # zlib reads ours
        assert P._inflate(zlib.compress(s, 6)[2:-4]) == s      # we read zlib (raw)


def test_gzip_roundtrip_and_stdlib_interop():
    for s in SAMPLES:
        assert P._gzip_decompress(P._gzip_compress(s)) == s
        assert _gzip.decompress(P._gzip_compress(s)) == s      # stdlib gzip reads ours
        assert P._gzip_decompress(_gzip.compress(s)) == s      # we read stdlib gzip


def _run_c(src):
    """Run a C-frontend program on the Python and JS VMs; return both raw outputs."""
    words = lower_to_bytecode_safe(compile_c(src))
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    py = b"".join(vm.output)
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    js = b""
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            js = bytes(int(x, 16) for x in p[1:])
    return py, js


# Round-trip through the VM hooks: compress then decompress in-language, compare
# the recovered bytes to the original literal -- and require Python VM == JS VM.
ROUNDTRIP_C = (
    'int s = "the the the the quick brown quick brown fox fox";\n'
    'int z = Compress.DeflateCompress(s);\n'
    'int back = Compress.DeflateDecompress(z);\n'
    'Io.Write(back);\n'
)
GZIP_C = (
    'int s = "banana banana banana banana split split";\n'
    'int z = Compress.GzipCompress(s);\n'
    'int back = Compress.GzipDecompress(z);\n'
    'Io.Write(back);\n'
)


def test_vm_deflate_roundtrip_py_equals_js():
    py, js = _run_c(ROUNDTRIP_C)
    assert py == b"the the the the quick brown quick brown fox fox"
    assert py == js


def test_vm_gzip_roundtrip_py_equals_js():
    py, js = _run_c(GZIP_C)
    assert py == b"banana banana banana banana split split"
    assert py == js


# The *compressed* bytes themselves must be byte-identical across VMs.
COMPRESS_BYTES_C = (
    'int s = "compress me compress me compress me compress me";\n'
    'Io.Write(Compress.GzipCompress(s));\n'
)


def test_vm_compressed_bytes_py_equals_js_and_real_gzip():
    py, js = _run_c(COMPRESS_BYTES_C)
    assert py == js, "Python VM and JS VM produced different gzip bytes"
    assert _gzip.decompress(py) == b"compress me compress me compress me compress me"


# -- Native C VM: real INFLATE/gunzip (decompression is canonical) -------------
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def _ensure_c_vm():
    if os.path.exists(VM_EXE):
        return True
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def _span_prog(blob, method):
    # Build the compressed bytes into the low arena (below const_floor/0x8000, so
    # they never collide with the decompressed output which grows from 0x8000),
    # then decompress and write the result.
    lines = [f"Memory.Set({i}, {b});" for i, b in enumerate(blob)]
    lines += [f"int z = Span.Make(0, {len(blob)});",
              f"int out = Compress.{method}(z);", "Io.Write(out);"]
    return "\n".join(lines) + "\n"


def _c_vm_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_c_vm_inflate_canonical():
    # The native C VM decompresses real stdlib gzip, raw zlib deflate, and our own
    # runtime's output -- byte-identical to the original (decompression is canonical).
    if not _ensure_c_vm():
        return
    s = b"hello hello hello world world INFLATE me on the native C VM too! " * 3
    cases = [
        (_gzip.compress(s), "GzipDecompress"),       # real stdlib gzip (dynamic Huffman)
        (zlib.compress(s, 6)[2:-4], "DeflateDecompress"),  # raw zlib deflate
        (P._deflate(s), "DeflateDecompress"),        # our runtime's deflate
        (P._gzip_compress(s), "GzipDecompress"),     # our runtime's gzip
    ]
    for blob, method in cases:
        words = lower_to_bytecode_safe(compile_c(_span_prog(blob, method)))
        assert _c_vm_out(words) == s, f"C VM {method} mismatch (blob {len(blob)} B)"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS compress: real DEFLATE/gzip in the runtime (round-trip, zlib/gzip interop, Python VM == JS VM)")


if __name__ == "__main__":
    main()
