#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_ast_frontend.py -- spike for the AST-JSON dialect (picoscript_ast.py).

Verifies the core claim of the spike/ast-json-dialect branch: a program
expressed as JSON-serialized AST (rather than parsed from any surface
syntax) round-trips losslessly and lowers to byte-identical bytecode
compared to the same program's BASIC source, and runs correctly on PicoVM.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import tokenize, Parser, compile_basic  # noqa: E402
from picoscript_ast import ast_to_json, json_to_ast, compile_ast  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

BASIC_SRC = """
LET total = 0
FOR i = 1 TO 5
    total = total + i
NEXT
PRINT total
"""


def _s32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def _prog():
    return Parser(tokenize(BASIC_SRC)).parse_program()


def test_ast_json_round_trip_is_structurally_identical():
    prog = _prog()
    as_json_text = json.dumps(ast_to_json(prog))
    rebuilt = json_to_ast(json.loads(as_json_text))
    # Dataclasses generate structural __eq__, so this checks every field,
    # recursively, including nested statement lists / expression trees.
    assert rebuilt == prog


def test_ast_frontend_byte_identical_to_basic():
    ast_json_text = json.dumps(ast_to_json(_prog()))
    basic_words = lower_to_bytecode_safe(compile_basic(BASIC_SRC))
    ast_words = lower_to_bytecode_safe(compile_ast(ast_json_text))
    assert ast_words == basic_words


def test_ast_frontend_runs_and_produces_expected_output():
    ast_json_text = json.dumps(ast_to_json(_prog()))
    words = lower_to_bytecode_safe(compile_ast(ast_json_text))
    vm = PicoVM().run(words)
    output = [_s32(int.from_bytes(chunk, "big")) for chunk in vm.output]
    assert output == [15]  # 1+2+3+4+5


def test_ast_frontend_rejects_unknown_node_kind():
    import pytest
    with pytest.raises(ValueError):
        compile_ast(json.dumps([{"node": "NotARealNode"}]))


def test_ast_to_json_rejects_foreign_same_named_node_class():
    """picoscript_cfront.py (the C-style frontend) has its own independent AST
    + Lowerer -- it never imports from picoscript_basic -- but some of its
    node classes happen to share a name with a picoscript_basic node (e.g.
    both define a `ConstDecl` with fields `name`/`value`). ast_to_json must
    reject these by class *identity*, not just name: silently accepting them
    would let json_to_ast reconstruct a cfront node as the wrong
    (picoscript_basic) class, a silent cross-dialect mix-up rather than a
    clear error. See picoscript_ast.ast_to_json's identity check.
    """
    import pytest
    from picoscript_cfront import tokenize as c_tokenize, Parser as CParser
    prog = CParser(c_tokenize("const int X = 5;\n")).parse_program()
    with pytest.raises(TypeError):
        ast_to_json(prog)
