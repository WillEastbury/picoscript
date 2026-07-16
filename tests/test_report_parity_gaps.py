#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parity checks for newly added REPORT frontend constructs."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic, event_type_hash  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_vm import PicoVM, PicoFault  # noqa: E402


def _words(il):
    return lower_to_bytecode_safe(il)


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def _assert_report_basic_parity(report_src: str, basic_src: str, expected_output):
    report_words = _words(compile_report(report_src))
    basic_words = _words(compile_basic(basic_src))
    assert report_words == basic_words
    assert _out(PicoVM().run(report_words)) == expected_output


def test_report_add_and_subtract_one_match_basic_incdec_bytecode_and_runtime():
    report_src = """\
DATA: x TYPE i VALUE 5.
ADD 1 TO x.
SUBTRACT 1 FROM x.
ADD 1 TO x.
WRITE x.
"""
    basic_src = """\
LET X = 5
INC X
DEC X
INC X
PRINT X
"""
    _assert_report_basic_parity(report_src, basic_src, [6])


def test_report_dispatch_matches_basic_dispatch_bytecode_and_runtime():
    report_src = """\
DATA: x TYPE i VALUE 2.
DISPATCH x.
  WHEN 1.
    WRITE 10.
  WHEN 2.
    WRITE 20.
  WHEN OTHERS.
    WRITE 99.
ENDDISPATCH.
"""
    basic_src = """\
LET X = 2
DISPATCH X
CASE 1
PRINT 10
CASE 2
PRINT 20
DEFAULT
PRINT 99
ENDDISPATCH
"""
    _assert_report_basic_parity(report_src, basic_src, [20])


def test_report_constants_matches_basic_const_bytecode_and_runtime():
    report_src = """\
CONSTANTS: retry TYPE i VALUE 3.
WRITE retry.
"""
    basic_src = """\
CONST RETRY = 3
PRINT RETRY
"""
    _assert_report_basic_parity(report_src, basic_src, [3])


def test_report_enum_matches_basic_enum_bytecode_and_runtime():
    report_src = """\
ENUM HttpCode.
  OK VALUE 200.
  CREATED VALUE 201.
  ACCEPTED.
ENDENUM.
WRITE HTTPCODE_OK.
WRITE HTTPCODE_CREATED.
WRITE HTTPCODE_ACCEPTED.
"""
    basic_src = """\
ENUM HTTPCODE
OK = 200
CREATED = 201
ACCEPTED
ENDENUM
PRINT HTTPCODE_OK
PRINT HTTPCODE_CREATED
PRINT HTTPCODE_ACCEPTED
"""
    _assert_report_basic_parity(report_src, basic_src, [200, 201, 202])


def test_report_try_cleanup_happy_path_matches_basic_bytecode_and_runtime():
    report_src = """\
DATA: x TYPE i VALUE 0.
TRY.
  x = 1.
CATCH.
  x = 999.
CLEANUP.
  x = x + 1000.
ENDTRY.
WRITE x.
"""
    basic_src = """\
LET X = 0
TRY
    X = 1
EXCEPT
    X = 999
FINALLY
    X = X + 1000
ENDTRY
PRINT X
"""
    _assert_report_basic_parity(report_src, basic_src, [1001])


def test_report_try_catch_raise_matches_basic_bytecode_and_runtime():
    report_src = """\
DATA: x TYPE i VALUE 0.
TRY.
  x = 1.
  RAISE 42.
  x = 999.
CATCH.
  x = x + 100.
CLEANUP.
  x = x + 1000.
ENDTRY.
WRITE x.
"""
    basic_src = """\
LET X = 0
TRY
    X = 1
    RAISE 42
    X = 999
EXCEPT
    X = X + 100
FINALLY
    X = X + 1000
ENDTRY
PRINT X
"""
    _assert_report_basic_parity(report_src, basic_src, [1101])


def test_report_uncaught_raise_matches_basic_and_propagates_picofault():
    report_src = "RAISE 7.\nWRITE 1.\n"
    basic_src = "RAISE 7\nPRINT 1\n"
    report_words = _words(compile_report(report_src))
    basic_words = _words(compile_basic(basic_src))
    assert report_words == basic_words
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(report_words)
    assert exc.value.code == 7


def test_report_on_block_matches_basic_bytecode_and_runtime():
    type_code = event_type_hash("Ui", "Click")
    report_src = f"""\
DATA: hits TYPE i VALUE 0,
      target TYPE i VALUE 0.
Event.Post({type_code}, 5).
Event.Post(999, 9).
ON Ui.Click.
  hits = hits + 1.
  target = Event.Target(__event__).
ENDON.
WRITE hits.
WRITE target.
"""
    basic_src = f"""\
LET HITS = 0
LET TARGET = 0
EVENT POST {type_code} 5
EVENT POST 999 9
ON Ui.Click
    HITS = HITS + 1
    TARGET = EVENT TARGET __event__
END ON
PRINT HITS
PRINT TARGET
"""
    _assert_report_basic_parity(report_src, basic_src, [1, 5])
