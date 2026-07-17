#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_js_grammar_all_frontends.py -- JS (vm/picoc.js) parity for the
new English/COBOL/Report/Functional grammar (TryExcept/Raise/OnBlock/
Dispatch/ConstDecl/EnumDecl/IncDec/ServerMain), added as part of the
full-language-equivalence pass.

Two background agents ported this grammar to vm/picoc.js's EnParser/
CobParser/RepParser/FunParser (reusing the already-shared BLowerer.lowerTry/
lowerOnBlock -- see tests/test_js_port_exception_eventing.py for that
lower-level verification). Neither agent left a permanent pytest file behind
(both used throwaway temp scripts), so this file exists specifically to give
this work durable regression coverage, matching the Python-side
tests/test_{english,cobol,report,functional}_parity_gaps.py files this
mirrors.

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
from picoscript_english import compile_english  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not available")

_COMPILE_FN_JS = {
    "english": "compileEnglish",
    "cobol": "compileCobol",
    "report": "compileReport",
    "functional": "compileFunctional",
}
_COMPILE_FN_PY = {
    "english": compile_english,
    "cobol": compile_cobol,
    "report": compile_report,
    "functional": compile_functional,
}


def _js_words(lang: str, src: str):
    fn = _COMPILE_FN_JS[lang]
    script = f"""
    var P = require('./vm/picoc.js');
    var r = P.{fn}({json.dumps(src)});
    console.log(JSON.stringify(r.words));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip())


def _py_words(lang: str, src: str):
    return lower_to_bytecode_safe(_COMPILE_FN_PY[lang](src))


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def _assert_js_matches_python(lang: str, src: str, expected_output):
    js_words = _js_words(lang, src)
    py_words = _py_words(lang, src)
    assert js_words == py_words, f"{lang}: JS/Python bytecode mismatch"
    vm = PicoVM().run(py_words)
    assert _out(vm) == expected_output


# ---- English ----------------------------------------------------------------

def test_js_english_try_except_finally_raise():
    src = (
        "Set x to 0.\n"
        "Try:\n"
        "    Set x to 1.\n"
        "    Raise 42.\n"
        "    Set x to 999.\n"
        "Except:\n"
        "    Set x to x plus 100.\n"
        "Finally:\n"
        "    Set x to x plus 1000.\n"
        "Print x.\n"
    )
    _assert_js_matches_python("english", src, [1101])


def test_js_english_on_block():
    # Post events via the generic Event.Post host call (English has no EVENT
    # keyword sugar, unlike BASIC) then declare an On block matching one.
    src = (
        "Set hits to 0.\n"
        "Set target to 0.\n"
        "Event.Post(1573047153, 5).\n"
        "Event.Post(999, 9).\n"
        "On Ui.Click:\n"
        "    Set hits to hits plus 1.\n"
        "    Set target to Event.Target(__event__).\n"
        "Print hits.\n"
        "Print target.\n"
    )
    _assert_js_matches_python("english", src, [1, 5])


def test_js_english_server_wrapper():
    src = "Server:\n    Set x to 77.\n    Print x.\n"
    _assert_js_matches_python("english", src, [77])


# ---- COBOL --------------------------------------------------------------

_COBOL_HEADER = (
    "IDENTIFICATION DIVISION.\n"
    "PROGRAM-ID. TEST.\n"
    "DATA DIVISION.\n"
)


def test_js_cobol_dispatch():
    src = (
        _COBOL_HEADER +
        "01 X PIC 9 VALUE 2.\n"
        "PROCEDURE DIVISION.\n"
        "    DISPATCH X.\n"
        "        WHEN 0.\n"
        "            DISPLAY 100.\n"
        "        WHEN 2.\n"
        "            DISPLAY 300.\n"
        "        WHEN OTHER.\n"
        "            DISPLAY 999.\n"
        "    END-DISPATCH.\n"
        "    STOP RUN.\n"
    )
    _assert_js_matches_python("cobol", src, [300])


def test_js_cobol_try_except_finally_raise():
    src = (
        _COBOL_HEADER +
        "01 X PIC 9(4) VALUE 0.\n"
        "PROCEDURE DIVISION.\n"
        "    TRY.\n"
        "        MOVE 1 TO X.\n"
        "        RAISE 42.\n"
        "        MOVE 999 TO X.\n"
        "    EXCEPT.\n"
        "        COMPUTE X = X + 100.\n"
        "    FINALLY.\n"
        "        COMPUTE X = X + 1000.\n"
        "    END-TRY.\n"
        "    DISPLAY X.\n"
        "    STOP RUN.\n"
    )
    _assert_js_matches_python("cobol", src, [1101])


def test_js_cobol_level_78_const():
    src = (
        _COBOL_HEADER +
        "78 STRIDE VALUE 40.\n"
        "PROCEDURE DIVISION.\n"
        "    DISPLAY STRIDE.\n"
        "    STOP RUN.\n"
    )
    _assert_js_matches_python("cobol", src, [40])


# ---- Report / 4GL ---------------------------------------------------------

def test_js_report_incdec():
    src = "DATA: x TYPE i VALUE 5.\nADD 1 TO x.\nSUBTRACT 1 FROM x.\nADD 1 TO x.\nWRITE x.\n"
    _assert_js_matches_python("report", src, [6])


def test_js_report_try_catch_finally_raise():
    src = (
        "DATA: x TYPE i VALUE 0.\n"
        "TRY.\n"
        "    MOVE 1 TO x.\n"
        "    RAISE 42.\n"
        "    MOVE 999 TO x.\n"
        "CATCH.\n"
        "    COMPUTE x = x + 100.\n"
        "CLEANUP.\n"
        "    COMPUTE x = x + 1000.\n"
        "ENDTRY.\n"
        "WRITE x.\n"
    )
    _assert_js_matches_python("report", src, [1101])


def test_js_report_constants():
    src = "CONSTANTS: retry TYPE i VALUE 3.\nWRITE retry.\n"
    _assert_js_matches_python("report", src, [3])


# ---- Functional -----------------------------------------------------------

def test_js_functional_dispatch():
    src = "let x = 2\ndispatch x with\n| 0 -> printfn 10\n| 2 -> printfn 20\n| _ -> printfn 99\n"
    _assert_js_matches_python("functional", src, [20])


def test_js_functional_try_with_finally_raise():
    src = (
        "let x = 0\n"
        "try\n"
        "    let x = 1\n"
        "    raise 42\n"
        "    let x = 999\n"
        "with\n"
        "    let x = x + 100\n"
        "finally\n"
        "    let x = x + 1000\n"
        "printfn x\n"
    )
    _assert_js_matches_python("functional", src, [1101])


def test_js_functional_const():
    src = "const RETRY = 3\nprintfn RETRY\n"
    _assert_js_matches_python("functional", src, [3])


def test_js_functional_server():
    src = "server do\n    let x = 77\n    printfn x\n"
    _assert_js_matches_python("functional", src, [77])
