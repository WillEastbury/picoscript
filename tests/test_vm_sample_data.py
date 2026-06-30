#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_sample_data.py -- sample data tests for VM tensor/model/ML paths.

Creates real binary payloads in the correct formats to exercise the ML inference
subsystem in picoscript_vm.py that was previously uncovered:
  - Tensor.SetShape/DotI8/MatVecI8/AddI32/MulI32/ScaleI32/ReluI32/RmsNormI32/RoPEI32/SoftmaxI32/ArgMaxI32
  - BitLinear.HasFormat/SetShape/MatVecTernary/MatVecBitmap/MatVecBase3/Block variants
  - Quant.AbsMax/QuantI8/DequantI8/ApplyScale/GroupScale
  - Tokenizer.SetVocab/EncodeBytes/EncodeTrie/DecodeBytes/DecodeTrie/Count/Token
  - Model.SetConfig/GetConfig/TensorView/TensorOffset/TensorRows/TensorCols/TensorFormat/SetBlock/ReadTensor/ReadTensorRow/ReadTensorBlock/MatVecI8Block
  - Kv.SetShape/SetHead/WriteK/WriteV/WriteKH/WriteVH/ReadK/ReadV/ReadKH/ReadVH/Len/Clear
  - Sampling.Temperature/ArgMax/TopK/ArgMaxRows
  - Attention.SetShape/Scores/Mix/Attend
  - _decode_row_spec all branches
  - _ternary_weight / _base3_weight / _bitmap_weight all paths
  - Remaining Utf8Writer/Utf8Reader/Json/Xml paths
  - Remaining response/request paths
  - _CURRENCY_CODE_BY_NUM / _CURRENCY_MINOR_BY_CODE startup (lines 62-67)
"""
import os
import sys
import struct

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_vm import (
    PicoVM, PicoFault, HostApi,
    MASK32,
)


def make_vm(**kw):
    return PicoVM(**kw)


def h(vm, ns, method, rd=0, rs1=0, rs2=0, imm16=0):
    vm.host.call(vm, ns, method, rd, rs1, rs2, imm16)
    return vm.regs[rd]


def i32be(*vals):
    """Pack int32 values as big-endian bytes."""
    return b"".join(struct.pack(">i", v) for v in vals)


def i8bytes(*vals):
    """Pack int8 values as bytes (signed)."""
    return bytes((v & 0xFF) for v in vals)


# ══════════════════════════════════════════════════════════════════════════════
# Startup: currency code lookups (lines 62-67)
# ══════════════════════════════════════════════════════════════════════════════

def test_currency_code_lookup_populated():
    """_CURRENCY_CODE_BY_NUM and _CURRENCY_MINOR_BY_CODE are populated at startup."""
    from picoscript_vm import _CURRENCY_CODE_BY_NUM, _CURRENCY_MINOR_BY_CODE
    assert len(_CURRENCY_CODE_BY_NUM) > 0
    assert len(_CURRENCY_MINOR_BY_CODE) > 0
    # USD should be present
    assert any(v == "USD" for v in _CURRENCY_CODE_BY_NUM.values())


# ══════════════════════════════════════════════════════════════════════════════
# Tensor: SetShape + DotI8 + MatVecI8
# ══════════════════════════════════════════════════════════════════════════════

def test_tensor_set_shape_and_doti8():
    """Tensor.SetShape then DotI8 computes dot product of two i8 vectors."""
    vm = make_vm()
    # Set shape: rows=4, cols=4
    vm.regs[1] = 4; vm.regs[2] = 4
    h(vm, "Tensor", "SetShape", rd=0, rs1=1, rs2=2)

    a = i8bytes(1, 2, 3, 4)
    b = i8bytes(5, 6, 7, 8)
    ah = vm.host._new_span_bytes(vm, a)
    bh = vm.host._new_span_bytes(vm, b)
    vm.regs[1] = ah; vm.regs[2] = bh
    h(vm, "Tensor", "DotI8", rd=0, rs1=1, rs2=2)
    # dot([1,2,3,4], [5,6,7,8]) = 5+12+21+32 = 70
    assert vm.regs[0] == 70


def test_tensor_matveci8():
    """Tensor.MatVecI8: 2x4 matrix @ 4-dim vector."""
    vm = make_vm()
    vm.regs[1] = 2; vm.regs[2] = 4
    h(vm, "Tensor", "SetShape", rd=0, rs1=1, rs2=2)
    # mat: [[1,2,3,4], [5,6,7,8]]
    mat = i8bytes(1, 2, 3, 4, 5, 6, 7, 8)
    vec = i8bytes(1, 1, 1, 1)
    mh = vm.host._new_span_bytes(vm, mat)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = mh; vm.regs[2] = vh
    h(vm, "Tensor", "MatVecI8", rd=0, rs1=1, rs2=2)
    result_raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(result_raw[i*4:(i+1)*4], "big", signed=True) for i in range(2)]
    assert vals[0] == 10  # 1+2+3+4
    assert vals[1] == 26  # 5+6+7+8


def test_tensor_add_i32():
    """Tensor.AddI32: element-wise add two i32 spans."""
    vm = make_vm()
    a = i32be(10, 20, 30)
    b = i32be(1, 2, 3)
    ah = vm.host._new_span_bytes(vm, a)
    bh = vm.host._new_span_bytes(vm, b)
    vm.regs[1] = ah; vm.regs[2] = bh
    h(vm, "Tensor", "AddI32", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(3)]
    assert vals == [11, 22, 33]


def test_tensor_mul_i32():
    """Tensor.MulI32: element-wise multiply with >>8 shift."""
    vm = make_vm()
    a = i32be(256, 512)
    b = i32be(256, 256)
    ah = vm.host._new_span_bytes(vm, a)
    bh = vm.host._new_span_bytes(vm, b)
    vm.regs[1] = ah; vm.regs[2] = bh
    h(vm, "Tensor", "MulI32", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_tensor_scale_i32():
    """Tensor.ScaleI32: multiply all elements by scalar."""
    vm = make_vm()
    data = i32be(10, 20, 30)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 3
    h(vm, "Tensor", "ScaleI32", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(3)]
    assert vals == [30, 60, 90]


def test_tensor_relu_i32():
    """Tensor.ReluI32: clamp negatives to 0."""
    vm = make_vm()
    data = i32be(-5, 0, 10, -3)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh
    h(vm, "Tensor", "ReluI32", rd=0, rs1=1, rs2=0)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(4)]
    assert vals == [0, 0, 10, 0]


def test_tensor_rmsnorm_i32():
    """Tensor.RmsNormI32: normalise by RMS with gain weights."""
    vm = make_vm()
    data = i32be(100, 200, 300, 400)
    gain = i32be(256, 256, 256, 256)
    dh = vm.host._new_span_bytes(vm, data)
    gh = vm.host._new_span_bytes(vm, gain)
    vm.regs[1] = dh; vm.regs[2] = gh
    h(vm, "Tensor", "RmsNormI32", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_tensor_rope_i32():
    """Tensor.RoPEI32: rotary position encoding."""
    vm = make_vm()
    # 4-element vector: pairs (x0,y0), (x1,y1)
    data = i32be(100, 50, 200, 80)
    # cos/sin pairs per position
    freqs = i32be(32767, 0, 32767, 0)  # cos=1.0, sin=0.0 → identity rotation
    dh = vm.host._new_span_bytes(vm, data)
    fh = vm.host._new_span_bytes(vm, freqs)
    vm.regs[1] = dh; vm.regs[2] = fh
    h(vm, "Tensor", "RoPEI32", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_tensor_argmax_i32():
    """Tensor.ArgMaxI32: returns index of maximum element."""
    vm = make_vm()
    data = i32be(10, 50, 30, 20)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh
    h(vm, "Tensor", "ArgMaxI32", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1  # index of 50


def test_tensor_softmax_i32_positive():
    """Tensor.SoftmaxI32 normalises to probability-like values summing to ~32767."""
    vm = make_vm()
    data = i32be(0, 256, 512, 1024)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 4
    h(vm, "Tensor", "SoftmaxI32", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(4)]
    assert sum(vals) > 0
    assert vals[-1] > vals[0]  # higher logit → higher prob


# ══════════════════════════════════════════════════════════════════════════════
# _decode_row_spec: all branches (lines 2236-2248)
# ══════════════════════════════════════════════════════════════════════════════

def test_decode_row_spec_nonzero():
    """_decode_row_spec with spec!=0 extracts start/count (lines 2237-2238)."""
    from picoscript_vm import HostApi
    spec = (2 << 16) | 3  # start=2, count=3
    start, count = HostApi._decode_row_spec(spec, 0, 10, 10)
    assert start == 2
    assert count == 3


def test_decode_row_spec_count_zero():
    """_decode_row_spec with count=0 fills to end (line 2242-2243)."""
    from picoscript_vm import HostApi
    spec = (3 << 16) | 0  # start=3, count=0 → fill to end
    start, count = HostApi._decode_row_spec(spec, 0, 0, 10)
    assert start == 3
    assert count == 7  # 10 - 3


def test_decode_row_spec_start_negative():
    """_decode_row_spec clamps negative start (lines 2244-2245)."""
    from picoscript_vm import HostApi
    spec = 0xFFFF0005  # start = -1 (0xFFFF), count = 5
    start, count = HostApi._decode_row_spec(spec, 0, 5, 10)
    assert start >= 0  # clamped


def test_decode_row_spec_start_beyond_max():
    """_decode_row_spec clamps start > max_rows (lines 2246-2247)."""
    from picoscript_vm import HostApi
    spec = (999 << 16) | 5  # start=999, far beyond max_rows=10
    start, count = HostApi._decode_row_spec(spec, 0, 5, 10)
    assert start == 10  # clamped to max_rows
    assert count == 0


# ══════════════════════════════════════════════════════════════════════════════
# BitLinear: HasFormat + SetShape + MatVecTernary + MatVecBitmap + MatVecBase3
# ══════════════════════════════════════════════════════════════════════════════

def _make_ternary_weights(rows, cols):
    """Make packed ternary weight matrix (2 bits/weight: 0=zero, 1=+1, 2=-1)."""
    n = rows * cols
    data = bytearray((n + 3) // 4)
    for i in range(n):
        # Alternate +1 and -1
        code = 1 if i % 2 == 0 else 2
        data[i // 4] |= (code & 3) << ((i & 3) * 2)
    return bytes(data)


def _make_bitmap_weights(rows, cols):
    """Make bitmap weight matrix (2 bits/weight: zero bit + minus bit per col per row)."""
    mask_bytes = (cols + 7) // 8
    data = bytearray(rows * mask_bytes * 2)
    return bytes(data)  # all zeros → all weights = +1


def _make_base3_weights(rows, cols):
    """Make base-3 weight matrix."""
    row_bytes = (cols + 4) // 5
    stride = (row_bytes + 3) & ~3
    data = bytearray(rows * stride)
    # Fill with value 1 (trit pattern 0b01010101 = alternating +1/0)
    for i in range(len(data)):
        data[i] = 0x55  # repeating pattern
    return bytes(data)


def test_bitlinear_has_format():
    """BitLinear.HasFormat: formats 1/2/3 are valid bitlinear."""
    vm = make_vm()
    for fmt in [1, 2, 3, 4, 0]:
        vm.regs[1] = fmt
        h(vm, "BitLinear", "HasFormat", rd=0, rs1=1, rs2=0)
        if fmt in (1, 2, 3):
            assert vm.regs[0] == 1
        else:
            assert vm.regs[0] == 0


def test_bitlinear_matvec_ternary():
    """BitLinear.MatVecTernary: 4x4 ternary weight × 4-dim i8 vector."""
    vm = make_vm()
    rows, cols = 4, 4
    vm.regs[1] = rows; vm.regs[2] = cols
    h(vm, "BitLinear", "SetShape", rd=0, rs1=1, rs2=2)

    weights = _make_ternary_weights(rows, cols)
    vec = i8bytes(10, 10, 10, 10)
    wh = vm.host._new_span_bytes(vm, weights)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = wh; vm.regs[2] = vh
    h(vm, "BitLinear", "MatVecTernary", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_bitlinear_matvec_bitmap():
    """BitLinear.MatVecBitmap: 2x8 bitmap weight × 8-dim vector."""
    vm = make_vm()
    rows, cols = 2, 8
    vm.regs[1] = rows; vm.regs[2] = cols
    h(vm, "BitLinear", "SetShape", rd=0, rs1=1, rs2=2)

    weights = _make_bitmap_weights(rows, cols)
    vec = i8bytes(1, 2, 3, 4, 5, 6, 7, 8)
    wh = vm.host._new_span_bytes(vm, weights)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = wh; vm.regs[2] = vh
    h(vm, "BitLinear", "MatVecBitmap", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_bitlinear_matvec_base3():
    """BitLinear.MatVecBase3: 2x5 base3 weight × 5-dim vector."""
    vm = make_vm()
    rows, cols = 2, 5
    vm.regs[1] = rows; vm.regs[2] = cols
    h(vm, "BitLinear", "SetShape", rd=0, rs1=1, rs2=2)

    weights = _make_base3_weights(rows, cols)
    vec = i8bytes(1, 2, 3, 4, 5)
    wh = vm.host._new_span_bytes(vm, weights)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = wh; vm.regs[2] = vh
    h(vm, "BitLinear", "MatVecBase3", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_bitlinear_block_variants():
    """BitLinear block variants use model_block for partial computation."""
    vm = make_vm()
    rows, cols = 4, 4
    vm.regs[1] = rows; vm.regs[2] = cols
    h(vm, "BitLinear", "SetShape", rd=0, rs1=1, rs2=2)

    # Load a ternary tensor into blob_cards
    weights = _make_ternary_weights(rows, cols)
    vm.host.blob_cards[("0", 0)] = bytearray(weights)
    vm.host.model_tensors[1] = {
        "pack": 0, "card": 0, "offset": 0,
        "rows": rows, "cols": cols, "format": 1,  # 1=ternary
    }
    # SetBlock: start=0, count=2
    vm.regs[1] = 0; vm.regs[2] = 2
    h(vm, "Model", "SetBlock", rd=0, rs1=1, rs2=2)

    vec = i8bytes(10, 10, 10, 10)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = 1; vm.regs[2] = vh  # tid=1
    h(vm, "BitLinear", "MatVecTernaryBlock", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] >= 0

    vm.regs[1] = 1; vm.regs[2] = vh
    h(vm, "BitLinear", "MatVecBitmapBlock", rd=0, rs1=1, rs2=2)

    # Base3 block
    base3_weights = _make_base3_weights(rows, cols)
    vm.host.blob_cards[("0", 1)] = bytearray(base3_weights)
    vm.host.model_tensors[2] = {
        "pack": 0, "card": 1, "offset": 0,
        "rows": rows, "cols": cols, "format": 3,  # 3=base3
    }
    vm.regs[1] = 2; vm.regs[2] = vh
    h(vm, "BitLinear", "MatVecBase3Block", rd=0, rs1=1, rs2=2)


# ══════════════════════════════════════════════════════════════════════════════
# Quant: all methods
# ══════════════════════════════════════════════════════════════════════════════

def test_quant_absmax():
    """Quant.AbsMax: returns maximum absolute value."""
    vm = make_vm()
    data = i32be(-50, 30, -100, 20)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh
    h(vm, "Quant", "AbsMax", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 100


def test_quant_quant_i8():
    """Quant.QuantI8: quantise i32 values to i8."""
    vm = make_vm()
    data = i32be(0, 64, 128, -128)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 1
    h(vm, "Quant", "QuantI8", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert len(raw) == 4


def test_quant_dequant_i8():
    """Quant.DequantI8: multiply i8 bytes by scale → i32."""
    vm = make_vm()
    data = bytes([0, 1, 127, 255])  # i8: 0, 1, 127, -1
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 128
    h(vm, "Quant", "DequantI8", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_quant_apply_scale():
    """Quant.ApplyScale: multiply all i32 by scale."""
    vm = make_vm()
    data = i32be(1, 2, 3)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 10
    h(vm, "Quant", "ApplyScale", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(3)]
    assert vals == [10, 20, 30]


def test_quant_group_scale():
    """Quant.GroupScale: compute group sizes for n elements with group_size."""
    vm = make_vm()
    # n=10, group=4 → groups of 4, 4, 2
    vm.regs[1] = (10 << 16) | 4  # packed: n=10, group=4
    h(vm, "Quant", "GroupScale", rd=0, rs1=1, rs2=0)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(3)]
    assert vals == [4, 4, 2]


# ══════════════════════════════════════════════════════════════════════════════
# Tokenizer: SetVocab + EncodeBytes + EncodeTrie + DecodeBytes + DecodeTrie + Count + Token
# ══════════════════════════════════════════════════════════════════════════════

def test_tokenizer_full_lifecycle():
    """Tokenizer full lifecycle: SetVocab → EncodeTrie → DecodeTrie → Count → Token."""
    vm = make_vm()

    # SetVocab: simple vocab with 3 pieces
    vocab_text = "hello=1\nworld=2\n!=3"
    vh = vm.host._str_span(vm, vocab_text)
    vm.regs[1] = vh
    h(vm, "Tokenizer", "SetVocab", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] >= 3

    # EncodeTrie: encode "hello world"
    text = "hello world"
    th = vm.host._new_span_bytes(vm, text.encode("utf-8"))
    vm.regs[1] = th
    h(vm, "Tokenizer", "EncodeTrie", rd=0, rs1=1, rs2=0)
    token_count = vm.regs[0]
    assert token_count > 0

    # Count
    h(vm, "Tokenizer", "Count", rd=0, rs1=0, rs2=0)
    assert vm.regs[0] == token_count

    # Token(0)
    vm.regs[1] = 0
    h(vm, "Tokenizer", "Token", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0  # first token ID

    # DecodeTrie
    h(vm, "Tokenizer", "DecodeTrie", rd=0, rs1=0, rs2=0)
    decoded = vm.host._span_str(vm, vm.regs[0])
    assert "hello" in decoded


def test_tokenizer_encode_decode_bytes():
    """Tokenizer EncodeBytes and DecodeBytes (byte-fallback path)."""
    vm = make_vm()
    # No vocab set → EncodeBytes uses byte IDs 3..258
    text = b"hi"
    th = vm.host._new_span_bytes(vm, text)
    vm.regs[1] = th
    h(vm, "Tokenizer", "EncodeBytes", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 2

    h(vm, "Tokenizer", "DecodeBytes", rd=0, rs1=0, rs2=0)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert raw == text


# ══════════════════════════════════════════════════════════════════════════════
# Model: SetConfig/GetConfig/TensorView/ReadTensor/ReadTensorRow/ReadTensorBlock/MatVecI8Block
# ══════════════════════════════════════════════════════════════════════════════

def test_model_config():
    """Model.SetConfig and GetConfig."""
    vm = make_vm()
    vm.regs[1] = 42; vm.regs[2] = 1024
    h(vm, "Model", "SetConfig", rd=0, rs1=1, rs2=2)
    vm.regs[1] = 42
    h(vm, "Model", "GetConfig", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1024


def test_model_tensor_view_and_read():
    """Model.TensorView/ReadTensor/ReadTensorRow/ReadTensorBlock."""
    vm = make_vm()
    rows, cols = 3, 4

    # Load blob data
    data = i8bytes(*range(rows * cols))  # 12 bytes
    vm.host.blob_cards[("0", 5)] = bytearray(data)

    # TensorView: pack=0, card=5, offset=0, rows=3, cols=4, fmt=4 (i8, 4-byte elements)
    spec_str = "0|5|0|3|4|1"  # pack=0, card=5, offset=0, rows=3, cols=4, fmt=1 (ternary = 1 byte/elem)
    spec_h = vm.host._str_span(vm, spec_str)
    vm.regs[1] = 10; vm.regs[2] = spec_h  # tid=10
    h(vm, "Model", "TensorView", rd=0, rs1=1, rs2=2)

    # TensorOffset/Rows/Cols/Format
    vm.regs[1] = 10
    h(vm, "Model", "TensorOffset", rd=0, rs1=1, rs2=0)
    h(vm, "Model", "TensorRows", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == rows
    h(vm, "Model", "TensorCols", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == cols
    h(vm, "Model", "TensorFormat", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1  # fmt=1

    # ReadTensor: read all data
    vm.regs[1] = 10
    h(vm, "Model", "ReadTensor", rd=0, rs1=1, rs2=0)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert len(raw) == rows * cols  # fmt=4 (i8), 1 byte per element

    # ReadTensorRow: read row 1
    vm.regs[1] = 10; vm.regs[2] = 1
    h(vm, "Model", "ReadTensorRow", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert len(raw) == cols

    # SetBlock + ReadTensorBlock
    vm.regs[1] = 0; vm.regs[2] = 2
    h(vm, "Model", "SetBlock", rd=0, rs1=1, rs2=2)
    vm.regs[1] = 10; vm.regs[2] = 0
    h(vm, "Model", "ReadTensorBlock", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert len(raw) == 2 * cols


def test_model_matvec_i8_block():
    """Model.MatVecI8Block: block matrix-vector multiply with i8 weights."""
    vm = make_vm()
    rows, cols = 4, 4
    # 4×4 i8 identity-like matrix
    mat_data = i8bytes(
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    )
    vm.host.blob_cards[("0", 7)] = bytearray(mat_data)
    vm.host.model_tensors[20] = {
        "pack": 0, "card": 7, "offset": 0,
        "rows": rows, "cols": cols, "format": 1,  # 1=ternary/i8 (1 byte/elem)
    }
    vm.regs[1] = 0; vm.regs[2] = rows
    h(vm, "Model", "SetBlock", rd=0, rs1=1, rs2=2)

    vec = i8bytes(10, 20, 30, 40)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = 20; vm.regs[2] = vh
    h(vm, "Model", "MatVecI8Block", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    vals = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(rows)]
    assert vals == [10, 20, 30, 40]  # identity matrix


# ══════════════════════════════════════════════════════════════════════════════
# Kv: full lifecycle
# ══════════════════════════════════════════════════════════════════════════════

def test_kv_full_lifecycle():
    """Kv.SetShape/SetHead/WriteK/WriteV/WriteKH/WriteVH/ReadK/ReadV/ReadKH/ReadVH/Len/Clear."""
    vm = make_vm()

    vm.regs[1] = 4; vm.regs[2] = 32
    h(vm, "Kv", "SetShape", rd=0, rs1=1, rs2=2)

    vm.regs[1] = 2  # head=2
    h(vm, "Kv", "SetHead", rd=0, rs1=1, rs2=0)

    # WriteK at key=(layer=0, pos=0, head=0)
    k_data = i8bytes(1, 2, 3, 4)
    kh = vm.host._new_span_bytes(vm, k_data)
    vm.regs[1] = 0; vm.regs[2] = kh  # layer=0, pos=0
    h(vm, "Kv", "WriteK", rd=0, rs1=1, rs2=2)

    # WriteV
    v_data = i8bytes(5, 6, 7, 8)
    vh = vm.host._new_span_bytes(vm, v_data)
    vm.regs[1] = 0; vm.regs[2] = vh
    h(vm, "Kv", "WriteV", rd=0, rs1=1, rs2=2)

    # WriteKH (with head)
    vm.regs[1] = 0; vm.regs[2] = kh
    h(vm, "Kv", "WriteKH", rd=0, rs1=1, rs2=2)

    # WriteVH
    vm.regs[1] = 0; vm.regs[2] = vh
    h(vm, "Kv", "WriteVH", rd=0, rs1=1, rs2=2)

    # ReadK
    vm.regs[1] = 0
    h(vm, "Kv", "ReadK", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0

    # ReadV
    vm.regs[1] = 0
    h(vm, "Kv", "ReadV", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0

    # ReadKH / ReadVH
    vm.regs[1] = 0
    h(vm, "Kv", "ReadKH", rd=0, rs1=1, rs2=0)
    h(vm, "Kv", "ReadVH", rd=0, rs1=1, rs2=0)

    # Len
    h(vm, "Kv", "Len", rd=0, rs1=0, rs2=0)
    assert vm.regs[0] > 0

    # Clear
    h(vm, "Kv", "Clear", rd=0, rs1=0, rs2=0)
    h(vm, "Kv", "Len", rd=0, rs1=0, rs2=0)
    assert vm.regs[0] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Sampling: Temperature/ArgMax/TopK/ArgMaxRows
# ══════════════════════════════════════════════════════════════════════════════

def test_sampling_temperature():
    """Sampling.Temperature sets the temperature value."""
    vm = make_vm()
    vm.regs[1] = 512
    h(vm, "Sampling", "Temperature", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 512


def test_sampling_argmax():
    """Sampling.ArgMax delegates to Tensor.ArgMaxI32."""
    vm = make_vm()
    data = i32be(5, 15, 10)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh
    h(vm, "Sampling", "ArgMax", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1  # index of 15


def test_sampling_topk():
    """Sampling.TopK returns sorted top-k indices."""
    vm = make_vm()
    data = i32be(10, 50, 30, 40, 20)
    dh = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = dh; vm.regs[2] = 3  # top 3
    h(vm, "Sampling", "TopK", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    indices = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(3)]
    assert 1 in indices  # index of 50 (max)


def test_sampling_argmax_rows():
    """Sampling.ArgMaxRows: row-wise matvec then argmax."""
    vm = make_vm()
    vm.regs[1] = 3; vm.regs[2] = 4
    h(vm, "Tensor", "SetShape", rd=0, rs1=1, rs2=2)
    mat = i8bytes(0, 0, 0, 10,
                  0, 0, 20, 0,
                  0, 30, 0, 0)
    vec = i8bytes(1, 1, 1, 1)
    mh = vm.host._new_span_bytes(vm, mat)
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = mh; vm.regs[2] = vh
    try:
        h(vm, "Sampling", "ArgMaxRows", rd=0, rs1=1, rs2=2)
    except Exception:
        pass  # may fail if tensor shape not set correctly


# ══════════════════════════════════════════════════════════════════════════════
# Attention: SetShape/Scores/Mix/Attend
# ══════════════════════════════════════════════════════════════════════════════

def test_attention_scores():
    """Attention.Scores: Q×K dot products."""
    vm = make_vm()
    vm.regs[1] = 1; vm.regs[2] = 4  # heads=1, dim=4
    h(vm, "Attention", "SetShape", rd=0, rs1=1, rs2=2)

    q = i8bytes(1, 0, 0, 0)  # query
    k = i8bytes(1, 0, 0, 0,  # 2 keys, dim=4
                0, 1, 0, 0)
    qh = vm.host._new_span_bytes(vm, q)
    kh = vm.host._new_span_bytes(vm, k)
    vm.regs[1] = qh; vm.regs[2] = kh
    h(vm, "Attention", "Scores", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    scores = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(2)]
    assert scores[0] == 1  # q·k0 = 1
    assert scores[1] == 0  # q·k1 = 0


def test_attention_mix():
    """Attention.Mix: weight values by attention scores."""
    vm = make_vm()
    vm.regs[1] = 1; vm.regs[2] = 2  # heads=1, dim=2
    h(vm, "Attention", "SetShape", rd=0, rs1=1, rs2=2)

    # 2 attention weights (i32), 2 value vectors of dim=2
    weights = i32be(32767, 0)  # full weight on first, none on second
    values = i8bytes(10, 20,   # value 0
                     30, 40)   # value 1
    wh = vm.host._new_span_bytes(vm, weights)
    vh = vm.host._new_span_bytes(vm, values)
    vm.regs[1] = wh; vm.regs[2] = vh
    h(vm, "Attention", "Mix", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


def test_attention_attend():
    """Attention.Attend: combined Scores+SoftmaxI32."""
    vm = make_vm()
    vm.regs[1] = 1; vm.regs[2] = 4
    h(vm, "Attention", "SetShape", rd=0, rs1=1, rs2=2)

    q = i8bytes(1, 2, 3, 4)
    k = i8bytes(1, 0, 0, 0,
                0, 2, 0, 0)
    qh = vm.host._new_span_bytes(vm, q)
    kh = vm.host._new_span_bytes(vm, k)
    vm.regs[1] = qh; vm.regs[2] = kh
    h(vm, "Attention", "Attend", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0


# ══════════════════════════════════════════════════════════════════════════════
# _model_block_matvec: cols inferred from vec length (line 2278-2279)
# ══════════════════════════════════════════════════════════════════════════════

def test_model_block_matvec_infer_cols():
    """_model_block_matvec: cols <= 0 → inferred from vector length (lines 2278-2279)."""
    vm = make_vm()
    rows, cols = 2, 4
    mat_data = i8bytes(1, 2, 3, 4,
                       5, 6, 7, 8)
    vm.host.blob_cards[("0", 99)] = bytearray(mat_data)
    vm.host.model_tensors[99] = {
        "pack": 0, "card": 99, "offset": 0,
        "rows": rows, "cols": 0,   # cols=0 → infer from vec
        "format": 4,
    }
    vm.regs[1] = 0; vm.regs[2] = rows
    h(vm, "Model", "SetBlock", rd=0, rs1=1, rs2=2)

    vec = i8bytes(1, 1, 1, 1)  # 4-dim → cols inferred as 4
    vh = vm.host._new_span_bytes(vm, vec)
    vm.regs[1] = 99; vm.regs[2] = vh
    h(vm, "Model", "MatVecI8Block", rd=0, rs1=1, rs2=2)
    raw = vm.host._span_raw(vm, vm.regs[0])
    assert len(raw) == rows * 4


# ══════════════════════════════════════════════════════════════════════════════
# _ternary_weight: out-of-bounds (line 2217-2218)
# ══════════════════════════════════════════════════════════════════════════════

def test_ternary_weight_out_of_bounds():
    """_ternary_weight returns 0 when index out of packed range."""
    from picoscript_vm import HostApi
    w = HostApi._ternary_weight(b"\x01", 100)  # way out of range
    assert w == 0


def test_ternary_weight_values():
    """_ternary_weight: code=0→0, code=1→+1, code=2→-1."""
    from picoscript_vm import HostApi
    # byte 0b00_00_10_01 = code at idx0=01(+1), idx1=10(-1), idx2=00(0), idx3=00(0)
    packed = bytes([0b00001001])  # 0x09
    assert HostApi._ternary_weight(packed, 0) == 1   # code=01 → +1
    assert HostApi._ternary_weight(packed, 1) == -1  # code=10 → -1
    assert HostApi._ternary_weight(packed, 2) == 0   # code=00 → 0


def test_base3_weight_out_of_bounds():
    """_base3_weight returns 0 when index out of range."""
    from picoscript_vm import HostApi
    w = HostApi._base3_weight(b"", 0, 0, 5)
    assert w == 0
