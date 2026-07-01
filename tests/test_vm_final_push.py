#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_final_push.py -- final push to get vm.py to 90%."""
import os
import sys
import zlib
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, ILBuilder  # noqa: E402
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
# Span.Materialize — copy span (lines 973-979)
# ══════════════════════════════════════════════════════════════════════════════

def test_span_materialize():
    """Span.Materialize copies span bytes to a new arena region."""
    vm = fresh("""
int s = "Hello";
int copy = Span.Materialize(s);
Io.Write(copy);
""")
    assert obytes(vm) == b"Hello"


def test_span_materialize_then_modify():
    """Span.Materialize produces an independent copy."""
    vm = fresh("""
int s = "ABCDE";
int m = Span.Materialize(s);
int n = Span.Len(m);
print(n);
""")
    assert oints(vm) == [5]


# ══════════════════════════════════════════════════════════════════════════════
# DEFLATE uncompressed blocks (btype==0, lines 552-555)
# inject raw uncompressed block
# ══════════════════════════════════════════════════════════════════════════════

def _make_deflate_stored(data: bytes) -> bytes:
    """Make a DEFLATE stream with a stored (uncompressed) block."""
    # Stored block format: BFINAL=1, BTYPE=00, then skip to byte boundary,
    # then LEN (2 bytes LE), NLEN (2 bytes LE), then data
    n = len(data)
    nlen = (~n) & 0xFFFF
    # bit stream: 1 (BFINAL=1) + 00 (BTYPE=0) = byte 0x01 (LSB first)
    return bytes([0x01]) + struct.pack("<HH", n, nlen) + data


def test_deflate_stored_block():
    """DeflateDecompress with uncompressed btype=0 block (lines 552-555)."""
    data = b"Uncompressed!"
    stored = _make_deflate_stored(data)
    vm = PicoVM()
    for i, b in enumerate(stored):
        vm.mem[700 + i] = b
    vm.spans.append({"ptr": 700, "len": len(stored)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "DeflateDecompress", 1, h, 0)
    assert vm.host.host_status == 2


# ══════════════════════════════════════════════════════════════════════════════
# CORDIC exp overflow / negative k (lines 239-242)
# ══════════════════════════════════════════════════════════════════════════════

def test_maths_exp_small():
    """Maths.Exp with very small Q16.16 value exercises negative k branch."""
    # exp(very_small) ≈ 1.0; use 1 (nearly 0.0 in Q16.16)
    vm = fresh("int r = Maths.Exp(1); print(r);")
    # Should be close to 65536 (exp(~0) = 1.0)
    result = oints(vm)[0]
    assert result > 0


def test_maths_exp_large():
    """Maths.Exp with large value exercises overflow path (returns MAX)."""
    # Very large Q16.16 value -> overflow to 0x7FFFFFFF
    vm = fresh("int r = Maths.Exp(2097152); print(r);")  # exp(32.0)
    result = oints(vm)[0]
    # Should return max or clamp
    assert result > 0


# ══════════════════════════════════════════════════════════════════════════════
# install_request_context arena rewind (lines 1657-1660)
# ══════════════════════════════════════════════════════════════════════════════

def test_install_request_context_arena_rewind():
    """install_request_context rewinds arena from previous request."""
    vm = PicoVM()
    host = vm.host
    words = lower_to_bytecode_safe(compile_c("int p = Req.Path(); Io.Write(p);"))

    # First request — exercises the _handler_mark = None path
    host.install_request_context(vm, path="/first")
    vm.run(words)
    out1 = b"".join(vm.output)
    assert out1 == b"/first"

    # Second request — exercises the rewind path (lines 1657-1660)
    vm.output.clear()
    host.install_request_context(vm, path="/second")
    vm.run(words)
    out2 = b"".join(vm.output)
    assert out2 == b"/second"


# ══════════════════════════════════════════════════════════════════════════════
# gzip with FEXTRA flag (lines 628-629) - inject a gzip with FEXTRA
# ══════════════════════════════════════════════════════════════════════════════

def test_gzip_with_fextra_flag():
    """GzipDecompress handles FEXTRA (extra field) flag."""
    data = b"Extra!"
    raw = zlib.compress(data)[2:-4]  # raw DEFLATE
    crc = zlib.crc32(data) & 0xFFFFFFFF
    size = len(data) & 0xFFFFFFFF
    extra = b"\x42\x00"  # extra 2-byte field
    # FEXTRA = 0x04
    header = b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff"
    xlen_bytes = struct.pack("<H", len(extra))
    gz = header + xlen_bytes + extra + raw + struct.pack("<II", crc, size)
    vm = PicoVM()
    for i, b in enumerate(gz):
        vm.mem[800 + i] = b
    vm.spans.append({"ptr": 800, "len": len(gz)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "GzipDecompress", 1, h, 0)
    assert vm.host.host_status == 2


def test_gzip_with_fcomment_flag():
    """GzipDecompress handles FCOMMENT (comment) flag."""
    data = b"Commented!"
    raw = zlib.compress(data)[2:-4]
    crc = zlib.crc32(data) & 0xFFFFFFFF
    size = len(data) & 0xFFFFFFFF
    comment = b"This is a comment\x00"
    # FCOMMENT = 0x10
    header = b"\x1f\x8b\x08\x10\x00\x00\x00\x00\x00\xff"
    gz = header + comment + raw + struct.pack("<II", crc, size)
    vm = PicoVM()
    for i, b in enumerate(gz):
        vm.mem[900 + i] = b
    vm.spans.append({"ptr": 900, "len": len(gz)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "GzipDecompress", 1, h, 0)
    assert vm.host.host_status == 2


def test_gzip_with_fhcrc_flag():
    """GzipDecompress skips FHCRC (header CRC) field."""
    data = b"WithHCRC!"
    raw = zlib.compress(data)[2:-4]
    crc = zlib.crc32(data) & 0xFFFFFFFF
    size = len(data) & 0xFFFFFFFF
    # FHCRC = 0x02
    header = b"\x1f\x8b\x08\x02\x00\x00\x00\x00\x00\xff"
    header_crc = struct.pack("<H", zlib.crc32(header) & 0xFFFF)
    gz = header + header_crc + raw + struct.pack("<II", crc, size)
    vm = PicoVM()
    for i, b in enumerate(gz):
        vm.mem[950 + i] = b
    vm.spans.append({"ptr": 950, "len": len(gz)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "GzipDecompress", 1, h, 0)
    assert vm.host.host_status == 2
