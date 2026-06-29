#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_il_optimizer.py -- coverage for optimizer and edge-case paths in picoscript_il.py.

Targets: optimizer (opt=True vs opt=False), constant folding, dead code,
emit edge cases (large programs, deep nesting).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js, il_to_text  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_opt(src, opt=True):
    il = compile_c(src)
    words = lower_to_bytecode_safe(il, opt=opt)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── Optimizer on/off produces same semantics ─────────────────────────────────

def test_opt_vs_noopt_loop():
    """Optimizer produces same output as unoptimized."""
    src = "int s = 0; for (int i = 1; i <= 10; i++) { s += i; } print(s);"
    assert run_opt(src, opt=True) == run_opt(src, opt=False) == [55]


def test_opt_vs_noopt_conditionals():
    """Optimizer preserves conditional semantics."""
    src = "int x = 7; if (x > 5) { print(1); } else { print(0); }"
    assert run_opt(src, opt=True) == run_opt(src, opt=False) == [1]


def test_opt_vs_noopt_string():
    """Optimizer preserves string operations."""
    src = 'int s = "AB"; int n = String.Length(s); print(n);'
    assert run_opt(src, opt=True) == run_opt(src, opt=False) == [2]


def test_opt_fewer_words():
    """Optimizer produces fewer or equal bytecode words."""
    src = "int a = 1; int b = 2; int c = a + b; print(c);"
    il = compile_c(src)
    opt_words = lower_to_bytecode_safe(il, opt=True)
    noopt_words = lower_to_bytecode_safe(il, opt=False)
    assert len(opt_words) <= len(noopt_words)


# ── il_to_text ───────────────────────────────────────────────────────────────

def test_il_to_text_basic():
    """il_to_text produces readable IL representation."""
    il = compile_c("print(42);")
    text = il_to_text(il)
    assert len(text) > 0
    assert "const" in text or "mov" in text or "ret" in text


def test_il_to_text_loop():
    """il_to_text for a loop program shows labels and jumps."""
    il = compile_c("int s = 0; for (int i = 0; i < 5; i++) { s += i; } print(s);")
    text = il_to_text(il)
    assert "jmp" in text or "cmpbr" in text or "for" in text


# ── lower_to_c edge cases ───────────────────────────────────────────────────

def test_lower_to_c_empty_program():
    """lower_to_c handles minimal program."""
    il = compile_c("print(0);")
    c = lower_to_c(il, func_name="min", emit_main=True)
    assert "min" in c and "main" in c


def test_lower_to_c_multiple_strings():
    """lower_to_c with many string literals."""
    src = 'int a = "one"; int b = "two"; int c = "three"; Io.Write(a); Io.Write(b); Io.Write(c);'
    c = lower_to_c(compile_c(src), func_name="multi_str", emit_main=True)
    assert "multi_str" in c


def test_lower_to_c_deep_nesting():
    """lower_to_c handles deeply nested control flow."""
    src = """
int x = 5;
if (x > 1) {
    if (x > 2) {
        if (x > 3) {
            if (x > 4) {
                print(x);
            }
        }
    }
}
"""
    c = lower_to_c(compile_c(src), func_name="deep", emit_main=True)
    assert "deep" in c


# ── lower_to_js edge cases ───────────────────────────────────────────────────

def test_lower_to_js_empty():
    """lower_to_js produces valid module for trivial program."""
    js = lower_to_js(compile_c("print(0);"), module_name="trivial")
    assert "trivial" in js
    assert "module.exports" in js or "exports" in js


def test_lower_to_js_multiple_functions():
    """lower_to_js handles user-defined functions."""
    src = """
int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }
print(add(mul(3, 4), 5));
"""
    js = lower_to_js(compile_c(src), module_name="fns")
    assert "fns" in js


def test_lower_to_js_long_program():
    """lower_to_js handles a long program."""
    # Generate a program with many prints
    lines = [f"print({i});" for i in range(20)]
    src = "\n".join(lines)
    js = lower_to_js(compile_c(src), module_name="long")
    assert "long" in js
    assert len(js) > 500


# ── Bytecode size varies with optimization ───────────────────────────────────

def test_opt_removes_dead_assignments():
    """Optimizer may remove unused variable assignments."""
    src = """
int unused = 99;
int used = 42;
print(used);
"""
    il = compile_c(src)
    opt_words = lower_to_bytecode_safe(il, opt=True)
    noopt_words = lower_to_bytecode_safe(il, opt=False)
    # Both should produce correct output
    vm1 = PicoVM().run(opt_words)
    vm2 = PicoVM().run(noopt_words)
    out1 = [int.from_bytes(c, "big") for c in vm1.output]
    out2 = [int.from_bytes(c, "big") for c in vm2.output]
    assert out1 == out2 == [42]
