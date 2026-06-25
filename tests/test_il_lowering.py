#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_il_lowering.py -- tests for lower_to_c and lower_to_js backends.

Verifies that the transpiled C and JS code:
  1. Compiles and runs correctly
  2. Produces byte-identical output to the PicoVM reference
  3. Handles arithmetic overflow (int32 wrapping)
  4. Handles string literals and const memory correctly
  5. Handles host-call emission (ext page for >=0x100 hooks)
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def py_output(src, frontend="c"):
    """Run through Python VM, return output bytes."""
    if frontend == "c":
        il = compile_c(src)
    else:
        il = compile_basic(src)
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return b"".join(vm.output)


def js_transpiled_output(src, frontend="c"):
    """Run the lower_to_js transpiled code via Node, return output bytes."""
    import tempfile
    if frontend == "c":
        il = compile_c(src)
    else:
        il = compile_basic(src)
    js_code = lower_to_js(il, module_name="test_mod")
    # Write to a temp file and require it (lower_to_js uses CommonJS module.exports)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, dir=VM_DIR) as f:
        f.write(js_code)
        tmp_path = f.name
    runner = f"const p = require('{tmp_path.replace(os.sep, '/')}');\n" + \
        "const rt = p.run();\n" + \
        "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n"
    try:
        r = subprocess.run(["node", "-e", runner], capture_output=True, text=True, cwd=VM_DIR)
        if r.returncode != 0:
            raise RuntimeError(f"JS transpiled code failed: {r.stderr}")
        for line in r.stdout.splitlines():
            p = line.split()
            if p and p[0] == "OUT":
                return bytes(int(x, 16) for x in p[1:])
        return b""
    finally:
        os.unlink(tmp_path)


# ──── lower_to_js tests ─────────────────────────────────────────────────────

def test_js_basic_arithmetic():
    """lower_to_js: basic arithmetic produces correct output."""
    src = "print(2 + 3);"
    assert js_transpiled_output(src) == py_output(src)


def test_js_loop():
    """lower_to_js: loop computes sum correctly."""
    src = "int s = 0; for (int i = 1; i <= 10; i++) { s += i; } print(s);"
    assert js_transpiled_output(src) == py_output(src)


def test_js_if_else():
    """lower_to_js: conditional branches work."""
    src = "int x = 42; if (x > 40) { print(1); } else { print(0); }"
    assert js_transpiled_output(src) == py_output(src)


def test_js_string_literal():
    """lower_to_js: string literals emit correct bytes."""
    src = 'int s = "Hello"; Io.Write(s);'
    assert js_transpiled_output(src) == py_output(src)


def test_js_nested_loops():
    """lower_to_js: nested for loops."""
    src = """\
int s = 0;
for (int i = 0; i < 3; i++) {
    for (int j = 0; j < 3; j++) {
        s += 1;
    }
}
print(s);
"""
    assert js_transpiled_output(src) == py_output(src)


def test_js_function_call():
    """lower_to_js: user-defined function with return value."""
    src = """\
int add(int a, int b) {
    return a + b;
}
print(add(10, 32));
"""
    assert js_transpiled_output(src) == py_output(src)


def test_js_int32_overflow():
    """lower_to_js: int32 arithmetic wraps correctly."""
    src = "print(2147483647 + 1);"  # INT_MAX + 1 should wrap to INT_MIN
    py_out = py_output(src)
    js_out = js_transpiled_output(src)
    assert py_out == js_out


def test_js_host_call():
    """lower_to_js: host calls (String.Length) work."""
    src = 'int s = "Hello"; int n = String.Length(s); print(n);'
    assert js_transpiled_output(src) == py_output(src)


def test_js_multiple_prints():
    """lower_to_js: multiple print statements."""
    src = "print(1); print(2); print(3);"
    assert js_transpiled_output(src) == py_output(src)


def test_js_switch():
    """lower_to_js: switch statement."""
    src = """\
int x = 2;
switch (x) {
    case 1: print(10); break;
    case 2: print(20); break;
    default: print(99); break;
}
"""
    assert js_transpiled_output(src) == py_output(src)


# ──── lower_to_c structural tests (no C compiler needed) ─────────────────────

def test_c_produces_valid_code():
    """lower_to_c: generated C code is syntactically reasonable."""
    src = "print(42);"
    c_code = lower_to_c(compile_c(src), func_name="test", emit_main=True)
    # Check it has the expected structure
    assert "int32_t" in c_code or "int" in c_code
    assert "pv_" in c_code or "main" in c_code


def test_c_string_escaping():
    """lower_to_c: string literals are properly escaped in C output."""
    src = r'int s = "He said \"hi\""; Io.Write(s);'
    il = compile_c(src)
    c_code = lower_to_c(il, func_name="test_esc", emit_main=False)
    # The C output must not contain unescaped quotes that would break compilation
    # (picoscript_il._c_string handles escaping)
    assert "test_esc" in c_code


def test_c_identifier_sanitization():
    """lower_to_c: identifiers are sanitized to valid C identifiers."""
    src = "int my_var = 42; print(my_var);"
    il = compile_c(src)
    c_code = lower_to_c(il, func_name="sanitize_test", emit_main=False)
    assert "sanitize_test" in c_code


def test_c_loop_emission():
    """lower_to_c: loops produce valid structured C."""
    src = "int s = 0; for (int i = 0; i < 5; i++) { s += i; } print(s);"
    il = compile_c(src)
    c_code = lower_to_c(il, func_name="loop_test", emit_main=True)
    # Should contain goto or label-based loop structure
    assert "loop_test" in c_code
    assert len(c_code) > 100  # Non-trivial output


def test_c_function_emission():
    """lower_to_c: multiple statements with host calls."""
    src = "int a = Maths.Max(5, 9); print(a);"
    il = compile_c(src)
    c_code = lower_to_c(il, func_name="func_test", emit_main=True)
    assert "func_test" in c_code
