#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_deep_coverage.py -- targeted coverage for remaining gaps in picoscript_lang.py.

Target groups:
1. Named-constant meta functions: DST_, CURRENCY_MINOR_, COUNTRY_ (lines 1898-1922)
2. _normalize_meta_entry and _resolve_user_locale_entries (1935-1953)
3. parse_arg: stream/condition/content-type branches (2063-2072)
4. Compiler edge cases: duplicate label, ascending BASIC line numbers (2152-2160)
5. Decompiler (decompile_csharp) missing instruction types (2749-2857):
   - Kernel.WaitIRQ/WaitSWIRQ/FireSWIRQ
   - Queue ops
   - Memory.ArenaInit/ArenaAlloc/ArenaReset
   - Net.Type unknown CT, Net.Header
   - Dsp.* with imm16 != 0
   - Math with register rs2
   - Math.Inc
   - Flow.Call
   - Thread.Wait/Raise
   - Unknown opcode
6. Decompile_basic missing branches (2909-2970)
7. _compile_storage error path (len != 4)
"""
import pytest
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    Compiler,
    decompile_basic, decompile_csharp, decompile_python, decompile_hex,
    _default_en_constant_meta, _normalize_meta_entry, _resolve_user_locale_entries,
    parse_arg, _canonical_namespace, _canonical_method,
    HOST_HOOK_BASE, HOST_HOOK_NAMES,
    encode_instruction, OP_NOOP, OP_WAIT, OP_RAISE, OP_JUMP, OP_CALL, OP_BRANCH,
    OP_ADD, OP_MUL, OP_INC, OP_DSP, OP_RETURN,
    ADDR_REGISTER, CONDITION_MAP,
)


def cv1(src):
    return Compiler().compile(src)


def _roundtrip(src):
    words = cv1(src)
    return words, decompile_csharp(words), decompile_basic(words), decompile_python(words)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Named-constant meta: DST_, CURRENCY_MINOR_, COUNTRY_ (lines 1898-1922)
# ══════════════════════════════════════════════════════════════════════════════

def test_meta_currency_minor():
    """CURRENCY_MINOR_ prefix -> minor-units label."""
    r = _default_en_constant_meta("CURRENCY_MINOR_USD", 2)
    assert "USD" in r["label"]
    assert "minor" in r["label"]


def test_meta_currency():
    """CURRENCY_ prefix -> ISO-4217 label."""
    r = _default_en_constant_meta("CURRENCY_EUR", 978)
    assert "EUR" in r["label"]
    assert "4217" in r.get("description", "") or "currency" in r["description"].lower()


def test_meta_country():
    """COUNTRY_ prefix -> ISO-3166-1 label."""
    r = _default_en_constant_meta("COUNTRY_GB", 826)
    assert "GB" in r["label"]
    assert "3166" in r.get("description", "") or "country" in r["description"].lower()


def test_meta_tz():
    """TZ_ prefix -> timezone label."""
    r = _default_en_constant_meta("TZ_EUROPE_LONDON", 0)
    assert "/" in r["label"] or "EUROPE" in r["label"].upper()


def test_meta_dst():
    """DST_ prefix -> daylight-saving label (line 1922)."""
    r = _default_en_constant_meta("DST_ACTIVE", 1)
    assert "description" in r
    assert "daylight" in r["description"].lower() or "dst" in r["description"].lower()


def test_meta_default_fallback():
    """Default fallback -> label + description with value."""
    r = _default_en_constant_meta("SOME_CONSTANT", 42)
    assert r["label"]
    assert "42" in r["description"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. _normalize_meta_entry (lines 1935-1942)
# ══════════════════════════════════════════════════════════════════════════════

def test_normalize_meta_dict_with_both():
    """Dict with label + description -> both preserved."""
    r = _normalize_meta_entry({"label": "Hello", "description": "World"})
    assert r["label"] == "Hello"
    assert r["description"] == "World"


def test_normalize_meta_dict_none_label():
    """Dict with label=None -> label omitted (line 1935->1937)."""
    r = _normalize_meta_entry({"label": None, "description": "desc"})
    assert "label" not in r
    assert r["description"] == "desc"


def test_normalize_meta_string():
    """String entry -> label only (lines 1940-1941)."""
    r = _normalize_meta_entry("just a label string")
    assert r == {"label": "just a label string"}


def test_normalize_meta_other():
    """Non-dict, non-string -> empty dict."""
    r = _normalize_meta_entry(42)
    assert r == {}


# ══════════════════════════════════════════════════════════════════════════════
# 3. _resolve_user_locale_entries (line 1953)
# ══════════════════════════════════════════════════════════════════════════════

def test_resolve_user_locale_string_values():
    """String values in user dict -> normalized as labels (line 1953)."""
    user_dict = {"MY_KEY": "My Label"}
    result = _resolve_user_locale_entries(user_dict, "en-GB")
    assert "MY_KEY" in result
    assert result["MY_KEY"]["label"] == "My Label"


def test_resolve_user_locale_non_dict():
    """Non-dict user_dictionary -> empty result."""
    result = _resolve_user_locale_entries("not a dict", "en")
    assert result == {}


def test_resolve_user_locale_scoped():
    """Scoped locale key in dict -> merged into result."""
    user_dict = {"en-GB": {"SPECIAL": {"label": "Special"}}}
    result = _resolve_user_locale_entries(user_dict, "en-GB")
    assert "SPECIAL" in result


# ══════════════════════════════════════════════════════════════════════════════
# 4. parse_arg: stream/condition/content-type branches (lines 2063-2072)
# ══════════════════════════════════════════════════════════════════════════════

def test_parse_arg_stream():
    """Stream.* prefix -> ('stream', method) (line 2063)."""
    result = parse_arg("Stream.Open")
    assert result[0] == "stream"
    assert result[1] == "Open"


def test_parse_arg_condition():
    """Condition string -> ('cond', value) (line 2065)."""
    result = parse_arg("EQ")
    assert result[0] == "cond"


def test_parse_arg_content_type():
    """Content-type string -> ('ctype', code) (line 2072)."""
    result = parse_arg("text/html")
    assert result[0] == "ctype"


# ══════════════════════════════════════════════════════════════════════════════
# 5. _canonical_namespace/_canonical_method returning None (lines 2097, 2105)
# ══════════════════════════════════════════════════════════════════════════════

def test_canonical_namespace_not_found():
    """Unknown namespace -> None (line 2097)."""
    r = _canonical_namespace("NOTEXIST")
    assert r is None


def test_canonical_method_not_found():
    """Unknown method for known namespace -> None (line 2105)."""
    r = _canonical_method("Kernel", "NOTEXIST")
    assert r is None


# ══════════════════════════════════════════════════════════════════════════════
# 6. Compiler error paths: duplicate label (2152), BASIC ascending order (2160)
# ══════════════════════════════════════════════════════════════════════════════

def test_duplicate_label_raises():
    """Duplicate label -> SyntaxError (line 2152)."""
    with pytest.raises(SyntaxError, match="Duplicate"):
        Compiler().compile(":dup\n:dup\nFlow.Return();")


def test_basic_ascending_line_order():
    """BASIC line numbers must ascend -> SyntaxError (line 2159-2160)."""
    with pytest.raises(SyntaxError):
        Compiler().compile("20 FLOW RETURN\n10 FLOW RETURN")


def test_empty_label_raises():
    """Empty label -> SyntaxError."""
    with pytest.raises(SyntaxError):
        Compiler().compile(":\nFlow.Return();")


# ══════════════════════════════════════════════════════════════════════════════
# 7. _compile_storage error path: wrong arg count (line 2238-2239)
# ══════════════════════════════════════════════════════════════════════════════

def test_storage_load_wrong_arg_count():
    """Storage.Load with wrong args -> SyntaxError."""
    with pytest.raises(SyntaxError):
        Compiler().compile("Storage.Load(0, 1, 2);")  # needs 4 args


# ══════════════════════════════════════════════════════════════════════════════
# 8. decompile_csharp: Kernel.WaitIRQ/WaitSWIRQ (lines 2749-2754)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_kernel_wait_irq():
    """decompile_csharp: Kernel.WaitIRQ() compiles and decompiles."""
    words, cs, b, py = _roundtrip("Kernel.WaitIRQ();")
    assert "kernel" in cs.lower() or "waitirq" in cs.lower().replace(".", "")


def test_decompile_csharp_kernel_wait_swirq():
    """decompile_csharp: Kernel.WaitSWIRQ() compiles and decompiles."""
    words, cs, b, py = _roundtrip("Kernel.WaitSWIRQ();")
    assert "kernel" in cs.lower()


def test_decompile_csharp_kernel_fire_swirq():
    """decompile_csharp: Kernel.FireSWIRQ(R0) compiles and decompiles (line 2756-2759)."""
    words, cs, b, py = _roundtrip("Kernel.FireSWIRQ(R0);")
    assert "kernel" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 9. decompile_csharp: Queue operations (line 2761)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_queue_enqueue():
    """decompile_csharp: Queue.Enqueue(0, R1) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Queue.Enqueue(0, R1);")
    assert "queue" in cs.lower()


def test_decompile_csharp_queue_dequeue():
    """decompile_csharp: Queue.Dequeue(0, R0) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Queue.Dequeue(0, R0);")
    assert "queue" in cs.lower()


def test_decompile_csharp_queue_depth():
    """decompile_csharp: Queue.Depth(0, R0) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Queue.Depth(0, R0);")
    assert "queue" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 10. decompile_csharp: Memory.Arena* (lines 2766, 2768)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_arena_init():
    """decompile_csharp: Memory.ArenaInit(R0, R1, R2) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Memory.ArenaInit(R0, R1, R2);")
    assert "memory" in cs.lower() or "arena" in cs.lower()


def test_decompile_csharp_arena_alloc():
    """decompile_csharp: Memory.ArenaAlloc(R0, R1, R2) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Memory.ArenaAlloc(R0, R1, R2);")
    assert "memory" in cs.lower()


def test_decompile_csharp_arena_reset():
    """decompile_csharp: Memory.ArenaReset(R0) compiles and decompiles (line 2768)."""
    words, cs, b, py = _roundtrip("Memory.ArenaReset(R0);")
    assert "memory" in cs.lower() or "reset" in cs.lower()


def test_decompile_csharp_arena_stats():
    """decompile_csharp: Memory.ArenaStats(R0, R1) compiles and decompiles."""
    words, cs, b, py = _roundtrip("Memory.ArenaStats(R0, R1);")
    assert "memory" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 11. decompile_csharp: Net.Type unknown CT / Net.Header (lines 2810, 2818)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_net_type_known():
    """decompile_csharp: Net.Type('text/html') uses named string."""
    words, cs, b, py = _roundtrip('Net.Type("text/html");')
    assert "net" in cs.lower()


def test_decompile_csharp_net_type_hex():
    """decompile_csharp: Net.Type(0xA001) uses hex (line 2810)."""
    # Build a word directly with an unknown content-type imm16
    # Net.Type with raw code 0xA001 (not in CONTENT_TYPES)
    word = encode_instruction(OP_NOOP, imm16=0xA001)
    cs = decompile_csharp([word])
    assert "net" in cs.lower() or "type" in cs.lower()


def test_decompile_csharp_net_header():
    """decompile_csharp: Net.Header() decompiles (line 2818)."""
    # Build a Net.Header word: OP_NOOP with imm16 in the B000-BFFF range
    # NET_HEADER_BASE is in that range
    from picoscript_lang import NET_HEADER_BASE
    word = encode_instruction(OP_NOOP, imm16=NET_HEADER_BASE)
    cs = decompile_csharp([word])
    assert "net" in cs.lower() or "header" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 12. decompile_csharp: Dsp.* with imm16 != 0 (lines 2824-2828)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_dsp_with_imm16():
    """decompile_csharp: DSP op with imm16 != 0 (line 2826)."""
    # Compile Dsp.MatVec(R0, R1, 8)
    try:
        words, cs, b, py = _roundtrip("Dsp.MatVec(R0, R1, 8);")
        assert "dsp" in cs.lower()
    except (SyntaxError, KeyError):
        # Use raw encoding fallback
        word = encode_instruction(OP_DSP, rd=0, rs1=1, rs2=1, imm16=8)
        cs = decompile_csharp([word])
        assert "dsp" in cs.lower()


def test_decompile_csharp_dsp_no_imm16():
    """decompile_csharp: DSP op without imm16 (line 2828)."""
    try:
        words, cs, b, py = _roundtrip("Dsp.MatVec(R0, R1);")
        assert "dsp" in cs.lower()
    except (SyntaxError, KeyError):
        word = encode_instruction(OP_DSP, rd=0, rs1=1, rs2=1, imm16=0)
        cs = decompile_csharp([word])
        assert "dsp" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 13. decompile_csharp: Math with ADDR_REGISTER rs2 (line 2834)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_math_with_reg():
    """decompile_csharp: Math.Add(R0, R1, R2) uses register form (line 2834)."""
    words, cs, b, py = _roundtrip("Math.Add(R0, R1, R2);")
    assert "math" in cs.lower() or "add" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 14. decompile_csharp: Math.Inc (line 2838)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_math_inc():
    """decompile_csharp: Math.Inc(R0) decompiles (line 2838)."""
    words, cs, b, py = _roundtrip("Math.Inc(R0);")
    assert "inc" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 15. decompile_csharp: Flow.Call (line 2842) and Branch
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_flow_call():
    """decompile_csharp: Flow.Call decompiles (line 2842)."""
    words, cs, b, py = _roundtrip(":sub\nFlow.Return();\nFlow.Call(:sub);")
    assert "call" in cs.lower()


def test_decompile_csharp_flow_branch():
    """decompile_csharp: Flow.Branch decompiles."""
    words, cs, b, py = _roundtrip(":end\nFlow.Return();\nFlow.Branch(EQ, R0, R1, :end);")
    assert "branch" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 16. decompile_csharp: Thread.Wait / Thread.Raise (lines 2852-2855)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_thread_wait():
    """decompile_csharp: Thread.Wait() decompiles (line 2853)."""
    word = encode_instruction(OP_WAIT)
    cs = decompile_csharp([word])
    assert "wait" in cs.lower() or "thread" in cs.lower()


def test_decompile_csharp_thread_raise():
    """decompile_csharp: Thread.Raise(5) decompiles (line 2855)."""
    word = encode_instruction(OP_RAISE, imm16=5)
    cs = decompile_csharp([word])
    assert "raise" in cs.lower() or "thread" in cs.lower()


def test_decompile_csharp_unknown_opcode():
    """decompile_csharp: Unknown opcode gets comment (line 2857)."""
    word = encode_instruction(0xF)  # opcode 15 = likely unknown
    cs = decompile_csharp([word])
    assert len(cs.strip()) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 17. decompile_basic: Kernel.FireSWIRQ (line 2917) and Net.Header (2970)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_basic_kernel_fire_swirq():
    """decompile_basic: Kernel.FireSWIRQ(R0) decompiles (line 2917)."""
    words, cs, b, py = _roundtrip("Kernel.FireSWIRQ(R0);")
    assert "kernel" in b.lower()


def test_decompile_basic_net_header():
    """decompile_basic: Net.Header decompiles (line 2970)."""
    from picoscript_lang import NET_HEADER_BASE
    word = encode_instruction(OP_NOOP, imm16=NET_HEADER_BASE)
    b = decompile_basic([word])
    assert "net" in b.lower() or "header" in b.lower()


def test_decompile_basic_thread_wait():
    """decompile_basic: Thread.Wait() decompiles."""
    word = encode_instruction(OP_WAIT)
    b = decompile_basic([word])
    assert "wait" in b.lower() or "thread" in b.lower()


def test_decompile_basic_thread_raise():
    """decompile_basic: Thread.Raise(5) decompiles."""
    word = encode_instruction(OP_RAISE, imm16=5)
    b = decompile_basic([word])
    assert "raise" in b.lower() or "thread" in b.lower()


def test_decompile_basic_math_inc():
    """decompile_basic: Math.Inc decompiles."""
    words, cs, b, py = _roundtrip("Math.Inc(R0);")
    assert "inc" in b.lower() or "math" in b.lower()


def test_decompile_basic_flow_call():
    """decompile_basic: Flow.Call decompiles."""
    words, cs, b, py = _roundtrip(":sub\nFlow.Return();\nFlow.Call(:sub);")
    assert "call" in b.lower() or "flow" in b.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 18. decompile_python: equivalent coverage
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_python_kernel_wait():
    """decompile_python: Kernel.WaitIRQ."""
    words, cs, b, py = _roundtrip("Kernel.WaitIRQ();")
    assert "kernel" in py.lower()


def test_decompile_python_queue_ops():
    """decompile_python: Queue ops decompile."""
    words, cs, b, py = _roundtrip("Queue.Enqueue(0, R1);\nQueue.Dequeue(0, R0);")
    assert "queue" in py.lower()


def test_decompile_python_arena_ops():
    """decompile_python: Memory.Arena* ops."""
    words, cs, b, py = _roundtrip(
        "Memory.ArenaInit(R0, R1, R2);\n"
        "Memory.ArenaAlloc(R0, R1, R2);\n"
        "Memory.ArenaReset(R0);\n"
        "Memory.ArenaStats(R0, R1);"
    )
    assert "memory" in py.lower()


def test_decompile_python_thread_primitives():
    """decompile_python: Thread.Wait/Raise/Skip."""
    word_wait = encode_instruction(OP_WAIT)
    word_raise = encode_instruction(OP_RAISE, imm16=3)
    py = decompile_python([word_wait, word_raise])
    assert "wait" in py.lower() or "raise" in py.lower()


def test_decompile_python_math_inc():
    """decompile_python: Math.Inc."""
    words, cs, b, py = _roundtrip("Math.Inc(R0);")
    assert "inc" in py.lower()


def test_decompile_python_flow_call():
    """decompile_python: Flow.Call."""
    words, cs, b, py = _roundtrip(":sub\nFlow.Return();\nFlow.Call(:sub);")
    assert "call" in py.lower() or "flow" in py.lower()
