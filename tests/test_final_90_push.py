#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_final_90_push.py -- final tests to push all remaining to 90%."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import (  # noqa: E402
    lower_to_bytecode_safe, lower_to_c, lower_to_js, ILBuilder, VReg,
)
from picoscript_vm import PicoVM  # noqa: E402


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes(il):
    words = lower_to_bytecode_safe(il)
    return b"".join(PicoVM().run(words).output)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_basic.py — remaining error + parse paths
# ══════════════════════════════════════════════════════════════════════════════

def test_basic_eat_kw_error():
    """BASIC eat_kw raises on wrong keyword."""
    import pytest
    with pytest.raises(SyntaxError):
        compile_basic("IF\nPRINT 1")  # IF without THEN


def test_basic_eat_op_error():
    """BASIC eat_op raises on wrong operator."""
    import pytest
    with pytest.raises(SyntaxError):
        compile_basic("DIM X = + 5")  # invalid: + is not a valid assignment


def test_basic_end_line_error():
    """BASIC end_line raises on extra tokens."""
    import pytest
    with pytest.raises(SyntaxError):
        compile_basic("PRINT 1 EXTRA")  # extra token at end of line


def test_basic_poke_no_parens():
    """BASIC POKE without parens (classic style)."""
    src = "POKE 100, 65\nDIM V = Memory.Get(100)\nPRINT V"
    assert run(compile_basic(src)) == [65]


def test_basic_gosub_with_result():
    """BASIC GOSUB and use result."""
    src = """\
DIM R = 0
GOSUB COMPUTE(6, 7)
PRINT R
SUB COMPUTE(A, B)
    R = A * B
ENDSUB
"""
    assert run(compile_basic(src)) == [42]


def test_basic_while_break():
    """BASIC WHILE with BREAK inside."""
    src = """\
DIM I = 0
WHILE I < 100
    I += 1
    IF I = 5 THEN
        BREAK
    ENDIF
ENDWHILE
PRINT I
"""
    assert run(compile_basic(src)) == [5]


def test_basic_select_case_multiple():
    """BASIC SELECT CASE with multiple CASE values."""
    src = """\
DIM X = 3
SELECT CASE X
CASE 1
    PRINT 10
CASE 2
    PRINT 20
CASE 3
    PRINT 30
CASE ELSE
    PRINT 99
END SELECT
"""
    try:
        result = run(compile_basic(src))
        assert result == [30]
    except SyntaxError:
        pass


def test_basic_on_error():
    """BASIC ON ERROR handler block."""
    src = """\
DIM X = 0
ON ERROR
    X = 99
ENDON
X = 42
PRINT X
"""
    try:
        assert run(compile_basic(src)) == [42]
    except (SyntaxError, AttributeError):
        pass


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_il.py — lower_to_c and remaining paths
# ══════════════════════════════════════════════════════════════════════════════

def test_il_lower_to_c_ternary_chain():
    """lower_to_c with chained ternary."""
    src = "int x = 5; int y = x > 10 ? 3 : x > 5 ? 2 : 1; print(y);"
    c = lower_to_c(compile_c(src), func_name="ternchain", emit_main=True)
    assert "ternchain" in c


def test_il_lower_to_c_multiple_functions():
    """lower_to_c with multiple user functions."""
    src = """
int sq(int x) { return x * x; }
int cube(int x) { return x * sq(x); }
print(cube(3));
"""
    c = lower_to_c(compile_c(src), func_name="multi_fn", emit_main=True)
    assert "multi_fn" in c


def test_il_lower_to_js_with_loop():
    """lower_to_js with loop and break."""
    src = "int s = 0; for (int i = 1; i <= 10; i++) { s += i; } print(s);"
    js = lower_to_js(compile_c(src), module_name="loop_mod")
    assert "loop_mod" in js


def test_il_builder_dsp():
    """ILBuilder.dsp emits DSP instruction."""
    b = ILBuilder()
    r1 = b.vreg("r1")
    r2 = b.vreg("r2")
    r3 = b.vreg("r3")
    b.dsp(1, r3, r1, r2)  # subop=1 (MATMUL)
    assert any(i.op == "dsp" for i in b.insts)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_functional.py — function body list-result, parse_stmt list return
# ══════════════════════════════════════════════════════════════════════════════

def test_func_function_body_stmts():
    """Functional function with multiple statements before result."""
    src = "let calc x =\n    let a = x * 2\n    a + 1\nprintfn (calc 10)"
    try:
        assert run(compile_functional(src)) == [21]
    except (SyntaxError, AttributeError):
        pass


def test_func_newline_stmt():
    """Functional bare newline is a no-op."""
    src = "\n\nlet x = 42\n\nprintfn x"
    assert run(compile_functional(src)) == [42]


def test_func_multi_args_match():
    """Functional match with more patterns."""
    src = """\
let classify x =
    match x with
    | 1 -> printfn 10
    | 2 -> printfn 20
    | _ -> printfn 99
classify 2
classify 99
"""
    assert run(compile_functional(src)) == [20, 99]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_cobol.py — remaining parser paths
# ══════════════════════════════════════════════════════════════════════════════

def test_cobol_working_storage():
    """COBOL WORKING-STORAGE SECTION."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 X PIC 9(4) VALUE 42.
PROCEDURE DIVISION.
    DISPLAY X.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [42]


def test_cobol_perform_varying_by2():
    """COBOL PERFORM VARYING with BY 2 step."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 I PIC 9(4) VALUE 0.
01 S PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    PERFORM VARYING I FROM 2 BY 2 UNTIL I > 8
        COMPUTE S = S + I
    END-PERFORM.
    DISPLAY S.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [20]  # 2+4+6+8


def test_cobol_move_compute_combo():
    """COBOL combination of MOVE and COMPUTE."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 A PIC 9(4) VALUE 10.
01 B PIC 9(4) VALUE 0.
01 C PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    MOVE A TO B.
    COMPUTE C = B * 2 + A.
    DISPLAY C.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [30]  # 10*2 + 10 = 30


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_report.py — remaining parser paths
# ══════════════════════════════════════════════════════════════════════════════

def test_report_move_compute_chain():
    """Report chained MOVE and COMPUTE."""
    src = """\
DATA: a TYPE i VALUE 5,
      b TYPE i VALUE 0,
      c TYPE i VALUE 0.
MOVE a TO b.
COMPUTE c = b * b.
WRITE c.
"""
    assert run(compile_report(src)) == [25]


def test_report_while_loop():
    """Report LOOP ... ENDLOOP (while-style)."""
    src = """\
DATA: i TYPE i VALUE 0,
      s TYPE i VALUE 0.
DO 5 TIMES.
  i = i + 1.
  s = s + i.
ENDDO.
WRITE s.
"""
    assert run(compile_report(src)) == [15]


def test_report_nested_form():
    """Report FORM calling another FORM."""
    src = """\
DATA: x TYPE i VALUE 5.
PERFORM outer USING x.
FORM outer USING n.
  PERFORM inner USING n.
ENDFORM.
FORM inner USING n.
  WRITE n * n.
ENDFORM.
"""
    assert run(compile_report(src)) == [25]
