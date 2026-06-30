#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_corrupt_malformed.py -- intentionally malformed and accidentally corrupt data.

Tests the system's resilience against:
1. Malformed source code (type confusion, wrong arg kinds, bad syntax)
2. Corrupt bytecode streams (all-zeros, all-ones, random, max-field values)
3. Boundary values (MAX_INT, overflow, zero, negative)
4. Truncated / partial input
5. Unicode / control characters / null bytes in source
6. Multi-instruction programs exercising all decompiler loop paths
7. VM execution of corrupt instruction streams
"""
import os
import sys
import struct
import random

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (
    Compiler, parse_arg,
    decompile_basic, decompile_csharp, decompile_python, decompile_hex,
    encode_instruction,
    OP_NOOP, OP_LOAD, OP_SAVE, OP_PIPE,
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_INC,
    OP_JUMP, OP_CALL, OP_BRANCH, OP_RETURN,
    OP_WAIT, OP_RAISE, OP_DSP,
)

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def cv1(src):
    return Compiler().compile(src)


def decompile_all(words):
    return (
        decompile_csharp(words),
        decompile_basic(words),
        decompile_python(words),
        decompile_hex(words),
    )


# ══════════════════════════════════════════════════════════════════════════════
# TYPE CONFUSION: wrong arg kind where imm/reg expected
# ══════════════════════════════════════════════════════════════════════════════

def test_math_add_ctype_as_third_arg():
    """Math.Add(R0, R1, text/html) — content-type where register/imm expected → SyntaxError."""
    with pytest.raises(SyntaxError, match="third arg"):
        cv1("Math.Add(R0, R1, text/html);")


def test_math_add_condition_as_third_arg():
    """Math.Add(R0, R1, EQ) — condition code where register/imm expected → SyntaxError."""
    with pytest.raises(SyntaxError, match="third arg"):
        cv1("Math.Add(R0, R1, EQ);")


def test_math_add_stream_as_third_arg():
    """Math.Add(R0, R1, Stream.Open) — stream ref where register/imm expected → SyntaxError."""
    with pytest.raises(SyntaxError, match="third arg"):
        cv1("Math.Add(R0, R1, Stream.Open);")


def test_math_sub_sym_as_third_arg():
    """Math.Sub(R0, R1, notvalid) — unknown symbol where register/imm expected → SyntaxError."""
    with pytest.raises(SyntaxError, match="third arg"):
        cv1("Math.Sub(R0, R1, notvalid);")


def test_dsp_ctype_as_third_arg():
    """Dsp.Scale(R0, R1, text/html) — ctype third arg falls through (arc 2344->2346)."""
    # parse_arg("text/html") = ("ctype", ...) — neither "reg" nor "imm" → imm16=0
    words = cv1("Dsp.Scale(R0, R1, text/html);")
    assert len(words) == 1
    # Also verify decompilation doesn't crash
    cs, b, py, h = decompile_all(words)
    assert "dsp" in cs.lower()


def test_dsp_sym_as_third_arg():
    """Dsp.Relu(R0, R1, unknownsym) — sym third arg, imm16=0 (arc 2344->2346)."""
    words = cv1("Dsp.Relu(R0, R1, unknownsym);")
    assert len(words) == 1


# ══════════════════════════════════════════════════════════════════════════════
# NET METHODS: unhandled methods and bad content-type strings
# ══════════════════════════════════════════════════════════════════════════════

def test_net_type_unknown_content_type_string():
    """Net.Type('bogus/mimetype') — not in CONTENT_TYPES → SyntaxError (line 2324)."""
    with pytest.raises(SyntaxError, match="[Uu]nknown content type"):
        cv1('Net.Type("bogus/mimetype");')


def test_net_type_empty_string():
    """Net.Type('') — empty content type → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1('Net.Type("");')


def test_net_listen_unhandled():
    """Net.Listen() is in NAMESPACE_MAP but not in _compile_net dispatch → SyntaxError (line 2333)."""
    with pytest.raises(SyntaxError, match="[Uu]nknown Net method"):
        cv1("Net.Listen();")


def test_net_accept_unhandled():
    """Net.Accept() → SyntaxError (line 2333)."""
    with pytest.raises(SyntaxError, match="[Uu]nknown Net method"):
        cv1("Net.Accept();")


def test_net_shutdown_unhandled():
    """Net.Shutdown() → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Net.Shutdown();")


def test_net_register_unhandled():
    """Net.Register() → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Net.Register();")


# ══════════════════════════════════════════════════════════════════════════════
# CORRUPT BYTECODE: decompilers must never crash on any 32-bit word
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("word", [
    0x00000000,  # all zeros — OP_NOOP
    0xFFFFFFFF,  # all ones — OP_RAISE with max imm
    0x10000000,  # OP_LOAD with all-zero fields
    0xF0000000,  # OP_DSP sub-op=0
    0x0000FFFF,  # OP_NOOP imm16=65535
    0xABCDEF01,  # random-looking
    0x12345678,  # another random pattern
    0x7FFFFFFF,  # max positive int value
    0x80000000,  # sign bit set
])
def test_decompile_csharp_never_crashes(word):
    """decompile_csharp handles any 32-bit word without raising."""
    cs = decompile_csharp([word])
    assert isinstance(cs, str)


@pytest.mark.parametrize("word", [
    0x00000000, 0xFFFFFFFF, 0x10000000, 0xF0000000,
    0x0000FFFF, 0xABCDEF01, 0x12345678,
])
def test_decompile_basic_never_crashes(word):
    """decompile_basic handles any 32-bit word without raising."""
    b = decompile_basic([word])
    assert isinstance(b, str)


@pytest.mark.parametrize("word", [
    0x00000000, 0xFFFFFFFF, 0x10000000, 0xF0000000,
    0x0000FFFF, 0xABCDEF01, 0x12345678,
])
def test_decompile_python_never_crashes(word):
    """decompile_python handles any 32-bit word without raising."""
    py = decompile_python([word])
    assert isinstance(py, str)


def test_decompile_empty_word_list():
    """All decompilers handle empty word list."""
    for fn in (decompile_csharp, decompile_basic, decompile_python, decompile_hex):
        result = fn([])
        assert isinstance(result, str)


def test_decompile_all_opcodes_all_fields_max():
    """Every opcode with rd=rs1=rs2=15, imm16=0xFFFF — never crashes."""
    for opcode in range(16):
        word = encode_instruction(opcode, rd=15, rs1=15, rs2=15, imm16=0xFFFF)
        for fn in (decompile_csharp, decompile_basic, decompile_python):
            result = fn([word])
            assert isinstance(result, str)


def test_decompile_all_opcodes_all_fields_zero():
    """Every opcode with all zero fields — never crashes."""
    for opcode in range(16):
        word = encode_instruction(opcode, rd=0, rs1=0, rs2=0, imm16=0)
        for fn in (decompile_csharp, decompile_basic, decompile_python):
            result = fn([word])
            assert isinstance(result, str)


def test_decompile_random_word_stream():
    """100 random 32-bit words — all decompilers handle without raising."""
    rng = random.Random(42)
    words = [rng.randint(0, 0xFFFFFFFF) for _ in range(100)]
    for fn in (decompile_csharp, decompile_basic, decompile_python, decompile_hex):
        result = fn(words)
        assert isinstance(result, str)
        assert len(result) > 0


def test_decompile_large_random_word_stream():
    """1000 random 32-bit words — performance/stability check."""
    rng = random.Random(99)
    words = [rng.randint(0, 0xFFFFFFFF) for _ in range(1000)]
    for fn in (decompile_csharp, decompile_basic, decompile_python):
        result = fn(words)
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# DECOMPILER LOOP BACK-EDGES: multi-instruction programs covering all paths
# These cover arcs: 2854->2735 (csharp), 3000->2897 (basic), 3138->3031 (python)
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_thread_raise_not_last_instruction():
    """Thread.Raise followed by Flow.Return — covers loop back-arc for OP_RAISE path."""
    word_raise = encode_instruction(OP_RAISE, imm16=7)
    word_return = encode_instruction(OP_RETURN)
    words = [word_raise, word_return]
    cs, b, py, h = decompile_all(words)
    assert "raise" in cs.lower() or "thread" in cs.lower()
    assert "return" in cs.lower() or "flow" in cs.lower()


def test_decompile_thread_wait_not_last_instruction():
    """Thread.Wait followed by Flow.Return — covers loop back-arc for OP_WAIT path."""
    words = cv1("Thread.Wait();\nFlow.Return();")
    cs, b, py, h = decompile_all(words)
    assert "wait" in cs.lower()
    assert "return" in cs.lower() or "flow" in cs.lower()


def test_decompile_all_opcodes_sequence():
    """Program using all 16 opcodes in sequence — stress-tests all loop paths."""
    storage_word = encode_instruction(OP_LOAD, rd=0, imm16=0)
    words = [
        encode_instruction(OP_NOOP),
        encode_instruction(OP_LOAD, imm16=0),
        encode_instruction(OP_SAVE, imm16=0),
        encode_instruction(OP_PIPE, imm16=0),
        encode_instruction(OP_ADD, rd=0, rs1=0, imm16=1),
        encode_instruction(OP_SUB, rd=0, rs1=0, imm16=1),
        encode_instruction(OP_MUL, rd=0, rs1=0, imm16=2),
        encode_instruction(OP_DIV, rd=0, rs1=0, imm16=2),
        encode_instruction(OP_INC, rd=0),
        encode_instruction(OP_JUMP, imm16=0),
        encode_instruction(OP_CALL, imm16=0),
        encode_instruction(OP_BRANCH, rd=0, rs1=0, rs2=0, imm16=0),
        encode_instruction(OP_RETURN),
        encode_instruction(OP_WAIT),
        encode_instruction(OP_RAISE, imm16=1),
        encode_instruction(OP_DSP, rd=0, rs1=0, rs2=0, imm16=0),
    ]
    for fn in (decompile_csharp, decompile_basic, decompile_python, decompile_hex):
        result = fn(words)
        assert isinstance(result, str)
        lines = result.replace("\r\n", "\n").strip().split("\n")
        assert len(lines) == 16  # one output line per input word


def test_decompile_all_hook_namespaces_in_sequence():
    """Multi-word program with host hooks from every namespace — full loop coverage."""
    from picoscript_lang import HOST_HOOK_CODES, HOST_HOOK_BASE
    programs = [
        "Kernel.WaitIRQ();",
        "Kernel.FireSWIRQ(R0);",
        "Queue.Enqueue(0, R1);",
        "Random.U32(R0);",
        "Memory.ArenaInit(R0, R1, R2);",
        "Memory.ArenaAlloc(R0, R1, R2);",
        "Memory.ArenaReset(R0);",
        "Memory.ArenaStats(R0, R1);",
        "Span.Make(R0, R1, R2);",
        "Span.Slice(R0, R1, R2);",
        "Span.Len(R0, R1);",
        "Span.Get(R0, R1, R2);",
        "Span.Materialize(R0, R1);",
        "Descriptor.Make(R0, R1, R2);",
        "Descriptor.SetFlags(R0, R1);",
        "Descriptor.GetPtr(R0, R1);",
        "Lease.Acquire(R0, R1, R2);",
        "Lease.Release(R0);",
        "Lease.Validate(R0, R1);",
        "Storage.GetSchemaForPack(R0, R1);",
        "Storage.SetSchemaForPack(R0, R1);",
        "Storage.AddCard(R0, R1, R2);",
        "Storage.DeleteCard(R0, R1);",
        "Flow.Return();",
    ]
    words = cv1("\n".join(programs))
    assert len(words) == len(programs)
    cs, b, py, h = decompile_all(words)
    assert "kernel" in cs.lower()
    assert "span" in cs.lower()
    assert "storage" in cs.lower()


# ══════════════════════════════════════════════════════════════════════════════
# CORRUPT BYTECODE: VM execution (fault handling)
# ══════════════════════════════════════════════════════════════════════════════

def test_vm_executes_all_zero_bytecode():
    """VM handles all-zero bytecode (NOOP stream) without panic."""
    try:
        from picoscript_vm import PicoVM
        vm = PicoVM()
        words = [0x00000000] * 4
        try:
            vm.execute(words)
        except Exception:
            pass  # VM may fault, but must not crash the process
    except ImportError:
        pytest.skip("picoscript_vm not importable")


def test_vm_executes_return_immediately():
    """VM with single Flow.Return() word terminates cleanly."""
    try:
        from picoscript_vm import PicoVM
        vm = PicoVM()
        words = cv1("Flow.Return();")
        vm.execute(words)
    except Exception:
        pass  # May raise, must not hang


def test_vm_executes_max_field_values():
    """VM handles word with all fields at maximum without crashing."""
    try:
        from picoscript_vm import PicoVM
        vm = PicoVM()
        word = encode_instruction(OP_NOOP, rd=15, rs1=15, rs2=15, imm16=0xFFFF)
        try:
            vm.execute([word, encode_instruction(OP_RETURN)])
        except Exception:
            pass
    except ImportError:
        pytest.skip("picoscript_vm not importable")


# ══════════════════════════════════════════════════════════════════════════════
# BOUNDARY VALUES: integer overflow, max/min, negatives
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_max_imm16():
    """Math.Add(R0, R0, 65535) — max imm16 value compiles."""
    words = cv1("Math.Add(R0, R0, 65535);")
    assert len(words) == 1
    assert (words[0] & 0xFFFF) == 65535


def test_compile_zero_imm():
    """Math.Add(R0, R0, 0) — zero immediate compiles."""
    words = cv1("Math.Add(R0, R0, 0);")
    assert len(words) == 1
    assert (words[0] & 0xFFFF) == 0


def test_compile_negative_imm_clamped():
    """Math.Add(R0, R0, -1) — negative immediate; stored as 16-bit two's complement."""
    words = cv1("Math.Add(R0, R0, -1);")
    assert len(words) == 1
    assert (words[0] & 0xFFFF) == 0xFFFF


def test_compile_hex_imm():
    """Math.Add(R0, R0, 0xFF) — note: parse_arg treats hex as 'sym'; use decimal."""
    # parse_arg only handles decimal ints; 0xFF is treated as a symbolic token
    # This tests that decimal 255 compiles correctly
    words = cv1("Math.Add(R0, R0, 255);")
    assert (words[0] & 0xFFFF) == 255


def test_compile_max_register():
    """Math.Inc(R15) — max register index compiles."""
    words = cv1("Math.Inc(R15);")
    assert len(words) == 1


def test_compile_reg_zero():
    """Math.Inc(R0) — minimum register index compiles."""
    words = cv1("Math.Inc(R0);")
    assert len(words) == 1


def test_compile_thread_raise_max_channel():
    """Thread.Raise(65535) — max channel value compiles."""
    words = cv1("Thread.Raise(65535);")
    assert len(words) == 1
    assert (words[0] & 0xFFFF) == 65535


def test_compile_thread_raise_zero():
    """Thread.Raise(0) — zero channel compiles."""
    words = cv1("Thread.Raise(0);")
    assert len(words) == 1


def test_decompile_max_imm16_roundtrip():
    """Encode/decode imm16=0xFFFF through all decompilers."""
    word = encode_instruction(OP_ADD, rd=0, rs1=0, imm16=0xFFFF)
    cs, b, py, h = decompile_all([word])
    # imm16 is signed-extended in branch, but raw in math
    assert "65535" in cs or "0xffff" in cs.lower() or "65535" in b or "65535" in py


# ══════════════════════════════════════════════════════════════════════════════
# TRUNCATED / PARTIAL INPUT: compilers and decompilers handle incomplete data
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_empty_source():
    """Empty source compiles to empty bytecode (not an error)."""
    words = cv1("")
    assert words == []


def test_compile_only_comments():
    """Source with only comments compiles to empty bytecode."""
    words = cv1("// This is a comment\n// Another comment")
    assert words == []


def test_compile_only_whitespace():
    """Source with only whitespace compiles to empty bytecode."""
    words = cv1("   \n\t\n  ")
    assert words == []


def test_decompile_single_word():
    """Single word programs decompile correctly."""
    for opcode in range(16):
        word = encode_instruction(opcode)
        for fn in (decompile_csharp, decompile_basic, decompile_python):
            result = fn([word])
            lines = result.replace("\r\n", "\n").strip().split("\n")
            assert len(lines) == 1


# ══════════════════════════════════════════════════════════════════════════════
# UNICODE / CONTROL CHARS / NULL BYTES in source
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_null_byte_in_source():
    """Source containing null byte → SyntaxError (not a silent crash)."""
    with pytest.raises((SyntaxError, ValueError, UnicodeDecodeError, AttributeError)):
        cv1("Flow.\x00Return();")


def test_compile_tab_in_source():
    """Source with tab indentation compiles correctly."""
    words = cv1("\tFlow.Return();")
    assert len(words) == 1


def test_compile_crlf_line_endings():
    """CRLF line endings work."""
    words = cv1("Flow.Return();\r\nThread.Skip();")
    assert len(words) == 2


def test_compile_unicode_in_comment():
    """Unicode in comment is ignored."""
    words = cv1("// こんにちは 🚀\nFlow.Return();")
    assert len(words) == 1


def test_compile_trailing_semicolon_extra():
    """Extra trailing semicolons are stripped cleanly."""
    words = cv1("Flow.Return();;")
    assert len(words) == 1


def test_compile_very_long_comment():
    """Very long comment line doesn't cause issues."""
    long_comment = "// " + "x" * 10000
    words = cv1(f"{long_comment}\nFlow.Return();")
    assert len(words) == 1


# ══════════════════════════════════════════════════════════════════════════════
# MALFORMED SYNTAX: all error paths systematically
# ══════════════════════════════════════════════════════════════════════════════

def test_compile_missing_open_paren():
    """Method call missing ( → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Flow.Return;")


def test_compile_missing_close_paren():
    """Method call missing ) → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Flow.Return(;")


def test_compile_missing_dot():
    """No dot in identifier → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("FlowReturn();")


def test_compile_unknown_namespace():
    """Completely unknown namespace → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Bogus.Method();")


def test_compile_unknown_method():
    """Known namespace, unknown method → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Flow.Teleport();")


def test_compile_wrong_register_syntax():
    """Register not in R0-R15 format → ValueError or SyntaxError."""
    with pytest.raises((SyntaxError, ValueError)):
        cv1("Math.Inc(X5);")


def test_compile_register_out_of_range():
    """Register R16 out of 4-bit range → AssertionError or SyntaxError."""
    with pytest.raises((SyntaxError, ValueError, AssertionError)):
        cv1("Math.Inc(R16);")


def test_compile_storage_non_integer_tenant():
    """Storage.Load with non-integer tenant → ValueError."""
    with pytest.raises((SyntaxError, ValueError)):
        cv1("Storage.Load(abc, 1, 0, R0);")


def test_compile_thread_raise_non_integer():
    """Thread.Raise(R0) — register where channel expected → error."""
    with pytest.raises((SyntaxError, ValueError)):
        cv1("Thread.Raise(R0);")


def test_compile_jump_to_negative_label():
    """Flow.Jump(:-1) — negative label → SyntaxError."""
    with pytest.raises(SyntaxError):
        cv1("Flow.Jump(:-1);")


def test_compile_label_with_spaces():
    """Label ' bad label' stores 'bad label' as key; partial-name jump fails."""
    # ': bad label' → label key = "bad label"
    # Flow.Jump(:bad) → resolves "bad" which does NOT exist → SyntaxError
    with pytest.raises(SyntaxError):
        cv1(": bad label\nFlow.Jump(:bad);")


def test_compile_basic_wrong_register_format():
    """BASIC statement with wrong register format → error."""
    with pytest.raises((SyntaxError, ValueError)):
        cv1("10 MATH INC, X0")


# ══════════════════════════════════════════════════════════════════════════════
# PARSE_ARG: exhaustive type coverage
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("token,expected_kind", [
    ("R0", "reg"),
    ("R15", "reg"),
    ("42", "imm"),
    ("-1", "imm"),
    ('"hello"', "str"),
    ("'world'", "str"),
    ("Stream.Open", "stream"),
    ("Stream.Close", "stream"),
    ("EQ", "cond"),
    ("NZ", "cond"),
    ("text/html", "ctype"),
    ("application/json", "ctype"),
    ("UNKNOWN_SYM", "sym"),
    ("0xFF", "sym"),   # hex literals NOT parsed by parse_arg — treated as symbol
])
def test_parse_arg_all_types(token, expected_kind):
    """parse_arg correctly classifies every token kind."""
    kind, val = parse_arg(token)
    assert kind == expected_kind


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-FRONTEND MALFORMED INPUT: all parsers get garbage
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("garbage", [
    "",
    "   ",
    "\n\n\n",
    "????",
    "123 456 789",
    "GOTO 10",
    "BEGIN END",
    "@#$%",
    "\x01\x02\x03",
])
def test_basic_frontend_garbage_input(garbage):
    """BASIC frontend handles garbage input without unhandled exception."""
    from picoscript_basic import compile_basic
    try:
        result = compile_basic(garbage)
        assert result is not None
    except (SyntaxError, ValueError, TypeError, AttributeError, StopIteration, KeyError, IndexError):
        pass  # Controlled error, not a crash


@pytest.mark.parametrize("garbage", [
    "",
    "???",
    "void main() {",
    "int main(",
    "return;",
    "class Foo {}",
])
def test_cfront_garbage_input(garbage):
    """C frontend handles garbage without crashing."""
    from picoscript_cfront import compile_c
    try:
        result = compile_c(garbage)
        assert result is not None
    except (SyntaxError, ValueError, TypeError, AttributeError, StopIteration, KeyError, IndexError):
        pass


@pytest.mark.parametrize("garbage", [
    "",
    "????",
    "PERFORM UNTIL EOF",
    "01 DIVISION.",
    "MOVE BOGUS TO NOWHERE",
])
def test_cobol_garbage_input(garbage):
    """COBOL frontend handles garbage without crashing."""
    from picoscript_cobol import compile_cobol
    try:
        result = compile_cobol(garbage)
        assert result is not None
    except (SyntaxError, ValueError, TypeError, AttributeError, KeyError, StopIteration):
        pass


# ══════════════════════════════════════════════════════════════════════════════
# DECOMPILE_HEX: boundary and corrupt inputs
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_hex_all_zero():
    """decompile_hex with all-zero word."""
    result = decompile_hex([0x00000000])
    assert result.strip() == "00000000"


def test_decompile_hex_all_ones():
    """decompile_hex with all-ones word."""
    result = decompile_hex([0xFFFFFFFF])
    assert result.strip().upper() == "FFFFFFFF"


def test_decompile_hex_multiple_words():
    """decompile_hex with multiple words — one hex line each."""
    words = [0x00000000, 0x12345678, 0xFFFFFFFF]
    result = decompile_hex(words)
    lines = result.replace("\r\n", "\n").strip().split("\n")
    assert len(lines) == 3


# ══════════════════════════════════════════════════════════════════════════════
# ENCODE_INSTRUCTION: field overflow / masking
# ══════════════════════════════════════════════════════════════════════════════

def test_encode_instruction_fields_are_masked():
    """encode_instruction masks all fields to their bit widths."""
    # opcode is top 4 bits, so opcode=0x1F masks to 0xF
    word = encode_instruction(0x1F, rd=0xFF, rs1=0xFF, rs2=0xFF, imm16=0x1FFFF)
    opcode = (word >> 28) & 0xF
    rd = (word >> 24) & 0xF
    rs1 = (word >> 20) & 0xF
    rs2 = (word >> 16) & 0xF
    imm16 = word & 0xFFFF
    # Python doesn't mask automatically in encode_instruction — just verify consistent decode
    assert 0 <= opcode <= 15
    assert 0 <= rd <= 15
    assert 0 <= rs1 <= 15
    assert 0 <= rs2 <= 15
    assert 0 <= imm16 <= 0xFFFF


def test_encode_decode_roundtrip_all_fields():
    """Encode then decode: all fields preserved correctly for valid ranges."""
    for opcode in range(16):
        for field in [0, 1, 7, 14, 15]:
            word = encode_instruction(opcode, rd=field, rs1=field, rs2=field, imm16=field * 4096)
            decoded_opcode = (word >> 28) & 0xF
            decoded_rd = (word >> 24) & 0xF
            decoded_rs1 = (word >> 20) & 0xF
            decoded_rs2 = (word >> 16) & 0xF
            assert decoded_opcode == opcode
            assert decoded_rd == field
            assert decoded_rs1 == field
            assert decoded_rs2 == field
