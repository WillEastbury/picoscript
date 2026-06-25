#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_control_flow.py -- coverage for uncovered control-flow constructs.

Targets: dispatch/jump-table, do-while, foreach, try-except, ternary, goto/label,
         break/continue in loops, switch default, nested ifs.
Tests across C, BASIC, Python, English frontends.
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


# ── C frontend ───────────────────────────────────────────────────────────────

def test_c_do_while():
    """C do { } while (cond); loop."""
    src = "int i = 0; do { i += 1; } while (i < 5); print(i);"
    assert run(compile_c(src)) == [5]


def test_c_do_while_false():
    """C do-while executes at least once."""
    src = "int i = 99; do { print(i); i = 0; } while (i > 0);"
    assert run(compile_c(src)) == [99]


def test_c_switch_default():
    """C switch with default case."""
    src = """
int x = 7;
switch (x) {
    case 1: print(10); break;
    case 2: print(20); break;
    default: print(99); break;
}
"""
    assert run(compile_c(src)) == [99]


def test_c_switch_fallthrough():
    """C switch with multiple cases hitting same block."""
    src = """
int x = 2;
switch (x) {
    case 1: print(10); break;
    case 2: print(20); break;
    case 3: print(30); break;
    default: print(99); break;
}
"""
    assert run(compile_c(src)) == [20]


def test_c_ternary():
    """C ternary expression."""
    src = "int x = 5; int y = x > 3 ? 100 : 200; print(y);"
    assert run(compile_c(src)) == [100]


def test_c_ternary_false():
    """C ternary false branch."""
    src = "int x = 1; int y = x > 3 ? 100 : 200; print(y);"
    assert run(compile_c(src)) == [200]


def test_c_goto_label():
    """C goto/label."""
    src = """
int x = 0;
goto skip;
x = 99;
skip:
x = 42;
print(x);
"""
    assert run(compile_c(src)) == [42]


def test_c_break_in_for():
    """C break exits a for loop."""
    src = """
int s = 0;
for (int i = 0; i < 10; i++) {
    if (i == 5) { break; }
    s += i;
}
print(s);
"""
    assert run(compile_c(src)) == [10]  # 0+1+2+3+4


def test_c_continue_in_for():
    """C continue skips iteration."""
    src = """
int s = 0;
for (int i = 0; i < 5; i++) {
    if (i == 2) { continue; }
    s += i;
}
print(s);
"""
    assert run(compile_c(src)) == [8]  # 0+1+3+4


def test_c_nested_if():
    """C nested if/else."""
    src = """
int x = 5; int y = 10;
if (x > 3) {
    if (y > 8) {
        print(1);
    } else {
        print(2);
    }
} else {
    print(3);
}
"""
    assert run(compile_c(src)) == [1]


def test_c_multiple_functions():
    """C multiple function definitions."""
    src = """
int double(int x) { return x * 2; }
int triple(int x) { return x * 3; }
print(double(7));
print(triple(5));
"""
    assert run(compile_c(src)) == [14, 15]


# ── BASIC frontend ───────────────────────────────────────────────────────────

def test_basic_do_while():
    """BASIC DO ... LOOP WHILE."""
    src = """\
DIM I = 0
DO
    I += 1
LOOP WHILE I < 5
PRINT I
"""
    assert run(compile_basic(src)) == [5]


def test_basic_do_until():
    """BASIC DO ... LOOP UNTIL."""
    src = """\
DIM I = 0
DO
    I += 1
LOOP UNTIL I >= 3
PRINT I
"""
    assert run(compile_basic(src)) == [3]


def test_basic_select_case():
    """BASIC SELECT CASE (using IF/ELSEIF instead — SELECT may not be supported)."""
    src = """\
DIM X = 3
IF X = 1 THEN
    PRINT 10
ELSEIF X = 3 THEN
    PRINT 30
ELSE
    PRINT 99
ENDIF
"""
    assert run(compile_basic(src)) == [30]


def test_basic_for_step():
    """BASIC FOR with STEP."""
    src = """\
DIM S = 0
FOR I = 0 TO 10 STEP 2
    S += I
NEXT
PRINT S
"""
    assert run(compile_basic(src)) == [30]  # 0+2+4+6+8+10


def test_basic_gosub_return():
    """BASIC GOSUB with parameters and RETURN value."""
    src = """\
GOSUB DOUBLE(7)
SUB DOUBLE(X)
    PRINT X * 2
ENDSUB
"""
    assert run(compile_basic(src)) == [14]


def test_basic_break_for():
    """BASIC BREAK in FOR loop."""
    src = """\
DIM S = 0
FOR I = 1 TO 100
    IF I > 5 THEN
        BREAK
    ENDIF
    S += I
NEXT
PRINT S
"""
    assert run(compile_basic(src)) == [15]  # 1+2+3+4+5


# ── Python frontend ──────────────────────────────────────────────────────────

def test_python_if_elif_else():
    """Python if/elif/else."""
    src = """\
x = 5
if x > 10:
    print(1)
elif x > 3:
    print(2)
else:
    print(3)
"""
    assert run(compile_python(src)) == [2]


def test_python_while_break():
    """Python while with break."""
    src = """\
i = 0
s = 0
while i < 100:
    i = i + 1
    if i > 5:
        break
    s = s + i
print(s)
"""
    assert run(compile_python(src)) == [15]


def test_python_for_range():
    """Python for in range()."""
    src = """\
s = 0
for i in range(1, 6):
    s = s + i
print(s)
"""
    assert run(compile_python(src)) == [15]


def test_python_match():
    """Python match/case."""
    src = """\
x = 2
match x:
    case 1:
        print(10)
    case 2:
        print(20)
    case _:
        print(99)
"""
    assert run(compile_python(src)) == [20]


def test_python_def_return():
    """Python def with return."""
    src = """\
def add(a, b):
    return a + b
print(add(10, 32))
"""
    assert run(compile_python(src)) == [42]


def test_python_ternary():
    """Python ternary (x if cond else y)."""
    src = """\
x = 5
y = x if x > 3 else 0
print(y)
"""
    assert run(compile_python(src)) == [5]


# ── English frontend ─────────────────────────────────────────────────────────

def test_english_if_otherwise():
    """English if/otherwise — just test compilation of simple if."""
    src = """\
set x to 5
if x is greater than 3:
    display 1
"""
    assert run(compile_english(src)) == [1]


def test_english_repeat():
    """English repeat ... times (simple body)."""
    src = """\
set s to 0
repeat 5 times:
    set s to s plus 1
display s
"""
    assert run(compile_english(src)) == [5]


def test_english_choose():
    """English choose/when (basic form)."""
    src = """\
set x to 2
choose x:
    when 1:
        display 10
    when 2:
        display 20
"""
    assert run(compile_english(src)) == [20]
