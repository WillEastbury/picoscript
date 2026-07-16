#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Functional frontend parity checks for newly added shared-AST constructs."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic, event_type_hash  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoFault, PicoVM  # noqa: E402


def _words_basic(src: str):
    return lower_to_bytecode_safe(compile_basic(src))


def _words_functional(src: str):
    return lower_to_bytecode_safe(compile_functional(src))


def _out(vm: PicoVM):
    return [
        int.from_bytes(chunk, "big") - (0x100000000 if int.from_bytes(chunk, "big") & 0x80000000 else 0)
        for chunk in vm.output
    ]


def _assert_bytecode_and_runtime(functional_src: str, basic_src: str, expected_output):
    functional_words = _words_functional(functional_src)
    basic_words = _words_basic(basic_src)
    assert functional_words == basic_words
    assert _out(PicoVM().run(functional_words)) == expected_output


def test_functional_dispatch_matches_basic_bytecode_and_runtime():
    functional_src = """\
let x = 2
dispatch x with
| 0 -> printfn 100
| 1 -> printfn 200
| 2 -> printfn 300
| _ -> printfn 999
"""
    basic_src = """\
LET x = 2
DISPATCH x
CASE 0
    PRINT 100
CASE 1
    PRINT 200
CASE 2
    PRINT 300
DEFAULT
    PRINT 999
ENDDISPATCH
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [300])


def test_functional_const_and_enum_match_basic_bytecode_and_runtime():
    functional_src = """\
const RETRY = 3
enum HttpCode with
    | OK = 200
    | CREATED = 201
    | ACCEPTED
printfn RETRY
printfn HTTPCODE_OK
printfn HTTPCODE_CREATED
printfn HTTPCODE_ACCEPTED
"""
    basic_src = """\
CONST RETRY = 3
ENUM HTTPCODE
OK = 200
CREATED = 201
ACCEPTED
ENDENUM
PRINT RETRY
PRINT HTTPCODE_OK
PRINT HTTPCODE_CREATED
PRINT HTTPCODE_ACCEPTED
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [3, 200, 201, 202])


def test_functional_try_with_finally_and_raise_match_basic_bytecode_and_runtime():
    functional_src = """\
let x = 1
try
    let x = 2
    raise 9
with
    let x = 3
finally
    let x = x + 4
printfn x
"""
    basic_src = """\
LET x = 1
TRY
    LET x = 2
    RAISE 9
EXCEPT
    LET x = 3
FINALLY
    LET x = x + 4
ENDTRY
PRINT x
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [7])


def test_functional_bare_raise_matches_basic_bytecode_and_runtime():
    functional_src = """\
let caught = 0
try
    raise
with
    let caught = 1
printfn caught
"""
    basic_src = """\
LET caught = 0
TRY
    RAISE
EXCEPT
    LET caught = 1
ENDTRY
PRINT caught
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [1])


def test_functional_uncaught_raise_propagates_as_picofault():
    functional_src = "raise 7\nprintfn 1\n"
    basic_src = "RAISE 7\nPRINT 1\n"
    functional_words = _words_functional(functional_src)
    assert functional_words == _words_basic(basic_src)
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(functional_words)
    assert exc.value.code == 7


def test_functional_on_block_matches_basic_bytecode_and_runtime():
    type_code = event_type_hash("Ui", "Click")
    functional_src = f"""\
let hits = 0
let target = 0
Event.Post({type_code}, 5)
Event.Post(999, 9)
on Ui.Click do
    let hits = hits + 1
    let target = Event.Target(__event__)
printfn hits
printfn target
"""
    basic_src = f"""\
LET hits = 0
LET target = 0
EVENT POST {type_code} 5
EVENT POST 999 9
ON Ui.Click
    LET hits = hits + 1
    LET target = EVENT TARGET __event__
END ON
PRINT hits
PRINT target
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [1, 5])


def test_functional_server_wrapper_matches_basic_bytecode_and_runtime():
    functional_src = """\
server do
    printfn 1
printfn 2
"""
    basic_src = """\
SERVER
    PRINT 1
ENDSERVER
PRINT 2
"""
    _assert_bytecode_and_runtime(functional_src, basic_src, [1, 2])
