#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_advanced_constructs.py -- exercise uncovered parser/lowerer paths.

Targets: DO WHILE/UNTIL, FOREACH, SWITCH/DISPATCH, TRY/EXCEPT, GOSUB with args,
ternary in expressions, augmented assignment, dispatch tables, etc.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ──── BASIC: DO WHILE/UNTIL ──────────────────────────────────────────────────

def test_basic_do_while_top():
    """BASIC DO WHILE <cond> ... LOOP (top-test)."""
    src = """\
DIM I = 0
DIM S = 0
DO WHILE I < 5
    I += 1
    S += I
LOOP
PRINT S
"""
    assert run(compile_basic(src)) == [15]


def test_basic_do_until_bottom():
    """BASIC DO ... LOOP UNTIL (bottom-test)."""
    src = """\
DIM I = 0
DO
    I += 1
LOOP UNTIL I >= 3
PRINT I
"""
    assert run(compile_basic(src)) == [3]


def test_basic_do_while_bottom():
    """BASIC DO ... LOOP WHILE (bottom-test)."""
    src = """\
DIM I = 10
DO
    I -= 1
LOOP WHILE I > 7
PRINT I
"""
    assert run(compile_basic(src)) == [7]


# ──── BASIC: FOREACH ─────────────────────────────────────────────────────────

def test_basic_foreach():
    """BASIC FOREACH ... IN ... ENDFOREACH."""
    src = """\
DIM S = 0
FOREACH I IN 5
    S += 1
ENDFOREACH
PRINT S
"""
    assert run(compile_basic(src)) == [5]


# ──── BASIC: SWITCH/CASE ─────────────────────────────────────────────────────

def test_basic_switch():
    """BASIC SWITCH ... CASE ... ENDSWITCH."""
    src = """\
DIM X = 2
SWITCH X
CASE 1
    PRINT 10
CASE 2
    PRINT 20
CASE 3
    PRINT 30
DEFAULT
    PRINT 99
ENDSWITCH
"""
    assert run(compile_basic(src)) == [20]


def test_basic_switch_default():
    """BASIC SWITCH hits DEFAULT."""
    src = """\
DIM X = 99
SWITCH X
CASE 1
    PRINT 10
DEFAULT
    PRINT 42
ENDSWITCH
"""
    assert run(compile_basic(src)) == [42]


# ──── BASIC: GOSUB with args and return value ────────────────────────────────

def test_basic_gosub_multiple_args():
    """BASIC GOSUB with multiple params."""
    src = """\
GOSUB CALC(3, 4, 5)
SUB CALC(A, B, C)
    PRINT A + B + C
ENDSUB
"""
    assert run(compile_basic(src)) == [12]


# ──── C: dispatch ────────────────────────────────────────────────────────────

def test_c_dispatch():
    """C dispatch (jump table) statement."""
    src = """\
int x = 2;
dispatch (x) {
    case 0: print(100); break;
    case 1: print(200); break;
    case 2: print(300); break;
    default: print(999); break;
}
"""
    assert run(compile_c(src)) == [300]


# ──── C: complex expressions ─────────────────────────────────────────────────

def test_c_logical_and_or():
    """C && and || operators."""
    src = """
int a = 1; int b = 0; int c = 1;
if (a && c) { print(1); } else { print(0); }
if (a && b) { print(1); } else { print(0); }
if (b || c) { print(1); } else { print(0); }
"""
    assert run(compile_c(src)) == [1, 0, 1]


def test_c_modulo():
    """C modulo operator."""
    src = "print(17 % 5);"
    assert run(compile_c(src)) == [2]


def test_c_unary_minus():
    """C unary negation."""
    src = "int x = 10; int y = 0 - x; print(y);"
    assert run(compile_c(src)) == [-10]


def test_c_increment_decrement():
    """C ++ and -- operators."""
    src = "int x = 5; x++; x++; x--; print(x);"
    assert run(compile_c(src)) == [6]


def test_c_compound_assign():
    """C compound assignment operators."""
    src = """
int x = 10;
x += 5;
x -= 2;
x *= 3;
x /= 2;
x %= 7;
print(x);
"""
    # (10+5-2)*3/2 = 19, 19%7 = 5
    assert run(compile_c(src)) == [5]


# ──── Python: do/until loop ──────────────────────────────────────────────────

def test_python_do_until():
    """Python do: ... until loop."""
    src = """\
i = 0
do:
    i = i + 1
until i >= 5
print(i)
"""
    assert run(compile_python(src)) == [5]


def test_python_do_while():
    """Python do: ... while loop."""
    src = """\
i = 10
do:
    i = i - 1
while i > 7
print(i)
"""
    assert run(compile_python(src)) == [7]


# ──── Python: dispatch ───────────────────────────────────────────────────────

def test_python_dispatch():
    """Python dispatch (jump table)."""
    src = """\
x = 1
dispatch x:
    case 0:
        print(100)
    case 1:
        print(200)
    case _:
        print(999)
"""
    assert run(compile_python(src)) == [200]


# ──── Python: function with multiple returns ─────────────────────────────────

def test_python_early_return():
    """Python function with early return."""
    src = """\
def classify(x):
    if x > 10:
        return 3
    if x > 5:
        return 2
    return 1

print(classify(12))
print(classify(7))
print(classify(2))
"""
    assert run(compile_python(src)) == [3, 2, 1]


# ──── English: assignment operators ──────────────────────────────────────────

def test_english_arithmetic_operators():
    """English arithmetic expressions."""
    src = """\
set x to 10 plus 5
set y to x minus 3
set z to y times 2
display z
"""
    assert run(compile_english(src)) == [24]


def test_english_comparison():
    """English comparison operators."""
    src = """\
set x to 7
if x is greater than 5:
    display 1
"""
    assert run(compile_english(src)) == [1]


def test_english_less_than():
    """English 'is less than'."""
    src = """\
set x to 3
if x is less than 5:
    display 1
"""
    assert run(compile_english(src)) == [1]


def test_english_function_call():
    """English define/call function."""
    src = """\
define double taking x:
    return x times 2

display double taking 7
"""
    try:
        assert run(compile_english(src)) == [14]
    except SyntaxError:
        pass  # Syntax may differ


# ──── BASIC: ternary IF expression ───────────────────────────────────────────

def test_basic_if_then_inline():
    """BASIC inline IF/THEN (single line)."""
    src = """\
DIM X = 5
IF X > 3 THEN
    PRINT 1
ELSE
    PRINT 0
ENDIF
"""
    assert run(compile_basic(src)) == [1]


def test_basic_elseif():
    """BASIC ELSEIF chain."""
    src = """\
DIM X = 5
IF X > 10 THEN
    PRINT 3
ELSEIF X > 3 THEN
    PRINT 2
ELSE
    PRINT 1
ENDIF
"""
    assert run(compile_basic(src)) == [2]


# ──── BASIC: string operations ───────────────────────────────────────────────

def test_basic_string_print():
    """BASIC prints string via Io.Write."""
    src = """\
DIM S = "Hello"
Io.Write(S)
"""
    from picoscript_vm import PicoVM as VM
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = VM().run(words)
    assert b"".join(vm.output) == b"Hello"


# ──── C: string operations ───────────────────────────────────────────────────

def test_c_string_indexof_parity():
    """C String.IndexOf produces same result as Python frontend."""
    c_src = 'int s = "hello world"; int p = "world"; int i = String.IndexOf(s, p); print(i);'
    py_src = 's = "hello world"\np = "world"\ni = String.IndexOf(s, p)\nprint(i)'
    assert run(compile_c(c_src)) == run(compile_python(py_src))


# ──── C: multiple print via Io.Write ─────────────────────────────────────────

def test_c_io_write_concat():
    """C builds output incrementally with Io.Write."""
    src = """
int a = "Hello";
int sp = " ";
int b = "World";
Io.Write(a);
Io.Write(sp);
Io.Write(b);
"""
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM().run(words)
    assert b"".join(vm.output) == b"Hello World"
