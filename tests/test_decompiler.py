#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_decompiler.py -- coverage for decompile_basic, decompile_csharp, decompile_python.

Also tests _decode_word and the disassembler more thoroughly.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    Compiler,
    decompile_basic,
    decompile_csharp,
    decompile_python,
    decompile_hex,
    disassemble,
    _decode_word,
    encode_instruction,
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_NOOP, OP_JUMP, OP_RETURN,
    OP_BRANCH, OP_INC, OP_LOAD, OP_CALL,
    HOST_HOOK_BASE, HOST_HOOK_CODES, EXT_HOST_HOOK_BASE,
)


def make_program():
    """Create a multi-instruction program for decompilation testing."""
    c = Compiler()
    src = """\
10 MATH ADD, R1, R0, 42
20 MATH INC, R2
30 RANDOM U32, R3
40 NET STATUS, 200
50 NET CLOSE
"""
    return c.compile(src)


def make_host_hook_program():
    """Create program with host hooks for decompilation."""
    c = Compiler()
    src = """\
Memory.Set(100, 65);
Memory.Get(R1, 100);
Random.U32(R2);
Net.Close();
"""
    return c.compile(src)


# ── _decode_word ─────────────────────────────────────────────────────────────

def test_decode_word_add():
    word = encode_instruction(OP_ADD, rd=1, rs1=2, imm16=42)
    d = _decode_word(word)
    assert d["opcode"] == OP_ADD
    assert d["rd"] == 1
    assert d["rs1"] == 2
    assert d["imm16"] == 42


def test_decode_word_noop():
    word = encode_instruction(OP_NOOP, imm16=0)
    d = _decode_word(word)
    assert d["opcode"] == OP_NOOP


def test_decode_word_jump():
    word = encode_instruction(OP_JUMP, imm16=100)
    d = _decode_word(word)
    assert d["opcode"] == OP_JUMP
    assert d["imm16"] == 100


def test_decode_word_return():
    word = encode_instruction(OP_RETURN)
    d = _decode_word(word)
    assert d["opcode"] == OP_RETURN


def test_decode_word_host_hook():
    hook = HOST_HOOK_CODES[("Memory", "Set")]
    word = encode_instruction(OP_NOOP, rd=1, rs1=2, imm16=HOST_HOOK_BASE | hook)
    d = _decode_word(word)
    assert d["opcode"] == OP_NOOP
    assert (d["imm16"] & 0xFF) == hook


# ── decompile_basic ──────────────────────────────────────────────────────────

def test_decompile_basic_output():
    """decompile_basic produces numbered BASIC-style lines."""
    words = make_program()
    result = decompile_basic(words)
    assert "10 " in result
    assert "MATH" in result.upper() or "ADD" in result.upper()


def test_decompile_basic_host_hooks():
    """decompile_basic shows host-hook names."""
    words = make_host_hook_program()
    result = decompile_basic(words)
    assert "MEMORY" in result.upper() or "MEM" in result.upper()


def test_decompile_basic_net():
    """decompile_basic formats NET commands."""
    c = Compiler()
    words = c.compile("NET STATUS, 200\nNET BODY\nNET CLOSE")
    result = decompile_basic(words)
    assert "NET" in result.upper()
    lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
    assert len(lines) == 3


def test_decompile_basic_empty():
    """decompile_basic on empty list."""
    assert decompile_basic([]) == "" or decompile_basic([]).strip() == ""


# ── decompile_csharp ─────────────────────────────────────────────────────────

def test_decompile_csharp_output():
    """decompile_csharp produces Namespace.Method() style."""
    words = make_host_hook_program()
    result = decompile_csharp(words)
    assert "." in result  # Namespace.Method format
    assert "\r\n" in result  # CRLF


def test_decompile_csharp_net():
    """decompile_csharp includes Net operations."""
    c = Compiler()
    words = c.compile("NET STATUS, 200\nNET CLOSE")
    result = decompile_csharp(words)
    assert len(result) > 0


# ── decompile_python ─────────────────────────────────────────────────────────

def test_decompile_python_output():
    """decompile_python produces Python-style output."""
    words = make_host_hook_program()
    result = decompile_python(words)
    assert len(result) > 0


def test_decompile_python_indented():
    """decompile_python formats control flow with indentation."""
    c = Compiler()
    words = c.compile("10 MATH ADD, R0, R0, 1\n20 FLOW BRANCH, EQ, R0, R1, 10\n30 FLOW RETURN")
    result = decompile_python(words)
    assert len(result.strip()) > 0


# ── decompile_hex ────────────────────────────────────────────────────────────

def test_decompile_hex():
    """decompile_hex produces hex dump."""
    words = [0x12345678, 0xDEADBEEF]
    result = decompile_hex(words)
    assert "12345678" in result
    assert "deadbeef" in result.lower()


def test_decompile_hex_empty():
    """decompile_hex on empty list."""
    assert decompile_hex([]).strip() == "" or decompile_hex([]) == ""


# ── disassemble (more thorough) ──────────────────────────────────────────────

def test_disassemble_arithmetic():
    """disassemble shows arithmetic ops."""
    words = [
        encode_instruction(OP_ADD, rd=1, rs1=2, imm16=10),
        encode_instruction(OP_SUB, rd=3, rs1=4, imm16=5),
        encode_instruction(OP_MUL, rd=5, rs1=6, imm16=3),
    ]
    result = disassemble(words)
    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert len(lines) == 3


def test_disassemble_control_flow():
    """disassemble shows jumps and branches."""
    words = [
        encode_instruction(OP_JUMP, imm16=50),
        encode_instruction(OP_BRANCH, rd=0, rs1=1, rs2=0, imm16=10),
        encode_instruction(OP_RETURN),
    ]
    result = disassemble(words)
    assert len(result.strip()) > 0


def test_disassemble_ext_hook():
    """disassemble handles extended host hooks (>=0x100)."""
    hook = HOST_HOOK_CODES[("Crypto", "Sha256")]
    word = encode_instruction(OP_NOOP, rd=1, imm16=EXT_HOST_HOOK_BASE | hook)
    result = disassemble([word])
    assert "Crypto" in result or "Sha256" in result


def test_disassemble_roundtrip():
    """Compile then disassemble produces meaningful text."""
    c = Compiler()
    words = c.compile("Memory.Set(100, 42);\nRandom.U32(R1);\nNet.Close();")
    result = disassemble(words)
    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert len(lines) == 3
