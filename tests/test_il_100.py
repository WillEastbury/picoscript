#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import (  # noqa: E402
    Imm,
    Inst,
    RegisterPressureError,
    VReg,
    _ConstExpansion,
    _emit_c,
    _emit_js_function,
    _emit_js_inst,
    _emit_word,
    _legalize_spills,
    _phys,
    lower_to_bytecode,
    lower_to_bytecode_safe,
    lower_to_c,
    lower_to_js,
    optimize,
    source_line_text,
    verify_response_ownership,
)
from picoscript_vm import PicoVM  # noqa: E402


def _c_name(v):
    return f"g{v.id}" if v.pinned else f"v{v.id}"


def _c_op(x):
    return str(x.value) if isinstance(x, Imm) else _c_name(x)


def _j_name(v):
    return f"g{v.id}" if v.pinned else f"v{v.id}"


def _j_op(x):
    return str(x.value) if isinstance(x, Imm) else _j_name(x)


def _out_ints(words):
    vm = PicoVM().run(words)
    out = []
    for cell in vm.output:
        n = int.from_bytes(cell, "big")
        out.append(n - 0x100000000 if n & 0x80000000 else n)
    return out


def test_verify_response_ownership_exercises_cfg_edges():
    resp = Inst("host", ns="Resp", method="Status", args=(Imm(200),))
    ok = [
        resp,
        Inst("cmpbr", a=Imm(0), b=Imm(1), cond="EQ", label="missing"),
        Inst("ret"),
    ]
    verify_response_ownership(ok)

    branch = [
        resp,
        Inst("jmptab", a=Imm(1), targets=("case0", "case1"), label="default"),
        Inst("label", label="case0"),
        Inst("host", ns="Resp", method="Header", args=(Imm(1), Imm(2))),
        Inst("ret"),
        Inst("label", label="case1"),
        Inst("host", ns="Resp", method="Write", args=(Imm(3),)),
        Inst("ret"),
        Inst("label", label="default"),
        Inst("host", ns="Resp", method="End", args=()),
        Inst("ret"),
    ]
    verify_response_ownership(branch)
    verify_response_ownership([resp, Inst("jmptab", a=Imm(0), targets=("case0",), label="missing"), Inst("label", label="case0"), Inst("ret")])

    callee = [
        resp,
        Inst("call", label="sub"),
        Inst("ret"),
        Inst("label", label="sub"),
        Inst("host", ns="Resp", method="Header", args=(Imm(7), Imm(8))),
        Inst("ret"),
    ]
    verify_response_ownership(callee)
    verify_response_ownership([resp, Inst("call", label="missing"), Inst("ret")])


def test_optimize_and_source_line_end_of_buffer():
    v = VReg("v")
    out = optimize(
        [
            Inst("add", dst=v, a=Imm(6), b=Imm(7)),
            Inst("mov", dst=v, a=v),
        ]
    )
    assert out[0].op == "const"
    assert source_line_text("tail", 4) == "tail"


def test_spill_legalization_handles_all_operand_kinds():
    s = VReg("spill", pinned=True)
    other = VReg("other")
    insts = [
        Inst("add", dst=other, a=s, b=s),
        Inst("host", dst=s, ns="Host", method="Op", args=(other, s)),
        Inst("cmpbr", dst=s, a=s, b=s, cond="EQ", label="done"),
        Inst("label", label="done"),
        Inst("inc", dst=s),
    ]
    legal = _legalize_spills(insts, {s.id})
    ops = [ins.op for ins in legal]
    assert ops.count("load") >= 7
    assert ops.count("save") >= 2
    assert any(ins.op == "cmpbr" and ins.dst is not s for ins in legal)


def test_phys_and_lower_to_bytecode_label_and_opt_paths():
    v = VReg("x")
    with pytest.raises(RegisterPressureError):
        _phys({}, v)

    words = lower_to_bytecode(
        [Inst("label", label="start"), Inst("wait"), Inst("ret")],
        opt=True,
    )
    assert len(words) == 2


def test_emit_word_edge_cases():
    v = VReg("x")
    mapping = {v.id: 1}
    labels = {"L": 5}

    with pytest.raises(_ConstExpansion):
        _emit_word(Inst("const", dst=v, imm=123), mapping, labels, 0)
    with pytest.raises(_ConstExpansion):
        _emit_word(Inst("mov", dst=v, a=Imm(9)), mapping, labels, 0)
    with pytest.raises(ValueError, match="jmptab"):
        _emit_word(Inst("jmptab", a=v, targets=("L",), label="L"), mapping, labels, 0)

    assert isinstance(_emit_word(Inst("wait", a=v), mapping, labels, 0), int)
    assert isinstance(_emit_word(Inst("raise", imm=9), mapping, labels, 0), int)

    with pytest.raises(ValueError, match="unknown net kind"):
        _emit_word(Inst("net", method="bogus"), mapping, labels, 0)
    with pytest.raises(ValueError, match="cannot lower IL op"):
        _emit_word(Inst("bogus"), mapping, labels, 0)


def test_lower_to_bytecode_safe_debug_paths(monkeypatch):
    debug = {}
    words = lower_to_bytecode_safe(
        [Inst("label", label="L0"), Inst("mov", dst=VReg("x"), a=Imm(40000)), Inst("ret")],
        debug=debug,
        check_ownership=False,
    )
    assert words
    assert 0 in debug and 1 in debug

    def fake_emit_word(ins, mapping, labels, pc):
        raise _ConstExpansion(3, 99)

    monkeypatch.setattr("picoscript_il._emit_word", fake_emit_word)
    assert lower_to_bytecode_safe([Inst("wait")], check_ownership=False)


def test_lower_to_bytecode_safe_spill_integration():
    src = "".join(f"int a{i} = {i};" for i in range(20)) + "int s = " + " + ".join(
        f"a{i}" for i in range(20)
    ) + "; Io.WriteByte(s);"
    assert b"".join(PicoVM().run(lower_to_bytecode_safe(compile_c(src))).output) == bytes([190])


def test_lower_to_c_opt_false_and_emit_variants():
    assert "demo" in lower_to_c([Inst("wait"), Inst("ret")], func_name="demo", opt=False)

    v = VReg("x")
    lbl = {"sub": "sub_fn"}
    assert "!= 0" in _emit_c(Inst("cmpbr", a=v, b=Imm(0), cond="NZ", label="sub"), _c_op, _c_name, lbl, True)
    assert "pv_cond" in _emit_c(Inst("cmpbr", a=v, b=Imm(0), cond="EOF", label="sub"), _c_op, _c_name, lbl, True)
    assert "pv_host" in _emit_c(Inst("host", dst=v, ns="Bits", method="Rol", args=(v, Imm(1))), _c_op, _c_name, lbl, True)
    assert "pv_load" in _emit_c(Inst("load", dst=v, imm=12), _c_op, _c_name, lbl, True)
    assert "pv_net_header" in _emit_c(Inst("net", method="header"), _c_op, _c_name, lbl, True)
    assert "unhandled IL op net" in _emit_c(Inst("net", method="bogus"), _c_op, _c_name, lbl, True)
    assert "pv_dsp" in _emit_c(Inst("dsp", dst=v, a=v, b=Imm(2), imm=4), _c_op, _c_name, lbl, True)
    assert "pv_wait" in _emit_c(Inst("wait"), _c_op, _c_name, lbl, True)
    assert "pv_raise" in _emit_c(Inst("raise", imm=7), _c_op, _c_name, lbl, True)
    assert "unhandled IL op bogus" in _emit_c(Inst("bogus"), _c_op, _c_name, lbl, True)


def test_lower_to_js_opt_false_and_emit_variants():
    assert "js_demo" in lower_to_js([Inst("wait")], module_name="js_demo", opt=False)

    func_lines = _emit_js_function(
        "f",
        [Inst("const", dst=VReg("tmp"), imm=1)],
        _j_name,
        _j_op,
        {},
        {},
    )
    assert any("_b = -1; continue;" in line for line in func_lines)

    targets = {"L": 1, "D": 2}
    funcs = {"sub": "sub_fn"}
    v = VReg("x")
    assert "=== 0" in _emit_js_inst(Inst("cmpbr", a=v, b=Imm(0), cond="Z", label="L"), _j_op, _j_name, targets, funcs)[0]
    assert "!== 0" in _emit_js_inst(Inst("cmpbr", a=v, b=Imm(0), cond="NZ", label="L"), _j_op, _j_name, targets, funcs)[0]
    assert "false" in _emit_js_inst(Inst("cmpbr", a=v, b=Imm(0), cond="BAD", label="L"), _j_op, _j_name, targets, funcs)[0]
    assert "|" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Or", args=(v, Imm(1))), _j_op, _j_name, targets, funcs)[0]
    assert "^" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Xor", args=(v, Imm(1))), _j_op, _j_name, targets, funcs)[0]
    assert "~" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Not", args=(v,)), _j_op, _j_name, targets, funcs)[0]
    assert "<<" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Shl", args=(v, Imm(1))), _j_op, _j_name, targets, funcs)[0]
    assert ">>" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Sar", args=(v, Imm(1))), _j_op, _j_name, targets, funcs)[0]
    assert "rt.host" in _emit_js_inst(Inst("host", dst=v, ns="Bits", method="Rol", args=(v, Imm(1))), _j_op, _j_name, targets, funcs)[0]
    assert "dotLenSet" in _emit_js_inst(Inst("host", ns="Dot8", method="Len", args=(Imm(3),)), _j_op, _j_name, targets, funcs)[0]
    assert "dot8" in _emit_js_inst(Inst("host", dst=v, ns="Dot8", method="Of", args=(v, Imm(2))), _j_op, _j_name, targets, funcs)[0]
    assert "rt.load" in _emit_js_inst(Inst("load", dst=v, imm=4), _j_op, _j_name, targets, funcs)[0]
    assert "rt.netHeader" in _emit_js_inst(Inst("net", method="header"), _j_op, _j_name, targets, funcs)[0]
    assert "unhandled net" in _emit_js_inst(Inst("net", method="bogus"), _j_op, _j_name, targets, funcs)[0]
    assert "rt.dsp" in _emit_js_inst(Inst("dsp", dst=v, a=v, b=Imm(2), imm=9), _j_op, _j_name, targets, funcs)[0]
    assert _emit_js_inst(Inst("wait"), _j_op, _j_name, targets, funcs)[1] is True
    assert "raise 8" in _emit_js_inst(Inst("raise", imm=8), _j_op, _j_name, targets, funcs)[0]
    assert "unhandled bogus" in _emit_js_inst(Inst("bogus"), _j_op, _j_name, targets, funcs)[0]
