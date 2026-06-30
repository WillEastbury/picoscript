#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picoscript as isa  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_lang import (  # noqa: E402
    Compiler,
    CONTENT_TYPES,
    NET_BODY_MARKER,
    NET_CLOSE_MARKER,
    NET_STATUS_BASE,
    encode_card_addr,
    encode_instruction,
)
from picoscript_vm import (  # noqa: E402
    PicoFault,
    PicoVM,
    PV_FAULT_ALLOC,
    PV_FAULT_BAD_JUMP,
    PV_FAULT_BAD_HOOK,
    PV_FAULT_CALL_OVERFLOW,
    PV_FAULT_RET_UNDERFLOW,
    PV_FAULT_STEP_BUDGET,
    Q16_HALF_PI,
    Q16_ONE,
    Q16_TWO_PI,
    _default_locale_tag,
    _default_timezone_name,
    _q16_exp,
    _q16_log,
    _q16_sincos,
    _q16_tan,
    run_v1,
)


def run_c(src: str, *, with_request_context: bool = False) -> PicoVM:
    vm = PicoVM()
    if with_request_context:
        vm.host.install_request_context(
            vm,
            path="/json",
            headers={"content-type": "application/json"},
            body=[b"{}"],
        )
    vm.run(lower_to_bytecode_safe(compile_c(src)))
    return vm



def set_bytes(base: int, data: bytes) -> str:
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


class _NegativeModuloAngle:
    def __mod__(self, other):
        assert other == Q16_TWO_PI
        return -1000


@pytest.mark.parametrize(
    ("angle", "signs"),
    [
        (Q16_HALF_PI + 1000, (1, -1)),
        (Q16_HALF_PI * 2 + 1000, (-1, -1)),
        (Q16_HALF_PI * 3 + 1000, (-1, 1)),
    ],
)
def test_q16_sincos_reduces_all_remaining_quadrants(angle, signs):
    s, c = _q16_sincos(angle)
    assert s != 0 and c != 0
    assert (1 if s > 0 else -1, 1 if c > 0 else -1) == signs



def test_q16_sincos_handles_negative_modulo_result():
    assert _q16_sincos(_NegativeModuloAngle()) == _q16_sincos(Q16_TWO_PI - 1000)


@pytest.mark.parametrize(
    ("fake_sincos", "expected"),
    [
        (lambda angle: (123, 0), 0x7FFFFFFF),
        (lambda angle: (-123, 0), -0x80000000),
    ],
)
def test_q16_tan_saturates_when_cosine_is_zero(monkeypatch, fake_sincos, expected):
    monkeypatch.setattr("picoscript_vm._q16_sincos", fake_sincos)
    assert _q16_tan(Q16_HALF_PI) == expected



def test_q16_exp_handles_extremes_and_negative_k_branch():
    assert _q16_exp(700000) == 0x7FFFFFFF
    assert _q16_exp(-700000) == 0
    mid = _q16_exp(-50000)
    assert 0 < mid < Q16_ONE



def test_q16_log_handles_non_positive_and_normalization_loops():
    assert _q16_log(0) == -0x80000000
    assert _q16_log(4 * Q16_ONE) > 0
    assert _q16_log(Q16_ONE // 4) < 0



def test_default_timezone_name_falls_back_to_tzname(monkeypatch):
    class FakeTZ:
        key = None

        def tzname(self, _dt):
            return "Fallback/TZ"

    class FakeDateTime:
        @classmethod
        def now(cls):
            return SimpleNamespace(astimezone=lambda: SimpleNamespace(tzinfo=FakeTZ()))

    monkeypatch.setattr(datetime, "datetime", FakeDateTime)
    value = _default_timezone_name()
    assert isinstance(value, str)
    assert value == "Fallback/TZ"



def test_default_timezone_name_falls_back_to_utc(monkeypatch):
    class FakeTZ:
        key = None

        def tzname(self, _dt):
            return None

    class FakeDateTime:
        @classmethod
        def now(cls):
            return SimpleNamespace(astimezone=lambda: SimpleNamespace(tzinfo=FakeTZ()))

    monkeypatch.setattr(datetime, "datetime", FakeDateTime)
    value = _default_timezone_name()
    assert isinstance(value, str)
    assert value == "UTC"



def test_storage_load_save_pipe_and_output_text():
    src = encode_card_addr(0, 0, 1)
    dst = encode_card_addr(0, 0, 2)
    words = [
        encode_instruction(isa.OP_LOAD, rd=1, imm16=src),
        encode_instruction(isa.OP_SAVE, rs1=1, imm16=dst),
        encode_instruction(isa.OP_PIPE, imm16=dst),
        encode_instruction(isa.OP_RETURN),
    ]
    vm = PicoVM()
    vm.cards[src] = 0x48454C4C
    vm.run(words)
    assert vm.regs[1] == 0x48454C4C
    assert vm.cards[dst] == 0x48454C4C
    assert vm.output == [b"HELL"]
    assert vm.output_text() == "HELL"



def test_vm_dispatch_inc_wait_raise_and_div_zero():
    vm_inc = run_v1("Math.Inc(R0);\nFlow.Return();")
    assert vm_inc.regs[0] == 1

    vm_wait = run_v1("Thread.Wait();")
    assert vm_wait.waiting is True
    assert vm_wait.halted is True

    vm_raise = run_v1("Thread.Raise(5);\nFlow.Return();")
    assert any("raise swirq" in line for line in vm_raise.host.log)

    vm_div = run_v1("Math.Div(R0, R1, 0);\nFlow.Return();")
    assert vm_div.regs[0] == 0



def test_vm_dispatch_jump_modes_and_register_arithmetic():
    ret = encode_instruction(isa.OP_RETURN)

    vm_indirect = PicoVM()
    vm_indirect.regs[0] = 1
    vm_indirect.run([
        encode_instruction(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER),
        ret,
    ])
    assert vm_indirect.halted is True

    vm_indexed = PicoVM()
    vm_indexed.regs[0] = 0
    vm_indexed.run([
        encode_instruction(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REG_OFF, imm16=1),
        ret,
    ])
    assert vm_indexed.halted is True

    vm_add = PicoVM()
    vm_add.regs[1] = 10
    vm_add.regs[2] = 5
    vm_add.run([
        encode_instruction(isa.OP_ADD, rd=0, rs1=1, rs2=isa.ADDR_REGISTER, imm16=2),
        ret,
    ])
    assert vm_add.regs[0] == 15



def test_vm_dispatch_bad_jump_and_bad_branch_target_faults():
    vm_jump = PicoVM()
    vm_jump.regs[0] = 9999
    with pytest.raises(PicoFault) as exc_jump:
        vm_jump.run([encode_instruction(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER)])
    assert exc_jump.value.code == PV_FAULT_BAD_JUMP

    vm_branch = PicoVM()
    with pytest.raises(PicoFault) as exc_branch:
        vm_branch.run([
            encode_instruction(isa.OP_BRANCH, rd=0, rs1=0, rs2=isa.BRANCH_Z, imm16=0x7FFF),
        ])
    assert exc_branch.value.code == PV_FAULT_BAD_JUMP



def test_vm_dispatch_call_return_and_bad_call_target():
    vm = run_v1(":main\nFlow.Call(:sub);\nFlow.Return();\n:sub\nFlow.Return();")
    assert vm.halted is True

    with pytest.raises(PicoFault) as exc:
        PicoVM().run([encode_instruction(isa.OP_CALL, imm16=9999)])
    assert exc.value.code == PV_FAULT_BAD_JUMP



def test_vm_dispatch_dsp_subops():
    ret = encode_instruction(isa.OP_RETURN)

    vm_relu = PicoVM()
    vm_relu.regs[1] = (-10) & 0xFFFFFFFF
    vm_relu.run([encode_instruction(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_RELU), ret])
    assert vm_relu.regs[0] == 0

    vm_scale = PicoVM()
    vm_scale.regs[1] = 5
    vm_scale.run([encode_instruction(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_SCALE, imm16=3), ret])
    assert vm_scale.regs[0] == 15

    vm_vadd = PicoVM()
    vm_vadd.regs[1] = 10
    vm_vadd.regs[2] = 20
    vm_vadd.run([encode_instruction(isa.OP_DSP, rd=0, rs1=1, rs2=isa.DSP_VADD, imm16=2), ret])
    assert vm_vadd.regs[0] == 30



def test_vm_step_budget_and_error_handler_recovery():
    words = [
        encode_instruction(isa.OP_INC, rd=0),
        encode_instruction(isa.OP_JUMP, imm16=0),
    ]
    with pytest.raises(PicoFault) as exc:
        PicoVM(max_steps=3).run(words)
    assert exc.value.code == PV_FAULT_STEP_BUDGET

    vm = PicoVM()
    vm.regs[0] = 9999
    vm.host._error_handler_pc = 1
    vm.run([
        encode_instruction(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER),
        encode_instruction(isa.OP_RETURN),
    ])
    assert vm.halted is True
    assert vm.host._error_code == PV_FAULT_BAD_JUMP
    assert vm.host._error_resume_pc == 1



def test_vm_profile_counts_host_hooks_and_net_ops():
    words = Compiler().compile("Random.U32(R0);\nNet.Status(201);\nFlow.Return();")
    vm = PicoVM()
    vm.profile = True
    vm.run(words)
    assert vm.op_hist[isa.OP_NOOP] == 2
    assert vm.host_calls == 1
    assert vm.net_ops == 1
    assert vm.http_status == 201



def test_vm_noop_net_status_type_body_and_close():
    unknown_type = 0xA123
    vm = PicoVM()
    vm.run([
        encode_instruction(isa.OP_NOOP, imm16=NET_STATUS_BASE | 200),
        encode_instruction(isa.OP_NOOP, imm16=unknown_type),
        encode_instruction(isa.OP_NOOP, imm16=NET_BODY_MARKER),
        encode_instruction(isa.OP_RETURN),
    ])
    assert vm.http_status == 200
    assert vm.http_type == "application/octet-stream"

    vm_close = PicoVM().run([encode_instruction(isa.OP_NOOP, imm16=NET_CLOSE_MARKER)])
    assert vm_close.halted is True



def test_vm_verify_cache_skips_second_verification(monkeypatch):
    words = Compiler().compile("Flow.Return();")
    vm = PicoVM().run(words)
    vm.reset_for_request()

    def fail_verify():
        raise AssertionError("_verify should not be called again")

    monkeypatch.setattr(vm, "_verify", fail_verify)
    vm.run()
    assert vm.halted is True



def test_verify_rejects_static_bad_jump_before_execution():
    vm = PicoVM()
    with pytest.raises(PicoFault) as exc:
        vm.run([encode_instruction(isa.OP_JUMP, imm16=9999)])
    assert exc.value.code == PV_FAULT_BAD_JUMP



def test_no_alloc_mode_raises_alloc_fault():
    vm = PicoVM()
    vm.host.no_alloc = True
    with pytest.raises(PicoFault) as exc:
        vm.host._new_span_bytes(vm, b"hello")
    assert exc.value.code == PV_FAULT_ALLOC



def test_span_str_returns_empty_for_falsey_span_entry():
    vm = PicoVM()
    vm.spans.append({})
    assert vm.host._span_str(vm, len(vm.spans) - 1) == ""



def test_json_parse_unescapes_all_escape_forms_via_vm():
    raw = b'{"k":"a\\nb\\tc\\rd\\be\\ff\\u0041g\\\"h\\\\i\\/j"}'
    program = (
        set_bytes(1000, raw)
        + f"int s = Span.Make(1000, {len(raw)});"
        + "int m = Http.ParseJson(s); Io.Write(m);"
    )
    vm = run_c(program, with_request_context=True)
    assert b"".join(vm.output) == b'k=a\nb\tc\rd\x08e\x0cfAg"h\\i/j\n'

