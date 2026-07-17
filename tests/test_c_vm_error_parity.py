#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_c_vm_error_parity.py -- Error.* (real try/except) parity between
the Python VM (picoscript_vm.py) and the C VM interpreter (vm/picovm.c).

Part of the namespace-equalization pass (see docs/FEATURE_MATRIX.md). Unlike
most of this repo's "5-path parity" namespaces, Error.*/TryExcept is
deliberately checked on only 2 of the 5 paths here:
  - Python VM (bytecode interpreter)
  - C VM (bytecode interpreter, vm/picovm.c + vm/picovm_run.c)
JS VM parity is already covered by tests/test_exception_engine.py and
tests/test_js_port_exception_eventing.py. Native C transpile (lower_to_c) and
native JS transpile (lower_to_js) explicitly REJECT TryExcept/Raise at
compile time (see docs/EXCEPTION_ENGINE.md's scope section) -- that is
correct, intentional behavior, not a gap this test should exercise.

Uses ziglang (a pip-installed C compiler) to build vm/picovm_run.exe, same
as tests/test_native_toc.py -- marked "slow" by conftest.py's ziglang
detection; run with `pytest --runslow`.

Key finding this pass: `laddr` (the IL construct lower_try/Raise use to pass
a handler PC into Error.SetHandler) needs NO new C bytecode opcode -- it
lowers to plain SUB/ADD/MUL words (the same "wide constant load" form used
for any large integer literal; see picoscript_il.py's _emit_const), which
the C interpreter already executes. The only real gap was the Error.* host-
hook dispatch and the handler-stack/pending-jump mechanism in vm/picovm.c's
pv_set_fault + pv_vm_run main loop -- both added this pass.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, PicoFault, isa  # noqa: E402
from picoscript_lang import HOST_HOOK_CODES  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run_error_test.exe")
HOST_HOOK_BASE = 0x6000


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def parse_out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def py_out(words):
    return b"".join(PicoVM(max_steps=20000).run(list(words)).output)


def check(prog, expected):
    words = [w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c(prog))]
    py = py_out(words)
    c = c_out(words)
    assert py == expected, f"Python {py!r} != expected {expected!r}"
    assert c == expected, f"C interpreter {c!r} != expected {expected!r}"


def check_try_catch_finally_raise():
    check(
        "int x = 0;\n"
        "try {\n    x = 1;\n    raise 42;\n    x = 999;\n"
        "} catch {\n    x = x + 100;\n"
        "} finally {\n    x = x + 1000;\n}\n"
        "print(x);\n",
        (1101).to_bytes(4, "big"),
    )


def check_try_catch_happy_path_no_exception():
    check("int x = 0;\ntry {\n    x = 1;\n} catch {\n    x = 999;\n}\nprint(x);\n",
          (1).to_bytes(4, "big"))


def check_nested_try_catch():
    check(
        "int x = 0;\n"
        "try {\n"
        "    try {\n        raise 1;\n"
        "    } catch {\n        x = x + 10;\n        raise 2;\n    }\n"
        "} catch {\n    x = x + 100;\n}\n"
        "print(x);\n",
        (110).to_bytes(4, "big"),
    )


def check_uncaught_raise_reports_fault_identically():
    words = [w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c("raise 7;\nprint(1);\n"))]
    try:
        PicoVM(max_steps=20000).run(list(words))
        raise AssertionError("expected PicoFault, none raised")
    except PicoFault as exc:
        assert exc.code == 7
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    assert "FAULT 7" in out, out


def check_genuine_vm_fault_caught_by_handler_stack():
    """A real bad-computed-jump fault (PV_FAULT_BAD_JUMP=3), not a script
    Raise, must be caught the same way on the C VM -- exercises
    pv_set_fault's handler-stack check (not just the in-band Error.Raise
    host-hook path), mirroring
    tests/test_exception_engine.py::test_genuine_vm_fault_caught_by_try_except."""
    setcode = HOST_HOOK_CODES[("Error", "SetHandler")]
    codecode = HOST_HOOK_CODES[("Error", "Code")]
    E = isa.encode_instruction
    words = [
        E(isa.OP_SUB, rd=1, rs1=1, rs2=isa.ADDR_REGISTER, imm16=1),          # reg1 = 0
        E(isa.OP_ADD, rd=1, rs1=1, imm16=6),                                  # reg1 = 6 (handler=pc6)
        E(isa.OP_NOOP, rd=0, rs1=1, rs2=0, imm16=HOST_HOOK_BASE | setcode),   # SetHandler(6)
        E(isa.OP_SUB, rd=0, rs1=0, rs2=isa.ADDR_REGISTER, imm16=0),           # reg0 = 0
        E(isa.OP_ADD, rd=0, rs1=0, imm16=9999),                               # reg0 = 9999 (bad target)
        E(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER),                         # bad jump -> caught, redirect
        E(isa.OP_NOOP, rd=2, rs1=0, rs2=0, imm16=HOST_HOOK_BASE | codecode),  # reg2 = Error.Code()
        E(isa.OP_SAVE, rs1=2, imm16=0xFFFE),
        E(isa.OP_PIPE, imm16=0xFFFE),
        E(isa.OP_RETURN),
    ]
    words = [w & 0xFFFFFFFF for w in words]
    py = py_out(words)
    c = c_out(words)
    assert py == (3).to_bytes(4, "big"), py   # PV_FAULT_BAD_JUMP
    assert c == py


def check_cross_function_raise_unwinds_call_stack():
    """Regression test: Error.Raise inside a called subroutine, caught by a
    try in the CALLER, must unwind the call stack back to its depth at
    Error.SetHandler time -- otherwise the stale return address left by the
    subroutine call gets popped by a later, unrelated RETURN and resumes
    execution mid-try-body, re-running code that should have been skipped
    (discovered while adding native C transpile trycatch support; see
    docs/EXCEPTION_ENGINE.md). Fixed via a parallel call-stack-depth array
    recorded at SetHandler time in all 3 bytecode VMs (picoscript_vm.py's
    _error_handler_call_depth, vm/picovm.js's _errState.callDepth,
    vm/picovm.c's ctx->err_call_depth)."""
    check(
        "int x = 0;\n"
        "void boom() {\n"
        "    raise 55;\n"
        "}\n"
        "try {\n"
        "    x = 1;\n"
        "    boom();\n"
        "    x = 999;\n"     # must NOT execute -- would if call stack weren't unwound
        "} catch {\n"
        "    x = x + 100;\n"
        "}\n"
        "print(x);\n",
        (101).to_bytes(4, "big"),
    )


def main():
    build_c_vm()
    try:
        check_try_catch_finally_raise()
        check_try_catch_happy_path_no_exception()
        check_nested_try_catch()
        check_uncaught_raise_reports_fault_identically()
        check_genuine_vm_fault_caught_by_handler_stack()
        check_cross_function_raise_unwinds_call_stack()
        print("PASS: Error.*/try-except byte-identical, Python VM == C VM interpreter")
    finally:
        if os.path.exists(VM_EXE):
            os.remove(VM_EXE)


def test_main():
    main()


if __name__ == "__main__":
    main()
