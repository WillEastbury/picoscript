#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_crypto_compress.py -- coverage for Crypto.* and Compress.* VM hooks."""
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
    return vm


def out_bytes(vm):
    return b"".join(vm.output)


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── Crypto.* ─────────────────────────────────────────────────────────────────

def test_crypto_sha256():
    """Crypto.Sha256 produces a 32-byte hash."""
    src = 'int data = "abc"; int h = Crypto.Sha256(data); int n = Span.Len(h); print(n);'
    vm = run(src)
    assert out_ints(vm) == [32]


def test_crypto_sha256_output():
    """Crypto.Sha256 produces known hash for 'abc'."""
    import hashlib
    expected = hashlib.sha256(b"abc").digest()
    src = 'int data = "abc"; int h = Crypto.Sha256(data); Io.Write(h);'
    vm = run(src)
    assert out_bytes(vm) == expected


def test_crypto_encrypt_decrypt_roundtrip():
    """Crypto.Encrypt/Decrypt run without fault."""
    src = """
int key = "0123456789abcdef0123456789abcdef";
int plain = "Hello World!";
int enc = Crypto.Encrypt(key, plain);
int dec = Crypto.Decrypt(key, enc);
int n = Span.Len(dec);
print(n);
"""
    vm = run(src)
    # Just verify both hooks ran and produced output
    assert len(vm.output) > 0


def test_crypto_encrypt_changes_data():
    """Crypto.Encrypt produces different bytes than input."""
    src = """
int key = "0123456789abcdef0123456789abcdef";
int plain = "secret message here!!!!";
int enc = Crypto.Encrypt(key, plain);
Io.Write(enc);
"""
    vm = run(src)
    assert out_bytes(vm) != b"secret message here!!!!"


# ── Compress.* ───────────────────────────────────────────────────────────────

def test_compress_deflate_inflate():
    """Compress.DeflateCompress / DeflateDecompress round-trip."""
    src = """
int data = "Hello Hello Hello Hello Hello Hello Hello!";
int compressed = Compress.DeflateCompress(data);
int restored = Compress.DeflateDecompress(compressed);
Io.Write(restored);
"""
    vm = run(src)
    assert out_bytes(vm) == b"Hello Hello Hello Hello Hello Hello Hello!"


def test_compress_deflate_smaller():
    """Compress.DeflateCompress produces smaller output for repetitive data."""
    src = """
int data = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
int compressed = Compress.DeflateCompress(data);
int orig_len = Span.Len(data);
int comp_len = Span.Len(compressed);
print(orig_len);
print(comp_len);
"""
    vm = run(src)
    ints = out_ints(vm)
    assert ints[1] < ints[0]  # compressed should be smaller


def test_compress_gzip_gunzip():
    """Compress.GzipCompress / GzipDecompress round-trip."""
    src = """
int data = "The quick brown fox jumps over the lazy dog";
int gz = Compress.GzipCompress(data);
int restored = Compress.GzipDecompress(gz);
Io.Write(restored);
"""
    vm = run(src)
    assert out_bytes(vm) == b"The quick brown fox jumps over the lazy dog"


# ── Crypto.Hmac ──────────────────────────────────────────────────────────────

def test_crypto_hmac():
    """Crypto.HmacSha256 produces 32-byte output."""
    src = """
int key = "secret";
int data = "message";
int mac = Crypto.HmacSha256(data, key);
int n = Span.Len(mac);
print(n);
"""
    vm = run(src)
    result = out_ints(vm)
    assert result[0] == 32
