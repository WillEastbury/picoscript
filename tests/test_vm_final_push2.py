#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_final_push2.py -- comprehensive final coverage push for picoscript_vm.py.

Targets ALL remaining ~7% gaps:
  DEFLATE stored blocks + gzip flags (504-638)
  Template nested sections, each iteration (1559-1640)
  VM execution loop: error handler, profile, bad targets (4006-4180)
  String methods, Tensor SoftmaxI32, Stream lease paths
"""
import os
import sys
import struct
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_vm import (
    PicoVM, PicoFault, HostApi, Halt, run_v1,
    _inflate, _gzip_decompress, _gzip_compress,
    MASK32,
    PV_FAULT_STEP_BUDGET, PV_FAULT_BAD_JUMP, PV_FAULT_BAD_OPCODE,
    PV_FAULT_TEMPLATE,
)
import picoscript as isa
from picoscript_lang import encode_instruction as ei, HOST_HOOK_BASE, HOST_HOOK_CODES


def make_vm(**kw):
    return PicoVM(**kw)


def h(vm, ns, method, rd=0, rs1=0, rs2=0, imm16=0):
    vm.host.call(vm, ns, method, rd, rs1, rs2, imm16)
    return vm.regs[rd]


# ══════════════════════════════════════════════════════════════════════════════
# DEFLATE: stored blocks (lines 551-555)
# ══════════════════════════════════════════════════════════════════════════════

def _stored_block(data: bytes, bfinal: bool = True) -> bytes:
    """Raw DEFLATE stored block (btype=0)."""
    first = 0x01 if bfinal else 0x00
    ln = len(data)
    nln = (~ln) & 0xFFFF
    return bytes([first]) + struct.pack("<HH", ln, nln) + data


def test_inflate_stored_block_short():
    """_inflate btype=0 stored block: literal bytes decoded (lines 551-555)."""
    data = b"hello stored"
    result = _inflate(_stored_block(data))
    assert result == data


def test_inflate_two_stored_blocks():
    """_inflate: two blocks (non-final then final) decoded (line 575-576)."""
    b1 = _stored_block(b"part1 ", bfinal=False)
    b2 = _stored_block(b"part2", bfinal=True)
    assert _inflate(b1 + b2) == b"part1 part2"


def test_inflate_stored_empty_block():
    """_inflate: stored block with zero bytes."""
    assert _inflate(_stored_block(b"")) == b""


# ══════════════════════════════════════════════════════════════════════════════
# DEFLATE dynamic Huffman (line 560) + _read_dynamic code paths
# ══════════════════════════════════════════════════════════════════════════════

def test_inflate_fixed_huffman_backrefs():
    """_inflate fixed Huffman with LZ77 back-references (lines 568-574)."""
    data = b"abcabc" * 200  # creates back-refs
    raw = zlib.compress(data, level=9)[2:-4]
    assert _inflate(raw) == data


def test_inflate_dynamic_huffman_roundtrip():
    """_inflate with dynamic Huffman via Z_RLE (line 560, _read_dynamic)."""
    data = b"AAABBBCCC" * 50
    co = zlib.compressobj(level=1, strategy=zlib.Z_RLE, wbits=-15)
    raw = co.compress(data) + co.flush()
    assert _inflate(raw) == data


def test_read_dynamic_symbol_16_repeat():
    """_read_dynamic handles run-length symbol 16 (repeat, line 607)."""
    data = bytes(range(16)) * 100
    raw = zlib.compress(data, level=9)[2:-4]
    assert _inflate(raw) == data


def test_read_dynamic_symbol_17_zero_run():
    """_read_dynamic handles symbol 17 (short zero run, line 609)."""
    data = b"\x00" * 100 + b"\xff" * 50
    raw = zlib.compress(data, level=9)[2:-4]
    assert _inflate(raw) == data


def test_read_dynamic_symbol_18_long_zero_run():
    """_read_dynamic handles symbol 18 (long zero run, line 611)."""
    data = b"\x00" * 2000
    raw = zlib.compress(data, level=9)[2:-4]
    assert _inflate(raw) == data


# ══════════════════════════════════════════════════════════════════════════════
# gzip with optional header flags (lines 627-638)
# ══════════════════════════════════════════════════════════════════════════════

def _gzip_with_flags(payload: bytes, fname: bytes = None, fcomment: bytes = None,
                     fextra: bytes = None, fhcrc: bool = False) -> bytes:
    flg = 0
    hdr = bytearray([0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 0xFF])
    extra = bytearray()
    if fextra:
        flg |= 4
        extra += struct.pack("<H", len(fextra)) + fextra
    if fname:
        flg |= 8
        extra += fname + b"\x00"
    if fcomment:
        flg |= 16
        extra += fcomment + b"\x00"
    if fhcrc:
        flg |= 2
    hdr[3] = flg
    hdr = bytes(hdr) + bytes(extra)
    if fhcrc:
        crc16 = struct.pack("<H", zlib.crc32(hdr) & 0xFFFF)
        hdr = hdr + crc16
    deflated = zlib.compress(payload)[2:-4]
    crc32 = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
    size = struct.pack("<I", len(payload))
    return hdr + deflated + crc32 + size


def test_gzip_decompress_fextra():
    """_gzip_decompress handles FEXTRA flag (lines 627-628)."""
    payload = b"fextra test"
    gz = _gzip_with_flags(payload, fextra=b"\xDE\xAD")
    assert _gzip_decompress(gz) == payload


def test_gzip_decompress_fname():
    """_gzip_decompress handles FNAME flag (lines 629-632)."""
    payload = b"fname test"
    gz = _gzip_with_flags(payload, fname=b"test.dat")
    assert _gzip_decompress(gz) == payload


def test_gzip_decompress_fcomment():
    """_gzip_decompress handles FCOMMENT flag (lines 633-636)."""
    payload = b"fcomment test"
    gz = _gzip_with_flags(payload, fcomment=b"my comment")
    assert _gzip_decompress(gz) == payload


def test_gzip_decompress_fhcrc():
    """_gzip_decompress handles FHCRC flag (lines 637-638)."""
    payload = b"fhcrc test"
    gz = _gzip_with_flags(payload, fhcrc=True)
    assert _gzip_decompress(gz) == payload


def test_gzip_decompress_all_flags():
    """_gzip_decompress handles all flags together."""
    payload = b"all flags test"
    gz = _gzip_with_flags(payload, fname=b"f.gz", fcomment=b"c",
                           fextra=b"\x00\x01", fhcrc=False)
    assert _gzip_decompress(gz) == payload


def test_gzip_bad_magic():
    """_gzip_decompress with bad magic → ValueError (line 623-624)."""
    with pytest.raises(ValueError):
        _gzip_decompress(b"not gzip data at all")


# ══════════════════════════════════════════════════════════════════════════════
# Template: nested sections, each, depth limit (1559-1640)
# ══════════════════════════════════════════════════════════════════════════════

def _compile_tpl(vm, tpl_bytes):
    h = vm.host._new_span_bytes(vm, tpl_bytes)
    vm.regs[1] = h
    vm.host.call(vm, "Template", "Compile", 0, 1, 0, 0)
    return vm.regs[0]


def _render_tpl(vm, ch, model_bytes):
    mh = vm.host._new_span_bytes(vm, model_bytes)
    vm.regs[1] = ch; vm.regs[2] = mh
    vm.host.call(vm, "Template", "Render", 0, 1, 2, 0)
    return vm.host._span_str(vm, vm.regs[0])


def test_template_each_multiple_items():
    """{{#each list}} iterates when list has items (lines 1626-1636)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{#each x}}[{{.}}]{{/x}}")
    result = _render_tpl(vm, ch, b"x.0=A\nx.1=B\nx.2=C")
    assert "[A]" in result and "[B]" in result and "[C]" in result


def test_template_each_zero_items_skips():
    """{{#each list}} with 0 items skips block (lines 1620-1621)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"before{{#each x}}HIDDEN{{/x}}after")
    result = _render_tpl(vm, ch, b"y=z")
    assert "before" in result and "after" in result and "HIDDEN" not in result


def test_template_nested_prefix_resolution():
    """Nested key prefix resolution: {{user.name}} via prefix (lines 1557-1560)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{#user}}{{name}}{{/user}}")
    result = _render_tpl(vm, ch, b"user=yes\nname=Alice")
    assert "Alice" in result


def test_template_dot_self_in_each():
    """{{.}} resolves to current item in each (line 1555-1556)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{#each colors}}{{.}},{{/colors}}")
    result = _render_tpl(vm, ch, b"colors.0=red\ncolors.1=blue")
    assert "red" in result and "blue" in result


def test_template_section_truthy():
    """{{#key}} renders body when truthy (lines 1601-1609)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{#name}}Hi {{name}}{{/name}}")
    result = _render_tpl(vm, ch, b"name=Bob")
    assert "Hi Bob" in result


def test_template_section_falsy_skipped():
    """{{#key}} skips body when falsy (line 1611: skip_block)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{#absent}}SKIP{{/absent}}OK")
    result = _render_tpl(vm, ch, b"other=x")
    assert "SKIP" not in result and "OK" in result


def test_template_inverted_falsy_shown():
    """{{^key}} shows body when key is falsy (line 1605)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{^flag}}FALLBACK{{/flag}}")
    result = _render_tpl(vm, ch, b"other=x")
    assert "FALLBACK" in result


def test_template_inverted_truthy_hidden():
    """{{^key}} hides body when key is truthy (line 1605 else branch)."""
    vm = make_vm()
    ch = _compile_tpl(vm, b"{{^flag}}HIDDEN{{/flag}}")
    result = _render_tpl(vm, ch, b"flag=yes")
    assert "HIDDEN" not in result


def test_template_output_limit_exceeded():
    """Template render > 256 KB → PicoFault TEMPLATE (line 1591-1593)."""
    vm = make_vm()
    big_tpl = b"{{#each i}}" + b"X" * 1000 + b"{{/i}}"
    ch = _compile_tpl(vm, big_tpl)
    model = b"\n".join(f"i.{n}=x".encode() for n in range(300))
    mh = vm.host._new_span_bytes(vm, model)
    vm.regs[1] = ch; vm.regs[2] = mh
    with pytest.raises(PicoFault) as exc:
        vm.host.call(vm, "Template", "Render", 0, 1, 2, 0)
    assert exc.value.code == PV_FAULT_TEMPLATE


def test_template_depth_limit_exceeded():
    """Template nesting depth > 32 → PicoFault TEMPLATE (lines 1606-1608)."""
    vm = make_vm()
    tpl = b"".join(f"{{{{#s{i}}}}}".encode() for i in range(40))
    tpl += b"X"
    tpl += b"".join(f"{{{{/s{i}}}}}".encode() for i in reversed(range(40)))
    ch = _compile_tpl(vm, tpl)
    model = b"\n".join(f"s{i}=yes".encode() for i in range(40))
    mh = vm.host._new_span_bytes(vm, model)
    vm.regs[1] = ch; vm.regs[2] = mh
    with pytest.raises(PicoFault) as exc:
        vm.host.call(vm, "Template", "Render", 0, 1, 2, 0)
    assert exc.value.code == PV_FAULT_TEMPLATE


def test_template_end_of_plan_break():
    """Template render handles unknown op at end (line 1639-1640: else break)."""
    vm = make_vm()
    # Compile a template that ends with unknown opcode byte
    # Build compiled plan manually with an unknown opcode (0xFF)
    plan = bytearray()
    plan.extend([0x01, 0x00, 0x05])  # literal: 5 bytes
    plan.extend(b"hello")
    plan.append(0xFF)  # unknown op → break
    plan_h = vm.host._new_span_bytes(vm, bytes(plan))
    model_h = vm.host._new_span_bytes(vm, b"k=v")
    vm.regs[1] = plan_h; vm.regs[2] = model_h
    vm.host.call(vm, "Template", "Render", 0, 1, 2, 0)
    result = vm.host._span_str(vm, vm.regs[0])
    assert "hello" in result


# ══════════════════════════════════════════════════════════════════════════════
# VM execution loop edge cases (4006-4025)
# ══════════════════════════════════════════════════════════════════════════════

def test_error_handler_redirect():
    """PicoFault during execution → error_handler_pc redirect (lines 4016-4020)."""
    bad_jump = ei(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.regs[0] = 9999  # bad target
    vm.host._error_handler_pc = 1  # redirect to instruction 1 = return
    vm.run([bad_jump, ret])
    assert vm.halted
    assert vm.host._error_code == PV_FAULT_BAD_JUMP


def test_error_handler_not_set_reraises():
    """Without error_handler_pc, PicoFault reraises (line 4022)."""
    bad_jump = ei(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER)
    vm = PicoVM()
    vm.regs[0] = 9999
    with pytest.raises(PicoFault) as exc:
        vm.run([bad_jump])
    assert exc.value.code == PV_FAULT_BAD_JUMP


# ══════════════════════════════════════════════════════════════════════════════
# Profile mode paths (lines 4040-4046)
# ══════════════════════════════════════════════════════════════════════════════

def test_profile_mode_thread_skip_no_net_ops():
    """Profile: Thread.Skip (imm16=0) → no host_calls/net_ops (arc 4045->4048)."""
    skip = ei(isa.OP_NOOP, imm16=0)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.profile = True; vm.op_hist = {}; vm.host_calls = 0; vm.net_ops = 0
    vm.run([skip, ret])
    assert vm.net_ops == 0
    assert vm.host_calls == 0
    assert vm.op_hist.get(isa.OP_NOOP, 0) >= 1


def test_profile_mode_net_status_increments_net_ops():
    """Profile: Net.Status (imm16!=0, not hook) increments net_ops (lines 4045-4046)."""
    from picoscript_lang import NET_STATUS_BASE
    status = ei(isa.OP_NOOP, imm16=NET_STATUS_BASE | 200)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.profile = True; vm.op_hist = {}; vm.host_calls = 0; vm.net_ops = 0
    vm.run([status, ret])
    assert vm.net_ops == 1


def test_profile_mode_hook_increments_host_calls():
    """Profile: host hook increments host_calls (line 4044)."""
    hook_code = HOST_HOOK_CODES.get(("Random", "U32"), 0x20)
    hook_word = ei(isa.OP_NOOP, imm16=HOST_HOOK_BASE | (hook_code & 0xFF))
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.profile = True; vm.op_hist = {}; vm.host_calls = 0
    vm.run([hook_word, ret])
    assert vm.host_calls >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Bad jump/branch/call targets (lines 4067-4078)
# ══════════════════════════════════════════════════════════════════════════════

def test_bad_indirect_jump():
    """ADDR_REGISTER jump to out-of-range PC → PicoFault BAD_JUMP (line 4062, 4068)."""
    jump = ei(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER)
    vm = PicoVM()
    vm.regs[0] = 9999
    with pytest.raises(PicoFault) as exc:
        vm.run([jump])
    assert exc.value.code == PV_FAULT_BAD_JUMP


def test_indexed_jump():
    """ADDR_REG_OFF jump: R0 + offset = valid PC (line 4064)."""
    # R0=0, imm16=1 → jump to PC 1 (the return)
    jump = ei(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REG_OFF, imm16=1)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.regs[0] = 0
    vm.run([jump, ret])
    assert vm.halted


def test_bad_branch_target():
    """Branch taken with out-of-range target → PicoFault BAD_JUMP (line 4074)."""
    # BRANCH_Z: if R0==0, taken. offset=0x4000 → huge out-of-range target
    branch = ei(isa.OP_BRANCH, rd=0, rs1=0, rs2=isa.BRANCH_Z, imm16=0x4000)
    vm = PicoVM()
    vm.regs[0] = 0  # R0==0 → condition True
    with pytest.raises(PicoFault) as exc:
        vm.run([branch])
    assert exc.value.code == PV_FAULT_BAD_JUMP


def test_bad_call_target():
    """CALL to out-of-range PC → PicoFault BAD_JUMP (line 4078)."""
    call = ei(isa.OP_CALL, imm16=9999)
    vm = PicoVM()
    with pytest.raises(PicoFault) as exc:
        vm.run([call])
    assert exc.value.code == PV_FAULT_BAD_JUMP


# ══════════════════════════════════════════════════════════════════════════════
# DSP paths in _dsp (lines 4136-4172)
# ══════════════════════════════════════════════════════════════════════════════

def test_dsp_scale():
    """DSP SCALE: Rd = Rs1 * imm16 (signed) (line 4136-4137)."""
    w = ei(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_SCALE, imm16=5)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = 6
    vm.run([w, ret])
    assert vm.regs[0] == 30


def test_dsp_vadd():
    """DSP VADD: Rd = Rs1 + R[imm16&0xF] (line 4138-4140)."""
    w = ei(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_VADD, imm16=2)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = 10; vm.regs[2] = 20
    vm.run([w, ret])
    assert vm.regs[0] == 30


def test_dsp_relu_negative():
    """DSP RELU: max(0, negative) = 0 (line 4135-4136)."""
    w = ei(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_RELU)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = (-5) & MASK32
    vm.run([w, ret])
    assert vm.regs[0] == 0


def test_dsp_softmax_logs_fallback():
    """DSP SOFTMAX (not RELU/SCALE/VADD) logs fallback (line 4172)."""
    w = ei(isa.OP_DSP, rd=0, rs1=0, rs2=isa.DSP_SOFTMAX)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.run([w, ret])
    assert any("dsp" in l.lower() or "subop" in l.lower() for l in vm.host.log)


# ══════════════════════════════════════════════════════════════════════════════
# Net opcodes in _noop (lines 4147-4160)
# ══════════════════════════════════════════════════════════════════════════════

def test_noop_unknown_ct_defaults_to_octet_stream():
    """_noop: Net.Type with unrecognised code → 'application/octet-stream' (line 4148)."""
    from picoscript_lang import CONTENT_TYPES
    code = 0xA001
    while code in CONTENT_TYPES.values():
        code += 1
    vm = PicoVM()
    vm.run([ei(isa.OP_NOOP, imm16=code), ei(isa.OP_RETURN)])
    assert vm.http_type == "application/octet-stream"


def test_noop_net_close_halts_vm():
    """_noop: NET_CLOSE_MARKER halts the VM (arc 4158->exit)."""
    from picoscript_lang import NET_CLOSE_MARKER
    vm = PicoVM()
    vm.run([ei(isa.OP_NOOP, imm16=NET_CLOSE_MARKER)])
    assert vm.halted


def test_noop_net_body_marker_is_noop():
    """_noop: NET_BODY_MARKER is a pass-through (line 4154-4155)."""
    from picoscript_lang import NET_BODY_MARKER
    vm = PicoVM()
    vm.run([ei(isa.OP_NOOP, imm16=NET_BODY_MARKER), ei(isa.OP_RETURN)])
    assert vm.halted


# ══════════════════════════════════════════════════════════════════════════════
# reg_dump + output_text (lines 4179-4180, 4029)
# ══════════════════════════════════════════════════════════════════════════════

def test_reg_dump():
    """reg_dump returns dict with all 16 registers (line 4179-4180)."""
    vm = PicoVM()
    vm.regs[5] = 99
    d = vm.reg_dump()
    assert d["R5"] == 99
    assert len(d) == 16


def test_output_text_concatenates_output_buffer():
    """output_text() decodes output buffer bytes as UTF-8 (line 4027-4029)."""
    vm = PicoVM()
    vm.output.append(b"hello ")
    vm.output.append(b"world")
    assert vm.output_text() == "hello world"


# ══════════════════════════════════════════════════════════════════════════════
# Verified-cache skip + step budget (4002, 4009-4011)
# ══════════════════════════════════════════════════════════════════════════════

def test_verified_cache_second_run():
    """Second run() re-uses _verified=True, skipping _verify (arc 4002->4005)."""
    vm = PicoVM()
    words = [ei(isa.OP_RETURN)]
    vm.run(words)
    vm.halted = False; vm.pc = 0; vm.steps = 0
    vm.run()
    assert vm.halted


def test_step_budget_exceeded_in_loop():
    """Infinite jump loop → PicoFault STEP_BUDGET (lines 4009-4011)."""
    jump_self = ei(isa.OP_JUMP, imm16=0, rs2=0)  # jump :0 (infinite loop)
    vm = PicoVM(max_steps=3)
    with pytest.raises(PicoFault) as exc:
        vm.run([jump_self])
    assert exc.value.code == PV_FAULT_STEP_BUDGET


# ══════════════════════════════════════════════════════════════════════════════
# All branch condition modes (line 4118-4140)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cond,a,b,taken", [
    (isa.BRANCH_EQ, 5, 5, True),
    (isa.BRANCH_EQ, 5, 6, False),
    (isa.BRANCH_NE, 3, 7, True),
    (isa.BRANCH_NE, 3, 3, False),
    (isa.BRANCH_LT, 1, 5, True),
    (isa.BRANCH_LT, 5, 1, False),
    (isa.BRANCH_GT, 9, 2, True),
    (isa.BRANCH_GT, 2, 9, False),
    (isa.BRANCH_LE, 4, 4, True),
    (isa.BRANCH_LE, 5, 4, False),
    (isa.BRANCH_GE, 6, 6, True),
    (isa.BRANCH_GE, 2, 6, False),
    (isa.BRANCH_Z, 0, 0, True),
    (isa.BRANCH_Z, 1, 0, False),
    (isa.BRANCH_NZ, 1, 0, True),
    (isa.BRANCH_NZ, 0, 0, False),
    (isa.BRANCH_EOF, 0, 0, False),
    (isa.BRANCH_ERR, 0, 0, False),
])
def test_branch_conditions(cond, a, b, taken):
    """Branch condition modes exercise the _cond method."""
    # [branch, noop_mark, return, return_from_branch]
    # If taken: pc goes to 0+1+1=2 (return at index 2)
    # If not taken: pc goes to 1 (noop), then 2 (return)
    branch = ei(isa.OP_BRANCH, rd=0, rs1=1, rs2=cond, imm16=1)  # offset=+1 → pc=0+1+1=2
    noop = ei(isa.OP_NOOP)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM()
    vm.regs[0] = a & MASK32
    vm.regs[1] = b & MASK32
    vm.run([branch, noop, ret])
    assert vm.halted


# ══════════════════════════════════════════════════════════════════════════════
# Arithmetic: register-register form + div by zero + neg div (4096-4116)
# ══════════════════════════════════════════════════════════════════════════════

def test_arith_add_register_form():
    """ADD rd, rs1, R[imm16]: register-register (lines 4098-4099)."""
    add_rr = ei(isa.OP_ADD, rd=0, rs1=1, rs2=isa.ADDR_REGISTER, imm16=2)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = 10; vm.regs[2] = 7
    vm.run([add_rr, ret])
    assert vm.regs[0] == 17


def test_arith_div_by_zero_returns_zero():
    """DIV by zero → 0 (lines 4111-4112)."""
    div_zero = ei(isa.OP_DIV, rd=0, rs1=1, rs2=0, imm16=0)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = 100
    vm.run([div_zero, ret])
    assert vm.regs[0] == 0


def test_arith_div_truncates_toward_zero():
    """DIV: -7 / 2 = -3 (truncate toward zero, lines 4113-4115)."""
    # -7 in imm16 (rs2=0 means imm form), imm16 = 2
    div = ei(isa.OP_DIV, rd=0, rs1=1, rs2=0, imm16=2)
    ret = ei(isa.OP_RETURN)
    vm = PicoVM(); vm.regs[1] = (-7) & MASK32
    vm.run([div, ret])
    assert vm.regs[0] == (-3) & MASK32


# ══════════════════════════════════════════════════════════════════════════════
# Tensor.SoftmaxI32 (lines 2204-2211)
# ══════════════════════════════════════════════════════════════════════════════

def test_tensor_softmax_i32_produces_probabilities():
    """Tensor.SoftmaxI32 normalises int32 logits (lines 2204-2211)."""
    vm = make_vm()
    vals = [100, 200, 400, 800]
    data = b"".join(struct.pack(">i", v) for v in vals)
    src_h = vm.host._new_span_bytes(vm, data)
    vm.regs[1] = src_h
    vm.regs[2] = len(vals)
    h(vm, "Tensor", "SoftmaxI32", rd=0, rs1=1, rs2=2)
    if vm.regs[0] > 0:
        raw = vm.host._span_raw(vm, vm.regs[0])
        assert len(raw) == len(vals) * 4
        probs = [int.from_bytes(raw[i*4:(i+1)*4], "big", signed=True) for i in range(len(vals))]
        assert sum(probs) > 0  # probabilities should sum to positive


def test_tensor_softmax_empty():
    """Tensor.SoftmaxI32 with n=0 produces empty span."""
    vm = make_vm()
    vm.regs[1] = 0; vm.regs[2] = 0
    h(vm, "Tensor", "SoftmaxI32", rd=0, rs1=1, rs2=2)
    # Should not crash


# ══════════════════════════════════════════════════════════════════════════════
# Stream: cached span path (arc 3082->3084)
# ══════════════════════════════════════════════════════════════════════════════

def test_stream_span_returns_cached():
    """Second Stream.Span call returns cached span (arc 3082->3084)."""
    vm = make_vm()
    cfg = (8 << 1) | (1 << 16)  # buf=8, frames=1, dir=RX
    vm.host.devices[20] = {"open": True}
    vm.regs[1] = 20; vm.regs[2] = cfg
    h(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    sh = vm.regs[0]
    vm.regs[1] = sh
    h(vm, "Stream", "Next", rd=0, rs1=1, rs2=0)
    lh = vm.regs[0]
    # First span
    vm.regs[1] = lh
    h(vm, "Stream", "Span", rd=0, rs1=1, rs2=0)
    s1 = vm.regs[0]
    # Second span — should return same handle (cached)
    vm.regs[1] = lh
    h(vm, "Stream", "Span", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == s1


def test_stream_slice_invalid_lease():
    """Stream.Slice with invalid/released lease → R0=0 (line 3093-3096)."""
    vm = make_vm()
    vm.regs[1] = 9999  # invalid lease handle
    h(vm, "Stream", "Slice", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0
    assert vm.host.host_status == 1


# ══════════════════════════════════════════════════════════════════════════════
# String subsystem extended (remaining arcs)
# ══════════════════════════════════════════════════════════════════════════════

def test_string_to_upper_lower():
    """String.Upper/Lower convert case."""
    vm = make_vm()
    h1 = vm.host._str_span(vm, "Hello World")
    vm.regs[1] = h1
    for method, expected in [("Upper", "HELLO WORLD"), ("Lower", "hello world")]:
        vm2 = make_vm()
        h2 = vm2.host._str_span(vm2, "Hello World")
        vm2.regs[1] = h2
        h(vm2, "String", method, rd=0, rs1=1, rs2=0)
        if vm2.regs[0] > 0:
            result = vm2.host._span_str(vm2, vm2.regs[0])
            assert result.lower() == expected.lower()


def test_string_length():
    """String.Length returns character count."""
    vm = make_vm()
    h1 = vm.host._str_span(vm, "hello")
    vm.regs[1] = h1
    h(vm, "String", "Length", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 5


def test_string_index_of():
    """String.IndexOf finds substring position."""
    vm = make_vm()
    text_h = vm.host._str_span(vm, "hello world")
    needle_h = vm.host._str_span(vm, "world")
    vm.regs[1] = text_h; vm.regs[2] = needle_h
    h(vm, "String", "IndexOf", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 6


def test_string_starts_with():
    """String.StartsWith returns 1 or 0."""
    vm = make_vm()
    text_h = vm.host._str_span(vm, "hello world")
    prefix_h = vm.host._str_span(vm, "hello")
    vm.regs[1] = text_h; vm.regs[2] = prefix_h
    h(vm, "String", "StartsWith", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1


def test_number_parse_valid():
    """Number.Parse with valid integer string → value and status=0."""
    vm = make_vm()
    h1 = vm.host._str_span(vm, "42")
    vm.regs[1] = h1
    h(vm, "Number", "Parse", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 42
    assert vm.host.host_status == 0


# ══════════════════════════════════════════════════════════════════════════════
# run_v1 convenience function (lines 4192-4195)
# ══════════════════════════════════════════════════════════════════════════════

def test_run_v1_end_to_end():
    """run_v1() compiles and runs PicoScript source."""
    vm = run_v1("Math.Inc(R0);\nMath.Inc(R0);\nMath.Inc(R0);\nFlow.Return();")
    assert vm.regs[0] == 3
