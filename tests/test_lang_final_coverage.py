#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_final_coverage.py -- final targeted coverage for picoscript_lang.py.

Target the remaining ~15% gaps:
1. _canonical_named_constant_key: DST./CURRENCY./CURRENCYMINOR./COUNTRY./UOM. prefixes (1866,1870,1874)
2. _compile_instruction: DSP tuple path (2213-2214), Thread dispatch (2224),
   dot_pos>paren_pos error (2194), unknown namespace (2234)
3. _compile_thread: Thread.Raise C-style (2250-2253)
4. _compile_math: third arg neither imm nor reg (2268), raise SyntaxError path
5. _compile_flow: unknown method (2291), _resolve_label out-of-range (2298)
6. _resolve_basic_line ValueError (2306-2307)
7. _compile_net C-style: unknown Type, Header, error (2324-2333)
8. _compile_dsp: with third arg (imm/reg) (2340-2345)
9. BASIC statement: empty (2353), NET TYPE/hex + error (2382,2386,2395)
10. BASIC thread/storage/math/flow/dsp unknown method errors (2404,2409,2421,2444,2448,2457)
11. BASIC host hook: unknown namespace/method (2463,2466)
12. _compile_host_hook: Kernel error paths (2505,2514,2521,2524,2530,2539)
13. Context/Io/Memory/Span/Descriptor/Lease/Storage validation errors
14. decompile_csharp: unknown hook_id (2746->2804), Kernel.WaitIRQ ADDR_IMMEDIATE (2752,2754),
    FireSWIRQ else (2759), Net.Type hex (2810), unknown opcode (2857)
15. decompile_basic: unknown hook_id (2909->2959), unknown opcode (3009)
16. decompile_python: Net.header (3110), unknown opcode (3149)
"""
import pytest
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    _canonical_named_constant_key,
    Compiler,
    decompile_basic, decompile_csharp, decompile_python,
    encode_instruction, OP_NOOP, OP_WAIT, OP_RAISE,
    HOST_HOOK_BASE, EXT_HOST_HOOK_BASE, NET_HEADER_BASE,
    ADDR_IMMEDIATE, ADDR_REGISTER,
)


def cv1(src):
    return Compiler().compile(src)


def _roundtrip(src):
    words = cv1(src)
    return words, decompile_csharp(words), decompile_basic(words), decompile_python(words)


# ══════════════════════════════════════════════════════════════════════════════
# 1. _canonical_named_constant_key: remaining prefix branches (1866,1870,1874)
# ══════════════════════════════════════════════════════════════════════════════

def test_canonical_key_dst_dot():
    """DST.ACTIVE -> DST_ACTIVE (line 1866)."""
    r = _canonical_named_constant_key("DST.ACTIVE")
    assert r == "DST_ACTIVE"


def test_canonical_key_currency_dot():
    """CURRENCY.EUR -> CURRENCY_EUR (line 1868)."""
    r = _canonical_named_constant_key("CURRENCY.EUR")
    assert r == "CURRENCY_EUR"


def test_canonical_key_currencyminor_dot():
    """CURRENCYMINOR.USD -> CURRENCY_MINOR_USD (line 1870)."""
    r = _canonical_named_constant_key("CURRENCYMINOR.USD")
    assert r == "CURRENCY_MINOR_USD"


def test_canonical_key_country_dot():
    """COUNTRY.GB -> COUNTRY_GB (line 1872)."""
    r = _canonical_named_constant_key("COUNTRY.GB")
    assert r == "COUNTRY_GB"


def test_canonical_key_uom_dot():
    """UOM.KG -> UOM_KG (line 1874)."""
    r = _canonical_named_constant_key("UOM.KG")
    assert r == "UOM_KG"


# ══════════════════════════════════════════════════════════════════════════════
# 2. _compile_instruction: DSP tuple path (lines 2213-2214)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_instruction_dsp_tuple():
    """Dsp.MatMul(R0, R1) hits isinstance(op_entry, tuple) path (2213-2214)."""
    words = cv1("Dsp.MatMul(R0, R1);")
    assert len(words) == 1


def test_compile_instruction_dsp_dot():
    """Dsp.Softmax(R0, R1) hits DSP tuple path."""
    words = cv1("Dsp.Softmax(R0, R1);")
    assert len(words) == 1


def test_compile_instruction_dsp_with_imm():
    """Dsp.Dot(R0, R1, 8) hits DSP tuple path with third imm arg."""
    words = cv1("Dsp.Dot(R0, R1, 8);")
    assert len(words) == 1


def test_compile_instruction_dsp_with_reg():
    """Dsp.Dot(R0, R1, R2) hits DSP tuple path with third reg arg."""
    words = cv1("Dsp.Dot(R0, R1, R2);")
    assert len(words) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. _compile_instruction: Thread dispatch (line 2224)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_instruction_thread_wait():
    """Thread.Wait() hits Thread dispatch path (line 2224)."""
    words = cv1("Thread.Wait();")
    assert len(words) == 1


def test_compile_instruction_thread_raise():
    """Thread.Raise(5) hits Thread dispatch + _compile_thread Raise branch (2250-2253)."""
    words = cv1("Thread.Raise(5);")
    assert len(words) == 1


def test_compile_instruction_thread_skip():
    """Thread.Skip() hits Thread dispatch."""
    words = cv1("Thread.Skip();")
    assert len(words) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. _compile_instruction: dot_pos > paren_pos error (line 2194)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_instruction_paren_before_dot():
    """'Foo(Bar.X)' - paren before dot → SyntaxError (line 2194)."""
    with pytest.raises(SyntaxError):
        cv1("Foo(Bar.X);")


# ══════════════════════════════════════════════════════════════════════════════
# 5. _compile_flow: unknown method (line 2291)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_flow_unknown_method():
    """Flow.Unknown() → SyntaxError (line 2291)."""
    with pytest.raises(SyntaxError, match="Flow"):
        cv1("Flow.Unknown();")


# ══════════════════════════════════════════════════════════════════════════════
# 6. _resolve_label: out-of-range numeric target (line 2298)
# ══════════════════════════════════════════════════════════════════════════════

def test_resolve_label_out_of_range():
    """Flow.Jump(:99) where PC 99 doesn't exist → SyntaxError (line 2298)."""
    with pytest.raises(SyntaxError):
        cv1("Flow.Return();\nFlow.Jump(:99);")


# ══════════════════════════════════════════════════════════════════════════════
# 7. _resolve_basic_line: non-numeric target → ValueError → SyntaxError (2306-2307)
# ══════════════════════════════════════════════════════════════════════════════

def test_resolve_basic_line_non_numeric():
    """BASIC FLOW JUMP with non-numeric target → SyntaxError (line 2307)."""
    with pytest.raises(SyntaxError):
        cv1("10 FLOW RETURN\n20 FLOW JUMP, notanumber")


# ══════════════════════════════════════════════════════════════════════════════
# 8. _compile_net: unknown Type / Header (lines 2324-2333)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_net_header():
    """Net.Header() compiles (line 2327-2328)."""
    words = cv1("Net.Header();")
    assert len(words) == 1


def test_compile_net_header_with_arg():
    """Net.Header(0xB001) compiles."""
    words = cv1("Net.Header(0xB001);")
    assert len(words) == 1


def test_compile_net_type_numeric():
    """Net.Type(0xA001) compiles with numeric arg (line 2320)."""
    words = cv1("Net.Type(0xA001);")
    assert len(words) == 1


def test_compile_net_unknown_method():
    """Net.Bogus() → SyntaxError (line 2333)."""
    with pytest.raises(SyntaxError):
        cv1("Net.Bogus();")


# ══════════════════════════════════════════════════════════════════════════════
# 9. _compile_dsp with third arg (lines 2340-2345)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_dsp_with_third_imm():
    """Dsp.Scale(R0, R1, 16) third arg is imm (line 2344-2345)."""
    words = cv1("Dsp.Scale(R0, R1, 16);")
    assert len(words) == 1
    cs = decompile_csharp(words)
    assert "dsp" in cs.lower()


def test_compile_dsp_with_third_reg():
    """Dsp.Scale(R0, R1, R2) third arg is reg (line 2342-2343)."""
    words = cv1("Dsp.Scale(R0, R1, R2);")
    assert len(words) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 10. BASIC: NET TYPE/hex and error paths (lines 2382, 2386, 2395)
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_net_type_hex_prefix():
    """BASIC NET TYPE, TYPE/0x1 compiles (line 2382)."""
    words = cv1('10 NET TYPE, "TYPE/0x1"')
    assert len(words) == 1


def test_basic_net_type_unknown_raises():
    """BASIC NET TYPE, unknown → SyntaxError (line 2386)."""
    with pytest.raises(SyntaxError):
        cv1('10 NET TYPE, "bogus/type"')


def test_basic_net_unknown_method_raises():
    """BASIC NET BOGUS → SyntaxError (line 2395)."""
    with pytest.raises(SyntaxError):
        cv1("10 NET BOGUS")


# ══════════════════════════════════════════════════════════════════════════════
# 11. BASIC thread/storage/math/flow/dsp unknown method errors
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_thread_unknown_method():
    """BASIC THREAD BOGUS → SyntaxError (line 2404)."""
    with pytest.raises(SyntaxError):
        cv1("10 THREAD BOGUS")


def test_basic_storage_unknown_method():
    """BASIC STORAGE BOGUS → SyntaxError (line 2409)."""
    with pytest.raises(SyntaxError):
        cv1("10 STORAGE BOGUS, 0, 0, 0, R0")


def test_basic_math_unknown_method():
    """BASIC MATH BOGUS → SyntaxError (line 2421)."""
    with pytest.raises(SyntaxError):
        cv1("10 MATH BOGUS, R0, R1, R2")


def test_basic_flow_unknown_method():
    """BASIC FLOW BOGUS → SyntaxError (line 2444)."""
    with pytest.raises(SyntaxError):
        cv1("10 FLOW BOGUS")


def test_basic_dsp_unknown_method():
    """BASIC DSP BOGUS → SyntaxError (line 2448)."""
    with pytest.raises(SyntaxError):
        cv1("10 DSP BOGUS, R0, R1")


def test_basic_dsp_third_arg_invalid():
    """BASIC DSP MATMUL R0, R1, 'notvalid' → SyntaxError (line 2457)."""
    with pytest.raises((SyntaxError, ValueError)):
        # Pass a non-imm/reg third arg type (content-type string that is a 'ctype')
        cv1('10 DSP MATMUL, R0, R1, "text/html"')


# ══════════════════════════════════════════════════════════════════════════════
# 12. BASIC host hook: unknown namespace/method (lines 2463, 2466)
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_host_hook_unknown_namespace():
    """BASIC BOGUSNS METHOD → SyntaxError (line 2463)."""
    with pytest.raises(SyntaxError):
        cv1("10 BOGUSNS METHOD, R0")


def test_basic_host_hook_unknown_method():
    """BASIC KERNEL BOGUSMETHOD → SyntaxError (line 2466)."""
    with pytest.raises(SyntaxError):
        cv1("10 KERNEL BOGUSMETHOD, R0")


# ══════════════════════════════════════════════════════════════════════════════
# 13. _compile_host_hook: Kernel error paths (lines 2505,2514,2521,2524,2530,2539)
# ══════════════════════════════════════════════════════════════════════════════

def test_kernel_wait_irq_too_many_args():
    """Kernel.WaitIRQ(R0, R1) with too many args → SyntaxError (line 2505)."""
    with pytest.raises(SyntaxError):
        cv1("Kernel.WaitIRQ(R0, R1);")


def test_kernel_wait_irq_non_register_arg():
    """Kernel.WaitIRQ(42) with non-register arg → SyntaxError (line 2514)."""
    with pytest.raises(SyntaxError):
        cv1("Kernel.WaitIRQ(42);")


def test_kernel_fire_swirq_wrong_arg_count():
    """Kernel.FireSWIRQ() with no args → SyntaxError (line 2521)."""
    with pytest.raises(SyntaxError):
        cv1("Kernel.FireSWIRQ();")


def test_kernel_fire_swirq_non_register_arg():
    """Kernel.FireSWIRQ(42) with imm arg → SyntaxError (line 2530)."""
    with pytest.raises(SyntaxError):
        cv1("Kernel.FireSWIRQ(42);")


def test_queue_wrong_arg_count():
    """Queue.Enqueue() with no args → SyntaxError (line 2533)."""
    with pytest.raises(SyntaxError):
        cv1("Queue.Enqueue(0);")  # only 1 arg, needs 2


def test_queue_first_arg_not_imm():
    """Queue.Enqueue(R0, R1) with register queue_id → SyntaxError (line 2536)."""
    with pytest.raises(SyntaxError):
        cv1("Queue.Enqueue(R0, R1);")


def test_queue_second_arg_not_reg():
    """Queue.Enqueue(0, 42) with immediate second arg → SyntaxError (line 2539)."""
    with pytest.raises(SyntaxError):
        cv1("Queue.Enqueue(0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 14. Context/Io error paths (lines 2554, 2561)
# ══════════════════════════════════════════════════════════════════════════════

def test_context_wrong_arg_count():
    """Context.GetPath() with no args → SyntaxError (line 2551)."""
    with pytest.raises(SyntaxError):
        cv1("Context.GetPath();")


def test_context_non_register_arg():
    """Context.GetPath(42) with imm arg → SyntaxError (line 2554)."""
    with pytest.raises(SyntaxError):
        cv1("Context.GetPath(42);")


def test_io_wrong_arg_count():
    """Io.Write() with no args → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Io.Write();")


def test_io_non_register_arg():
    """Io.Write(42) with imm arg → SyntaxError (line 2561)."""
    with pytest.raises(SyntaxError):
        cv1("Io.Write(42);")


# ══════════════════════════════════════════════════════════════════════════════
# 15. Memory validation errors (lines 2569-2590)
# ══════════════════════════════════════════════════════════════════════════════

def test_memory_arena_init_wrong_arg_count():
    """Memory.ArenaInit() with wrong arg count → SyntaxError (line 2566)."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaInit(R0, R1);")


def test_memory_arena_init_non_register():
    """Memory.ArenaInit(R0, R1, 42) with imm third arg → SyntaxError (line 2569)."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaInit(R0, R1, 42);")


def test_memory_arena_alloc_wrong_arg_count():
    """Memory.ArenaAlloc(R0) → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaAlloc(R0);")


def test_memory_arena_alloc_non_register():
    """Memory.ArenaAlloc(R0, 42, R2) → SyntaxError (line 2576)."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaAlloc(R0, 42, R2);")


def test_memory_arena_reset_wrong_arg_count():
    """Memory.ArenaReset() → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaReset();")


def test_memory_arena_reset_non_register():
    """Memory.ArenaReset(42) → SyntaxError (line 2583)."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaReset(42);")


def test_memory_arena_stats_wrong_arg_count():
    """Memory.ArenaStats(R0) → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaStats(R0);")


def test_memory_arena_stats_non_register():
    """Memory.ArenaStats(R0, 42) → SyntaxError (line 2590)."""
    with pytest.raises(SyntaxError):
        cv1("Memory.ArenaStats(R0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 16. Span validation errors (lines 2595-2626)
# ══════════════════════════════════════════════════════════════════════════════

def test_span_make_wrong_arg_count():
    """Span.Make(R0, R1) → SyntaxError (line 2595)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Make(R0, R1);")


def test_span_make_non_register():
    """Span.Make(R0, R1, 42) → SyntaxError (line 2598)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Make(R0, R1, 42);")


def test_span_slice_wrong_arg_count():
    """Span.Slice(R0, R1) → SyntaxError (line 2602)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Slice(R0, R1);")


def test_span_slice_non_register():
    """Span.Slice(R0, R1, 42) → SyntaxError (line 2605)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Slice(R0, R1, 42);")


def test_span_len_wrong_arg_count():
    """Span.Len(R0) → SyntaxError (line 2609)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Len(R0);")


def test_span_len_non_register():
    """Span.Len(R0, 42) → SyntaxError (line 2612)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Len(R0, 42);")


def test_span_get_wrong_arg_count():
    """Span.Get(R0, R1) → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Span.Get(R0, R1);")


def test_span_get_non_register():
    """Span.Get(R0, R1, 42) → SyntaxError (line 2619)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Get(R0, R1, 42);")


def test_span_materialize_wrong_arg_count():
    """Span.Materialize(R0) → SyntaxError (line 2623)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Materialize(R0);")


def test_span_materialize_non_register():
    """Span.Materialize(R0, 42) → SyntaxError (line 2626)."""
    with pytest.raises(SyntaxError):
        cv1("Span.Materialize(R0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 17. Descriptor validation errors (lines 2631-2648)
# ══════════════════════════════════════════════════════════════════════════════

def test_descriptor_make_wrong_arg_count():
    """Descriptor.Make(R0, R1) → SyntaxError (line 2631)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.Make(R0, R1);")


def test_descriptor_make_non_register():
    """Descriptor.Make(R0, R1, 42) → SyntaxError (line 2634)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.Make(R0, R1, 42);")


def test_descriptor_setflags_wrong_arg_count():
    """Descriptor.SetFlags(R0) → SyntaxError (line 2638)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.SetFlags(R0);")


def test_descriptor_setflags_non_register():
    """Descriptor.SetFlags(R0, 42) → SyntaxError (line 2641)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.SetFlags(R0, 42);")


def test_descriptor_getptr_wrong_arg_count():
    """Descriptor.GetPtr(R0) → SyntaxError (line 2645)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.GetPtr(R0);")


def test_descriptor_getptr_non_register():
    """Descriptor.GetPtr(R0, 42) → SyntaxError (line 2648)."""
    with pytest.raises(SyntaxError):
        cv1("Descriptor.GetPtr(R0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 18. Lease validation errors (lines 2656-2670)
# ══════════════════════════════════════════════════════════════════════════════

def test_lease_acquire_wrong_arg_count():
    """Lease.Acquire(R0, R1) → SyntaxError (line 2653)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Acquire(R0, R1);")


def test_lease_acquire_non_register():
    """Lease.Acquire(R0, R1, 42) → SyntaxError (line 2656)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Acquire(R0, R1, 42);")


def test_lease_release_wrong_arg_count():
    """Lease.Release() → SyntaxError (line 2660)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Release();")


def test_lease_release_non_register():
    """Lease.Release(42) → SyntaxError (line 2663)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Release(42);")


def test_lease_validate_wrong_arg_count():
    """Lease.Validate(R0) → SyntaxError (line 2667)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Validate(R0);")


def test_lease_validate_non_register():
    """Lease.Validate(R0, 42) → SyntaxError (line 2670)."""
    with pytest.raises(SyntaxError):
        cv1("Lease.Validate(R0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 19. Storage validation errors (lines 2675-2700)
# ══════════════════════════════════════════════════════════════════════════════

def test_storage_get_schema_wrong_count():
    """Storage.GetSchemaForPack(R0) → SyntaxError (line 2675)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.GetSchemaForPack(R0);")


def test_storage_get_schema_non_register():
    """Storage.GetSchemaForPack(R0, 42) → SyntaxError (line 2678)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.GetSchemaForPack(R0, 42);")


def test_storage_set_schema_wrong_count():
    """Storage.SetSchemaForPack(R0) → SyntaxError (line 2682)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.SetSchemaForPack(R0);")


def test_storage_set_schema_non_register():
    """Storage.SetSchemaForPack(R0, 42) → SyntaxError (line 2685)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.SetSchemaForPack(R0, 42);")


def test_storage_add_card_wrong_count():
    """Storage.AddCard(R0, R1) → SyntaxError (line 2689)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.AddCard(R0, R1);")


def test_storage_add_card_non_register():
    """Storage.AddCard(R0, R1, 42) → SyntaxError (line 2692)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.AddCard(R0, R1, 42);")


def test_storage_delete_card_wrong_count():
    """Storage.DeleteCard(R0) → SyntaxError (line 2696)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.DeleteCard(R0);")


def test_storage_delete_card_non_register():
    """Storage.DeleteCard(R0, 42) → SyntaxError (line 2699)."""
    with pytest.raises(SyntaxError):
        cv1("Storage.DeleteCard(R0, 42);")


# ══════════════════════════════════════════════════════════════════════════════
# 20. decompile_csharp: unknown hook_id (2746->2804)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_unknown_hook():
    """decompile_csharp with hook not in HOST_HOOK_NAMES → generic output (2746->2804)."""
    # Build a NOOP with imm16 that has HOST_HOOK_BASE pattern but unknown hook id
    # HOST_HOOK_BASE = 0x7000, use id 0xFF (hook 255 may not be in HOST_HOOK_NAMES)
    from picoscript_lang import HOST_HOOK_NAMES
    # Find an id that has the hook pattern but is NOT in HOST_HOOK_NAMES
    for candidate in range(240, 256):
        imm16 = HOST_HOOK_BASE | candidate
        if candidate not in HOST_HOOK_NAMES:
            word = encode_instruction(OP_NOOP, imm16=imm16)
            cs = decompile_csharp([word])
            assert len(cs.strip()) > 0
            return
    # Fallback: use a raw OP_NOOP with a hook-shaped imm not in names
    word = encode_instruction(OP_NOOP, imm16=HOST_HOOK_BASE | 0xFE)
    cs = decompile_csharp([word])
    assert len(cs.strip()) > 0


def test_decompile_basic_unknown_hook():
    """decompile_basic with hook not in HOST_HOOK_NAMES → fallback (2909->2959)."""
    from picoscript_lang import HOST_HOOK_NAMES
    for candidate in range(240, 256):
        if candidate not in HOST_HOOK_NAMES:
            word = encode_instruction(OP_NOOP, imm16=HOST_HOOK_BASE | candidate)
            b = decompile_basic([word])
            assert len(b.strip()) > 0
            return
    word = encode_instruction(OP_NOOP, imm16=HOST_HOOK_BASE | 0xFE)
    b = decompile_basic([word])
    assert len(b.strip()) > 0


def test_decompile_python_unknown_hook():
    """decompile_python with hook not in HOST_HOOK_NAMES → fallback (3043->3099)."""
    from picoscript_lang import HOST_HOOK_NAMES
    for candidate in range(240, 256):
        if candidate not in HOST_HOOK_NAMES:
            word = encode_instruction(OP_NOOP, imm16=HOST_HOOK_BASE | candidate)
            py = decompile_python([word])
            assert len(py.strip()) > 0
            return
    word = encode_instruction(OP_NOOP, imm16=HOST_HOOK_BASE | 0xFE)
    py = decompile_python([word])
    assert len(py.strip()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 21. decompile_csharp: Kernel.WaitIRQ with ADDR_IMMEDIATE (lines 2752, 2754)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_kernel_waitirq_no_reg():
    """Kernel.WaitIRQ() (no register arg) → ADDR_IMMEDIATE path in decompiler (line 2752)."""
    # Compile with no args → rs2 = ADDR_IMMEDIATE (default)
    words = cv1("Kernel.WaitIRQ();")
    cs = decompile_csharp(words)
    assert "kernel" in cs.lower()


def test_decompile_csharp_kernel_wait_irq_with_reg():
    """Kernel.WaitIRQ(R3) with register → ADDR_REGISTER path in decompiler (line 2750)."""
    words = cv1("Kernel.WaitIRQ(R3);")
    cs = decompile_csharp(words)
    assert "kernel" in cs.lower()


def test_decompile_csharp_kernel_fire_swirq_no_reg():
    """Kernel.FireSWIRQ else branch (rs2 != ADDR_REGISTER, line 2759)."""
    # Build a Kernel.FireSWIRQ word with rs2 = 0 (not ADDR_REGISTER)
    from picoscript_lang import HOST_HOOK_CODES
    hook = HOST_HOOK_CODES.get(("Kernel", "FireSWIRQ"), 3)
    imm16 = HOST_HOOK_BASE | hook
    word = encode_instruction(OP_NOOP, rs1=0, rs2=0, imm16=imm16)  # rs2=0, not ADDR_REGISTER
    cs = decompile_csharp([word])
    assert "kernel" in cs.lower() or "fire" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 22. decompile_csharp: Net.Type hex (line 2810)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_net_type_unknown_code():
    """decompile_csharp Net.Type with unknown content-type code → hex output (line 2810)."""
    from picoscript_lang import CONTENT_TYPES
    # Use a 0xA*** code that is NOT in CONTENT_TYPES
    for candidate in range(0xA001, 0xA010):
        if candidate not in CONTENT_TYPES.values():
            word = encode_instruction(OP_NOOP, imm16=candidate)
            cs = decompile_csharp([word])
            assert "net" in cs.lower() or "0x" in cs.lower()
            return


# ══════════════════════════════════════════════════════════════════════════════
# 23. decompile_csharp/basic/python: unknown opcode (lines 2857, 3009, 3149)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_all_opcodes_covered():
    """All 16 valid opcodes (0-15) decode without error in decompile_csharp."""
    for opcode in range(16):
        word = encode_instruction(opcode)
        cs = decompile_csharp([word])
        assert len(cs.strip()) > 0


def test_decompile_basic_all_opcodes_covered():
    """All 16 valid opcodes (0-15) decode without error in decompile_basic."""
    for opcode in range(16):
        word = encode_instruction(opcode)
        b = decompile_basic([word])
        assert len(b.strip()) > 0


def test_decompile_python_all_opcodes_covered():
    """All 16 valid opcodes (0-15) decode without error in decompile_python."""
    for opcode in range(16):
        word = encode_instruction(opcode)
        py = decompile_python([word])
        assert len(py.strip()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 24. decompile_python: Net.header (line 3110)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_python_net_header():
    """decompile_python: Net.Header → net.header() (line 3110)."""
    word = encode_instruction(OP_NOOP, imm16=NET_HEADER_BASE)
    py = decompile_python([word])
    assert "net" in py.lower() or "header" in py.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 25. Decompile round-trips for Dsp (line 2824-2828 in csharp, 3142-3147 in python)
# ══════════════════════════════════════════════════════════════════════════════

def test_roundtrip_dsp_matmul():
    """Dsp.MatMul(R0, R1) round-trips through all decompilers."""
    words = cv1("Dsp.MatMul(R0, R1);")
    cs = decompile_csharp(words)
    b = decompile_basic(words)
    py = decompile_python(words)
    assert "dsp" in cs.lower()
    assert "dsp" in b.lower()
    assert "dsp" in py.lower()


def test_roundtrip_dsp_with_imm():
    """Dsp.Relu(R0, R1, 4) round-trips with imm16 (tests line 2826/2828 in csharp)."""
    words = cv1("Dsp.Relu(R0, R1, 4);")
    cs = decompile_csharp(words)
    py = decompile_python(words)
    assert "dsp" in cs.lower()
    assert "4" in cs  # imm16 printed
    assert "dsp" in py.lower()
    assert "4" in py
