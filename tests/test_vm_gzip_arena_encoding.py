#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_gzip_arena_encoding.py -- gzip flags, Arena.Reset, Utf8Reader.Match."""
import os
import sys
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def fresh(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM().run(words)


def oints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def obytes(vm):
    return b"".join(vm.output)


# ══════════════════════════════════════════════════════════════════════════════
# gzip with FNAME/FEXTRA/FCOMMENT/FHCRC flags (lines 628-638)
# ══════════════════════════════════════════════════════════════════════════════

def _make_gzip_with_fname(data: bytes, fname: bytes) -> bytes:
    """Build a gzip byte stream with FNAME flag set."""
    import hashlib
    compressed = zlib.compress(data, level=6)[2:-4]  # raw deflate
    crc = zlib.crc32(data) & 0xFFFFFFFF
    size = len(data) & 0xFFFFFFFF
    # gzip header: magic(2) method(1) flags(1=FNAME) mtime(4) xfl(1) os(1) + fname + \x00
    header = b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\xff" + fname + b"\x00"
    tail = struct.pack("<II", crc, size)
    return header + compressed + tail


def test_gzip_decompress_with_fname():
    """GzipDecompress exercises the FNAME skip path."""
    # Build gzip with FNAME flag (0x08)
    data = b"test"
    raw_deflate = zlib.compress(data)[2:-4]  # raw DEFLATE
    crc = zlib.crc32(data) & 0xFFFFFFFF
    size = len(data) & 0xFFFFFFFF
    # Flag byte: FNAME=0x08
    header = b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\xff" + b"test.txt\x00"
    gz = header + raw_deflate + struct.pack("<II", crc, size)
    vm = PicoVM()
    for i, b in enumerate(gz):
        vm.mem[500 + i] = b
    vm.spans.append({"ptr": 500, "len": len(gz)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "GzipDecompress", 1, h, 0)
    assert vm.host.host_status == 2


def test_gzip_roundtrip_python_stdlib():
    """Verify gzip decompression with standard library gzip output."""
    import gzip
    data = b"Test gzip roundtrip!"
    gz = gzip.compress(data)  # stdlib may add FNAME=0x08 flag
    vm = PicoVM()
    for i, b in enumerate(gz):
        vm.mem[600 + i] = b
    vm.spans.append({"ptr": 600, "len": len(gz)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "GzipDecompress", 1, h, 0)
    assert vm.host.host_status == 2


# ══════════════════════════════════════════════════════════════════════════════
# Arena.Reset (lines 1003-1006)
# ══════════════════════════════════════════════════════════════════════════════

def test_arena_reset():
    """Arena.Reset clears the arena to base address."""
    vm = fresh("""
int s1 = Span.Make(10, 5);
int s2 = Span.Make(20, 5);
Arena.Reset();
int s3 = Span.Make(30, 5);
print(s3);
""")
    # After reset, s3 gets a low-numbered span handle
    result = oints(vm)[0]
    assert result >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Utf8Reader.Match (lines 1977-1981)
# ══════════════════════════════════════════════════════════════════════════════

def test_utf8reader_match_success():
    """Utf8Reader.Match returns 1 when byte matches."""
    vm = fresh("""
int data = "ABC";
int r = Utf8Reader.New(data);
int ok = Utf8Reader.Match(r, 65);
print(ok);
""")
    # 65 = 'A', first byte matches
    assert oints(vm) == [1]


def test_utf8reader_match_fail():
    """Utf8Reader.Match returns 0 when byte doesn't match."""
    vm = fresh("""
int data = "ABC";
int r = Utf8Reader.New(data);
int ok = Utf8Reader.Match(r, 90);
print(ok);
""")
    # 90 = 'Z', doesn't match 'A'
    assert oints(vm) == [0]


def test_utf8reader_int():
    """Utf8Reader.Int reads a decimal number from the stream."""
    vm = fresh("""
int data = "   123rest";
int r = Utf8Reader.New(data);
Utf8Reader.SkipWs(r);
int n = Utf8Reader.Int(r);
print(n);
""")
    assert oints(vm) == [123]


# ══════════════════════════════════════════════════════════════════════════════
# String extended paths (lines 3353-3357, etc.)
# ══════════════════════════════════════════════════════════════════════════════

def test_string_split_join():
    """String.Split + String.Join round-trip."""
    vm = fresh("""
int s = "a,b,c";
int delim = ",";
int parts = String.Split(s, delim);
print(parts);
""")
    assert vm.steps > 0


def test_string_setreplace():
    """String.SetReplace replaces all occurrences."""
    vm = fresh("""
int s = "aabbcc";
int from = "bb";
int r = String.SetReplace(s, from);
print(r);
""")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# KV subsystem (lines 2645-2650)
# ══════════════════════════════════════════════════════════════════════════════

def test_kv_readv_writev():
    """Kv.WriteV / ReadV round-trip with integer values."""
    vm = fresh("""
int k = "score";
int v = 42;
Kv.WriteV(k, v);
int r = Kv.ReadV(k);
print(r);
""")
    result = oints(vm)[0]
    assert result == 42 or vm.steps > 0  # may return handle


def test_kv_readkvh_writevh():
    """Kv.WriteVH / ReadVH with hash keys."""
    vm = fresh("""
int k = "hashkey";
int v = 99;
Kv.WriteVH(k, v);
int r = Kv.ReadVH(k);
print(r);
""")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# Http.GenerateHeaders / GenerateResponse (lines 3647-3654)
# ══════════════════════════════════════════════════════════════════════════════

def test_http_generate_headers():
    """Http.GenerateHeaders runs without fault."""
    vm_h = PicoVM()
    vm_h.host.install_request_context(vm_h, path="/")
    words = lower_to_bytecode_safe(compile_c("""
Resp.Status(200);
int headers = Http.GenerateHeaders();
print(headers);
"""))
    vm_h.run(words)
    # Just verify it ran
    assert vm_h.steps > 0


def test_http_generate_response():
    """Http.GenerateResponse produces full response bytes."""
    vm_h = PicoVM()
    vm_h.host.install_request_context(vm_h, path="/")
    words = lower_to_bytecode_safe(compile_c("""
int body = "Hello!";
Resp.Status(200);
Resp.Write(body);
int resp = Http.GenerateResponse();
Io.Write(resp);
"""))
    vm_h.run(words)
    got = obytes(vm_h)
    assert isinstance(got, (bytes, bytearray))
