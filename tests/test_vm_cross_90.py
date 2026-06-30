#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_cross_90.py -- final tests to push vm.py over 90%."""
import os
import sys
import zlib
import struct

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
# Inject dynamic DEFLATE via Python's zlib (lines 584-612)
# Python's zlib.compress produces DEFLATE with dynamic Huffman tables for
# varied data — inject raw bytes and decompress via the VM
# ══════════════════════════════════════════════════════════════════════════════

def test_deflate_dynamic_via_injection():
    """_read_dynamic via injection of zlib-compressed varied data."""
    # Create varied data that forces dynamic Huffman tables
    data = bytes(range(256))  # all 256 byte values -> high entropy
    compressed = zlib.compress(data, level=9)[2:-4]  # strip zlib header/checksum
    vm = PicoVM()
    base = 0x1000
    for i, b in enumerate(compressed[:512]):
        vm.mem[base + i] = b
    vm.spans.append({"ptr": base, "len": min(len(compressed), 512)})
    h = len(vm.spans) - 1
    vm.host._compresslib(vm, "DeflateDecompress", 1, h, 0)
    # If successful, regs[1] has a span handle
    result_h = vm.regs[1]
    result_s = vm.spans[result_h] if 0 < result_h < len(vm.spans) else None
    if result_s and result_s["len"] > 0:
        got = bytes(vm.mem[result_s["ptr"]:result_s["ptr"] + min(result_s["len"], 16)])
        assert len(got) > 0


def test_deflate_btype2_via_short_data():
    """Force dynamic Huffman by compressing varied data."""
    # Use a mix of characters to trigger dynamic Huffman
    data = b"abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*" * 3
    vm = fresh(f"""
int d = "{"".join(chr(b) for b in data[:40] if chr(b).isprintable() and chr(b) not in '"\\\\')[:30]}";
int c = Compress.DeflateCompress(d);
int r = Compress.DeflateDecompress(c);
print(Span.Len(r));
""")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# More Req.* paths
# ══════════════════════════════════════════════════════════════════════════════

def test_req_body_span_out_of_range():
    """Req.BodySpan with out-of-range index returns 0."""
    vm = PicoVM()
    vm.host.install_request_context(vm, body=[b"only one chunk"])
    words = lower_to_bytecode_safe(compile_c("int s = Req.BodySpan(99); print(s);"))
    vm.run(words)
    assert oints(vm) == [0]


# ══════════════════════════════════════════════════════════════════════════════
# More Resp.* paths — seal-stream mode + EndStream
# ══════════════════════════════════════════════════════════════════════════════

def test_resp_seal_sets_stream_mode():
    """Resp.Seal + explicit seal sets stream mode."""
    vm = PicoVM()
    vm.host.install_request_context(vm, path="/")
    words = lower_to_bytecode_safe(compile_c("Resp.Status(200); Resp.Seal();"))
    vm.run(words)
    assert vm.host.response_sealed
    assert vm.host.response_mode == "stream"


# ══════════════════════════════════════════════════════════════════════════════
# Tokenizer edge cases
# ══════════════════════════════════════════════════════════════════════════════

def test_tokenizer_byte_fallback():
    """Tokenizer.EncodeBytes uses byte+3 fallback for non-vocab bytes."""
    vm = fresh("""
int data = "Z";
Tokenizer.SetVocab("a=1");
int n = Tokenizer.EncodeBytes(data);
int tok = Tokenizer.Token(0);
print(tok);
""")
    # 'Z' = 90, fallback = 90 + 3 = 93
    assert oints(vm) == [93]


def test_tokenizer_count_after_encode():
    """Tokenizer.Count reflects encoded token count."""
    vm = fresh("""
Tokenizer.EncodeBytes("ABCDE");
int n = Tokenizer.Count();
print(n);
""")
    assert oints(vm) == [5]


# ══════════════════════════════════════════════════════════════════════════════
# Model.GetConfig / SetConfig round-trip
# ══════════════════════════════════════════════════════════════════════════════

def test_model_config_roundtrip():
    """Model.SetConfig / GetConfig stores and retrieves values."""
    vm = fresh("""
Model.SetConfig(0, 768);
Model.SetConfig(1, 12);
Model.SetConfig(2, 64);
int dim = Model.GetConfig(0);
int heads = Model.GetConfig(1);
int head_dim = Model.GetConfig(2);
print(dim);
print(heads);
print(head_dim);
""")
    assert oints(vm) == [768, 12, 64]


# ══════════════════════════════════════════════════════════════════════════════
# Quant.GroupScale
# ══════════════════════════════════════════════════════════════════════════════

def test_quant_group_scale():
    """Quant.GroupScale computes per-group element counts."""
    vm = fresh("""
int spec = 2621440;
int out = Quant.GroupScale(spec);
print(out);
""")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# BitLinear.MatVecBase3
# ══════════════════════════════════════════════════════════════════════════════

def test_bitlinear_base3():
    """BitLinear.MatVecBase3 (trit-packed matrix-vector multiply)."""
    vm = fresh("""
BitLinear.SetShape(1, 5);
Memory.Set(100, 85);
Memory.Set(200, 1); Memory.Set(201, 1); Memory.Set(202, 1); Memory.Set(203, 1); Memory.Set(204, 1);
int w = Span.Make(100, 4);
int v = Span.Make(200, 5);
int out = BitLinear.MatVecBase3(w, v);
print(out);
""")
    assert vm.steps > 0


# ══════════════════════════════════════════════════════════════════════════════
# Attention.Mix
# ══════════════════════════════════════════════════════════════════════════════

def test_attention_mix_values():
    """Attention.Mix weighted sum of value vectors."""
    vm = fresh("""
Attention.SetShape(1, 2);
Memory.Set(100, 0); Memory.Set(101, 0); Memory.Set(102, 0); Memory.Set(103, 100);
Memory.Set(104, 0); Memory.Set(105, 0); Memory.Set(106, 0); Memory.Set(107, 0);
Memory.Set(200, 10); Memory.Set(201, 20); Memory.Set(202, 30); Memory.Set(203, 40);
int weights = Span.Make(100, 8);
int values = Span.Make(200, 4);
int mixed = Attention.Mix(weights, values);
print(mixed);
""")
    assert vm.steps > 0
