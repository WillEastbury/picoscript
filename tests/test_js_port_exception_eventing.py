#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_js_port_exception_eventing.py -- JS compiler (vm/picoc.js)
parity for TryExcept/Raise/OnBlock.

Part of the full-language-equivalence pass: vm/picoc.js's BLowerer
previously had NO branch at all for TryExcept/Raise/OnBlock (any of the
6 shared-AST frontends' source using these constructs would throw "BASIC:
cannot lower TryExcept" if it somehow reached the JS compiler). This ported
picoscript_basic.py's lower_try/Raise/lower_on_block logic to
vm/picoc.js's BLowerer (laddr IL instruction + Error.SetHandler/
PopHandler/Raise host calls + the Event.* drain-dispatch loop), and added
matching BASIC-syntax parser grammar (TRY/EXCEPT/FINALLY/ENDTRY/RAISE/
ON..END ON) to vm/picoc.js's BParser.

Also removed the now-obsolete AST_JSON_UNSUPPORTED blocklist in
vm/picoc.js's jsonToAst -- the JS AST-JSON bridge now accepts these node
kinds, matching Python's picoscript_ast.py.

Requires Node; skips cleanly if unavailable (matching the repo's existing
convention for JS-parity tests).
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not available")


def _run_js_compile_basic(src: str):
    script = f"""
    var P = require('./vm/picoc.js');
    var r = P.compileBasic({json.dumps(src)});
    console.log(JSON.stringify(r.words));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def _run_js_program(src: str):
    script = f"""
    var P = require('./vm/picoc.js');
    var VM = require('./vm/picovm.js');
    var r = P.compileBasic({json.dumps(src)});
    var vm = new VM();
    vm.run(r.words);
    var out = [];
    for (var i = 0; i < vm.output.length; i += 4) {{
      var v = (vm.output[i]<<24 | vm.output[i+1]<<16 | vm.output[i+2]<<8 | vm.output[i+3]) >>> 0;
      out.push(v | 0);
    }}
    console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


TRY_EXCEPT_FINALLY_SRC = (
    "LET x = 0\n"
    "TRY\n"
    "    LET x = 1\n"
    "    RAISE 42\n"
    "EXCEPT\n"
    "    LET x = x + 100\n"
    "FINALLY\n"
    "    LET x = x + 1000\n"
    "ENDTRY\n"
    "PRINT x\n"
)

ON_BLOCK_SRC = (
    "LET hits = 0\n"
    "LET target = 0\n"
    "EVENT RAISE Ui.Click 5\n"
    "EVENT RAISE Ui.Hover 9\n"
    "ON Ui.Click\n"
    "    LET hits = hits + 1\n"
    "    LET target = EVENT TARGET __event__\n"
    "END ON\n"
    "PRINT hits\n"
    "PRINT target\n"
)


def test_js_compiles_try_except_finally_raise():
    js_words = _run_js_compile_basic(TRY_EXCEPT_FINALLY_SRC)
    py_words = lower_to_bytecode_safe(compile_basic(TRY_EXCEPT_FINALLY_SRC))
    assert js_words == py_words


def test_js_runs_try_except_finally_raise_correctly():
    js_out = _run_js_program(TRY_EXCEPT_FINALLY_SRC)
    assert js_out == [1101]  # 1 -> +100 (except) -> +1000 (finally)


def test_js_compiles_on_block_byte_identical_to_python():
    js_words = _run_js_compile_basic(ON_BLOCK_SRC)
    py_words = lower_to_bytecode_safe(compile_basic(ON_BLOCK_SRC))
    assert js_words == py_words


def test_js_runs_on_block_dispatch_correctly():
    js_out = _run_js_program(ON_BLOCK_SRC)
    assert js_out == [1, 5]  # only the matching event fires; target = 5


def test_js_ast_json_bridge_now_accepts_tryexcept_raise_onblock():
    """AST_JSON_UNSUPPORTED (TryExcept/Raise/OnBlock) was removed once the
    JS BLowerer gained real lowering support for these -- this was
    previously a hard rejection (jsonToAst threw immediately)."""
    script = f"""
    var P = require('./vm/picoc.js');
    var src = {json.dumps(TRY_EXCEPT_FINALLY_SRC)};
    var astJson = P.translate(src, 'basic', 'ast');
    var hasTryExcept = astJson.indexOf('TryExcept') >= 0;
    var r = P.compileAst(astJson);
    console.log(JSON.stringify({{hasTryExcept: hasTryExcept, wordCount: r.words.length}}));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout.strip())
    assert result["hasTryExcept"] is True
    assert result["wordCount"] > 0
