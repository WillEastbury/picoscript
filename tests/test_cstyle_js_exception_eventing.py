#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_cstyle_js_exception_eventing.py -- C-style JS mirror
(CParser/CLowerer in vm/picoc.js) parity for TryExcept/Raise/OnBlock.

Closes the scoped-out gap documented in docs/EXCEPTION_ENGINE.md and
docs/DIALECT_PARITY.md: `CParser`/`CLowerer` in vm/picoc.js previously had
NO grammar or lowering at all for try/catch/finally/raise/on (a third,
independent implementation of the mechanism, after picoscript_basic.py's
Lowerer/BLowerer's JS port). "try"/"catch"/"finally"/"raise"/"on" were not
even in C_KW, so `try` would tokenize as a plain identifier and fail with a
confusing parse error rather than being recognized as the keyword.

This ported picoscript_cfront.py's parse_try/parse_on_block grammar and
lower_try/lower_on_block lowering to CParser/CLowerer, verified byte-
identical bytecode to the Python cfront compiler (picoscript_cfront.py),
and byte-identical runtime output on the JS VM (vm/picovm.js) vs the Python
VM (picoscript_vm.py).

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

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not available")


def _run_js_compile_c(src: str):
    script = f"""
    var P = require('./vm/picoc.js');
    var r = P.compileC({json.dumps(src)});
    console.log(JSON.stringify(r.words));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def _run_js_program_c(src: str):
    script = f"""
    var P = require('./vm/picoc.js');
    var VM = require('./vm/picovm.js');
    var r = P.compileC({json.dumps(src)});
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


TRY_CATCH_FINALLY_SRC = (
    "int x = 0;\n"
    "try {\n"
    "    x = 1;\n"
    "    raise 42;\n"
    "    x = 999;\n"
    "} catch {\n"
    "    x = x + 100;\n"
    "} finally {\n"
    "    x = x + 1000;\n"
    "}\n"
    "print(x);\n"
)

HAPPY_PATH_SRC = "int x = 0;\ntry {\n    x = 1;\n} catch {\n    x = 999;\n}\nprint(x);\n"

NESTED_TRY_SRC = (
    "int x = 0;\n"
    "try {\n"
    "    try {\n        raise 1;\n"
    "    } catch {\n        x = x + 10;\n        raise 2;\n    }\n"
    "} catch {\n    x = x + 100;\n}\n"
    "print(x);\n"
)


def _out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def test_js_compile_c_try_catch_finally_byte_identical_to_python():
    py_words = [w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c(TRY_CATCH_FINALLY_SRC))]
    js_words = [w & 0xFFFFFFFF for w in _run_js_compile_c(TRY_CATCH_FINALLY_SRC)]
    assert js_words == py_words


def test_js_compile_c_nested_try_byte_identical_to_python():
    py_words = [w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c(NESTED_TRY_SRC))]
    js_words = [w & 0xFFFFFFFF for w in _run_js_compile_c(NESTED_TRY_SRC)]
    assert js_words == py_words


def test_js_runtime_try_catch_finally_raise():
    out = _run_js_program_c(TRY_CATCH_FINALLY_SRC)
    assert out == [1101]
    vm = PicoVM(max_steps=1000)
    vm.run([w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c(TRY_CATCH_FINALLY_SRC))])
    assert _out_ints(vm) == [1101]


def test_js_runtime_happy_path_no_exception():
    out = _run_js_program_c(HAPPY_PATH_SRC)
    assert out == [1]


def test_js_runtime_nested_try_catch():
    out = _run_js_program_c(NESTED_TRY_SRC)
    assert out == [110]


def test_js_runtime_on_block_dispatches_matching_event():
    from picoscript_basic import event_type_hash
    type_code = event_type_hash("Ui", "Click")
    src = (
        f"int hits = 0;\n"
        f"Event.Post({type_code}, 5);\n"
        f"Event.Post(999, 9);\n"
        f"on Ui.Click {{\n"
        f"    hits = hits + 1;\n"
        f"}}\n"
        f"print(hits);\n"
    )
    out = _run_js_program_c(src)
    assert out == [1]


def test_js_runtime_on_block_no_matching_events():
    src = (
        "int hits = 0;\n"
        "on Ui.Click {\n"
        "    hits = hits + 1;\n"
        "}\n"
        "print(hits);\n"
    )
    out = _run_js_program_c(src)
    assert out == [0]
