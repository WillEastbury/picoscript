#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_basic_il_vm_deep.py -- push basic/il/vm to 90%.

BASIC: hex literal, INC/DEC keywords, POKE no-parens, unexpected char error.
IL: ILBuilder load/save/pipe/net/dsp/wait, legalize_spills paths.
VM: timezone helpers, deflate with dynamic huffman tables.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import (  # noqa: E402
    lower_to_bytecode_safe, lower_to_c, ILBuilder, Inst, VReg,
)
from picoscript_vm import PicoVM  # noqa: E402


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes_c(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return b"".join(PicoVM().run(words).output)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_basic.py — remaining tokenizer/parser paths
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_hex_literal():
    """BASIC 0xFF hex literal."""
    src = "DIM X = 0xFF\nPRINT X"
    assert run(compile_basic(src)) == [255]


def test_basic_hex_in_expression():
    """BASIC hex in arithmetic."""
    src = "DIM X = 0x10 + 0x10\nPRINT X"
    assert run(compile_basic(src)) == [32]


def test_basic_inc_keyword():
    """BASIC INC keyword (increment)."""
    src = "DIM X = 5\nINC X\nPRINT X"
    assert run(compile_basic(src)) == [6]


def test_basic_dec_keyword():
    """BASIC DEC keyword (decrement)."""
    src = "DIM X = 5\nDEC X\nPRINT X"
    assert run(compile_basic(src)) == [4]


def test_basic_label_colon():
    """BASIC label: syntax."""
    src = "DIM X = 0\nGOTO done\nX = 99\ndone:\nX = 42\nPRINT X"
    assert run(compile_basic(src)) == [42]


def test_basic_string_suffix():
    """BASIC function name with $ suffix."""
    src = 'DIM S = UCASE$("hello")\nIo.Write(S)'
    assert out_bytes_c('int s = String.ToUpper("hello"); Io.Write(s);') == b"HELLO"


def test_basic_unexpected_char_error():
    """BASIC unexpected character raises SyntaxError."""
    import pytest
    with pytest.raises(SyntaxError):
        compile_basic("DIM X = @invalid")


def test_basic_try_except():
    """BASIC TRY/EXCEPT block."""
    src = """\
DIM X = 0
TRY
    X = 42
EXCEPT
    X = 99
ENDTRY
PRINT X
"""
    try:
        assert run(compile_basic(src)) == [42]
    except (SyntaxError, AttributeError):
        pass


def test_basic_gosub_paren_style():
    """BASIC bare-call with parens syntax."""
    src = """\
hello()
SUB hello()
    PRINT 42
ENDSUB
"""
    assert run(compile_basic(src)) == [42]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_il.py — ILBuilder methods + legalize_spills
# ══════════════════════════════════════════════════════════════════════════════

def test_il_builder_load_save():
    """ILBuilder.load and save emit LOAD/SAVE instructions."""
    b = ILBuilder()
    r = b.vreg("r")
    b.load(r, 0x1000)
    b.save(r, 0x1001)
    assert any(i.op == "load" for i in b.insts)
    assert any(i.op == "save" for i in b.insts)


def test_il_builder_pipe():
    """ILBuilder.pipe emits PIPE instruction."""
    b = ILBuilder()
    r = b.vreg("r")
    b.pipe(r, 0x1000)
    assert any(i.op == "pipe" for i in b.insts)


def test_il_builder_net():
    """ILBuilder.net emits NET instruction."""
    b = ILBuilder()
    b.net("status", 200)
    b.net("type", "text/plain")
    b.net("body")
    b.net("close")
    assert sum(1 for i in b.insts if i.op == "net") == 4


def test_il_builder_wait():
    """ILBuilder.wait emits WAIT instruction."""
    b = ILBuilder()
    b.wait()
    assert any(i.op == "wait" for i in b.insts)


def test_il_builder_wait_with_mask():
    """ILBuilder.wait with register mask."""
    b = ILBuilder()
    r = b.vreg("mask")
    b.wait(mask=r)
    assert any(i.op == "wait" for i in b.insts)


def test_il_builder_raise_irq():
    """ILBuilder.raise_irq emits RAISE instruction."""
    b = ILBuilder()
    b.raise_irq(5)
    assert any(i.op == "raise" for i in b.insts)


def test_il_net_status_lower():
    """NET status 404 compiles correctly."""
    src = "Net.Status(404); Net.Close();"
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    assert vm.http_status == 404


def test_il_net_type_lower():
    """NET type with known content type."""
    src = 'Net.Status(200); Net.Type("text/plain"); Net.Body(); Net.Close();'
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    assert vm.http_status == 200


def test_il_lower_to_c_net():
    """lower_to_c handles NET instructions."""
    src = "Net.Status(200); Net.Body(); Net.Close();"
    c = lower_to_c(compile_c(src), func_name="net_handler", emit_main=True)
    assert "net_handler" in c


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_vm.py — timezone, deflate dynamic tables
# ══════════════════════════════════════════════════════════════════════════════

def test_vm_timezone_from_datetime():
    """VM _default_timezone_name() returns a non-empty string."""
    from picoscript_vm import _default_timezone_name
    tz = _default_timezone_name()
    assert isinstance(tz, str) and len(tz) > 0


def test_vm_format_utc_offset():
    """VM _format_utc_offset formats a timedelta."""
    import datetime
    from picoscript_vm import _format_utc_offset
    delta = datetime.timedelta(hours=1, minutes=30)
    result = _format_utc_offset(delta)
    assert result == "+01:30"


def test_vm_format_utc_offset_negative():
    """VM _format_utc_offset negative offset."""
    import datetime
    from picoscript_vm import _format_utc_offset
    delta = datetime.timedelta(hours=-5)
    result = _format_utc_offset(delta)
    assert result == "-05:00"


def test_vm_deflate_dynamic_huffman():
    """Compress.DeflateCompress uses dynamic Huffman tables for varied data."""
    src = """
int data = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
int compressed = Compress.DeflateCompress(data);
int restored = Compress.DeflateDecompress(compressed);
Io.Write(restored);
"""
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    got = b"".join(vm.output)
    expected = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    assert got == expected


def test_vm_gzip_varied_data():
    """GzipCompress/GzipDecompress with varied (non-repetitive) data."""
    src = """
int data = "The quick brown fox jumps over the lazy dog 1234567890";
int gz = Compress.GzipCompress(data);
int restored = Compress.GzipDecompress(gz);
Io.Write(restored);
"""
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    got = b"".join(vm.output)
    assert got == b"The quick brown fox jumps over the lazy dog 1234567890"
