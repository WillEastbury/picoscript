#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_decompiler_deep.py -- exercise every branch in decompile_basic/python.

Compiles programs that produce each opcode type, then decompiles them to
exercise every formatting branch in the decompiler (lines 2896-3151).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    Compiler, decompile_basic, decompile_python,
)


def cv1(src):
    return Compiler().compile(src)


# ── Every opcode branch in decompile_basic ───────────────────────────────────

def test_decompile_basic_thread_skip():
    words = cv1("THREAD SKIP")
    out = decompile_basic(words)
    assert "THREAD" in out and "SKIP" in out


def test_decompile_basic_thread_wait():
    words = cv1("THREAD WAIT")
    out = decompile_basic(words)
    assert "THREAD" in out and "WAIT" in out


def test_decompile_basic_thread_raise():
    words = cv1("THREAD RAISE, 7")
    out = decompile_basic(words)
    assert "THREAD" in out and "RAISE" in out and "7" in out


def test_decompile_basic_storage_load():
    words = cv1("STORAGE LOAD, 1, 2, 3, R4")
    out = decompile_basic(words)
    assert "STORAGE" in out and "LOAD" in out


def test_decompile_basic_storage_save():
    words = cv1("STORAGE SAVE, 0, 1, 0, R5")
    out = decompile_basic(words)
    assert "STORAGE" in out and "SAVE" in out


def test_decompile_basic_math_add_imm():
    words = cv1("MATH ADD, R1, R0, 42")
    out = decompile_basic(words)
    assert "MATH" in out and "ADD" in out and "42" in out


def test_decompile_basic_math_add_reg():
    words = cv1("MATH ADD, R3, R1, R2")
    out = decompile_basic(words)
    assert "MATH" in out and "ADD" in out


def test_decompile_basic_math_sub():
    words = cv1("MATH SUB, R2, R1, 10")
    out = decompile_basic(words)
    assert "MATH" in out and "SUB" in out


def test_decompile_basic_math_mul():
    words = cv1("MATH MUL, R4, R2, 8")
    out = decompile_basic(words)
    assert "MUL" in out


def test_decompile_basic_math_div():
    words = cv1("MATH DIV, R5, R3, 4")
    out = decompile_basic(words)
    assert "DIV" in out


def test_decompile_basic_math_inc():
    words = cv1("MATH INC, R0")
    out = decompile_basic(words)
    assert "INC" in out


def test_decompile_basic_flow_jump():
    words = cv1("10 FLOW RETURN\n20 FLOW JUMP, 10")
    out = decompile_basic(words)
    assert "FLOW" in out and "JUMP" in out


def test_decompile_basic_flow_call():
    words = cv1("10 FLOW RETURN\n20 FLOW CALL, 10")
    out = decompile_basic(words)
    assert "CALL" in out


def test_decompile_basic_flow_branch():
    words = cv1("10 FLOW RETURN\n20 FLOW BRANCH, EQ, R0, R1, 10")
    out = decompile_basic(words)
    assert "BRANCH" in out and "EQ" in out


def test_decompile_basic_flow_return():
    words = cv1("FLOW RETURN")
    out = decompile_basic(words)
    assert "RETURN" in out


def test_decompile_basic_dsp_with_imm():
    words = cv1("DSP SCALE, R0, R1, 16")
    out = decompile_basic(words)
    assert "DSP" in out and "SCALE" in out and "16" in out


def test_decompile_basic_dsp_no_imm():
    words = cv1("DSP RELU, R0, R1")
    out = decompile_basic(words)
    assert "DSP" in out and "RELU" in out


def test_decompile_basic_net_status():
    words = cv1("NET STATUS, 200")
    out = decompile_basic(words)
    assert "NET" in out and "STATUS" in out and "200" in out


def test_decompile_basic_net_body():
    words = cv1("NET BODY")
    out = decompile_basic(words)
    assert "NET" in out and "BODY" in out


def test_decompile_basic_net_close():
    words = cv1("NET CLOSE")
    out = decompile_basic(words)
    assert "NET" in out and "CLOSE" in out


def test_decompile_basic_kernel_waitirq():
    words = cv1("Kernel.WaitIRQ(R0);")
    out = decompile_basic(words)
    assert "KERNEL" in out and "WAIT" in out.upper()


def test_decompile_basic_kernel_fire():
    words = cv1("Kernel.FireSWIRQ(R2);")
    out = decompile_basic(words)
    assert "KERNEL" in out and "FIRE" in out.upper()


def test_decompile_basic_queue():
    words = cv1("Queue.Enqueue(0, R1);")
    out = decompile_basic(words)
    assert "QUEUE" in out


def test_decompile_basic_random():
    words = cv1("Random.U32(R3);")
    out = decompile_basic(words)
    assert "RANDOM" in out


def test_decompile_basic_memory_arena():
    words = cv1("Memory.ArenaInit(R0, R1, R2);")
    out = decompile_basic(words)
    assert "MEMORY" in out and "ARENA" in out.upper()


def test_decompile_basic_memory_reset():
    words = cv1("Memory.ArenaReset(R0);")
    out = decompile_basic(words)
    assert "MEMORY" in out and "ARENA_RESET" in out


def test_decompile_basic_memory_stats():
    words = cv1("Memory.ArenaStats(R0, R1);")
    out = decompile_basic(words)
    assert "MEMORY" in out and "ARENA_STATS" in out


def test_decompile_basic_span_make():
    words = cv1("Span.Make(R0, R1, R2);")
    out = decompile_basic(words)
    assert "SPAN" in out and "MAKE" in out


def test_decompile_basic_span_slice():
    words = cv1("Span.Slice(R0, R1, R2);")
    out = decompile_basic(words)
    assert "SPAN" in out and "SLICE" in out


def test_decompile_basic_descriptor_make():
    words = cv1("Descriptor.Make(R0, R1, R2);")
    out = decompile_basic(words)
    assert "DESCRIPTOR" in out and "MAKE" in out


def test_decompile_basic_descriptor_setflags():
    words = cv1("Descriptor.SetFlags(R0, R1);")
    out = decompile_basic(words)
    assert "DESCRIPTOR" in out and "SET_FLAGS" in out


def test_decompile_basic_descriptor_getptr():
    words = cv1("Descriptor.GetPtr(R0, R1);")
    out = decompile_basic(words)
    assert "DESCRIPTOR" in out and "GETPTR" in out.upper()


def test_decompile_basic_lease_acquire():
    words = cv1("Lease.Acquire(R0, R1, R2);")
    out = decompile_basic(words)
    assert "LEASE" in out and "ACQUIRE" in out


def test_decompile_basic_lease_release():
    words = cv1("Lease.Release(R0);")
    out = decompile_basic(words)
    assert "LEASE" in out and "RELEASE" in out


def test_decompile_basic_lease_validate():
    words = cv1("Lease.Validate(R0, R1);")
    out = decompile_basic(words)
    assert "LEASE" in out and "VALIDATE" in out


def test_decompile_basic_storage_addcard():
    words = cv1("Storage.AddCard(R0, R1, R2);")
    out = decompile_basic(words)
    assert "STORAGE" in out and "ADDCARD" in out.upper()


def test_decompile_basic_storage_deletecard():
    words = cv1("Storage.DeleteCard(R0, R1);")
    out = decompile_basic(words)
    assert "STORAGE" in out and "DELETECARD" in out.upper()


def test_decompile_basic_storage_getschema():
    words = cv1("Storage.GetSchemaForPack(R0, R1);")
    out = decompile_basic(words)
    assert "STORAGE" in out and "GET_SCHEMA" in out.upper()


# ── decompile_python branches ────────────────────────────────────────────────

def test_decompile_python_math():
    words = cv1("MATH ADD, R1, R0, 42\nMATH MUL, R2, R1, R3\nMATH INC, R4")
    out = decompile_python(words)
    assert len(out.strip()) > 0


def test_decompile_python_flow():
    words = cv1("10 FLOW RETURN\n20 FLOW JUMP, 10\n30 FLOW BRANCH, NE, R0, R1, 10")
    out = decompile_python(words)
    assert len(out.strip()) > 0


def test_decompile_python_storage_load():
    words = cv1("STORAGE LOAD, 0, 0, 0, R0")
    out = decompile_python(words)
    assert "storage" in out.lower() or "load" in out.lower()


def test_decompile_python_dsp():
    words = cv1("DSP MATMUL, R0, R1, 16\nDSP GELU, R2, R3")
    out = decompile_python(words)
    assert len(out.strip()) > 0
