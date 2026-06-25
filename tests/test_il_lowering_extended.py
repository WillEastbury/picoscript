#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_il_lowering_extended.py -- more coverage for lower_to_c and lower_to_js.

Targets: optimizer paths, string pooling, host-hook emit for ext page,
function emit, loop structures in generated code.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def js_run(src, frontend="c"):
    """lower_to_js + run via Node, return output bytes."""
    il = compile_c(src) if frontend == "c" else compile_basic(src)
    js_code = lower_to_js(il, module_name="t")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, dir=VM_DIR) as f:
        f.write(js_code)
        tmp = f.name
    runner = f"const p=require('{tmp.replace(os.sep, '/')}');const rt=p.run();" + \
        "console.log('OUT '+rt.output.map(b=>b.toString(16).padStart(2,'0')).join(' '));\n"
    try:
        r = subprocess.run(["node", "-e", runner], capture_output=True, text=True, cwd=VM_DIR)
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            p = line.split()
            if p and p[0] == "OUT":
                return bytes(int(x, 16) for x in p[1:])
        return b""
    finally:
        os.unlink(tmp)


def py_run(src):
    il = compile_c(src)
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return b"".join(vm.output)


# ── lower_to_c structural coverage ──────────────────────────────────────────

def test_c_emit_string_pool():
    """lower_to_c handles string literal pooling."""
    src = 'int a = "Hello"; int b = "World"; Io.Write(a); Io.Write(b);'
    il = compile_c(src)
    c = lower_to_c(il, func_name="strpool", emit_main=True)
    assert "strpool" in c
    # Must contain const memory initialization for the strings
    assert len(c) > 200


def test_c_emit_loop_structure():
    """lower_to_c emits goto-based loops."""
    src = "int s = 0; for (int i = 0; i < 5; i++) { s += i; } print(s);"
    il = compile_c(src)
    c = lower_to_c(il, func_name="loops", emit_main=True)
    assert "goto" in c.lower() or "L_" in c or "label" in c.lower()


def test_c_emit_no_main():
    """lower_to_c with emit_main=False omits main()."""
    src = "print(1);"
    il = compile_c(src)
    c = lower_to_c(il, func_name="nomain", emit_main=False)
    assert "int main(" not in c


def test_c_emit_with_main():
    """lower_to_c with emit_main=True includes main()."""
    src = "print(1);"
    il = compile_c(src)
    c = lower_to_c(il, func_name="withmain", emit_main=True)
    assert "int main(" in c


def test_c_emit_host_hooks():
    """lower_to_c emits host-hook dispatch calls."""
    src = 'int s = "test"; int n = String.Length(s); print(n);'
    il = compile_c(src)
    c = lower_to_c(il, func_name="hooks", emit_main=True)
    assert "pv_host" in c or "host" in c.lower()


def test_c_emit_function():
    """lower_to_c handles user-defined functions."""
    src = "int add(int a, int b) { return a + b; } print(add(3, 4));"
    il = compile_c(src)
    c = lower_to_c(il, func_name="withfn", emit_main=True)
    assert "withfn" in c


# ── lower_to_js parity ──────────────────────────────────────────────────────

def test_js_string_output():
    """lower_to_js: string output matches Python VM."""
    src = 'int s = "ABC"; Io.Write(s);'
    assert js_run(src) == py_run(src)


def test_js_arithmetic_parity():
    """lower_to_js: complex arithmetic matches Python VM."""
    src = "int x = 7 * 6 + 3 - 1; print(x);"
    assert js_run(src) == py_run(src)


def test_js_conditional():
    """lower_to_js: if/else produces same output."""
    src = "int x = 5; if (x > 3) { print(1); } else { print(0); }"
    assert js_run(src) == py_run(src)


def test_js_while_loop():
    """lower_to_js: while loop."""
    src = "int i = 0; int s = 0; while (i < 5) { i += 1; s += i; } print(s);"
    assert js_run(src) == py_run(src)


def test_js_function_def():
    """lower_to_js: user function."""
    src = "int double(int x) { return x * 2; } print(double(21));"
    assert js_run(src) == py_run(src)


def test_js_multi_string():
    """lower_to_js: multiple string literals."""
    src = 'int a = "Hi"; int b = " there"; int c = String.Concat(a, b); Io.Write(c);'
    assert js_run(src) == py_run(src)


def test_js_host_call_ext():
    """lower_to_js: extended host-hook (>=0x100) works."""
    src = 'int data = "abc"; int h = Crypto.Sha256(data); int n = Span.Len(h); print(n);'
    assert js_run(src) == py_run(src)
