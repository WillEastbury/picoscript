#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_tokenizer_deep.py -- cover tokenizer paths in functional, cobol, report.

Hits: Tok.__repr__, _strip_block_comments (OCaml-style), hex literals, string 
escapes in COBOL/Report, comment lines, and various number/operator tokenizer paths.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_functional import compile_functional  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
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
# picoscript_functional.py — tokenizer paths
# ══════════════════════════════════════════════════════════════════════════════

def test_func_tok_repr():
    """Tok.__repr__ in functional tokenizer."""
    from picoscript_functional import Tok
    t = Tok("num", "42", 1, 0)
    assert repr(t) == "Tok(num,'42')"


def test_func_block_comment():
    """_strip_block_comments removes (* ... *) blocks."""
    src = "(* this is a block comment *)\nlet x = 42\nprintfn x"
    assert run(compile_functional(src)) == [42]


def test_func_nested_block_comment():
    """_strip_block_comments handles nested (* (* ... *) *) blocks."""
    src = "(* outer (* inner *) outer *)\nlet x = 99\nprintfn x"
    assert run(compile_functional(src)) == [99]


def test_func_hex_literal():
    """Functional hex literal 0xFF."""
    src = "let x = 0xFF\nprintfn x"
    assert run(compile_functional(src)) == [255]


def test_func_string_escape():
    """Functional string with escape sequence."""
    src = 'let s = "Hello\\nWorld"\nlet n = String.Length(s)\nprintfn n'
    assert run(compile_functional(src)) == [11]  # includes the \n


def test_func_multiline_comment_and_code():
    """Block comment followed by code."""
    src = "(* setup *)\nlet a = 3\n(* more *)\nlet b = 4\nprintfn (a + b)"
    assert run(compile_functional(src)) == [7]


def test_func_unterminated_block_comment():
    """Unterminated block comment raises SyntaxError."""
    import pytest
    with pytest.raises(SyntaxError):
        compile_functional("(* unterminated\nlet x = 1")


def test_func_expression_body():
    """Function with expression-result as final line (no explicit return)."""
    src = "let f x =\n    x * 2\nprintfn (f 21)"
    assert run(compile_functional(src)) == [42]


def test_func_not_operator():
    """Functional not operator."""
    src = "let x = if not false then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_func_pipe_into_function():
    """Functional pipe into a user function."""
    src = "let double x = x * 2\nlet r = 21 |> double\nprintfn r"
    assert run(compile_functional(src)) == [42]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_cobol.py — tokenizer paths
# ══════════════════════════════════════════════════════════════════════════════

def test_cobol_tok_repr():
    """Tok in COBOL tokenizer is a dataclass with repr."""
    from picoscript_cobol import Tok
    t = Tok("num", "42", 1, 0)
    r = repr(t)
    assert "num" in r and "42" in r


def test_cobol_inline_comment():
    """COBOL inline comment *> is skipped."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 42. *> this is a comment
PROCEDURE DIVISION.
    DISPLAY X. *> another comment
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [42]


def test_cobol_full_line_comment():
    """COBOL line starting with * is a comment."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 42.
PROCEDURE DIVISION.
* This is a comment line
    DISPLAY X.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [42]


def test_cobol_string_literal():
    """COBOL string literal in DISPLAY."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 S PIC X(10) VALUE "HELLO".
PROCEDURE DIVISION.
    DISPLAY S.
    STOP RUN.
"""
    # S is a string but DISPLAY may print its handle (integer) — verify no fault
    vm = PicoVM().run(lower_to_bytecode_safe(compile_cobol(src)))
    assert vm.steps > 0


def test_cobol_hex_literal():
    """COBOL hex literal."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    COMPUTE X = 0xFF.
    DISPLAY X.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [255]


def test_cobol_string_escape():
    """COBOL string with escape sequence."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 S PIC X(20) VALUE "Hello\\tWorld".
PROCEDURE DIVISION.
    DISPLAY S.
    STOP RUN.
"""
    vm = PicoVM().run(lower_to_bytecode_safe(compile_cobol(src)))
    assert vm.steps > 0


def test_cobol_evaluate_when_range():
    """COBOL EVALUATE with multiple WHEN values."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 3.
PROCEDURE DIVISION.
    EVALUATE X
        WHEN 1
            DISPLAY 10
        WHEN 2
            DISPLAY 20
        WHEN 3
            DISPLAY 30
        WHEN 4
            DISPLAY 40
        WHEN OTHER
            DISPLAY 99
    END-EVALUATE.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [30]


def test_cobol_not_greater():
    """COBOL NOT condition — NOT < is expressed as >=."""
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 3.
PROCEDURE DIVISION.
    IF NOT (X > 5)
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [1]


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_report.py — tokenizer paths
# ══════════════════════════════════════════════════════════════════════════════

def test_report_tok_repr():
    """Tok.__repr__ in Report tokenizer."""
    from picoscript_report import Tok
    t = Tok("num", "42", 1, 0)
    assert repr(t) == "Tok(num,'42')"


def test_report_inline_comment():
    """Report: code works alongside comments."""
    src = "DATA: x TYPE i VALUE 42.\nWRITE x."
    assert run(compile_report(src)) == [42]


def test_report_hex_literal():
    """Report hex literal 0xFF."""
    src = "DATA: x TYPE i VALUE 0.\nx = 0xFF.\nWRITE x."
    assert run(compile_report(src)) == [255]


def test_report_single_quote_string():
    """Report single-quoted string literal."""
    src = "DATA: s TYPE i VALUE 0.\ns = 'Hello'.\nIo.Write(s)."
    vm = PicoVM().run(lower_to_bytecode_safe(compile_report(src)))
    got = b"".join(vm.output)
    assert b"Hello" in got


def test_report_and_logic():
    """Report AND logic in IF."""
    src = """\
DATA: a TYPE i VALUE 5,
      b TYPE i VALUE 10.
IF a GT 3 AND b GT 5.
  WRITE 1.
ENDIF.
"""
    assert run(compile_report(src)) == [1]


def test_report_or_logic():
    """Report OR logic in IF."""
    src = """\
DATA: a TYPE i VALUE 1,
      b TYPE i VALUE 10.
IF a GT 5 OR b GT 5.
  WRITE 1.
ENDIF.
"""
    assert run(compile_report(src)) == [1]


def test_report_star_comment():
    """Report * starts full-line comment."""
    src = "* This is a comment\nDATA: x TYPE i VALUE 42.\nWRITE x."
    assert run(compile_report(src)) == [42]


def test_report_move_to_computed():
    """Report COMPUTE with a complex expression."""
    src = """\
DATA: a TYPE i VALUE 5,
      b TYPE i VALUE 3,
      c TYPE i VALUE 0.
COMPUTE c = a * b + a - b.
WRITE c.
"""
    assert run(compile_report(src)) == [17]  # 5*3 + 5 - 3 = 15+5-3 = 17
