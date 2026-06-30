#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted coverage push for picoscript_cfront.py."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import (  # noqa: E402
    Bin,
    Break,
    Call,
    Continue,
    Dispatch,
    DoWhile,
    ExprStmt,
    FieldRef,
    IncDec,
    Lowerer,
    Num,
    Parser,
    Str,
    Tok,
    Unary,
    Var,
    _intlit,
    _strlit,
    compile_c,
    tokenize,
)


class _NoneStmtParser(Parser):
    def _parse_stmt(self):
        return None


class _AttrlessStmtParser(Parser):
    def _parse_stmt(self):
        return object()


class _LineTok(Tok):
    pass


def _with_lines(tokens, line=1):
    for tok in tokens:
        tok.line = line
    return tokens


def test_tokenize_rejects_unexpected_char():
    with pytest.raises(SyntaxError, match="unexpected char"):
        tokenize("@")


def test_parse_stmt_handles_none_attrless_and_unknown_keyword_nodes():
    toks = [Tok("id", "x", 123), Tok("eof", "", 124)]
    assert _NoneStmtParser(toks).parse_stmt() is None
    node = _AttrlessStmtParser(toks).parse_stmt()
    assert node is not None
    assert not hasattr(node, "pos")
    with pytest.raises(SyntaxError, match="unexpected token"):
        Parser(_with_lines([Tok("kw", "mystery", 0), Tok("eof", "", 1)])).parse_stmt()


def test_bare_block_and_server_main_lower_cleanly():
    il = compile_c("{ int x; } Server.Main { int y; }")
    assert any(ins.op == "const" and ins.dst.name == "x" and ins.imm == 0 for ins in il)
    assert any(ins.op == "const" and ins.dst.name == "y" and ins.imm == 0 for ins in il)


def test_exprstmt_none_break_continue_and_unknown_statement_errors():
    lowerer = Lowerer()
    lowerer.stmt(ExprStmt(None))
    assert lowerer.b.insts == []
    with pytest.raises(SyntaxError, match="break outside loop"):
        Lowerer().stmt(Break())
    with pytest.raises(SyntaxError, match="continue outside loop"):
        Lowerer().stmt(Continue())
    with pytest.raises(SyntaxError, match="cannot lower statement"):
        Lowerer().stmt(object())


def test_parse_for_variants_cover_empty_and_compound_steps():
    src = """
int i;
for (;;) { break; }
for (var j; ; j = 1) { break; }
for (int k = 0; ; k += 1) { break; }
"""
    il = compile_c(src)
    assert any(ins.op == "const" and ins.dst.name == "j" and ins.imm == 0 for ins in il)
    assert any(ins.op == "add" and ins.dst.name == "k" for ins in il)


def test_switch_dispatch_and_do_while_variants_compile():
    il = compile_c(
        """
int x = 0;
switch (x) { case 0: }
switch (x) { case 0: default: x = 1; break; }
dispatch (x) { case 0: x = 1; break; default: x = 2; break; }
dispatch (x) { case 0: x = 1; break; }
do { int y; } while (0);
"""
    )
    assert any(ins.op == "jmptab" for ins in il)
    assert any(ins.op == "label" and str(ins.label).startswith("enddisp") for ins in il)


def test_parser_reports_invalid_switch_dispatch_and_do_tokens():
    switch_toks = _with_lines([
        Tok("kw", "switch", 0), Tok("op", "(", 1), Tok("num", "0", 2), Tok("op", ")", 3),
        Tok("op", "{", 4), Tok("id", "oops", 5), Tok("op", "}", 6), Tok("eof", "", 7),
    ])
    dispatch_toks = _with_lines([
        Tok("kw", "dispatch", 0), Tok("op", "(", 1), Tok("num", "0", 2), Tok("op", ")", 3),
        Tok("op", "{", 4), Tok("id", "oops", 5), Tok("op", "}", 6), Tok("eof", "", 7),
    ])
    do_toks = _with_lines(tokenize("do { } x;"))
    with pytest.raises(SyntaxError, match="expected case/default in switch"):
        Parser(switch_toks).parse_program()
    with pytest.raises(SyntaxError, match="expected case/default in dispatch"):
        Parser(dispatch_toks).parse_program()
    with pytest.raises(SyntaxError, match="expected 'while' after do block"):
        Parser(do_toks).parse_program()


def test_until_dowhile_branch_is_lowered_directly():
    lowerer = Lowerer()
    lowerer.lower_dowhile(DoWhile(Num(0), True, []))
    assert any(ins.op == "cmpbr" for ins in lowerer.b.insts)


def test_prefix_incdec_not_and_invalid_target_paths():
    il = compile_c("int x = 1; int y = !0; ++x; --x;")
    assert any(ins.op == "inc" and ins.dst.name == "x" for ins in il)
    assert any(ins.op == "sub" and ins.dst.name == "x" for ins in il)
    with pytest.raises(SyntaxError, match=r"\+\+/-- requires a variable"):
        Lowerer().eval_incdec(IncDec("++", Num(1), True))
    with pytest.raises(SyntaxError, match="cannot evaluate"):
        Lowerer().eval(Unary("~", Num(1)))


def test_local_calls_aliases_print_string_net_header_storage_and_net_errors():
    il = compile_c(
        """
void foo() { return; }
foo();
print("hi");
int n = strlen("hi");
Net.Header();
int r = 1;
Storage.Load(0, 1, 2, r);
Storage.Save(0, 1, 2, r);
Storage.Pipe(0, 1, 2, r);
Order ord = Storage.GetCard(1, r);
int ok = Storage.SaveCard(ord);
"""
    )
    assert any(ins.op == "call" and ins.label == "fn_foo" for ins in il)
    assert any(ins.op == "host" and ins.ns == "Io" and ins.method == "Write" for ins in il)
    assert any(ins.op == "host" and ins.ns == "String" and ins.method == "Length" for ins in il)
    assert any(ins.op == "net" and ins.method == "header" for ins in il)
    assert any(ins.op == "load" for ins in il)
    assert any(ins.op == "save" for ins in il)
    assert any(ins.op == "pipe" for ins in il)
    assert any(ins.op == "mov" and ins.dst.name == "ok" for ins in il)
    with pytest.raises(SyntaxError, match="unknown Net.Bogus"):
        Lowerer().lower_call(Call("Net", "Bogus", []), want_value=False)


def test_field_assignment_string_and_compound_paths():
    il = compile_c(
        """
int card = 1;
Order ord = Storage.GetCard(1, card);
ord.name = "bob";
ord.qty += 2;
"""
    )
    assert any(ins.op == "host" and ins.ns == "Storage" and ins.method == "SetFieldStr" for ins in il)
    assert any(ins.op == "host" and ins.ns == "Storage" and ins.method == "SetField" for ins in il)


def test_constant_expression_helpers_cover_success_and_failure_cases():
    lowerer = Lowerer()
    lowerer.user_constants.update({"A": 5, "E.X": 7})
    assert lowerer._eval_const_expr(Var("A")) == 5
    assert lowerer._eval_const_expr(FieldRef("E", "X")) == 7
    assert lowerer._eval_const_expr(Unary("-", Num(3))) == -3
    assert lowerer._eval_const_expr(Bin("+", Num(2), Num(3))) == 5
    assert lowerer._eval_const_expr(Bin("-", Num(5), Num(2))) == 3
    assert lowerer._eval_const_expr(Bin("*", Num(4), Num(3))) == 12
    with pytest.raises(SyntaxError, match="unknown constant 'MISSING'"):
        lowerer._eval_const_expr(Var("MISSING"))
    with pytest.raises(SyntaxError, match="unknown constant E\\.'MISSING'"):
        lowerer._eval_const_expr(FieldRef("E", "MISSING"))
    with pytest.raises(SyntaxError, match="unsupported unary op '!' in constant expression"):
        lowerer._eval_const_expr(Unary("!", Num(1)))
    with pytest.raises(SyntaxError, match="unsupported constant expression Bin"):
        lowerer._eval_const_expr(Bin("^", Num(1), Num(2)))
    with pytest.raises(SyntaxError, match="unsupported constant expression"):
        lowerer._eval_const_expr(Str("x"))


def test_compile_errors_cover_unterminated_enum_invalid_dispatch_and_unknown_eval():
    with pytest.raises(SyntaxError, match="unterminated enum declaration"):
        compile_c("enum E { A")
    with pytest.raises(SyntaxError, match="dispatch case must be a constant non-negative integer"):
        compile_c("int x = 0; dispatch (x) { case -1: x = 1; break; default: x = 2; break; }")
    with pytest.raises(SyntaxError, match="cannot evaluate"):
        Lowerer().eval(object())


def test_literal_helpers_accept_expected_types_and_reject_others():
    assert _intlit(Num(3)) == 3
    with pytest.raises(SyntaxError, match="expected integer literal"):
        _intlit(Str("x"))
    with pytest.raises(SyntaxError, match="expected string literal"):
        _strlit(Num(1))


def test_continue_statement_parses_before_lowerer_rejects_it():
    with pytest.raises(SyntaxError, match="continue outside loop"):
        compile_c("continue;")
