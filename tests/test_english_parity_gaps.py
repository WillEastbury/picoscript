#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parity checks for newly added English parser constructs."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic, event_type_hash  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _words(il):
    return lower_to_bytecode_safe(il)


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def test_english_try_except_finally_raise_matches_python_bytecode_and_runtime():
    english_src = """\
Set x to 1.
Try:
    Set x to 2.
    Raise 9.
Except:
    Set x to 3.
Finally:
    Set x to x plus 4.
Print x.
"""
    python_src = """\
x = 1
try:
    x = 2
    raise 9
except:
    x = 3
finally:
    x = x + 4
print(x)
"""
    english_words = _words(compile_english(english_src))
    python_words = _words(compile_python(python_src))
    assert english_words == python_words
    assert _out(PicoVM().run(english_words)) == [7]


def test_english_bare_raise_matches_python_bytecode_and_runtime():
    english_src = """\
Set caught to 0.
Try:
    Raise.
Except:
    Set caught to 1.
Print caught.
"""
    python_src = """\
caught = 0
try:
    raise
except:
    caught = 1
print(caught)
"""
    english_words = _words(compile_english(english_src))
    python_words = _words(compile_python(python_src))
    assert english_words == python_words
    assert _out(PicoVM().run(english_words)) == [1]


def test_english_on_block_matches_basic_bytecode_and_runtime():
    type_code = event_type_hash("Ui", "Click")
    english_src = f"""\
Set hits to 0.
Set target to 0.
Event.Post({type_code}, 5).
Event.Post(999, 9).
On Ui.Click:
    Set hits to hits plus 1.
    Set target to Event.Target(__event__).
Print hits.
Print target.
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
    english_words = _words(compile_english(english_src))
    basic_words = _words(compile_basic(basic_src))
    assert english_words == basic_words
    assert _out(PicoVM().run(english_words)) == [1, 5]


def test_english_server_wrapper_matches_basic_bytecode_and_runtime():
    english_src = """\
Server:
    Print 1.
Print 2.
"""
    basic_src = """\
SERVER
    PRINT 1
ENDSERVER
PRINT 2
"""
    english_words = _words(compile_english(english_src))
    basic_words = _words(compile_basic(basic_src))
    assert english_words == basic_words
    assert _out(PicoVM().run(english_words)) == [1, 2]
