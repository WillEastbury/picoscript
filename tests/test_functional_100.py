#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import runpy
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import Call, Let, Return, Sub, Var  # noqa: E402
from picoscript_functional import Parser, Tok, _CallTarget, compile_functional, tokenize  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_functional(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0) for c in vm.output]


def test_functional_tokenizer_and_indent_errors():
    toks = tokenize("let x = 0x1f // comment\nprintfn \"A\\n\\t\\\\\"\n")
    assert repr(Tok("id", "x", 1)) == "Tok(id,'x')"
    assert any(t.kind == "num" and t.value == "0x1f" for t in toks)
    assert any(t.kind == "str" and t.value == "A\n\t\\" for t in toks)

    with pytest.raises(SyntaxError, match="inconsistent indentation"):
        tokenize("if true then\n    printfn 1\n  printfn 2\n")

    with pytest.raises(SyntaxError, match="unterminated block comment"):
        tokenize("(* never ends")


def test_functional_manual_parser_branches(monkeypatch):
    blank_program = Parser([Tok("newline", "", 1, 0), Tok("eof", "", 1, 1)])
    assert blank_program.parse_program() == []

    suite = Parser([Tok("newline", "", 1, 0), Tok("indent", "", 2, 0), Tok("eof", "", 2, 0)])
    with pytest.raises(SyntaxError, match="unexpected EOF inside block"):
        suite.parse_suite()

    blank_suite = Parser([Tok("newline", "", 1, 0), Tok("indent", "", 2, 0), Tok("newline", "", 2, 0), Tok("dedent", "", 3, 0), Tok("eof", "", 3, 0)])
    assert blank_suite.parse_suite() == []

    stmt_body = Parser([Tok("id", "x", 1, 0), Tok("eof", "", 1, 1)])
    monkeypatch.setattr(stmt_body, "parse_stmt", lambda allow_func=False: None)
    assert stmt_body.parse_stmt_body() == []

    parse_stmt_none = Parser([Tok("newline", "", 1, 0), Tok("eof", "", 1, 1)])
    assert parse_stmt_none.parse_stmt() is None

    parse_stmt_after_start = Parser([Tok("id", "x", 1, 0), Tok("eof", "", 1, 1)])
    monkeypatch.setattr(parse_stmt_after_start, "_parse_stmt", lambda allow_func=False: None)
    assert parse_stmt_after_start.parse_stmt() is None

    list_stmt = Parser([Tok("id", "x", 1, 11), Tok("eof", "", 1, 12)])
    monkeypatch.setattr(list_stmt, "_parse_stmt", lambda allow_func=False: [1])
    assert list_stmt.parse_stmt() == [1]

    node_stmt = Parser([Tok("id", "x", 1, 12), Tok("eof", "", 1, 13)])
    monkeypatch.setattr(node_stmt, "_parse_stmt", lambda allow_func=False: 1)
    assert node_stmt.parse_stmt() == 1

    program_extend = Parser([Tok("id", "x", 1, 0), Tok("eof", "", 1, 1)])

    def program_list(allow_func=True):
        program_extend.next()
        return [Let("a", Var("b"))]

    monkeypatch.setattr(program_extend, "parse_stmt", program_list)
    assert len(program_extend.parse_program()) == 1

    suite_extend = Parser([Tok("newline", "", 1, 0), Tok("indent", "", 2, 0), Tok("id", "x", 2, 0), Tok("dedent", "", 3, 0), Tok("eof", "", 3, 1)])

    def suite_list(allow_func=False):
        suite_extend.next()
        return [Let("a", Var("b"))]

    monkeypatch.setattr(suite_extend, "parse_stmt", suite_list)
    assert len(suite_extend.parse_suite()) == 1


def test_functional_function_body_and_statement_errors(monkeypatch):
    with pytest.raises(SyntaxError, match="final line of a function body"):
        Parser(tokenize("let f x =\n    1\n    printfn 2\n")).parse_program()

    body_none = Parser([Tok("newline", "", 1, 0), Tok("indent", "", 2, 0), Tok("newline", "", 2, 0), Tok("dedent", "", 3, 0), Tok("eof", "", 3, 1)])
    body = body_none.parse_function_body()
    assert len(body) == 1 and isinstance(body[0], Return)

    body_extend = Parser([Tok("newline", "", 1, 0), Tok("indent", "", 2, 0), Tok("kw", "return", 2, 0), Tok("newline", "", 2, 6), Tok("dedent", "", 3, 0), Tok("eof", "", 3, 1)])
    monkeypatch.setattr(body_extend, "_line_starts_expr", lambda: False)

    def body_list(allow_func=False):
        body_extend.next()
        return [Let("x", Var("y"))]

    monkeypatch.setattr(body_extend, "parse_stmt", body_list)
    body = body_extend.parse_function_body()
    assert isinstance(body[0], Let) and isinstance(body[-1], Return)

    prog = Parser(tokenize("return\nbreak\ngoto target\nlabel target\nprintfn 1\n"))
    ast = prog.parse_program()
    assert isinstance(ast[0], Return) and ast[0].value is None
    assert ast[2].label == "target"
    assert ast[3].name == "target"

    with pytest.raises(SyntaxError, match="expression statement must be a call"):
        Parser(tokenize("42\n")).parse_program()

    with pytest.raises(SyntaxError, match="expression statement must be a call"):
        Parser(tokenize("true\n")).parse_program()

    with pytest.raises(SyntaxError, match="only allowed at top level"):
        Parser(tokenize("if true then\n    let f x = x\n")).parse_program()


def test_functional_calls_applications_and_atoms(monkeypatch):
    prog = Parser(tokenize("let s = Number.ToString 255\nlet t = Number.ToString()\nlet n = f 1 2\n"))
    ast = prog.parse_program()
    assert isinstance(ast[0].value, Call) and ast[0].value.ns == "Number" and ast[0].value.args[0].value == 255
    assert isinstance(ast[1].value, Call) and ast[1].value.args == []
    assert isinstance(ast[2].value, Call) and len(ast[2].value.args) == 2

    weird = Parser([Tok("num", "1", 1, 0), Tok("num", "2", 1, 1), Tok("eof", "", 1, 2)])
    monkeypatch.setattr(weird, "_callable_expr", lambda node: True)
    with pytest.raises(SyntaxError, match="cannot apply arguments"):
        weird.parse_application()

    method = Parser([Tok("id", "Ns", 1, 0), Tok("op", ".", 1, 2), Tok("op", "(", 1, 3), Tok("eof", "", 1, 4)])
    with pytest.raises(SyntaxError, match="expected method name after '.'"):
        method.parse_atom()

    bad_atom = Parser([Tok("indent", "", 1, 0), Tok("eof", "", 1, 1)])
    with pytest.raises(SyntaxError, match="unexpected token"):
        bad_atom.parse_atom()

    assert Parser([Tok("op", "-", 1, 0)])._line_starts_expr() is True


def test_functional_match_pipe_and_selftest():
    src = """\
let id x = x
let piped = 255 |> Number.ToString |> String.Length
match 2 with
| 1 -> printfn 10
| _ -> printfn piped
"""
    assert run(src) == [3]
    runpy.run_module("picoscript_functional", run_name="__main__")
