#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_v1_host_hook_encoding.py -- cover all host-hook argument packing in picoscript_lang.py.

Exercises every namespace branch in _compile_host_hook: Kernel, Queue, Random,
Context, Io, Memory (Arena*), Span (Make/Slice/Len/Get/Materialize),
Descriptor (Make/SetFlags/Get*), Lease (Acquire/Release/Validate/GetSpan/GetTypeHint),
Storage (GetSchemaForPack/SetSchemaForPack/AddCard/UpdateCard/DeleteCard/PatchCard/ReadCard/QueryCard).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import Compiler  # noqa: E402


def cv1(src):
    """Compile a v1 program and return the word list."""
    return Compiler().compile(src)


# ── Kernel namespace ─────────────────────────────────────────────────────────

def test_kernel_waitirq():
    assert len(cv1("Kernel.WaitIRQ();")) == 1

def test_kernel_waitirq_with_mask():
    assert len(cv1("Kernel.WaitIRQ(R0);")) == 1

def test_kernel_waitswirq():
    assert len(cv1("Kernel.WaitSWIRQ();")) == 1

def test_kernel_waitswirq_mask():
    assert len(cv1("Kernel.WaitSWIRQ(R1);")) == 1

def test_kernel_fireswirq():
    assert len(cv1("Kernel.FireSWIRQ(R2);")) == 1

def test_kernel_profilestart():
    assert len(cv1("Kernel.ProfileStart(R0);")) == 1

def test_kernel_profileend():
    assert len(cv1("Kernel.ProfileEnd(R0);")) == 1

def test_kernel_tracepoint():
    assert len(cv1("Kernel.TracePoint(R0);")) == 1


# ── Queue namespace ──────────────────────────────────────────────────────────

def test_queue_enqueue():
    assert len(cv1("Queue.Enqueue(0, R1);")) == 1

def test_queue_dequeue():
    assert len(cv1("Queue.Dequeue(0, R2);")) == 1

def test_queue_depth():
    assert len(cv1("Queue.Depth(0, R3);")) == 1


# ── Random namespace ─────────────────────────────────────────────────────────

def test_random_u32():
    assert len(cv1("Random.U32(R4);")) == 1


# ── Context namespace ────────────────────────────────────────────────────────

def test_context_getpath():
    assert len(cv1("Context.GetPath(R5);")) == 1

def test_context_gethost():
    assert len(cv1("Context.GetHost(R6);")) == 1


# ── Io namespace ─────────────────────────────────────────────────────────────

def test_io_write():
    assert len(cv1("Io.Write(R0);")) == 1

def test_io_writebyte():
    assert len(cv1("Io.WriteByte(R1);")) == 1


# ── Memory namespace (Arena ops) ─────────────────────────────────────────────

def test_memory_arenainit():
    assert len(cv1("Memory.ArenaInit(R0, R1, R2);")) == 1

def test_memory_arenaalloc():
    assert len(cv1("Memory.ArenaAlloc(R0, R1, R2);")) == 1

def test_memory_arenareset():
    assert len(cv1("Memory.ArenaReset(R0);")) == 1

def test_memory_arenastats():
    assert len(cv1("Memory.ArenaStats(R0, R1);")) == 1

def test_memory_set():
    assert len(cv1("Memory.Set(100, 42);")) == 1

def test_memory_get():
    assert len(cv1("Memory.Get(R0, 100);")) == 1


# ── Span namespace ───────────────────────────────────────────────────────────

def test_span_make():
    assert len(cv1("Span.Make(R0, R1, R2);")) == 1

def test_span_slice():
    assert len(cv1("Span.Slice(R0, R1, R2);")) == 1

def test_span_len():
    assert len(cv1("Span.Len(R0, R1);")) == 1

def test_span_get():
    assert len(cv1("Span.Get(R0, R1, R2);")) == 1

def test_span_materialize():
    assert len(cv1("Span.Materialize(R0, R1);")) == 1


# ── Descriptor namespace ─────────────────────────────────────────────────────

def test_descriptor_make():
    assert len(cv1("Descriptor.Make(R0, R1, R2);")) == 1

def test_descriptor_setflags():
    assert len(cv1("Descriptor.SetFlags(R0, R1);")) == 1

def test_descriptor_getptr():
    assert len(cv1("Descriptor.GetPtr(R0, R1);")) == 1

def test_descriptor_getlen():
    assert len(cv1("Descriptor.GetLen(R0, R1);")) == 1

def test_descriptor_getflags():
    assert len(cv1("Descriptor.GetFlags(R0, R1);")) == 1


# ── Lease namespace ──────────────────────────────────────────────────────────

def test_lease_acquire():
    assert len(cv1("Lease.Acquire(R0, R1, R2);")) == 1

def test_lease_release():
    assert len(cv1("Lease.Release(R0);")) == 1

def test_lease_validate():
    assert len(cv1("Lease.Validate(R0, R1);")) == 1

def test_lease_getspan():
    assert len(cv1("Lease.GetSpan(R0, R1);")) == 1

def test_lease_gettypehint():
    assert len(cv1("Lease.GetTypeHint(R0, R1);")) == 1


# ── Storage namespace (card ops) ─────────────────────────────────────────────

def test_storage_getschema():
    assert len(cv1("Storage.GetSchemaForPack(R0, R1);")) == 1

def test_storage_setschema():
    assert len(cv1("Storage.SetSchemaForPack(R0, R1);")) == 1

def test_storage_addcard():
    assert len(cv1("Storage.AddCard(R0, R1, R2);")) == 1

def test_storage_updatecard():
    assert len(cv1("Storage.UpdateCard(R0, R1, R2);")) == 1

def test_storage_deletecard():
    assert len(cv1("Storage.DeleteCard(R0, R1);")) == 1

def test_storage_patchcard():
    assert len(cv1("Storage.PatchCard(R0, R1, R2);")) == 1

def test_storage_readcard():
    assert len(cv1("Storage.ReadCard(R0, R1, R2);")) == 1

def test_storage_querycard():
    assert len(cv1("Storage.QueryCard(R0, R1, R2);")) == 1


# ── Error cases ──────────────────────────────────────────────────────────────

def test_unknown_namespace_error():
    import pytest
    with pytest.raises(SyntaxError, match="Unknown"):
        cv1("FakeNamespace.Method(R0);")

def test_unknown_method_error():
    import pytest
    with pytest.raises(SyntaxError):
        cv1("Memory.NonExistentMethod(R0);")

def test_queue_wrong_args_error():
    import pytest
    with pytest.raises(SyntaxError):
        cv1("Queue.Enqueue(0);")  # needs 2 args

def test_random_wrong_args_error():
    import pytest
    with pytest.raises(SyntaxError):
        cv1("Random.U32();")  # needs 1 arg
