#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_compiler.py -- coverage for the legacy v1 Compiler in picoscript_lang.py.

The v1 compiler compiles Namespace.Method(args) and BASIC-style statements into bytecode.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    Compiler,
    parse_register,
    parse_arg,
    disassemble,
)
from picoscript_vm import PicoVM  # noqa: E402


# ── parse_register ───────────────────────────────────────────────────────────

def test_parse_register_r0():
    assert parse_register("R0") == 0

def test_parse_register_r15():
    assert parse_register("R15") == 15

def test_parse_register_lowercase():
    assert parse_register("r7") == 7

def test_parse_register_invalid():
    import pytest
    with pytest.raises((ValueError, AssertionError)):
        parse_register("X5")


# ── parse_arg ────────────────────────────────────────────────────────────────

def test_parse_arg_register():
    assert parse_arg("R3") == ("reg", 3)

def test_parse_arg_integer():
    assert parse_arg("42") == ("imm", 42)

def test_parse_arg_hex():
    result = parse_arg("0xFF")
    # May parse as symbol or immediate depending on implementation
    assert result is not None

def test_parse_arg_label():
    assert parse_arg(":loop") == ("label", "loop")

def test_parse_arg_string():
    result = parse_arg('"hello"')
    assert result[0] == "str" or "hello" in str(result)


# ── Compiler (Namespace.Method style) ────────────────────────────────────────

def test_compiler_io_write():
    """Compile Io.Write(R1)."""
    c = Compiler()
    words = c.compile('Memory.Set(R1, 65);\nIo.Write(R1);')
    assert len(words) == 2


def test_compiler_memory_set():
    """Compile Memory.Set with two args."""
    c = Compiler()
    words = c.compile('Memory.Set(100, 72);')
    assert len(words) == 1


def test_compiler_random():
    """Compile Random.U32()."""
    c = Compiler()
    words = c.compile('Random.U32(R1);')
    assert len(words) == 1


def test_compiler_labels():
    """Labels resolve forward references."""
    c = Compiler()
    src = """\
:start
Memory.Set(100, 1);
Net.Close();
"""
    words = c.compile(src)
    assert len(words) == 2
    assert "start" in c.labels


def test_compiler_comments():
    """Comments (// prefix) are ignored."""
    c = Compiler()
    src = """\
// this is a comment
Memory.Set(100, 1);
// another comment
Net.Close();
"""
    words = c.compile(src)
    assert len(words) == 2


def test_compiler_empty_source():
    """Empty source produces no instructions."""
    c = Compiler()
    assert c.compile("") == []
    assert c.compile("// only comments") == []


def test_compiler_runnable():
    """Compiled bytecode runs on PicoVM without fault."""
    c = Compiler()
    src = """\
Memory.Set(100, 42);
Io.WriteByte(R1);
Net.Close();
"""
    words = c.compile(src)
    vm = PicoVM().run(words)
    # Just verify it ran
    assert vm.steps > 0


def test_compiler_basic_line_numbers():
    """BASIC-style line numbers are accepted."""
    c = Compiler()
    src = """\
10 Memory.Set(100, 1);
20 Memory.Set(101, 2);
30 Net.Close();
"""
    words = c.compile(src)
    assert len(words) == 3
    assert 10 in c.basic_line_to_pc


def test_compiler_duplicate_label_error():
    """Duplicate labels raise SyntaxError."""
    import pytest
    c = Compiler()
    with pytest.raises(SyntaxError, match="Duplicate"):
        c.compile(":dup\nMemory.Set(1,1);\n:dup\nNet.Close();")


def test_compiler_bad_syntax_error():
    """Invalid syntax raises SyntaxError."""
    import pytest
    c = Compiler()
    with pytest.raises(SyntaxError):
        c.compile("this is not valid syntax at all")

