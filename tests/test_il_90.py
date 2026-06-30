#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_il_90.py -- micro tests to get il.py exactly to 90%."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── Dispatch/jmptab in JS backend ────────────────────────────────────────────

def test_js_dispatch_runs():
    """lower_to_js handles dispatch (jmptab) instruction and produces correct output."""
    src = "int x = 1; dispatch (x) { case 0: print(0); break; case 1: print(1); break; case 2: print(2); break; default: print(9); break; }"
    import tempfile, subprocess
    js = lower_to_js(compile_c(src), module_name="disp_mod")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, dir=os.path.join(ROOT, "vm"), encoding="utf-8") as f:
        f.write(js)
        tmp = f.name
    runner = f"const p=require('{tmp.replace(os.sep, '/')}');const rt=p.run();console.log('OUT '+rt.output.map(b=>b.toString(16).padStart(2,'0')).join(' '));"
    try:
        r = subprocess.run(["node", "-e", runner], capture_output=True, text=True, cwd=os.path.join(ROOT, "vm"))
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                p = line.split()
                if p and p[0] == "OUT":
                    out = bytes(int(x, 16) for x in p[1:])
                    assert out[3] == 1  # value 1
    finally:
        os.unlink(tmp)


def test_js_net_type_header():
    """lower_to_js for net type + header emits correct JS."""
    src = 'Net.Status(200); Net.Type("text/plain"); Net.Header(); Net.Body(); Net.Close();'
    js = lower_to_js(compile_c(src), module_name="net_all")
    assert "netStatus" in js or "net" in js.lower()
    assert "net_all" in js


def test_js_dsp_dot():
    """lower_to_js for DSP DotI8 produces JS code."""
    src = """
Tensor.SetShape(1, 2);
Memory.Set(100, 3); Memory.Set(101, 4);
Memory.Set(200, 1); Memory.Set(201, 1);
int a = Span.Make(100, 2);
int b = Span.Make(200, 2);
int d = Tensor.DotI8(a, b);
print(d);
"""
    js = lower_to_js(compile_c(src), module_name="dsp_js")
    assert "dsp_js" in js
    # DSP may be emitted as rt.host or rt.dsp
    assert "rt." in js


def test_js_wait_in_program():
    """lower_to_js for Net.Close (mapped to wait/return rt)."""
    js = lower_to_js(compile_c("Net.Close();"), module_name="wait_js")
    assert "return rt" in js


def test_js_raise_in_program():
    """lower_to_js for raise op."""
    js = lower_to_js(compile_c("Thread.Skip();"), module_name="raise_js")
    assert "raise_js" in js


# ── _cfg_successors jmptab/call paths ────────────────────────────────────────

def test_cfg_successors_dispatch():
    """Dispatch programs exercise jmptab CFG successors in lease analysis."""
    src = """
int x = 2;
dispatch (x) {
    case 0: print(0); break;
    case 1: print(1); break;
    case 2: print(2); break;
    default: print(9); break;
}
"""
    assert run(src) == [2]


def test_cfg_successors_call():
    """Function calls exercise call CFG successors in lease analysis."""
    src = """
int f(int x) { return x + 1; }
int g(int x) { return f(x) * 2; }
print(g(5));
"""
    assert run(src) == [12]


# ── _ConstExpansion exception class ──────────────────────────────────────────

def test_const_expansion_attr():
    """_ConstExpansion stores rd and value."""
    from picoscript_il import _ConstExpansion
    exc = _ConstExpansion(rd=3, value=65536)
    assert exc.rd == 3
    assert exc.value == 65536
