#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_invariant_asserts.py -- test the assert False invariant guards.

Each assert documents a code path that IS reachable if internal invariants
break (e.g. NAMESPACE_MAP/HOST_HOOK_CODES drift, new methods added to one
but not the other). These tests call the methods directly to cover those lines.
"""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (
    Compiler, decompile_csharp, decompile_basic, decompile_python,
    encode_instruction, OP_NOOP, HOST_HOOK_BASE, HOST_HOOK_CODES, NAMESPACE_MAP,
    ADDR_REGISTER,
)


def _compiler():
    return Compiler()


# ══════════════════════════════════════════════════════════════════════════════
# _compile_flow: assert False for unhandled method (invariant guard)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_flow_assert_unreachable_method():
    """_compile_flow raises AssertionError for a method that bypasses NAMESPACE_MAP check."""
    c = _compiler()
    # Call _compile_flow directly with a method that's not Return/Jump/Call/Branch
    with pytest.raises(AssertionError, match="unreachable"):
        c._compile_flow(0, "Teleport", [], 0)


# ══════════════════════════════════════════════════════════════════════════════
# _compile_basic_statement: assert False for empty parts (invariant guard)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_basic_statement_assert_empty_parts():
    """_compile_basic_statement raises AssertionError if somehow called with empty line."""
    c = _compiler()
    # Directly call with a line that produces empty parts
    # (normally impossible via _compile_statement routing, but verifiable directly)
    with pytest.raises(AssertionError, match="unreachable empty parts"):
        c._compile_basic_statement("", 0)


# ══════════════════════════════════════════════════════════════════════════════
# _compile_basic_storage: assert False for non-LOAD/SAVE/PIPE (invariant guard)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_basic_storage_assert_unknown_method():
    """_compile_basic_storage raises AssertionError if called with non-LOAD/SAVE/PIPE."""
    c = _compiler()
    with pytest.raises(AssertionError, match="unreachable"):
        c._compile_basic_storage("BOGUS", ["0", "0", "0", "R0"], 0)


# ══════════════════════════════════════════════════════════════════════════════
# _compile_basic_host_hook: assert False for unknown namespace (invariant guard)
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_basic_host_hook_assert_unknown_namespace():
    """_compile_basic_host_hook raises AssertionError if called with non-NAMESPACE_MAP token."""
    c = _compiler()
    with pytest.raises(AssertionError, match="unreachable"):
        c._compile_basic_host_hook("COMPLETELY_UNKNOWN", "METHOD", [], 0)


def test_compile_host_hook_hook_not_in_hook_codes():
    """_compile_host_hook line 2513: fires when NAMESPACE_MAP has method HOST_HOOK_CODES lacks.

    This is the data integrity guard that catches NAMESPACE_MAP/HOST_HOOK_CODES drift.
    Simulate by temporarily adding 'Teleport' to NAMESPACE_MAP['Kernel'] without
    adding it to HOST_HOOK_CODES.
    """
    from picoscript_lang import NAMESPACE_MAP, HOST_HOOK_CODES, Compiler, OP_NOOP
    # Inject inconsistency: method in NAMESPACE_MAP but not in HOST_HOOK_CODES
    NAMESPACE_MAP["Kernel"]["Teleport"] = OP_NOOP
    try:
        with pytest.raises(SyntaxError, match="Unknown Kernel method"):
            Compiler().compile("Kernel.Teleport();")
    finally:
        del NAMESPACE_MAP["Kernel"]["Teleport"]


# ══════════════════════════════════════════════════════════════════════════════
# _compile_host_hook: Span/Descriptor/Lease/Storage assert False guards
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_host_hook_span_assert_unhandled_method():
    """_compile_host_hook raises AssertionError for unhandled Span method."""
    c = _compiler()
    # Temporarily inject a fake Span method into HOST_HOOK_CODES
    HOST_HOOK_CODES[("Span", "Teleport")] = 0x4F
    try:
        with pytest.raises(AssertionError, match="unhandled Span method"):
            c._compile_host_hook("Span", "Teleport", ["R0", "R1"], 0)
    finally:
        del HOST_HOOK_CODES[("Span", "Teleport")]


def test_compile_host_hook_descriptor_assert_unhandled_method():
    """_compile_host_hook raises AssertionError for unhandled Descriptor method."""
    c = _compiler()
    HOST_HOOK_CODES[("Descriptor", "Transmute")] = 0x5E
    try:
        with pytest.raises(AssertionError, match="unhandled Descriptor method"):
            c._compile_host_hook("Descriptor", "Transmute", ["R0", "R1"], 0)
    finally:
        del HOST_HOOK_CODES[("Descriptor", "Transmute")]


def test_compile_host_hook_lease_assert_unhandled_method():
    """_compile_host_hook raises AssertionError for unhandled Lease method."""
    c = _compiler()
    HOST_HOOK_CODES[("Lease", "Transfer")] = 0x5E
    try:
        with pytest.raises(AssertionError, match="unhandled Lease method"):
            c._compile_host_hook("Lease", "Transfer", ["R0", "R1"], 0)
    finally:
        del HOST_HOOK_CODES[("Lease", "Transfer")]


def test_compile_host_hook_storage_assert_unhandled_method():
    """_compile_host_hook raises AssertionError for unhandled Storage method."""
    c = _compiler()
    HOST_HOOK_CODES[("Storage", "ArchiveCard")] = 0x61
    try:
        with pytest.raises(AssertionError, match="unhandled Storage method"):
            c._compile_host_hook("Storage", "ArchiveCard", ["R0", "R1"], 0)
    finally:
        del HOST_HOOK_CODES[("Storage", "ArchiveCard")]


# ══════════════════════════════════════════════════════════════════════════════
# decompile_csharp/basic/python: assert False for unhandled opcode
# These are unreachable via (word >> 28) & 0xF but testable via monkeypatching
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_assert_unhandled_opcode():
    """disassemble covers all 16 opcodes — verify exhaustiveness by checking all decode."""
    # The assert False was removed: all 16 opcodes ARE handled and the else is dead.
    # Verify every opcode round-trips without error instead.
    import picoscript_lang
    for opcode in range(16):
        word = picoscript_lang.encode_instruction(opcode)
        result = picoscript_lang.disassemble([word])
        assert isinstance(result, str) and len(result.strip()) > 0


def test_decompile_basic_assert_unhandled_opcode():
    """decompile_basic assert False guard via direct call with injected dict."""
    import picoscript_lang
    # Temporarily wrap _decode_word to inject impossible opcode
    orig = picoscript_lang._decode_word

    def fake(w):
        d = orig(w)
        d["opcode"] = 0xFF
        return d

    picoscript_lang._decode_word = fake
    try:
        with pytest.raises(AssertionError, match="unhandled opcode"):
            picoscript_lang.decompile_basic([0])
    finally:
        picoscript_lang._decode_word = orig


def test_decompile_python_assert_unhandled_opcode():
    """decompile_python assert False guard via direct call with injected dict."""
    import picoscript_lang
    orig = picoscript_lang._decode_word

    def fake(w):
        d = orig(w)
        d["opcode"] = 0xFF
        return d

    picoscript_lang._decode_word = fake
    try:
        with pytest.raises(AssertionError, match="unhandled opcode"):
            picoscript_lang.decompile_python([0])
    finally:
        picoscript_lang._decode_word = orig


# ══════════════════════════════════════════════════════════════════════════════
# Kernel.WaitIRQ simplified decompiler: rs2 = arbitrary value → no-arg form
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_csharp_waitirq_corrupt_rs2():
    """Kernel.WaitIRQ with rs2 not ADDR_REGISTER → simplified else branch (no-arg form)."""
    from picoscript_lang import HOST_HOOK_CODES, HOST_HOOK_BASE
    hook = HOST_HOOK_CODES[("Kernel", "WaitIRQ")]
    imm16 = HOST_HOOK_BASE | hook
    # rs2 = 5 (not ADDR_REGISTER=1, not ADDR_IMMEDIATE=0) — corrupt/unexpected
    word = encode_instruction(OP_NOOP, rs1=2, rs2=5, imm16=imm16)
    cs = decompile_csharp([word])
    assert "Kernel.WaitIRQ()" in cs  # no register arg, using else branch


def test_decompile_csharp_waitirq_rs2_register():
    """Kernel.WaitIRQ(R3) with rs2 = ADDR_REGISTER → register-arg form."""
    from picoscript_lang import HOST_HOOK_CODES, HOST_HOOK_BASE
    hook = HOST_HOOK_CODES[("Kernel", "WaitIRQ")]
    imm16 = HOST_HOOK_BASE | hook
    word = encode_instruction(OP_NOOP, rs1=3, rs2=ADDR_REGISTER, imm16=imm16)
    cs = decompile_csharp([word])
    assert "Kernel.WaitIRQ(R3)" in cs
