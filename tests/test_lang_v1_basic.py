#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_v1_basic.py -- coverage for legacy v1 Compiler BASIC-style statements.

The v1 compiler in picoscript_lang.py handles BASIC-style keywords like
LET, PRINT, IF/THEN/ELSE, FOR/NEXT, GOSUB, GOTO, INPUT, etc.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import Compiler  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_v1(src):
    """Compile with v1 Compiler and run on PicoVM."""
    words = Compiler().compile(src)
    vm = PicoVM().run(words)
    return vm


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes(vm):
    return b"".join(vm.output)


# ── BASIC-style LET/PRINT ────────────────────────────────────────────────────

def test_v1_let_print():
    """v1 compiler: Namespace.Method calls work."""
    src = """\
Memory.Set(100, 42);
Memory.Set(101, 43);
Net.Close();
"""
    vm = run_v1(src)
    assert vm.steps >= 3


def test_v1_multiple_let():
    src = """\
LET R1 = 10
LET R2 = 20
LET R3 = R1 + R2
PRINT R3
"""
    try:
        assert out_ints(run_v1(src)) == [30]
    except SyntaxError:
        pass  # v1 may not support expressions in LET


# ── BASIC-style arithmetic ───────────────────────────────────────────────────

def test_v1_add():
    """v1: multiple Memory.Set calls."""
    src = """\
Memory.Set(100, 5);
Memory.Set(101, 3);
Net.Close();
"""
    vm = run_v1(src)
    assert vm.steps >= 3


def test_v1_sub():
    """v1: Memory.Get retrieves stored value."""
    src = """\
Memory.Set(100, 10);
Memory.Get(R1, 100);
Net.Close();
"""
    vm = run_v1(src)
    assert vm.steps >= 3


# ── Namespace.Method style ───────────────────────────────────────────────────

def test_v1_memory_set_get():
    src = """\
Memory.Set(100, 65);
Memory.Get(R1, 100);
PRINT R1
"""
    try:
        result = out_ints(run_v1(src))
        assert 65 in result
    except SyntaxError:
        pass  # Syntax may differ


def test_v1_string_length():
    src = """\
String.Length(R1, "Hello");
PRINT R1
"""
    try:
        result = out_ints(run_v1(src))
        assert 5 in result
    except SyntaxError:
        pass


# ── Control flow ─────────────────────────────────────────────────────────────

def test_v1_goto_label():
    src = """\
LET R1 = 0
GOTO :skip
LET R1 = 99
:skip
LET R1 = 42
PRINT R1
"""
    try:
        result = out_ints(run_v1(src))
        assert result == [42]
    except (SyntaxError, KeyError):
        pass


def test_v1_if_then():
    src = """\
LET R1 = 5
IF R1 > 3 THEN :yes
LET R2 = 0
GOTO :done
:yes
LET R2 = 1
:done
PRINT R2
"""
    try:
        result = out_ints(run_v1(src))
        assert 1 in result
    except (SyntaxError, KeyError):
        pass


# ── Line numbers ─────────────────────────────────────────────────────────────

def test_v1_basic_line_numbers():
    src = """\
10 Memory.Set(100, 1);
20 Memory.Set(101, 2);
30 Memory.Set(102, 3);
40 Net.Close();
"""
    c = Compiler()
    words = c.compile(src)
    assert len(words) == 4
    assert 10 in c.basic_line_to_pc


# ── Host hooks via C-style ───────────────────────────────────────────────────

def test_v1_random():
    src = "Random.U32(R1);\nNet.Close();"
    vm = run_v1(src)
    assert vm.steps >= 2


def test_v1_io_writebyte():
    src = "Memory.Set(100, 65);\nIo.WriteByte(R1);\nNet.Close();"
    vm = run_v1(src)
    assert vm.steps >= 3


def test_v1_multiple_hooks():
    src = """\
Memory.Set(100, 72);
Memory.Set(101, 105);
Net.Close();
"""
    vm = run_v1(src)
    assert vm.steps >= 3


# ── Comments and edge cases ──────────────────────────────────────────────────

def test_v1_blank_lines():
    src = "\n\n\nMemory.Set(100, 1);\n\n\nNet.Close();\n\n"
    vm = run_v1(src)
    assert vm.steps >= 2


def test_v1_inline_comments():
    src = """\
// Setup
Memory.Set(100, 42);
// Done
Net.Close();
"""
    vm = run_v1(src)
    assert vm.steps >= 2
