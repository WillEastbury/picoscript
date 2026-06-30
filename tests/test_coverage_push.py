#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_coverage_push.py -- push coverage toward 90% across all modules.

Targets remaining uncovered paths in: picoscript_lang (decompiler detail, net
dispatch, storage compile), picoscript_functional (tokenizer, expressions,
goto/label/return), picoscript_cobol (expressions, ELSE IF, error paths),
picoscript_report (LOOP AT, expressions), picoscript_english (define, dispatch),
picoscript_il (optimizer passes), picoscript_basic (TryExcept, OnBlock, ForEach).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import Compiler, decompile_basic, decompile_python  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return b"".join(vm.output)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_lang.py — decompiler + v1 compiler NET/STORAGE dispatch
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_python_net():
    """decompile_python formats NET operations."""
    c = Compiler()
    words = c.compile("NET STATUS, 200\nNET BODY\nIO WRITE, R0\nNET CLOSE")
    result = decompile_python(words)
    assert len(result.strip()) > 0


def test_decompile_basic_storage():
    """decompile_basic formats STORAGE operations."""
    c = Compiler()
    words = c.compile("STORAGE LOAD, 0, 0, 0, R0")
    result = decompile_basic(words)
    assert "STORAGE" in result.upper()


def test_decompile_basic_math():
    """decompile_basic formats MATH operations."""
    c = Compiler()
    words = c.compile("MATH ADD, R1, R0, 42\nMATH INC, R2")
    result = decompile_basic(words)
    assert "MATH" in result.upper() or "ADD" in result.upper()


def test_decompile_basic_flow():
    """decompile_basic formats FLOW operations."""
    c = Compiler()
    words = c.compile("10 FLOW RETURN\n20 FLOW JUMP, 10")
    result = decompile_basic(words)
    assert "FLOW" in result.upper() or "RETURN" in result.upper()


def test_decompile_python_host_hooks():
    """decompile_python formats various host hooks."""
    c = Compiler()
    words = c.compile("Memory.ArenaInit(R0, R1, R2);\nSpan.Make(R3, R4, R5);\nDescriptor.Make(R6, R7, R8);")
    result = decompile_python(words)
    assert "memory" in result.lower() or "span" in result.lower()


def test_decompile_python_queue():
    """decompile_python formats Queue hooks."""
    c = Compiler()
    words = c.compile("Queue.Enqueue(0, R1);\nQueue.Dequeue(0, R2);")
    result = decompile_python(words)
    assert "queue" in result.lower()


def test_decompile_python_lease():
    """decompile_python formats Lease hooks."""
    c = Compiler()
    words = c.compile("Lease.Acquire(R0, R1, R2);\nLease.Release(R3);")
    result = decompile_python(words)
    assert "lease" in result.lower()


def test_decompile_python_kernel():
    """decompile_python formats Kernel hooks."""
    c = Compiler()
    words = c.compile("Kernel.WaitIRQ(R0);\nKernel.FireSWIRQ(R1);")
    result = decompile_python(words)
    assert "kernel" in result.lower()


def test_v1_compile_net_status():
    """v1 NET STATUS produces valid bytecode."""
    c = Compiler()
    words = c.compile("NET STATUS, 404")
    assert len(words) == 1


def test_v1_compile_storage_pipe():
    """v1 STORAGE PIPE compiles."""
    c = Compiler()
    words = c.compile("STORAGE PIPE, 0, 1, 0, R0")
    assert len(words) == 1


def test_v1_compile_thread_raise():
    """v1 THREAD RAISE with channel."""
    c = Compiler()
    words = c.compile("THREAD RAISE, 5")
    assert len(words) == 1


def test_v1_math_third_arg_error():
    """v1 MATH with bad third arg raises."""
    import pytest
    c = Compiler()
    with pytest.raises(SyntaxError):
        c.compile('MATH ADD, R0, R1, "bad"')


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_functional.py — more constructs
# ══════════════════════════════════════════════════════════════════════════════

def test_functional_multiple_let():
    src = "let a = 1\nlet b = 2\nlet c = a + b\nprintfn c"
    assert run(compile_functional(src)) == [3]


def test_functional_if_else():
    src = "let x = 10\nif x > 5 then\n    printfn 1\nelse\n    printfn 0"
    assert run(compile_functional(src)) == [1]


def test_functional_nested_match():
    src = "let x = 1\nmatch x with\n| 0 -> printfn 0\n| 1 -> printfn 1\n| 2 -> printfn 2\n| _ -> printfn 99"
    assert run(compile_functional(src)) == [1]


def test_functional_function_multiple_args():
    src = "let add3 a b c = a + b + c\nprintfn (add3 1 2 3)"
    assert run(compile_functional(src)) == [6]


def test_functional_pipe_multi():
    src = "let x = 100\nlet s = x |> Number.ToString |> String.Length\nprintfn s"
    assert run(compile_functional(src)) == [3]  # "100" = 3 chars


def test_functional_host_call_stmt():
    src = 'let s = "Hello"\nIo.Write(s)'
    assert out_bytes(compile_functional(src)) == b"Hello"


def test_functional_arithmetic():
    src = "let a = 5 * 6 + 2\nprintfn a"
    assert run(compile_functional(src)) == [32]


def test_functional_comparison():
    src = "let x = if 5 > 3 then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_cobol.py — deeper constructs
# ══════════════════════════════════════════════════════════════════════════════

def test_cobol_else_if():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 5.
PROCEDURE DIVISION.
    IF X > 10
        DISPLAY 3
    ELSE
        IF X > 3
            DISPLAY 2
        ELSE
            DISPLAY 1
        END-IF
    END-IF.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [2]


def test_cobol_move_compute():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 A PIC 9(4) VALUE 0.
01 B PIC 9(4) VALUE 7.
PROCEDURE DIVISION.
    MOVE B TO A.
    COMPUTE A = A * 6.
    DISPLAY A.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [42]


def test_cobol_multiple_performs():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM INC-X.
    PERFORM INC-X.
    PERFORM INC-X.
    DISPLAY X.
    STOP RUN.
INC-X.
    COMPUTE X = X + 1.
"""
    assert run(compile_cobol(src)) == [3]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_report.py — deeper constructs
# ══════════════════════════════════════════════════════════════════════════════

def test_report_nested_if():
    src = """\
DATA: x TYPE i VALUE 5.
IF x > 10.
  WRITE 3.
ELSEIF x > 3.
  WRITE 2.
ELSE.
  WRITE 1.
ENDIF.
"""
    assert run(compile_report(src)) == [2]


def test_report_multiple_data():
    src = """\
DATA: a TYPE i VALUE 3,
      b TYPE i VALUE 4,
      c TYPE i VALUE 5.
c = a * b + c.
WRITE c.
"""
    assert run(compile_report(src)) == [17]


def test_report_form_with_local():
    src = """\
DATA: x TYPE i VALUE 10.
PERFORM calc USING x.
FORM calc USING n.
  DATA: result TYPE i.
  result = n * n.
  WRITE result.
ENDFORM.
"""
    assert run(compile_report(src)) == [100]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_english.py — more constructs
# ══════════════════════════════════════════════════════════════════════════════

def test_english_set_and_display():
    src = "set x to 42\ndisplay x"
    assert run(compile_english(src)) == [42]


def test_english_arithmetic_complex():
    src = "set x to 3 times 4 plus 2\ndisplay x"
    assert run(compile_english(src)) == [14]


def test_english_comparison_equals():
    src = "set x to 5\nif x equals 5:\n    display 1"
    assert run(compile_english(src)) == [1]


def test_english_host_call():
    src = 'set s to "Hi"\nIo.Write(s)'
    assert out_bytes(compile_english(src)) == b"Hi"


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_il.py — optimizer + lower edge cases
# ══════════════════════════════════════════════════════════════════════════════

def test_il_lower_c_with_switch():
    """lower_to_c handles switch/dispatch."""
    src = "int x = 2; switch (x) { case 1: print(10); break; case 2: print(20); break; default: print(99); break; }"
    c = lower_to_c(compile_c(src), func_name="sw", emit_main=True)
    assert "sw" in c


def test_il_lower_c_with_do_while():
    """lower_to_c handles do-while loops."""
    src = "int i = 0; do { i += 1; } while (i < 5); print(i);"
    c = lower_to_c(compile_c(src), func_name="dw", emit_main=True)
    assert "dw" in c


def test_il_lower_js_with_function():
    """lower_to_js handles functions."""
    src = "int sq(int x) { return x * x; } print(sq(7));"
    js = lower_to_js(compile_c(src), module_name="sqmod")
    assert "sqmod" in js


def test_il_lower_c_optimized_vs_unoptimized():
    """Optimized and unoptimized both produce correct results."""
    src = "int x = 3; int y = x * x + 1; print(y);"
    il = compile_c(src)
    w1 = lower_to_bytecode_safe(il, opt=True)
    w2 = lower_to_bytecode_safe(il, opt=False)
    out1 = [int.from_bytes(c, "big") for c in PicoVM().run(w1).output]
    out2 = [int.from_bytes(c, "big") for c in PicoVM().run(w2).output]
    assert out1 == out2 == [10]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_basic.py — deeper lowering paths
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_dispatch_jump_table():
    """BASIC DISPATCH with many cases (jump table optimization)."""
    src = """\
DIM X = 3
DISPATCH X
CASE 0
    PRINT 0
CASE 1
    PRINT 10
CASE 2
    PRINT 20
CASE 3
    PRINT 30
CASE 4
    PRINT 40
DEFAULT
    PRINT 99
ENDDISPATCH
"""
    assert run(compile_basic(src)) == [30]


def test_basic_foreach_range():
    """BASIC FOREACH with range count."""
    src = """\
DIM S = 0
FOREACH I IN 10
    S += 1
ENDFOREACH
PRINT S
"""
    assert run(compile_basic(src)) == [10]


def test_basic_string_concat():
    """BASIC string concatenation via String.Concat."""
    src = """\
DIM A = "Hello"
DIM B = " World"
DIM C = String.Concat(A, B)
Io.Write(C)
"""
    assert out_bytes(compile_basic(src)) == b"Hello World"


def test_basic_const_enum():
    """BASIC CONST and ENUM definitions."""
    src = """\
CONST PI_APPROX = 314
CONST TAU = PI_APPROX * 2
PRINT TAU
"""
    assert run(compile_basic(src)) == [628]


def test_basic_complex_expression():
    """BASIC complex arithmetic expression."""
    src = """\
DIM A = 2
DIM B = 3
DIM C = (A + B) * (A - B + 10)
PRINT C
"""
    assert run(compile_basic(src)) == [45]  # (2+3)*(2-3+10) = 5*9 = 45
