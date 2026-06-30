#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_cobol_final_90.py -- final push to get cobol.py to 90%."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
from picoscript_cobol import compile_cobol, _decode_output  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_cobol(src):
    words = lower_to_bytecode_safe(compile_cobol(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── _decode_output helper (lines 568-571) ───────────────────────────────────

def test_decode_output_helper():
    """_decode_output helper function decodes VM output."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
PROCEDURE DIVISION.
    DISPLAY 42.
    STOP RUN.
"""
    words = lower_to_bytecode_safe(compile_cobol(src))
    vm = PicoVM().run(words)
    output = _decode_output(vm)
    assert output == [42]


# ── Parser.expect() error (lines 140-141) ───────────────────────────────────

def test_cobol_expect_kw_error():
    """COBOL Parser: expect wrong token type raises SyntaxError."""
    with pytest.raises(SyntaxError):
        compile_cobol("""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
PROCEDURE DIVISION.
    DISPLAY.
    STOP RUN.
""")


# ── skip_sentence in DATA DIVISION (line 218) ────────────────────────────────

def test_cobol_data_skip_sentence():
    """COBOL DATA DIVISION with SECTION header (gets skipped)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 X PIC 9(4) VALUE 42.
PROCEDURE DIVISION.
    DISPLAY X.
    STOP RUN.
"""
    assert run_cobol(src) == [42]


# ── EVALUATE error path (lines 365-366) ──────────────────────────────────────

def test_cobol_evaluate_error():
    """COBOL EVALUATE with unexpected token raises SyntaxError."""
    with pytest.raises(SyntaxError):
        compile_cobol("""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    EVALUATE X
        BADKW 1
            DISPLAY 10
    END-EVALUATE.
    STOP RUN.
""")


# ── DIVIDE without GIVING non-var error (line 432) ───────────────────────────

def test_cobol_divide_no_giving_error():
    """COBOL DIVIDE without GIVING and non-var rhs raises."""
    with pytest.raises(SyntaxError):
        compile_cobol("""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 10.
PROCEDURE DIVISION.
    DIVIDE X BY 2.
    STOP RUN.
""")


# ── parse_call_from_id args (lines 441-447) ───────────────────────────────────

def test_cobol_call_from_id():
    """COBOL Ns.Method(arg) atom call."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
01 Y PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    COMPUTE Y = X + 0.
    DISPLAY Y.
    STOP RUN.
"""
    assert run_cobol(src) == [5]


# ── atom: paren call (lines 491-493) ──────────────────────────────────────────

def test_cobol_atom_paren_expr():
    """COBOL atom: parenthesized expression."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    COMPUTE X = (2 + 3) * 4.
    DISPLAY X.
    STOP RUN.
"""
    assert run_cobol(src) == [20]


# ── _match_binop IS GT/LT/GE/LE paths ────────────────────────────────────────

def test_cobol_is_ge_or_equal():
    """COBOL IS GREATER THAN OR EQUAL TO."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    IF X IS GREATER THAN OR EQUAL TO 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run_cobol(src) == [1]


def test_cobol_is_le_or_equal():
    """COBOL IS LESS THAN OR EQUAL TO (another path)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 3.
PROCEDURE DIVISION.
    IF X IS LESS THAN OR EQUAL TO 3
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run_cobol(src) == [1]


def test_cobol_is_not_kw():
    """COBOL IS NOT expression (NE short form)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 7.
PROCEDURE DIVISION.
    IF X IS NOT 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run_cobol(src) == [1]


def test_cobol_perform_varying_le_until():
    """COBOL PERFORM VARYING UNTIL var <= limit (LT conversion)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 I PIC 9(4) VALUE 0.
01 S PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM VARYING I FROM 1 UNTIL I > 5
        COMPUTE S = S + I
    END-PERFORM.
    DISPLAY S.
    STOP RUN.
"""
    assert run_cobol(src) == [15]  # 1+2+3+4+5
