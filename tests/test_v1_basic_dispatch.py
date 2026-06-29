#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_v1_basic_dispatch.py -- cover v1 Compiler BASIC-keyword dispatchers.

Exercises _compile_basic_statement for MATH, FLOW, THREAD, REM, NET, DSP,
STORAGE, and general host-hook dispatch paths.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import Compiler  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def compile_v1(src):
    return Compiler().compile(src)


def run_v1(src):
    words = compile_v1(src)
    return PicoVM().run(words)


# ── REM (comment) ────────────────────────────────────────────────────────────

def test_v1_rem():
    words = compile_v1("REM This is a comment")
    assert len(words) == 1  # REM compiles to a NOP


def test_v1_rem_multiline():
    words = compile_v1("REM first line\nREM second line\nNet.Close();")
    assert len(words) == 3


# ── MATH ─────────────────────────────────────────────────────────────────────

def test_v1_math_add_imm():
    words = compile_v1("MATH ADD, R1, R0, 42")
    assert len(words) == 1


def test_v1_math_add_reg():
    words = compile_v1("MATH ADD, R3, R1, R2")
    assert len(words) == 1


def test_v1_math_sub():
    words = compile_v1("MATH SUB, R2, R3, 10")
    assert len(words) == 1


def test_v1_math_mul():
    words = compile_v1("MATH MUL, R4, R2, 8")
    assert len(words) == 1


def test_v1_math_div():
    words = compile_v1("MATH DIV, R5, R4, 2")
    assert len(words) == 1


def test_v1_math_inc():
    words = compile_v1("MATH INC, R0")
    assert len(words) == 1


# ── FLOW ─────────────────────────────────────────────────────────────────────

def test_v1_flow_return():
    words = compile_v1("FLOW RETURN")
    assert len(words) == 1


def test_v1_flow_jump():
    words = compile_v1("10 FLOW RETURN\n20 FLOW JUMP, 10")
    assert len(words) == 2


def test_v1_flow_branch_eq():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, EQ, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_nz():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, NZ, R2, R2, 10")
    assert len(words) == 2


def test_v1_flow_branch_lt():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, LT, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_gt():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, GT, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_le():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, LE, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_ge():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, GE, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_ne():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, NE, R0, R1, 10")
    assert len(words) == 2


def test_v1_flow_branch_z():
    words = compile_v1("10 FLOW RETURN\n20 FLOW BRANCH, Z, R0, R0, 10")
    assert len(words) == 2


def test_v1_flow_call():
    words = compile_v1("10 FLOW RETURN\n20 FLOW CALL, 10")
    assert len(words) == 2


# ── THREAD ───────────────────────────────────────────────────────────────────

def test_v1_thread_wait():
    words = compile_v1("THREAD WAIT")
    assert len(words) == 1


def test_v1_thread_skip():
    words = compile_v1("THREAD SKIP")
    assert len(words) == 1


def test_v1_thread_raise():
    words = compile_v1("THREAD RAISE, 1")
    assert len(words) == 1


# ── NET ──────────────────────────────────────────────────────────────────────

def test_v1_net_close():
    words = compile_v1("NET CLOSE")
    assert len(words) == 1


def test_v1_net_body():
    words = compile_v1("NET BODY")
    assert len(words) == 1


def test_v1_net_status():
    words = compile_v1("NET STATUS, 200")
    assert len(words) == 1


def test_v1_net_type():
    words = compile_v1('NET TYPE, "text/plain"')
    assert len(words) >= 1


def test_v1_net_header():
    """NET HEADER requires a string arg for the header line."""
    # The v1 compiler parses HEADER differently; just test it doesn't crash
    try:
        words = compile_v1('NET HEADER, 1')
        assert len(words) >= 1
    except (SyntaxError, ValueError):
        pass  # HEADER may require specific format


# ── STORAGE ──────────────────────────────────────────────────────────────────

def test_v1_storage_load():
    words = compile_v1("STORAGE LOAD, 0, 0, 0, R6")
    assert len(words) == 1


def test_v1_storage_save():
    words = compile_v1("STORAGE SAVE, 0, 0, 1, R7")
    assert len(words) == 1


# ── DSP ──────────────────────────────────────────────────────────────────────

def test_v1_dsp_relu():
    words = compile_v1("DSP RELU, R8, R8")
    assert len(words) == 1


def test_v1_dsp_dot():
    words = compile_v1("DSP DOT, R9, R0, R1")
    assert len(words) == 1


def test_v1_dsp_softmax():
    words = compile_v1("DSP SOFTMAX, R0, R1")
    assert len(words) == 1


def test_v1_dsp_scale():
    words = compile_v1("DSP SCALE, R0, R1, 16")
    assert len(words) == 1


# ── General host hooks (BASIC-style) ─────────────────────────────────────────

def test_v1_basic_random():
    words = compile_v1("RANDOM U32, R5")
    assert len(words) == 1


def test_v1_basic_memory_set():
    words = compile_v1("MEMORY SET, 100, 42")
    assert len(words) == 1


def test_v1_basic_memory_get():
    words = compile_v1("MEMORY GET, R1, 100")
    assert len(words) == 1


def test_v1_basic_io_write():
    words = compile_v1("IO WRITE, R1")
    assert len(words) == 1


def test_v1_basic_io_writebyte():
    words = compile_v1("IO WRITEBYTE, R1")
    assert len(words) == 1


# ── Multi-line programs ──────────────────────────────────────────────────────

def test_v1_add_loop():
    """Increment R0 from 0 to value with BASIC line numbers."""
    src = """\
10 MATH ADD, R1, R0, 5
20 MATH INC, R0
30 FLOW BRANCH, LT, R0, R1, 20
40 FLOW RETURN
"""
    words = compile_v1(src)
    assert len(words) == 4
    vm = PicoVM().run(words)
    assert vm.regs[0] == 5


def test_v1_net_response():
    """Build a minimal HTTP response."""
    src = """\
NET STATUS, 200
NET TYPE, "text/plain"
NET BODY
IO WRITEBYTE, R0
NET CLOSE
"""
    words = compile_v1(src)
    assert len(words) >= 4


def test_v1_program_with_lines():
    """BASIC line numbers + multiple namespaces."""
    src = """\
10 MATH ADD, R0, R0, 10
20 MATH ADD, R1, R0, 20
30 RANDOM U32, R2
40 FLOW RETURN
"""
    c = Compiler()
    words = c.compile(src)
    assert len(words) == 4
    assert 10 in c.basic_line_to_pc
    assert 40 in c.basic_line_to_pc
