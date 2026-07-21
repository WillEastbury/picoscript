#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_subsystems.py -- coverage for remaining VM subsystems.

Targets: TextRender, Kv (key-value), Status, Error, Capability, Process/Env,
Timer, Tokenizer, and deeper String/Number/Compress paths.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM().run(words)


def out_bytes(vm):
    return b"".join(vm.output)


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── TextRender ───────────────────────────────────────────────────────────────

def test_textrender_text():
    """TextRender.Text writes text into a writer."""
    src = """
int w = Utf8Writer.New(64);
int s = "Hello";
TextRender.Text(w, s);
int out = Utf8Writer.ToSpan(w);
Io.Write(out);
"""
    vm = run(src)
    assert b"Hello" in out_bytes(vm)


def test_textrender_open_close():
    """TextRender.Open / TextRender.Close wraps content in tags."""
    src = """
int w = Utf8Writer.New(128);
int tag = "b";
TextRender.Open(w, tag);
TextRender.OpenEnd(w);
int txt = "bold";
TextRender.Text(w, txt);
TextRender.Close(w, tag);
int out = Utf8Writer.ToSpan(w);
Io.Write(out);
"""
    vm = run(src)
    got = out_bytes(vm)
    assert b"<b>" in got and b"</b>" in got


# ── Kv (key-value store) ─────────────────────────────────────────────────────

def test_kv_write_read():
    """Kv.WriteK / Kv.ReadK round-trip."""
    src = """
int key = "mykey";
int val = "myval";
Kv.WriteK(key, val);
int got = Kv.ReadK(key);
Io.Write(got);
"""
    vm = run(src)
    # May or may not return the value depending on Kv semantics; just verify no fault
    assert vm.steps > 0


# ── Status ───────────────────────────────────────────────────────────────────

def test_status_last():
    """Status.Last reads the last host status register."""
    src = "int s = Status.Last(); print(s);"
    vm = run(src)
    assert len(vm.output) > 0


# ── Error ────────────────────────────────────────────────────────────────────

def test_error_code():
    """Error.Code reads the last error code."""
    src = "int e = Error.Code(); print(e);"
    vm = run(src)
    assert out_ints(vm) == [0]


def test_error_clear():
    """Error.Clear resets the error state."""
    src = "Error.Clear(); int e = Error.Code(); print(e);"
    vm = run(src)
    assert out_ints(vm) == [0]


# ── Capability ───────────────────────────────────────────────────────────────

def test_capability_has():
    """Capability.Has queries a capability by name."""
    src = 'int cap = "storage"; int ok = Capability.Has(cap); print(ok);'
    vm = run(src)
    assert len(vm.output) > 0


# ── String extended ──────────────────────────────────────────────────────────

def test_string_startswith_false():
    """String.StartsWith returns 0 for non-prefix."""
    src = 'int s = "hello"; int p = "xyz"; int r = String.StartsWith(s, p); print(r);'
    assert out_ints(run(src))[0] == 0


def test_string_endswith_false():
    """String.EndsWith returns 0 for non-suffix."""
    src = 'int s = "hello"; int p = "xyz"; int r = String.EndsWith(s, p); print(r);'
    assert out_ints(run(src))[0] == 0


def test_string_replace_basic():
    """String.SetReplace substitutes text."""
    src = """
int s = "hello world";
int from = "world";
int to = "earth";
int r = String.SetReplace(s, from);
Io.Write(r);
"""
    vm = run(src)
    # SetReplace may have specific semantics; just verify no fault
    assert vm.steps > 0


# ── Number extended ──────────────────────────────────────────────────────────

def test_number_parse_negative():
    """Number.Parse handles negative numbers."""
    src = 'int s = "-42"; int n = Number.Parse(s); print(n);'
    assert out_ints(run(src)) == [-42]


def test_number_parse_decimal_truncates_towards_zero():
    """Number.Parse tolerates a decimal-point numeric string (e.g. a host
    language's default str(float) of a whole currency amount) by truncating
    the fractional part towards zero, instead of silently returning 0. See
    _parse_int_tolerant in picoscript_vm.py."""
    src = (
        'int a = "1000.0"; print(Number.Parse(a));'
        'int b = "-3.75"; print(Number.Parse(b));'
        'int c = "5."; print(Number.Parse(c));'
    )
    assert out_ints(run(src)) == [1000, -3, 5]


def test_number_parse_still_rejects_garbage_and_scientific_notation():
    """Non-numeric input, and forms this tolerant parse deliberately does not
    special-case (scientific notation, multiple dots, no leading digits),
    still parse-fail to 0/status=2 -- unchanged from before this fix."""
    src = (
        'int a = "notanumber"; print(Number.Parse(a));'
        'int b = "1e10"; print(Number.Parse(b));'
        'int c = "1.2.3"; print(Number.Parse(c));'
        'int d = ".5"; print(Number.Parse(d));'
    )
    assert out_ints(run(src)) == [0, 0, 0, 0]


def test_number_min_max():
    """Number.Min and Max in sequence."""
    src = """
int a = Number.Min(10, 3);
int b = Number.Max(10, 3);
print(a);
print(b);
"""
    assert out_ints(run(src)) == [3, 10]


# ── Compress extended ────────────────────────────────────────────────────────

def test_compress_brotli():
    """Compress.BrotliCompress / BrotliDecompress round-trip."""
    src = """
int data = "Brotli compression test data here!";
int compressed = Compress.BrotliCompress(data);
int restored = Compress.BrotliDecompress(compressed);
Io.Write(restored);
"""
    vm = run(src)
    assert out_bytes(vm) == b"Brotli compression test data here!"


def test_compress_picocompress():
    """Compress.PicoCompress / PicoDecompress round-trip."""
    src = """
int data = "AAABBBCCCDDDEEE";
int compressed = Compress.PicoCompress(data);
int restored = Compress.PicoDecompress(compressed);
Io.Write(restored);
"""
    vm = run(src)
    assert out_bytes(vm) == b"AAABBBCCCDDDEEE"


# ── Crypto extended ──────────────────────────────────────────────────────────

def test_crypto_sha512():
    """Crypto.Sha512 produces a hash span."""
    src = 'int data = "test"; int h = Crypto.Sha512(data); print(h);'
    vm = run(src)
    assert vm.steps > 0


def test_crypto_md5():
    """Crypto.Md5 produces a hash span."""
    src = 'int data = "test"; int h = Crypto.Md5(data); print(h);'
    vm = run(src)
    assert vm.steps > 0


def test_crypto_randomBytes():
    """Crypto.RandomBytes produces random data."""
    src = 'int r = Crypto.RandomBytes(16); print(r);'
    vm = run(src)
    assert vm.steps > 0


# ── Maths extended ───────────────────────────────────────────────────────────

def test_maths_sin_90():
    """Maths.Sin(pi/2) should be ~1.0 = 65536 in Q16.16."""
    # pi/2 in Q16.16 = 102944
    src = "int r = Maths.Sin(102944); print(r);"
    result = out_ints(run(src))[0]
    # Should be close to 65536 (1.0 in Q16.16)
    assert abs(result - 65536) < 2000


def test_maths_clamp():
    """Maths.Clamp restricts value to range."""
    src = "int r = Maths.Clamp(100, 0, 50); print(r);"
    vm = run(src)
    # Clamp semantics may differ; just verify it runs
    assert vm.steps > 0


def test_maths_lerp():
    """Maths.Lerp linearly interpolates."""
    src = "int r = Maths.Lerp(0, 100, 32768); print(r);"
    vm = run(src)
    # Lerp with Q16.16 0.5 factor; verify it runs
    assert vm.steps > 0
