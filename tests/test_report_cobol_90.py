#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_report_cobol_90.py -- push report/cobol to 90%."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_cobol(src):
    words = lower_to_bytecode_safe(compile_cobol(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def run_report(src):
    words = lower_to_bytecode_safe(compile_report(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_report.py — inline comment, ADD/SUBTRACT GIVING
# ══════════════════════════════════════════════════════════════════════════════

def test_report_double_quote_comment():
    """Report \" starts inline comment (line 69)."""
    src = 'DATA: x TYPE i VALUE 42. " comment here\nWRITE x.'
    assert run_report(src) == [42]


def test_report_add_giving():
    """Report ADD GIVING: result goes to new variable (lines 405-407)."""
    src = "DATA: a TYPE i VALUE 10,\n      b TYPE i VALUE 5,\n      r TYPE i VALUE 0.\nADD b TO a GIVING r.\nWRITE r."
    assert run_report(src) == [15]


def test_report_subtract_giving():
    """Report SUBTRACT GIVING: result goes to new variable (lines 416-418)."""
    src = "DATA: a TYPE i VALUE 10,\n      b TYPE i VALUE 3,\n      r TYPE i VALUE 0.\nSUBTRACT b FROM a GIVING r.\nWRITE r."
    assert run_report(src) == [7]


def test_report_multiply_giving():
    """Report MULTIPLY a BY b GIVING r."""
    src = "DATA: a TYPE i VALUE 6,\n      b TYPE i VALUE 7,\n      r TYPE i VALUE 0.\nMULTIPLY a BY b GIVING r.\nWRITE r."
    assert run_report(src) == [42]


def test_report_divide_giving():
    """Report DIVIDE a BY b GIVING r."""
    src = "DATA: a TYPE i VALUE 20,\n      b TYPE i VALUE 4,\n      r TYPE i VALUE 0.\nDIVIDE a BY b GIVING r.\nWRITE r."
    assert run_report(src) == [5]


def test_report_ge_comparison():
    """Report GE comparison."""
    src = "DATA: x TYPE i VALUE 5.\nIF x GE 5.\n  WRITE 1.\nENDIF."
    assert run_report(src) == [1]


def test_report_le_comparison():
    """Report LE comparison."""
    src = "DATA: x TYPE i VALUE 5.\nIF x LE 5.\n  WRITE 1.\nENDIF."
    assert run_report(src) == [1]


def test_report_move_to_giving():
    """Report MOVE TO with GIVING form is not standard but exercises parser."""
    src = "DATA: x TYPE i VALUE 42,\n      y TYPE i VALUE 0.\nMOVE x TO y.\nWRITE y."
    assert run_report(src) == [42]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_cobol.py — remaining expression paths
# ══════════════════════════════════════════════════════════════════════════════

def test_cobol_is_lt_or_equal():
    """COBOL IS LESS THAN OR EQUAL TO (line 517-518)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    IF X IS LESS THAN OR EQUAL TO 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run_cobol(src) == [1]


def test_cobol_not_equal_to_kw():
    """COBOL NOT EQUAL TO keyword form (lines 524-525)."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 7.
PROCEDURE DIVISION.
    IF X NOT EQUAL TO 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run_cobol(src) == [1]


def test_cobol_multiply_no_giving_error():
    """COBOL MULTIPLY without GIVING and non-var rhs raises."""
    with pytest.raises(SyntaxError):
        compile_cobol("""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    MULTIPLY X BY 7.
    STOP RUN.
""")


def test_cobol_call_from_id_method_error():
    """COBOL parse_call_from_id with bad method token."""
    with pytest.raises(SyntaxError):
        compile_cobol("""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
PROCEDURE DIVISION.
    COMPUTE X = Maths.(5).
    STOP RUN.
""")
