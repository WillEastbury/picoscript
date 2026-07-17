#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_native_js_trycatch.py -- native JS transpile (lower_to_js) real
try/except/finally/raise support.

Part of the structured-`trycatch`-IL redesign (see docs/EXCEPTION_ENGINE.md
and tests/test_native_toc_trycatch.py's docstring for the shared design
background). `lower_to_js` compiles a `trycatch` node into a REAL JS
`try { } catch (e) { } finally { }`, and `Raise` into a real `throw new
PicoRaise(code)` -- since JS has native exceptions, this naturally unwinds
across function calls (no return-code-propagation bookkeeping needed the
way native C requires), so cross-function raise is not a special case here.

Each of try_body/except_body/finally_body gets its own independent,
recursively-built basic-block state machine (JS has no goto, so this is the
mechanism that lets nested control flow -- e.g. a while loop -- work
correctly inside a try body); a `break`/`continue`/jump that crosses a try
boundary into an enclosing scope resolves against that outer scope's own
state machine and escapes via a labeled `continue`.

Requires Node; skips cleanly if unavailable (matching the repo's existing
convention for JS-parity tests).
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_js, lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not available")


def _run_native_js(prog, slot):
    jsrc = lower_to_js(compile_c(prog), module_name=f"pico_{slot}")
    script = jsrc + f"\nvar rt = module.exports.run(); console.log(JSON.stringify(rt.output));\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return bytes(json.loads(r.stdout.strip()))


def _py_out(prog):
    words = lower_to_bytecode_safe(compile_c(prog))
    return b"".join(PicoVM(max_steps=2000).run(list(words)).output)


def check(prog, expected, slot):
    py = _py_out(prog)
    js = _run_native_js(prog, slot)
    assert py == expected, f"[{slot}] Python VM {py!r} != expected {expected!r}"
    assert js == expected, f"[{slot}] native JS {js!r} != expected {expected!r}"


def test_try_catch_finally_raise():
    check(
        "int x = 0;\n"
        "try {\n    x = 1;\n    raise 42;\n    x = 999;\n"
        "} catch {\n    x = x + 100;\n"
        "} finally {\n    x = x + 1000;\n}\n"
        "print(x);\n",
        (1101).to_bytes(4, "big"), "finally_raise",
    )


def test_happy_path_no_exception():
    check(
        "int x = 0;\ntry {\n    x = 1;\n} catch {\n    x = 999;\n}\nprint(x);\n",
        (1).to_bytes(4, "big"), "happy",
    )


def test_nested_try_catch():
    check(
        "int x = 0;\n"
        "try {\n"
        "    try {\n        raise 1;\n"
        "    } catch {\n        x = x + 10;\n        raise 2;\n    }\n"
        "} catch {\n    x = x + 100;\n}\n"
        "print(x);\n",
        (110).to_bytes(4, "big"), "nested",
    )


def test_cross_function_raise_correctly_caught():
    """Unlike native C (which needs a return-code-propagation mechanism) and
    unlike the current interpretive bytecode VMs (which have a real,
    pre-existing call-stack-unwinding bug for this exact case -- see
    tests/test_native_toc_trycatch.py's cross-function test), native JS
    transpile gets this right for free: `throw`/`try/catch` are JS's own
    native exception mechanism, so a Raise inside a called subroutine
    naturally unwinds the real JS call stack to the nearest enclosing
    try/catch, exactly like any other JS exception would."""
    prog = (
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
    js = _run_native_js(prog, "crossfunc")
    assert js == (101).to_bytes(4, "big"), (
        f"native JS cross-function raise: expected 101 (x=1, boom() raises "
        f"55, caught -> x=101, x=999 correctly skipped), got {js!r}"
    )


def test_uncaught_raise_throws_out_of_run():
    prog = "raise 7;\nprint(1);\n"
    jsrc = lower_to_js(compile_c(prog), module_name="pico_uncaught")
    script = jsrc + "\ntry { module.exports.run(); console.log('NO_THROW'); } catch (e) { console.log('THREW ' + (e && e.code)); }\n"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "THREW 7" in r.stdout


def test_loop_inside_try_body_works():
    """A while loop entirely INSIDE a try body -- exercises the nested
    block-machine's own self-contained control flow (no cross-scope jump)."""
    check(
        "int x = 0; int i = 0;\n"
        "try {\n"
        "    while (i < 5) {\n        x = x + i;\n        i = i + 1;\n    }\n"
        "    raise 1;\n"
        "} catch {\n    x = x + 100;\n}\n"
        "print(x);\n",
        (110).to_bytes(4, "big"), "loop_in_try",  # 0+1+2+3+4=10, +100=110
    )


def test_break_from_loop_wrapping_try_crosses_scope_boundary():
    """A `break` inside a try body targeting an ENCLOSING while loop's exit
    -- exercises resolve_jump's cross-scope escape (labeled continue into
    the outer scope's own block machine), the trickiest part of the
    redesign for JS specifically (native C's plain goto handles this for
    free since C labels are function-scoped)."""
    check(
        "int x = 0; int i = 0;\n"
        "while (i < 10) {\n"
        "    try {\n"
        "        x = x + 1;\n"
        "        if (i == 3) { break; }\n"
        "    } catch { }\n"
        "    i = i + 1;\n"
        "}\n"
        "print(x);\n",
        (4).to_bytes(4, "big"), "break_cross_scope",  # i=0,1,2,3 -> x=1,2,3,4 then break
    )
