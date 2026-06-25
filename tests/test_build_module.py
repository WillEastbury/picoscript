#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_build_module.py -- coverage for picoscript_build.py.

Tests detect_lang, to_il, to_bytecode, decode_output.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_build import detect_lang, to_il, to_bytecode, decode_output  # noqa: E402


# ── detect_lang ──────────────────────────────────────────────────────────────

def test_detect_c():
    """detect_lang identifies C by extension."""
    assert detect_lang("test.pc", None) == "c"


def test_detect_basic():
    """detect_lang identifies BASIC by extension."""
    assert detect_lang("test.pbas", None) == "basic"


def test_detect_python():
    """detect_lang identifies Python by extension."""
    assert detect_lang("test.ppy", None) == "python"


def test_detect_english():
    """detect_lang identifies English by extension."""
    assert detect_lang("test.eng", None) == "english"


def test_detect_cobol():
    """detect_lang defaults to C for unknown extensions."""
    assert detect_lang("test.pcob", None) == "c"  # no COBOL extension registered


def test_detect_functional():
    """detect_lang respects forced language."""
    assert detect_lang("anything.txt", "functional") == "functional"


# ── to_il ────────────────────────────────────────────────────────────────────

def test_to_il_c():
    """to_il compiles C source to IL."""
    il = to_il("print(42);", "c")
    assert len(il) > 0


def test_to_il_basic():
    """to_il compiles BASIC source to IL."""
    il = to_il("PRINT 42", "basic")
    assert len(il) > 0


def test_to_il_python():
    """to_il compiles Python source to IL."""
    il = to_il("print(42)", "python")
    assert len(il) > 0


# ── to_bytecode ──────────────────────────────────────────────────────────────

def test_to_bytecode_c():
    """to_bytecode compiles C source to bytecode words."""
    words = to_bytecode("print(42);", "c")
    assert len(words) > 0
    assert all(isinstance(w, int) for w in words)


def test_to_bytecode_basic():
    """to_bytecode compiles BASIC source."""
    words = to_bytecode("PRINT 42", "basic")
    assert len(words) > 0


# ── decode_output ────────────────────────────────────────────────────────────

def test_decode_output_ints():
    """decode_output decodes 4-byte big-endian integers."""
    from picoscript_vm import PicoVM
    from picoscript_il import lower_to_bytecode_safe
    from picoscript_cfront import compile_c
    words = lower_to_bytecode_safe(compile_c("print(42); print(7);"))
    vm = PicoVM().run(words)
    result = decode_output(vm)
    assert result == [42, 7]
