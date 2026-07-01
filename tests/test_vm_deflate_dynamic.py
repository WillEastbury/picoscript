#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_deflate_dynamic.py -- force DEFLATE dynamic Huffman (btype=2)."""
import os, sys, zlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_vm import PicoVM  # noqa: E402


def _make_dynamic_deflate(data: bytes) -> bytes:
    """Produce raw DEFLATE with dynamic Huffman tables (btype=2) using Z_RLE strategy."""
    obj = zlib.compressobj(level=1, strategy=zlib.Z_RLE, wbits=-15)
    return obj.compress(data) + obj.flush()


def test_deflate_dynamic_huffman_tables():
    """_read_dynamic: DEFLATE with dynamic Huffman tables (btype=2, lines 584-612)."""
    data = bytes(i % 97 for i in range(500))
    compressed = _make_dynamic_deflate(data)

    # Verify it's actually btype=2
    assert (compressed[0] >> 1) & 3 == 2, f"Expected btype=2, got {(compressed[0]>>1)&3}"

    vm = PicoVM()
    base = 0x2000
    for i, b in enumerate(compressed):
        vm.mem[base + i] = b
    vm.spans.append({"ptr": base, "len": len(compressed)})
    h = len(vm.spans) - 1
    vm.regs[2] = h  # put span handle in register 2 for rs1=2

    vm.host._compresslib(vm, "DeflateDecompress", 1, 2, 0)

    result_h = vm.regs[1]
    result_s = vm.spans[result_h] if 0 < result_h < len(vm.spans) else None
    if result_s and result_s["len"] > 0:
        got = bytes(vm.mem[result_s["ptr"]:result_s["ptr"] + min(result_s["len"], len(data))])
        assert got == data[:len(got)]
    assert result_s is not None and result_s["len"] > 0


def test_deflate_btype2_short():
    """Short varied data with Z_RLE also produces dynamic Huffman."""
    data = bytes(range(100))
    compressed = _make_dynamic_deflate(data)

    vm = PicoVM()
    base = 0x3000
    for i, b in enumerate(compressed):
        vm.mem[base + i] = b
    vm.spans.append({"ptr": base, "len": len(compressed)})
    h = len(vm.spans) - 1
    vm.regs[2] = h  # span handle in register 2

    vm.host._compresslib(vm, "DeflateDecompress", 1, 2, 0)
    result_h = vm.regs[1]
    result_s = vm.spans[result_h] if 0 < result_h < len(vm.spans) else None
    if result_s and result_s["len"] > 0:
        got = bytes(vm.mem[result_s["ptr"]:result_s["ptr"] + result_s["len"]])
        assert got == data
    assert result_s is not None and result_s["len"] > 0
