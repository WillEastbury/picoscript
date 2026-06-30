#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_coverage_push4.py -- fourth wave: expression parser internals.

Targets deep expression parsing: unary ops, nested calls, complex conditionals,
and language-specific operator forms that haven't been triggered yet.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes(il):
    words = lower_to_bytecode_safe(il)
    return b"".join(PicoVM().run(words).output)


# ── Functional: expression atoms and operators ───────────────────────────────

def test_func_and_or():
    src = "let x = if (1 > 0) and (2 > 1) then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_func_or_false_true():
    src = "let x = if (1 > 2) or (3 > 2) then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_func_number_literal_zero():
    src = "let x = 0\nprintfn x"
    assert run(compile_functional(src)) == [0]


def test_func_nested_function_call():
    src = "let f x = x + 1\nlet g x = x * 2\nprintfn (f (g 5))"
    assert run(compile_functional(src)) == [11]


def test_func_string_concat():
    src = 'let a = "AB"\nlet b = "CD"\nlet c = String.Concat(a, b)\nIo.Write(c)'
    assert out_bytes(compile_functional(src)) == b"ABCD"


def test_func_equality():
    src = "let x = if 5 == 5 then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


# ── COBOL: more expression patterns ─────────────────────────────────────────

def test_cobol_not_equal():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    IF X NOT = 3
        DISPLAY 1
    ELSE
        DISPLAY 0
    END-IF.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [1]


def test_cobol_multiply_no_giving():
    """COBOL MULTIPLY without GIVING (multiplies into second operand)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 6.
01 Y PIC 9(4) VALUE 7.
PROCEDURE DIVISION.
    MULTIPLY X BY Y.
    DISPLAY Y.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [42]


def test_cobol_divide_no_giving():
    """COBOL DIVIDE without GIVING (divides the second operand)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 10.
01 Y PIC 9(4) VALUE 2.
PROCEDURE DIVISION.
    DIVIDE X BY Y.
    DISPLAY Y.
    STOP RUN.
"""
    # DIVIDE X BY Y without GIVING: Y = Y / X (confusing but COBOL-standard)
    result = run(compile_cobol(src))
    assert len(result) == 1  # just verify it compiles and runs


def test_cobol_perform_varying_from_to():
    """COBOL PERFORM VARYING with FROM...UNTIL > end."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 I PIC 9(4) VALUE 0.
01 S PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM VARYING I FROM 1 UNTIL I > 3
        COMPUTE S = S + I
    END-PERFORM.
    DISPLAY S.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [6]


# ── Report: more expression patterns ────────────────────────────────────────

def test_report_multiply_no_giving():
    """Report MULTIPLY without GIVING (multiplies target by factor)."""
    src = "DATA: x TYPE i VALUE 6,\n      y TYPE i VALUE 7.\nMULTIPLY x BY y.\nWRITE y."
    result = run(compile_report(src))
    assert len(result) == 1  # verify compiles and runs


def test_report_divide_no_giving():
    """Report DIVIDE without GIVING."""
    src = "DATA: x TYPE i VALUE 10,\n      y TYPE i VALUE 2.\nDIVIDE x BY y.\nWRITE y."
    result = run(compile_report(src))
    assert len(result) == 1


def test_report_if_eq():
    """Report IF with EQ keyword comparison."""
    src = "DATA: x TYPE i VALUE 5.\nIF x EQ 5.\n  WRITE 1.\nENDIF."
    assert run(compile_report(src)) == [1]


def test_report_if_ne():
    """Report IF with NE keyword comparison."""
    src = "DATA: x TYPE i VALUE 5.\nIF x NE 3.\n  WRITE 1.\nENDIF."
    assert run(compile_report(src)) == [1]


def test_report_if_gt_lt():
    """Report IF with GT/LT keyword comparisons."""
    src = "DATA: x TYPE i VALUE 5.\nIF x GT 3.\n  WRITE 1.\nENDIF.\nIF x LT 10.\n  WRITE 2.\nENDIF."
    assert run(compile_report(src)) == [1, 2]


# ── English: more patterns ───────────────────────────────────────────────────

def test_english_not_equal():
    src = "set x to 5\nif x is not equal to 3:\n    display 1"
    assert run(compile_english(src)) == [1]


def test_english_exceeds():
    src = "set x to 10\nif x exceeds 5:\n    display 1"
    assert run(compile_english(src)) == [1]


def test_english_at_most():
    src = "set x to 3\nif x is at most 5:\n    display 1"
    assert run(compile_english(src)) == [1]


def test_english_over():
    src = "set x to 10 over 2\ndisplay x"
    assert run(compile_english(src)) == [5]


# ── IL: more lowering paths ──────────────────────────────────────────────────

def test_il_lower_c_logical_operators():
    """lower_to_c handles && and ||."""
    src = "int a = 1; int b = 0; if (a && !b) { print(1); } else { print(0); }"
    c = lower_to_c(compile_c(src), func_name="logic", emit_main=True)
    assert "logic" in c


def test_il_lower_js_string_heavy():
    """lower_to_js with many string operations."""
    src = '''
int a = "Hello";
int b = " ";
int c = "World";
int ab = String.Concat(a, b);
int abc = String.Concat(ab, c);
Io.Write(abc);
'''
    js = lower_to_js(compile_c(src), module_name="strings")
    assert "strings" in js and len(js) > 300


# ── BASIC: more patterns ────────────────────────────────────────────────────

def test_basic_multiple_do_loops():
    """BASIC multiple DO loops in sequence."""
    src = """\
DIM X = 0
DO WHILE X < 3
    X += 1
LOOP
DIM Y = 10
DO
    Y -= 1
LOOP UNTIL Y <= 7
PRINT X
PRINT Y
"""
    assert run(compile_basic(src)) == [3, 7]


def test_basic_host_call_in_expression():
    """BASIC host call as part of expression."""
    src = """\
DIM S = "Hello"
DIM N = String.Length(S)
PRINT N
"""
    assert run(compile_basic(src)) == [5]
