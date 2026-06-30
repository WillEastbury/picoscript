#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import (  # noqa: E402
    Bin,
    Break,
    CallStmt,
    ConstDecl,
    DoLoop,
    EnumDecl,
    ForTo,
    Gosub,
    Let,
    Return,
    Skip,
    Sub,
)
from picoscript_english import Parser, Tok, compile_english, tokenize  # noqa: E402


def test_english_tokenizer_edges_and_errors():
    assert repr(Tok("word", "Hello", 1)) == "Tok(word,'Hello')"

    toks = tokenize("Set x to 0x1f. # trailing comment\nPrint \"A\\n\\t\\\"\\'\\\\z\".\n")
    nums = [t.value for t in toks if t.kind == "num"]
    strings = [t.value for t in toks if t.kind == "str"]
    assert "0x1f" in nums
    assert strings == ['A\n\t"\'\\z']

    with pytest.raises(SyntaxError, match="unterminated string"):
        tokenize('Print "oops')

    with pytest.raises(SyntaxError, match="unexpected char '@'"):
        tokenize("Set x to @.")

    with pytest.raises(SyntaxError, match="inconsistent indentation"):
        tokenize("If true:\n    Print 1.\n  Print 2.")


def test_english_manual_parser_error_branches(monkeypatch):
    p = Parser([Tok("word", "oops", 1, 0), Tok("eof", "", 1, 4)])
    with pytest.raises(SyntaxError, match="expected one of"):
        p.eat_word("ok")

    suite = Parser([Tok("op", ":", 1, 0), Tok("newline", "", 1, 1), Tok("indent", "", 2, 0), Tok("eof", "", 2, 0)])
    with pytest.raises(SyntaxError, match="unexpected EOF inside block"):
        suite.parse_suite()

    blank_program = Parser([Tok("newline", "", 1, 0), Tok("eof", "", 1, 1)])
    assert blank_program.parse_program() == []

    blank_suite = Parser(
        [Tok("op", ":", 1, 0), Tok("newline", "", 1, 1), Tok("indent", "", 2, 0), Tok("newline", "", 2, 0), Tok("dedent", "", 3, 0), Tok("eof", "", 3, 0)]
    )
    assert blank_suite.parse_suite() == []

    none_stmt = Parser([Tok("word", "x", 1, 7), Tok("eof", "", 1, 8)])
    monkeypatch.setattr(none_stmt, "_parse_stmt", lambda: None)
    assert none_stmt.parse_stmt() is None

    bad_pos = Parser([Tok("word", "x", 1, 9), Tok("eof", "", 1, 10)])
    monkeypatch.setattr(bad_pos, "_parse_stmt", lambda: 123)
    assert bad_pos.parse_stmt() == 123

    atom = Parser([Tok("indent", "", 1, 0), Tok("eof", "", 1, 0)])
    with pytest.raises(SyntaxError, match="unexpected token"):
        atom.parse_atom()

    with pytest.raises(SyntaxError, match="cannot parse statement"):
        Parser(tokenize(".")).parse_program()


def test_english_parser_constructs_and_operator_words():
    src = """\
Define a constant Answer as 42.
Define constant Limit 9.
Define enum Color:
    member Red is 1.
    Blue.
To routine named Worker(a, b):
    If false:
        Return.
    Otherwise if a and b:
        Stop out.
    Otherwise:
        Skip.
Call Worker.
Do Worker(1, 2).
Repeat:
    Print 1.
Until true.
Repeat:
    Print 2.
While false.
Repeat 3 times with idx:
    Print idx.
For each i from 1 to 3 by 2:
    Print i.
Set flag to false.
Set same to 1 is 1.
Set logic to true and false or true.
Net.Ping().
"""
    prog = Parser(tokenize(src)).parse_program()

    assert isinstance(prog[0], ConstDecl) and prog[0].name == "Answer"
    assert isinstance(prog[1], ConstDecl) and prog[1].name == "Limit"
    assert isinstance(prog[2], EnumDecl) and prog[2].members == [("Red", prog[2].members[0][1]), ("Blue", None)]
    assert isinstance(prog[3], Sub)
    assert isinstance(prog[3].body[0], Return) and prog[3].body[0].value is None
    assert isinstance(prog[3].body[1], Break)
    assert isinstance(prog[3].body[2], Skip)
    assert isinstance(prog[4], Gosub) and prog[4].args is None
    assert isinstance(prog[5], Gosub) and len(prog[5].args) == 2
    assert isinstance(prog[6], DoLoop) and prog[6].until is True
    assert isinstance(prog[7], DoLoop) and prog[7].until is False
    assert prog[8].var == "idx"
    assert isinstance(prog[9], ForTo) and prog[9].step.value == 2
    assert isinstance(prog[10], Let) and prog[10].value.value == 0
    assert prog[11].value.cond == "EQ"
    assert isinstance(prog[12].value, Bin) and prog[12].value.op == "OR"
    assert prog[12].value.lhs.op == "AND"
    assert isinstance(prog[13], CallStmt)


def test_english_choose_dispatch_repeat_errors():
    with pytest.raises(SyntaxError, match="Choose"):
        compile_english("Choose 1:\n    Print 1.\n")

    with pytest.raises(SyntaxError, match="Dispatch"):
        compile_english("Dispatch on 1:\n    Print 1.\n")

    with pytest.raises(SyntaxError, match="must be followed by 'Until' or 'While'"):
        compile_english("Repeat:\n    Print 1.\nPrint 2.\n")
