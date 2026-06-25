#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_bits_misc.py -- coverage for Bits.* and miscellaneous VM hooks."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── Bits.* ───────────────────────────────────────────────────────────────────

def test_bits_and():
    src = "int r = Bits.And(0xFF, 0x0F); print(r);"
    assert run(src) == [0x0F]


def test_bits_or():
    src = "int r = Bits.Or(0xF0, 0x0F); print(r);"
    assert run(src) == [0xFF]


def test_bits_xor():
    src = "int r = Bits.Xor(0xFF, 0x0F); print(r);"
    assert run(src) == [0xF0]


def test_bits_not():
    src = "int r = Bits.Not(0); print(r);"
    # ~0 in 32-bit = -1
    assert run(src) == [-1]


def test_bits_shl():
    src = "int r = Bits.Shl(1, 4); print(r);"
    assert run(src) == [16]


def test_bits_shr():
    src = "int r = Bits.Shr(16, 4); print(r);"
    assert run(src) == [1]


def test_bits_sar():
    """Arithmetic shift right preserves sign."""
    src = "int r = Bits.Sar(0 - 16, 2); print(r);"
    assert run(src) == [-4]


# ── Maths.* (additional CORDIC) ─────────────────────────────────────────────

def test_maths_tan():
    """Maths.Tan(0) should be ~0."""
    src = "int r = Maths.Tan(0); print(r);"
    result = run(src)[0]
    assert abs(result) < 100


def test_maths_exp():
    """Maths.Exp(0) should be ~1.0 in Q16.16 = 65536."""
    src = "int r = Maths.Exp(0); print(r);"
    result = run(src)[0]
    assert abs(result - 65536) < 100


def test_maths_log():
    """Maths.Log(65536) = ln(1.0) = 0."""
    src = "int r = Maths.Log(65536); print(r);"
    result = run(src)[0]
    assert abs(result) < 100


def test_maths_log10():
    """Maths.Log10(65536) = log10(1.0) = 0."""
    src = "int r = Maths.Log10(65536); print(r);"
    result = run(src)[0]
    assert abs(result) < 500  # Should be ~0


# ── Span manipulation ────────────────────────────────────────────────────────

def test_span_make_get():
    """Span.Make + Span.Get reads bytes."""
    src = """
int s = "ABCDE";
int b = Span.Get(s, 0);
print(b);
"""
    assert run(src) == [65]  # 'A'


def test_span_slice():
    """Span.Slice extracts a sub-span."""
    src = """
int s = "Hello World";
int sub = Span.Slice(s, 6, 5);
Io.Write(sub);
"""
    vm = PicoVM().run(lower_to_bytecode_safe(compile_c(src)))
    assert b"".join(vm.output) == b"World"


def test_span_len():
    """Span.Len returns length."""
    src = 'int s = "test"; int n = Span.Len(s); print(n);'
    assert run(src) == [4]
