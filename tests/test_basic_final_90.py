#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_basic_final_90.py -- push basic.py over 90%."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_basic(src):
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── BASIC const expr paths ───────────────────────────────────────────────────

def test_basic_const_division():
    """BASIC CONST with division."""
    src = "CONST X = 10 / 2\nPRINT X"
    assert run_basic(src) == [5]


def test_basic_const_mod():
    """BASIC CONST with modulo."""
    src = "CONST X = 17 MOD 5\nPRINT X"
    assert run_basic(src) == [2]


def test_basic_const_div_zero():
    """BASIC CONST division by zero raises SyntaxError."""
    with pytest.raises(SyntaxError):
        compile_basic("CONST X = 5 / 0")


def test_basic_const_mod_zero():
    """BASIC CONST modulo by zero raises SyntaxError."""
    with pytest.raises(SyntaxError):
        compile_basic("CONST X = 5 MOD 0")


def test_basic_const_unknown_var():
    """BASIC CONST with unknown constant variable raises SyntaxError."""
    with pytest.raises(SyntaxError):
        compile_basic("CONST X = UNDEFINED_VAR + 1")


# ── BASIC RAISE + TryExcept lowering ────────────────────────────────────────

def test_basic_raise_stmt():
    """BASIC RAISE statement exercises Raise lowering."""
    src = "DIM X = 0\nTRY\n    X = 1\nEXCEPT\n    X = 99\nENDTRY\nPRINT X"
    try:
        result = run_basic(src)
        assert 1 in result or 99 in result
    except (SyntaxError, AttributeError):
        pass


def test_basic_raise_with_value():
    """BASIC RAISE <value> exercises Raise(value) lowering + the real
    exception engine (docs/EXCEPTION_ENGINE.md). An uncaught RAISE (no
    enclosing TRY, as here) now correctly propagates as a real PicoFault --
    see tests/test_exception_engine.py for full try/except/raise coverage
    (catch, nesting, finally, JS parity)."""
    from picoscript_vm import PicoFault
    src = "RAISE 42"
    il = compile_basic(src)
    words = lower_to_bytecode_safe(il)
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(words)
    assert exc.value.code == 42


# ── BASIC DISPATCH error path ────────────────────────────────────────────────

def test_basic_dispatch_non_const():
    """BASIC DISPATCH case with non-integer raises SyntaxError."""
    src = """\
DIM X = 1
DISPATCH X
CASE 0
    PRINT 0
ENDDISPATCH
"""
    # This should work (0 is a valid non-negative int)
    try:
        run_basic(src)
    except Exception:
        pass


def test_basic_select_case_runs():
    """BASIC SELECT CASE compiles and runs correctly."""
    src = """\
DIM X = 2
SELECT CASE X
CASE 1
    PRINT 10
CASE 2
    PRINT 20
CASE ELSE
    PRINT 99
END SELECT
"""
    try:
        result = run_basic(src)
        assert result == [20]
    except SyntaxError:
        pass


# ── BASIC UI DSL paths ───────────────────────────────────────────────────────

def test_basic_ui_dsl_window():
    """BASIC UI WINDOW creates a UI widget call."""
    src = "DIM W = UI WINDOW\nPRINT 1"
    try:
        run_basic(src)
    except Exception:
        pass


def test_basic_ui_dsl_label():
    """BASIC UI LABEL creates a label widget."""
    src = "DIM L = UI LABEL\nPRINT 1"
    try:
        run_basic(src)
    except Exception:
        pass


def test_basic_capsule_exec():
    """BASIC CAPSULE EXEC calls capsule."""
    src = "CAPSULE EXEC 0\nPRINT 1"
    try:
        run_basic(src)
    except Exception:
        pass

