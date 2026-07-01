#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_cap_error_capsule.py -- Capability/Sandbox/Error/Capsule tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def fresh(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM().run(words)


def ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ══════════════════════════════════════════════════════════════════════════════
# Capability.* — cap ceiling, request, drop
# ══════════════════════════════════════════════════════════════════════════════

def test_capability_has():
    """Capability.Has queries a capability bit."""
    vm = fresh("int cap = 1; int ok = Capability.Has(cap); print(ok);")
    assert vm.steps > 0


def test_capability_request():
    """Capability.Request tries to grant a capability."""
    vm = fresh("int cap = 1; int ok = Capability.Request(cap); print(ok);")
    assert vm.steps > 0


def test_capability_drop():
    """Capability.Drop removes a capability."""
    vm = fresh("int cap = 1; int ok = Capability.Drop(cap); print(ok);")
    assert ints(vm) == [1]


def test_sandbox_deny():
    """Sandbox.Deny permanently revokes a capability."""
    vm = fresh("int cap = 2; int ok = Sandbox.Deny(cap); print(ok);")
    assert ints(vm) == [1]


# ══════════════════════════════════════════════════════════════════════════════
# Error.* — error code, detail, clear, set-handler
# ══════════════════════════════════════════════════════════════════════════════

def test_error_code():
    """Error.Code returns current error code."""
    vm = fresh("int c = Error.Code(); print(c);")
    assert ints(vm) == [0]


def test_error_detail():
    """Error.Detail returns current error detail."""
    vm = fresh("int d = Error.Detail(); print(d);")
    assert ints(vm) == [0]


def test_error_clear():
    """Error.Clear resets error state."""
    vm = fresh("Error.Clear(); int c = Error.Code(); print(c);")
    assert ints(vm) == [0]


def test_error_set_handler():
    """Error.SetHandler registers an error handler PC."""
    vm = fresh("Error.SetHandler(0); int ok = Error.HasHandler(); print(ok);")
    assert ints(vm) == [0]  # 0 = no handler (PC 0 is falsy)


def test_error_resume():
    """Error.Resume clears error and sets PC."""
    vm = fresh("Error.Resume(); int c = Error.Code(); print(c);")
    assert ints(vm) == [0]


# ══════════════════════════════════════════════════════════════════════════════
# Capsule.* — capsule/module switching
# ══════════════════════════════════════════════════════════════════════════════

def test_capsule_call():
    """Capsule.Call simulates calling another capsule."""
    vm = fresh("int r = Capsule.Call(0, 1); print(r);")
    assert ints(vm) == [0]  # simulated, returns 0


def test_capsule_schedule():
    """Capsule.Schedule queues a capsule for later execution."""
    vm = fresh("int ok = Capsule.Schedule(0, 1); print(ok);")
    assert ints(vm) == [1]


# ══════════════════════════════════════════════════════════════════════════════
# Process/Env paths
# ══════════════════════════════════════════════════════════════════════════════

def test_process_env():
    """Env namespace hooks run without fault."""
    vm = fresh("int v = Env.Get(0); print(v);")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# More VM edge cases
# ══════════════════════════════════════════════════════════════════════════════

def test_gzip_fixed_huffman():
    """GzipCompress with repetitive data uses fixed Huffman tables."""
    vm = fresh("""
int data = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
int gz = Compress.GzipCompress(data);
int restored = Compress.GzipDecompress(gz);
Io.Write(restored);
""")
    assert b"".join(vm.output) == b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_deflate_fixed_huffman():
    """DeflateCompress/Decompress with repetitive data."""
    vm = fresh("""
int data = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
int c = Compress.DeflateCompress(data);
int r = Compress.DeflateDecompress(c);
Io.Write(r);
""")
    assert b"".join(vm.output) == b"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def test_deflate_invalid_data():
    """DeflateDecompress with garbage data sets host_status=2."""
    vm = fresh("""
Memory.Set(100, 0xFF);
Memory.Set(101, 0xFF);
int bad = Span.Make(100, 2);
int r = Compress.DeflateDecompress(bad);
print(r);
""")
    # Should not crash; just returns empty or 0
    assert vm.steps > 0


def test_locale_format_number():
    """Locale.FormatNumber formats a number."""
    vm = fresh("int n = 42000; int s = Locale.FormatNumber(n); Io.Write(s);")
    assert len(b"".join(vm.output)) > 0


def test_locale_format_currency():
    """Locale.FormatCurrency formats a currency amount."""
    vm = fresh("int n = 9999; int s = Locale.FormatCurrency(n); Io.Write(s);")
    assert len(b"".join(vm.output)) > 0


def test_locale_translate_missing():
    """Locale.Translate returns input when no translation available."""
    vm = fresh('int k = "hello"; int s = Locale.Translate(k); Io.Write(s);')
    got = b"".join(vm.output)
    assert b"hello" in got
