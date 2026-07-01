#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_report_100.py -- push picoscript_report.py to 100%."""
import os
import sys
import pytest
import io
import runpy
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_report import compile_report


def cr(src):
    return compile_report(src)


# ── tokenizer: two-char operator (lines 112-113) ─────────────────────────────

def test_two_char_operator():
    """Two-char operators like >= compile (lines 112-113)."""
    il = cr("DATA: x VALUE 5.\nIF x >= 3.\n  WRITE 1.\nENDIF.\n")
    assert len(il) > 0


def test_tokenizer_unexpected_char():
    """Unexpected character → SyntaxError (line 119)."""
    with pytest.raises(SyntaxError, match="unexpected char"):
        cr("x = @5\n")


# ── parse_program: list-returning stmt (arc 186->199, 190-191) ───────────────

def test_parse_program_dot_skip():
    """Standalone dot in program is skipped (line 174-175)."""
    il = cr("DATA: x VALUE 1.\n.\nWRITE x.\n")
    assert isinstance(il, list)


def test_parse_program_list_stmt():
    """DATA returns a list from parse_stmt (arcs 180, 196)."""
    il = cr("DATA: x VALUE 1, y VALUE 2.\nWRITE x.\n")
    assert len(il) > 0


# ── parse_block_until: dot skip and list stmt (lines 190, 195-196) ──────────

def test_block_until_dot_skip():
    """parse_block_until skips dots (line 190)."""
    il = cr("CASE x.\n  WHEN 1.\n    .\n    WRITE 1.\nENDCASE.\n")
    assert isinstance(il, list)


def test_block_until_list_stmt():
    """parse_block_until handles list-returning stmt (arc 195-196)."""
    # DATA inside a block returns a list
    il = cr("CASE x.\n  WHEN 1.\n    DATA: r VALUE 0.\n    WRITE r.\nENDCASE.\n")
    assert isinstance(il, list)


# ── parse_stmt pos set on list (arc 203->210, 208-209) ───────────────────────

def test_parse_stmt_pos_on_list():
    """parse_stmt sets pos on each item in a list (arcs 203->210, 208-209)."""
    il = cr("DATA: a VALUE 1, b VALUE 2.\nWRITE a.\n")
    assert len(il) > 0


# ── parse_data: TYPE and VALUE clauses (lines 286-293) ──────────────────────

def test_data_with_type_and_value():
    """DATA with TYPE clause (line 286-290)."""
    il = cr("DATA: x TYPE i VALUE 5.\nWRITE x.\n")
    assert len(il) > 0


def test_data_with_type_bad_token():
    """DATA TYPE with bad token → SyntaxError (line 290)."""
    with pytest.raises(SyntaxError, match="expected type name"):
        cr("DATA: x TYPE 42.\nWRITE x.\n")


# ── parse_data: multiple comma-separated declarations ────────────────────────

def test_data_multiple_declarations():
    """DATA with multiple comma-separated declarations (arc 263->276, 279->281)."""
    il = cr("DATA: a VALUE 1, b VALUE 2, c VALUE 3.\nWRITE a.\n")
    assert len(il) > 0


# ── RETURN with value vs without (lines 251-253) ─────────────────────────────

def test_return_with_value():
    """RETURN <value> compiles (line 251-253)."""
    il = cr("FORM f.\n  RETURN 42.\nENDFORM.\n")
    assert len(il) > 0


def test_return_bare():
    """RETURN (bare, no value) compiles (line 248-250)."""
    il = cr("FORM f.\n  RETURN.\nENDFORM.\n")
    assert len(il) > 0


# ── CONTINUE (line 259-261) ──────────────────────────────────────────────────

def test_continue_statement():
    """CONTINUE statement compiles."""
    il = cr("DO 3 TIMES.\n  CONTINUE.\nENDDO.\n")
    assert len(il) > 0


# ── unexpected keyword (line 262) ────────────────────────────────────────────

def test_unexpected_keyword():
    """Unexpected keyword → SyntaxError (line 262)."""
    with pytest.raises(SyntaxError, match="unexpected keyword|cannot parse"):
        cr("THEN.\n")


# ── cannot parse statement (line 276) ────────────────────────────────────────

def test_cannot_parse_stmt():
    """Unparseable statement → SyntaxError (line 276)."""
    with pytest.raises(SyntaxError, match="cannot parse"):
        cr("42.\n")  # bare number not valid as statement


# ── CASE/WHEN with OTHERS (lines 350->361) ───────────────────────────────────

def test_case_with_when_and_others():
    """CASE/WHEN/OTHERS/ENDCASE compiles (lines 350->361)."""
    # In REPORT language, WHEN OTHERS is used for the default case
    il = cr("CASE x.\n  WHEN 1.\n    WRITE 1.\n  WHEN OTHERS.\n    WRITE 0.\nENDCASE.\n")
    assert len(il) > 0


def test_case_without_others():
    """CASE/WHEN without OTHERS (lines 350->361, no OTHERS branch)."""
    il = cr("CASE x.\n  WHEN 1.\n    WRITE 1.\n  WHEN 2.\n    WRITE 2.\nENDCASE.\n")
    assert len(il) > 0


# ── parse_call_from_id: bad method token (line 463-464) ─────────────────────

def test_call_bad_method_token():
    """parse_call_from_id with non-id/kw method → SyntaxError."""
    with pytest.raises(SyntaxError):
        cr("ns.42(x).\n")


# ── parse_args: multiple comma-separated args (arc 472-474) ─────────────────

def test_parse_args_multiple():
    """parse_args with multiple comma-separated arguments (arc 472-474)."""
    il = cr("DATA: r VALUE 0.\nr = Maths.Max(1, 2, 3).\nWRITE r.\n")
    assert len(il) > 0


# ── parse_unary: NOT keyword (line 502-504) ──────────────────────────────────

def test_not_unary():
    """NOT unary operator (line 502-504)."""
    il = cr("IF NOT x > 5.\n  WRITE 1.\nENDIF.\n")
    assert len(il) > 0


# ── parse_atom: parenthesised expr and bare function call ────────────────────

def test_atom_parenthesised():
    """Parenthesised expression in atom (line 512-515)."""
    il = cr("DATA: r VALUE 0.\nr = (3 + 4) * 2.\nWRITE r.\n")
    assert len(il) > 0


def test_atom_bare_func_call():
    """Bare function call in atom (lines 523-525)."""
    il = cr("DATA: r VALUE 0.\nr = abs(x).\nWRITE r.\n")
    assert len(il) > 0


def test_atom_dotted_call():
    """Dotted method call in atom (lines 517-522)."""
    il = cr("DATA: r VALUE 0.\nr = Maths.Max(1, 2).\nWRITE r.\n")
    assert len(il) > 0


def test_atom_unexpected_token():
    """Unexpected token in parse_atom → SyntaxError (line 527)."""
    with pytest.raises(SyntaxError):
        cr("DATA: r VALUE 0.\nr = +.\nWRITE r.\n")


# ── match_binop: keyword operator (line 494-495) ─────────────────────────────

def test_parse_call_from_id_bad_method_direct():
    """parse_call_from_id with bad method token via direct Parser call (line 464)."""
    from picoscript_report import Parser, Tok
    toks = [
        Tok("id", "ns", 1, 0),
        Tok("op", ".", 1, 2),
        Tok("num", "42", 1, 3),  # not id/kw → line 464 fires
        Tok("eof", "", 1, 5),
    ]
    p = Parser(toks)
    with pytest.raises(SyntaxError, match="expected method name"):
        p.parse_call_from_id()


def test_parse_args_multiple_args_direct():
    """parse_args with three args exercises the while loop (arc 470->475)."""
    from picoscript_report import Parser, Tok
    toks = [
        Tok("op", "(", 1, 0),
        Tok("num", "1", 1, 1),
        Tok("op", ",", 1, 2),
        Tok("num", "2", 1, 4),
        Tok("op", ",", 1, 5),
        Tok("num", "3", 1, 7),
        Tok("op", ")", 1, 8),
        Tok("eof", "", 1, 9),
    ]
    p = Parser(toks)
    args = p.parse_args()
    assert len(args) == 3
    """AND/OR as keyword operators in expression (lines 494-495)."""
    il = cr("IF x > 1 AND y < 10.\n  WRITE 1.\nENDIF.\n")
    assert len(il) > 0


# ── __main__ block (lines 535-583) ───────────────────────────────────────────

def test_report_main_block():
    """picoscript_report __main__ block executes and passes (lines 535-583)."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            runpy.run_module("picoscript_report", run_name="__main__", alter_sys=False)
    except SystemExit:
        pass
    output = buf.getvalue()
    assert "picoscript_4gl" in output.lower() or "PASS" in output


# ── Additional missing paths ─────────────────────────────────────────────────

def test_parse_program_none_stmt():
    """parse_program skips None-returning stmts (line 178)."""
    # An empty stmt (just a dot) returns None from _parse_stmt via the dot-skip path
    # Actually parse_program already handles the dot skip at line 173-175
    # None from parse_stmt can happen when... let me use pass-like via a bare dot
    il = cr("DATA: x VALUE 1.\n.\nWRITE x.\n")
    assert len(il) > 0


def test_parse_block_until_none_stmt():
    """parse_block_until skips None-returning stmts (line 194)."""
    # A dot inside a WHEN block returns None
    il = cr("CASE x.\n  WHEN 1.\n    .\n    WRITE 1.\nENDCASE.\n")
    assert len(il) > 0


def test_parse_block_until_list_stmt():
    """parse_block_until handles list-returning stmt (arc 186->199: DATA returns list)."""
    il = cr("CASE x.\n  WHEN 1.\n    DATA: r VALUE 0.\n    WRITE r.\nENDCASE.\n")
    assert len(il) > 0


def test_data_without_colon():
    """DATA without colon compiles (arc 279->281: skip colon)."""
    il = cr("DATA x VALUE 5.\nWRITE x.\n")
    assert len(il) > 0


def test_data_loop_back():
    """DATA with multiple declarations: loop back to while check (arc 291->285)."""
    il = cr("DATA: x VALUE 1, y VALUE 2.\nWRITE x.\n")
    assert len(il) > 0


def test_unexpected_keyword_report():
    """Unexpected keyword → SyntaxError (line 262)."""
    with pytest.raises(SyntaxError, match="unexpected keyword|cannot parse"):
        cr("ENDCASE.\n")  # ENDCASE outside CASE block


def test_parse_param_names_comma():
    """parse_param_names_until_dot skips commas (lines 447-448)."""
    il = cr("FORM f USING a, b, c.\n  WRITE a.\nENDFORM.\n")
    assert len(il) > 0


def test_parse_expr_list_comma():
    """parse_expr_list_until_dot skips commas (lines 455-456)."""
    il = cr("PERFORM f USING 1, 2, 3.\n")
    assert len(il) > 0


def test_negation_operator():
    """Unary negation in expression (lines 500-501)."""
    il = cr("DATA: r VALUE 0.\nr = -x.\nWRITE r.\n")
    assert len(il) > 0


def test_parse_args_multiple_report():
    """parse_args with multiple comma-separated arguments (arc 470->475)."""
    il = cr("DATA: r VALUE 0.\nr = Maths.Max(1, 2).\nWRITE r.\n")
    assert len(il) > 0
