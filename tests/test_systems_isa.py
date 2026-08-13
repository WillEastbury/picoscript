"""Contract tests for PicoScript's fixed two-word systems extension."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picoscript as isa
import pytest

from picoscript_cfront import compile_c, preprocess_c
from picoscript_il import ILBuilder, lower_to_bytecode_safe
from picoscript_systems import (
    SystemsPicoVM, decode_super, disassemble_systems, encode_super, is_super_word, reg_operand,
    F_INT64, F_MEMORY, F_FRAME, T_U64, T_PTR,
    S_MOVI64, S_ADD64, S_LOAD64, S_STORE64,
    S_FRAME_ENTER, S_FRAME_LEAVE,
)
from picoscript_vm import HostApi


def _run_c(source):
    words = lower_to_bytecode_safe(compile_c(source))
    vm = SystemsPicoVM().run(words)
    return vm, words, [int.from_bytes(item, "big") for item in vm.output]


def test_wire_format_is_exactly_two_words_in_reserved_noop_range():
    words = encode_super(F_INT64, S_ADD64, 2, 1, reg_operand(3), T_U64)
    assert len(words) == 2
    assert is_super_word(words[0])
    op, dst, src, family, imm = isa.decode_instruction_fast(words[0])
    assert (op, dst, src, family, imm >> 12) == (isa.OP_NOOP, 2, 1, F_INT64, 5)
    assert decode_super(*words).payload == reg_operand(3)
    text = disassemble_systems(words + [isa.encode_instruction(isa.OP_RETURN)])
    assert "INT64.ADD64.U64 X2, X1, X3" in text
    assert "  2: RETURN" in text


def test_il_and_reference_vm_execute_native_uint64_and_typed_memory():
    b = ILBuilder()
    base, a, one, total, loaded = [b.vreg(n) for n in ("base", "a", "one", "total", "loaded")]
    b.system(F_INT64, S_MOVI64, base, payload=128, typecode=T_PTR)
    b.system(F_INT64, S_MOVI64, a, payload=0xFFFFFFFF, typecode=T_U64)
    b.system(F_INT64, S_MOVI64, one, payload=2, typecode=T_U64)
    b.system(F_INT64, S_ADD64, total, a, one, T_U64)
    b.system(F_MEMORY, S_STORE64, total, base, payload=0, typecode=T_U64)
    b.system(F_MEMORY, S_LOAD64, loaded, base, payload=0, typecode=T_U64)
    b.ret()
    vm = SystemsPicoVM().run(lower_to_bytecode_safe(b.insts))
    assert 0x100000001 in vm.xregs


def test_c_arrays_packed_structs_pointers_and_sizeof():
    vm, words, output = _run_c("""
typedef struct __attribute__((packed)) Pair { uint8_t tag; uint32_t value; } Pair;
Pair p;
int values[4];
int x = 4;
int *px = &x;
p.value = 42;
values[2] = 7;
*px += 3;
print(p.value + values[2] + x + sizeof(values));
""")
    assert output == [72]
    assert any(is_super_word(word) for word in words)


def test_recursive_functions_have_reentrant_frame_local_storage():
    vm, words, output = _run_c("""
int fact(int n) {
    int scratch[2];
    scratch[0] = n;
    if (n <= 1) { return 1; }
    return scratch[0] * fact(n - 1);
}
print(fact(6));
""")
    assert output == [720]
    assert vm.frames == []
    decoded = [decode_super(words[i], words[i + 1]) for i in range(len(words) - 1)
               if is_super_word(words[i])]
    assert any(x.family == F_FRAME and x.opcode == S_FRAME_ENTER for x in decoded)
    assert any(x.family == F_FRAME and x.opcode == S_FRAME_LEAVE for x in decoded)


def test_callbacks_calloc_and_native_bitwise_syntax():
    _vm, _words, output = _run_c("""
typedef int (*callback_t)(int);
typedef struct Ops { callback_t apply; } Ops;
int plus1(int x) { return x + 1; }
callback_t cb = plus1;
Ops ops; ops.apply = plus1;
int *p = calloc(3, sizeof(int));
p[1] = (cb(4) << 1) | (ops.apply(0));
print(p[0] + p[1]);
free(p);
""")
    assert output == [11]


def test_preprocessor_include_shares_macros_and_pragma_pack_is_accepted():
    files = {"config.h": "#define COUNT 4\n"}
    out = preprocess_c(
        '#include "config.h"\n#pragma pack(push, 1)\n'
        'typedef struct P { uint8_t a; uint32_t b; } P;\n#pragma pack(pop)\nint n=COUNT;',
        source_path="root.c", include_resolver=lambda name, angled: files[name])
    assert "int n=4;" in out
    assert "__attribute__((packed))" in out
    compile_c(out)


def test_casts_const_and_volatile_have_systems_semantics():
    _vm, _words, output = _run_c("""
volatile uint32_t status = 3;
uint64_t wide = (uint64_t)status << 33;
status += 4;
print(status + (uint32_t)wide);
""")
    assert output == [7]
    with pytest.raises(SyntaxError, match="assignment to const"):
        compile_c("const uint64_t offset=1; offset=2;")
    with pytest.raises(SyntaxError, match="const lvalue"):
        compile_c("const int *p=(const int*)0; *p=1;")


def test_page_header_can_store_native_block_size_offset():
    source = """
struct page_header { uint32_t magic; uint32_t length; uint64_t offset; };
struct page_header h;
h.offset = Block.Size();
print((uint32_t)h.offset);
"""
    words = lower_to_bytecode_safe(compile_c(source))
    host = HostApi()
    host.block_data = bytearray(96 * 1024)
    vm = SystemsPicoVM(host=host).run(words)
    assert [int.from_bytes(item, "big") for item in vm.output] == [96 * 1024]
    decoded = [decode_super(words[i], words[i + 1]) for i in range(len(words) - 1)
               if is_super_word(words[i])]
    assert any(x.family == F_MEMORY and x.opcode == S_STORE64 for x in decoded)
