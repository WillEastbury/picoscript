#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_ast_json_cross_lang.py -- AST-JSON parity between Python and JS.

Spike (spike/ast-json-dialect): the same canonical AST-JSON document should
lower to byte-identical bytecode whether it's compiled by picoscript_ast.py
(Python) or vm/picoc.js's compileAst (the JS port used by the browser
portal/playground). This also exercises the JS `translate(src, "ast", X)` /
`translate(src, X, "ast")` bridge, proving AST-JSON is a first-class peer of
every other dialect in the cross-language translator -- not just a one-off
Python frontend.

Requires Node (skips cleanly if unavailable, matching test_translator_roundtrip.py).
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from picoscript_basic import tokenize, Parser  # noqa: E402
from picoscript_ast import ast_to_json, compile_ast  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402

BASIC_SRC = """
LET total = 0
FOR i = 1 TO 10
    total = total + i
NEXT
PRINT total
"""


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not available")


def _run_node(script: str) -> str:
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _ast_json_text():
    prog = Parser(tokenize(BASIC_SRC)).parse_program()
    return json.dumps(ast_to_json(prog))


def test_js_compile_ast_matches_python_bytecode():
    ast_json_text = _ast_json_text()
    py_words = lower_to_bytecode_safe(compile_ast(ast_json_text))

    script = f"""
    var P = require('./vm/picoc.js');
    var src = {json.dumps(ast_json_text)};
    var r = P.compileAst(src);
    console.log(JSON.stringify(r.words));
    """
    js_words = json.loads(_run_node(script))
    assert js_words == py_words


def test_js_translate_ast_to_english_and_back_compiles_in_python():
    ast_json_text = _ast_json_text()
    script = f"""
    var P = require('./vm/picoc.js');
    var src = {json.dumps(ast_json_text)};
    var eng = P.translate(src, 'ast', 'english');
    var back = P.translate(eng, 'english', 'ast');
    console.log(back);
    """
    js_roundtripped_json = _run_node(script)
    # Python's json_to_ast must tolerate the JS side's extra `pos` annotations.
    words = lower_to_bytecode_safe(compile_ast(js_roundtripped_json))
    assert len(words) > 0
