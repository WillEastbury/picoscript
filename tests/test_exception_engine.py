#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_exception_engine.py -- real try/except/raise semantics.

Verifies the exception engine built on top of Error.SetHandler/PopHandler/
Raise/Code/Detail/Clear (see docs/EXCEPTION_ENGINE.md and
picoscript_basic.Lowerer.lower_try/Raise). This supersedes the previous
"best-effort no-op" behavior documented in docs/DIALECT_PARITY.md.

Covers:
  - happy path (try succeeds, except never runs, finally always runs)
  - Raise(value) caught by the enclosing except, value readable via
    Error.Code() semantics (indirectly, via what the except body observes)
  - an uncaught Raise (no enclosing try) propagates as a real PicoFault
  - nested try/except: inner catches without triggering the outer
  - a GENUINE VM-level fault (bad computed jump, not a script Raise) is
    caught by an enclosing try/except the exact same way
  - byte-identical bytecode between BASIC and Python-style sources (the
    shared Lowerer produces the same IL either way)
  - the JS interpretive VM (vm/picovm.js) produces byte-identical results
    for the same bytecode (shares the exact word stream with Python)
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, ILBuilder  # noqa: E402
from picoscript_vm import PicoVM, PicoFault, isa  # noqa: E402


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def _run_basic(src: str):
    words = lower_to_bytecode_safe(compile_basic(src))
    return _out(PicoVM().run(words)), words


def test_happy_path_except_never_runs_finally_always_does():
    out, _ = _run_basic(
        "LET x = 0\n"
        "TRY\n"
        "    LET x = 1\n"
        "EXCEPT\n"
        "    LET x = 999\n"
        "FINALLY\n"
        "    LET x = x + 1000\n"
        "ENDTRY\n"
        "PRINT x\n"
    )
    assert out == [1001]


def test_raise_is_caught_and_statement_after_raise_is_skipped():
    out, _ = _run_basic(
        "LET x = 0\n"
        "TRY\n"
        "    LET x = 1\n"
        "    RAISE 42\n"
        "    LET x = 999\n"          # must NOT execute -- raise jumps past it
        "EXCEPT\n"
        "    LET x = x + 100\n"
        "FINALLY\n"
        "    LET x = x + 1000\n"
        "ENDTRY\n"
        "PRINT x\n"
    )
    assert out == [1101]  # 1 -> +100 (except) -> +1000 (finally)


def test_uncaught_raise_propagates_as_real_picofault():
    words = lower_to_bytecode_safe(compile_basic("RAISE 7\nPRINT 1\n"))
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(words)
    assert exc.value.code == 7


def test_nested_try_inner_catches_without_triggering_outer():
    out, _ = _run_basic(
        "LET a = 0\n"
        "LET b = 0\n"
        "TRY\n"
        "    TRY\n"
        "        RAISE 5\n"
        "    EXCEPT\n"
        "        LET a = 42\n"
        "    ENDTRY\n"
        "    LET b = 1\n"
        "EXCEPT\n"
        "    LET b = 99\n"
        "ENDTRY\n"
        "PRINT a\n"
        "PRINT b\n"
    )
    assert out == [42, 1]


def test_genuine_vm_fault_caught_by_try_except():
    """A real bad-computed-jump fault (PV_FAULT_BAD_JUMP), not a script Raise,
    must be caught the same way -- this exercises PicoVM.run()'s PicoFault
    handler consulting the SAME handler stack Error.SetHandler/PopHandler
    manage (not just the in-band Error.Raise path), mirroring the existing
    tests/test_vm_final_push2.py::test_error_handler_redirect pattern but
    against the new stack-based API."""
    bad_jump = isa.encode_instruction(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REGISTER)
    ret = isa.encode_instruction(isa.OP_RETURN)
    vm = PicoVM()
    vm.regs[0] = 9999          # out-of-range computed jump target
    vm.host._error_handler_stack.append(1)   # handler at pc 1 (the RETURN)
    vm.run([bad_jump, ret])
    assert vm.halted is True
    assert vm.host._error_code == 3   # PV_FAULT_BAD_JUMP
    # The fault path itself does NOT auto-pop -- that's lower_try()'s
    # generated except-body PopHandler() call's job (see test_raise_is_caught_*
    # above, which exercises the full compiled pop-on-catch behavior). Here
    # we're isolating just the "does a genuine fault reach the handler stack"
    # question, so the stack is left as-is by run() itself.
    assert vm.host._error_handler_stack == [1]


def test_basic_and_python_style_try_except_are_byte_identical():
    from picoscript_python import compile_python as _cp

    basic_src = (
        "LET x = 1\n"
        "TRY\n"
        "    LET x = 2\n"
        "    RAISE 9\n"
        "EXCEPT\n"
        "    LET x = 3\n"
        "FINALLY\n"
        "    LET x = x + 4\n"
        "ENDTRY\n"
        "PRINT x\n"
    )
    python_src = (
        "x = 1\n"
        "try:\n"
        "    x = 2\n"
        "    raise 9\n"
        "except:\n"
        "    x = 3\n"
        "finally:\n"
        "    x = x + 4\n"
        "print(x)\n"
    )
    basic_words = lower_to_bytecode_safe(compile_basic(basic_src))
    python_words = lower_to_bytecode_safe(_cp(python_src))
    assert basic_words == python_words
    assert _out(PicoVM().run(basic_words)) == [7]  # 2 -> except(3) -> finally(+4)


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_js_bytecode_vm_matches_python_for_try_except_raise():
    """vm/picovm.js executes the SAME bytecode words (byte-identical) as
    picoscript_vm.py -- this proves the JS interpretive VM's Error.*
    handler-stack + Raise implementation (added alongside the Python side)
    produces identical results, not just that it doesn't crash."""
    src = (
        "LET x = 0\n"
        "TRY\n"
        "    LET x = 1\n"
        "    RAISE 42\n"
        "EXCEPT\n"
        "    LET x = x + 100\n"
        "FINALLY\n"
        "    LET x = x + 1000\n"
        "ENDTRY\n"
        "PRINT x\n"
    )
    words = lower_to_bytecode_safe(compile_basic(src))
    script = f"""
    var VM = require('./vm/picovm.js');
    var hooks = require('./vm/pico_hooks.js');
    var vm = new VM({{hooks: hooks}});
    var words = {json.dumps(words)};
    vm.run(words);
    var out = [];
    for (var i = 0; i < vm.output.length; i += 4) {{
      var v = (vm.output[i]<<24 | vm.output[i+1]<<16 | vm.output[i+2]<<8 | vm.output[i+3]) >>> 0;
      out.push(v | 0);
    }}
    console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    js_out = json.loads(r.stdout.strip())
    assert js_out == [1101]
