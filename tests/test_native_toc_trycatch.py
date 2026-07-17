#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_native_toc_trycatch.py -- native C transpile (lower_to_c) real
try/except/finally/raise support.

Part of the structured-`trycatch`-IL redesign (see docs/EXCEPTION_ENGINE.md):
`Lowerer.lower_try` (picoscript_basic.py/picoscript_cfront.py) now builds a
STRUCTURED `trycatch` IL node (nested try_body/except_body/finally_body
instruction lists) instead of flattening into laddr/Error.SetHandler/label/
jmp at Lowerer time. `lower_to_bytecode_safe` still expands that into the
classic flat form (_flatten_trycatch in picoscript_il.py) for the bytecode
VMs -- byte-identical to before. But lower_to_c/lower_to_js now consume the
structured node directly, which is what makes real native exception support
possible without pattern-matching flat jump/label soup back into structure.

`lower_to_c` compiles trycatch into plain `goto`/labels (the handler's C
label is known at COMPILE TIME from the structured node -- no PC-addressable
value needed at all) for a Raise lexically inside the same C function, and a
`ctx->raise_active` return-code-propagation flag (checked after every
subroutine call site) for a Raise from inside a called subroutine -- this
correctly unwinds the native C call stack one frame at a time, which is
actually MORE correct than the interpretive bytecode VMs for this specific
case (see the note in this file's cross-function test below).

Uses ziglang (a pip-installed C compiler), same as tests/test_native_toc.py
and tests/test_c_vm_error_parity.py -- marked "slow" by conftest.py's
ziglang detection; run with `pytest --runslow`.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_c  # noqa: E402
from picoscript_vm import PicoVM, PicoFault  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD = os.path.join(ROOT, ".test_build_toc_trycatch")


def _compile_and_run(prog, slot):
    csrc = lower_to_c(compile_c(prog), func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "OUT":
            return bytes(int(x, 16) for x in parts[1:])
    return b""


def _py_out(prog):
    words = lower_to_bytecode_safe(compile_c(prog))
    return b"".join(PicoVM(max_steps=2000).run(list(words)).output)


def check(prog, expected, slot):
    py = _py_out(prog)
    c = _compile_and_run(prog, slot)
    assert py == expected, f"[{slot}] Python VM {py!r} != expected {expected!r}"
    assert c == expected, f"[{slot}] native C {c!r} != expected {expected!r}"


def main():
    if os.path.exists(BUILD):
        import shutil
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    try:
        check(
            "int x = 0;\n"
            "try {\n    x = 1;\n    raise 42;\n    x = 999;\n"
            "} catch {\n    x = x + 100;\n"
            "} finally {\n    x = x + 1000;\n}\n"
            "print(x);\n",
            (1101).to_bytes(4, "big"), "finally_raise",
        )
        check(
            "int x = 0;\ntry {\n    x = 1;\n} catch {\n    x = 999;\n}\nprint(x);\n",
            (1).to_bytes(4, "big"), "happy_path",
        )
        check(
            "int x = 0;\n"
            "try {\n"
            "    try {\n        raise 1;\n"
            "    } catch {\n        x = x + 10;\n        raise 2;\n    }\n"
            "} catch {\n    x = x + 100;\n}\n"
            "print(x);\n",
            (110).to_bytes(4, "big"), "nested",
        )

        # Uncaught raise: native C should NOT execute anything after it
        # (mirrors the Python VM's PicoFault propagating out of run()).
        prog = "raise 7;\nprint(1);\n"
        words = lower_to_bytecode_safe(compile_c(prog))
        try:
            PicoVM(max_steps=2000).run(list(words))
            raise AssertionError("expected PicoFault, none raised")
        except PicoFault as exc:
            assert exc.code == 7
        c_out = _compile_and_run(prog, "uncaught")
        assert c_out == b"", f"native C should not have printed anything, got {c_out!r}"

        # Cross-function raise: Raise from inside a called subroutine, caught
        # by a try in the CALLER. Note: this exposed a real, PRE-EXISTING bug
        # in the interpretive bytecode VMs (Python/JS/C) -- Error.Raise jumps
        # straight to the handler PC without unwinding vm.call_stack, so a
        # stale return address (pointing just past the subroutine call, i.e.
        # into the middle of the try body) gets popped by a LATER, unrelated
        # RETURN and resumes execution there, re-running code that should
        # have been skipped. Native C's return-code-propagation approach
        # does NOT have this bug (it unwinds via real C function returns),
        # so it is intentionally MORE correct here than the current bytecode
        # VMs -- this test asserts the CORRECT (native C) behavior and
        # documents the bytecode-VM bug via the `xfail`-style assertion
        # below rather than silently asserting the buggy behavior as
        # "expected". See the follow-up fix tracked for the bytecode VMs.
        prog2 = (
            "int x = 0;\n"
            "void boom() {\n"
            "    raise 55;\n"
            "}\n"
            "try {\n"
            "    x = 1;\n"
            "    boom();\n"
            "    x = 999;\n"
            "} catch {\n"
            "    x = x + 100;\n"
            "}\n"
            "print(x);\n"
        )
        c_out2 = _compile_and_run(prog2, "crossfunc")
        assert c_out2 == (101).to_bytes(4, "big"), (
            f"native C cross-function raise: expected 101 (x=1, boom() raises "
            f"55, caught -> x=101, x=999 correctly skipped), got {c_out2!r}"
        )
        py_out2 = _py_out(prog2)
        assert py_out2 != (101).to_bytes(4, "big"), (
            "if this now equals 101, the bytecode VM call-stack-unwinding bug "
            "has been fixed -- update this test to assert equality instead "
            "of documenting the discrepancy"
        )

        print("PASS: native C transpile try/except/finally/raise, "
              "byte-identical to Python VM for all cases except the "
              "known, pre-existing cross-function bytecode-VM bug")
    finally:
        import shutil
        shutil.rmtree(BUILD, ignore_errors=True)


def test_main():
    main()


if __name__ == "__main__":
    main()
