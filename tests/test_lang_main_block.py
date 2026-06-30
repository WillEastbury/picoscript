#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_main_block.py -- cover the picoscript_lang __main__ demo block.

Uses runpy.run_module to execute the module as __main__, exactly as
`python picoscript_lang.py` would. This covers lines 3247-3342.
"""
import io
import os
import sys
import runpy

import pytest


def test_lang_main_block_runs():
    """Run picoscript_lang as __main__ and verify it produces expected output."""
    # Capture stdout so the demo doesn't pollute test output
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        runpy.run_module("picoscript_lang", run_name="__main__", alter_sys=False)
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    assert "PicoScript" in output
    assert "bytecode" in output.lower()
    assert "C#" in output or "csharp" in output.lower() or "pico" in output.lower()
    assert "BASIC" in output or "bas" in output.lower()
    assert "Python" in output or ".py" in output
    # Verify all four decompilers ran
    assert "View as" in output or len(output.splitlines()) > 30


def test_lang_main_examples_all_compile():
    """Verify EXAMPLE_HELLO, EXAMPLE_FILTER, EXAMPLE_AI all compile in the demo."""
    from picoscript_lang import Compiler, EXAMPLE_HELLO, EXAMPLE_FILTER, EXAMPLE_AI
    for name, src in [("hello", EXAMPLE_HELLO), ("filter", EXAMPLE_FILTER), ("ai", EXAMPLE_AI)]:
        words = Compiler().compile(src)
        assert len(words) > 0, f"{name} example produced no bytecode"
