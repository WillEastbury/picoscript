#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parity checks for newly added COBOL frontend constructs."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic, event_type_hash  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, PicoFault  # noqa: E402


def _words_from_cobol(src: str):
    return lower_to_bytecode_safe(compile_cobol(src))


def _words_from_basic(src: str):
    return lower_to_bytecode_safe(compile_basic(src))


def _out(vm: PicoVM):
    vals = []
    for chunk in vm.output:
        v = int.from_bytes(chunk, "big")
        vals.append(v - 0x100000000 if v & 0x80000000 else v)
    return vals


def _assert_parity_and_output(cobol_src: str, basic_src: str, expected_output):
    cobol_words = _words_from_cobol(cobol_src)
    basic_words = _words_from_basic(basic_src)
    assert cobol_words == basic_words
    assert _out(PicoVM().run(cobol_words)) == expected_output


def test_cobol_level_78_const_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
78 STRIDE VALUE 40.
78 OFFSET VALUE STRIDE - 5.
PROCEDURE DIVISION.
    DISPLAY OFFSET.
    STOP RUN.
"""
    basic_src = """\
CONST STRIDE = 40
CONST OFFSET = STRIDE - 5
PRINT OFFSET
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [35])


def test_cobol_level_88_enum_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 COLOR PIC 9 VALUE 0.
88 RED VALUE 1.
88 GREEN VALUE 2.
88 BLUE VALUE 3.
PROCEDURE DIVISION.
    DISPLAY BLUE.
    STOP RUN.
"""
    basic_src = """\
LET COLOR = 0
ENUM COLOR
RED = 1
GREEN = 2
BLUE = 3
ENDENUM
PRINT BLUE
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [3])


def test_cobol_dispatch_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9 VALUE 2.
PROCEDURE DIVISION.
    DISPATCH X.
        WHEN 0.
            DISPLAY 100.
        WHEN 1.
            DISPLAY 200.
        WHEN 2.
            DISPLAY 300.
        WHEN OTHER.
            DISPLAY 999.
    END-DISPATCH.
    STOP RUN.
"""
    basic_src = """\
LET X = 2
DISPATCH X
CASE 0
    PRINT 100
CASE 1
    PRINT 200
CASE 2
    PRINT 300
DEFAULT
    PRINT 999
ENDDISPATCH
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [300])


def test_cobol_perform_varying_times_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 SUM PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM VARYING I TIMES 4.
        COMPUTE SUM = SUM + I.
    END-PERFORM.
    DISPLAY SUM.
    STOP RUN.
"""
    basic_src = """\
LET SUM = 0
FOREACH I IN 4
    LET SUM = SUM + I
ENDFOREACH
PRINT SUM
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [6])


def test_cobol_continue_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 SUM PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM VARYING I TIMES 5.
        IF I = 2
            CONTINUE.
        END-IF.
        COMPUTE SUM = SUM + I.
    END-PERFORM.
    DISPLAY SUM.
    STOP RUN.
"""
    basic_src = """\
LET SUM = 0
FOREACH I IN 5
    IF I = 2 THEN
        SKIP
    ENDIF
    LET SUM = SUM + I
ENDFOREACH
PRINT SUM
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [8])


def test_cobol_on_block_matches_basic():
    type_code = event_type_hash("Ui", "Click")
    cobol_src = f"""\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 HITS PIC 9(4) VALUE 0.
01 TARGET PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    Event.Post({type_code}, 5).
    Event.Post(999, 9).
    ON Ui.Click.
        COMPUTE HITS = HITS + 1.
        MOVE Event.Target(__EVENT__) TO TARGET.
    END-ON.
    DISPLAY HITS.
    DISPLAY TARGET.
    STOP RUN.
"""
    basic_src = f"""\
LET HITS = 0
LET TARGET = 0
EVENT POST {type_code} 5
EVENT POST 999 9
ON Ui.Click
    LET HITS = HITS + 1
    LET TARGET = EVENT TARGET __EVENT__
END ON
PRINT HITS
PRINT TARGET
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [1, 5])


def test_cobol_try_except_finally_happy_path_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    TRY.
        MOVE 1 TO X.
    EXCEPT.
        MOVE 999 TO X.
    FINALLY.
        COMPUTE X = X + 1000.
    END-TRY.
    DISPLAY X.
    STOP RUN.
"""
    basic_src = """\
LET X = 0
TRY
    LET X = 1
EXCEPT
    LET X = 999
FINALLY
    LET X = X + 1000
ENDTRY
PRINT X
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [1001])


def test_cobol_try_except_raise_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    TRY.
        MOVE 1 TO X.
        RAISE 42.
        MOVE 999 TO X.
    EXCEPT.
        COMPUTE X = X + 100.
    FINALLY.
        COMPUTE X = X + 1000.
    END-TRY.
    DISPLAY X.
    STOP RUN.
"""
    basic_src = """\
LET X = 0
TRY
    LET X = 1
    RAISE 42
    LET X = 999
EXCEPT
    LET X = X + 100
FINALLY
    LET X = X + 1000
ENDTRY
PRINT X
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [1101])


def test_cobol_bare_raise_matches_basic():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 CAUGHT PIC 9 VALUE 0.
PROCEDURE DIVISION.
    TRY.
        RAISE.
    EXCEPT.
        MOVE 1 TO CAUGHT.
    END-TRY.
    DISPLAY CAUGHT.
    STOP RUN.
"""
    basic_src = """\
LET CAUGHT = 0
TRY
    RAISE
EXCEPT
    LET CAUGHT = 1
ENDTRY
PRINT CAUGHT
RETURN
"""
    _assert_parity_and_output(cobol_src, basic_src, [1])


def test_cobol_uncaught_raise_matches_basic_and_propagates():
    cobol_src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
PROCEDURE DIVISION.
    RAISE 7.
    STOP RUN.
"""
    basic_src = """\
RAISE 7
RETURN
"""
    cobol_words = _words_from_cobol(cobol_src)
    basic_words = _words_from_basic(basic_src)
    assert cobol_words == basic_words
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(cobol_words)
    assert exc.value.code == 7
