#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picoscript_basic as basic_mod  # noqa: E402
from picoscript_basic import (  # noqa: E402
    Bin,
    Call,
    Lowerer,
    Num,
    Parser,
    Raise,
    Str,
    Tok,
    _intlit,
    _strlit,
    compile_basic,
    tokenize,
)
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run_basic(src):
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    return [
        int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
        for c in vm.output
    ]


def test_parser_eat_kw_error():
    with pytest.raises(SyntaxError, match="expected LET"):
        Parser(tokenize("DIM X\n")).eat_kw("LET")


def test_parser_block_unexpected_eof():
    with pytest.raises(SyntaxError, match="unexpected EOF"):
        compile_basic("WHILE 1\nPRINT 1\n")


def test_parse_stmt_ignores_non_assignable_pos():
    parser = Parser(tokenize("PRINT 1\n"))

    class NoPos:
        __slots__ = ()

    parser._parse_stmt = lambda: NoPos()
    assert isinstance(parser.parse_stmt(), NoPos)


def test_parse_stmt_allows_none():
    parser = Parser(tokenize("PRINT 1\n"))
    parser._parse_stmt = lambda: None
    assert parser.parse_stmt() is None


def test_return_without_value():
    il = compile_basic("RETURN\n")
    assert any(getattr(inst, "op", None) == "ret" for inst in il)


def test_unexpected_top_level_keyword():
    with pytest.raises(SyntaxError, match="unexpected keyword END"):
        compile_basic("END\n")


def test_parse_let_new_card():
    il = compile_basic("LET CARD1 NEW CARD\n")
    assert len(il) > 0


def test_enum_unexpected_eof():
    with pytest.raises(SyntaxError, match="expected ENDENUM"):
        compile_basic("ENUM Color\nRED\n")


def test_enum_invalid_member_token():
    with pytest.raises(SyntaxError, match="expected enum member name"):
        compile_basic("ENUM Color\n1\nENDENUM\n")


def test_eat_word_rejects_non_word():
    with pytest.raises(SyntaxError, match="expected a word"):
        Parser(tokenize("1\n"))._eat_word()


def test_expect_word_mismatch():
    with pytest.raises(SyntaxError, match="expected 'PACK', got 'CARD'"):
        compile_basic("STORE USE CARD 1\n")


@pytest.mark.parametrize(
    "src",
    [
        "DIM X = STORE SET 1 = 2\n",
        "DIM X = CARD WRITE 1 = 2\n",
        "DIM X = STREAM CLOSE 1\n",
    ],
)
def test_statement_only_dsl_rejected_as_value(src):
    with pytest.raises(SyntaxError, match="statement, not a value"):
        compile_basic(src)


def test_store_set_pack_branch():
    il = compile_basic("STORE SET PACK 1\n")
    assert len(il) > 0


def test_store_new_card_value():
    il = compile_basic("DIM C = STORE NEW CARD\n")
    assert len(il) > 0


def test_unknown_store_verb():
    with pytest.raises(SyntaxError, match="unknown STORE verb"):
        compile_basic("STORE NOPE 1\n")


def test_load_as_text():
    il = compile_basic("DIM T = LOAD FIELD AS TEXT\n")
    assert len(il) > 0


def test_gpio_write_rejected_as_value():
    with pytest.raises(SyntaxError, match="GPIO WRITE is a statement"):
        compile_basic("DIM X = GPIO WRITE 1 = 2\n")


def test_unknown_gpio_verb():
    with pytest.raises(SyntaxError, match="unknown GPIO verb"):
        compile_basic("GPIO BOGUS 1\n")


def test_gpio_dir_input_and_pull_variants():
    il = compile_basic("GPIO DIR 1 = INPUT\nGPIO PULL 2 = NONE\nGPIO PULL 3 = DOWN\n")
    assert len(il) > 0


def test_gpio_pull_expression_fallback():
    il = compile_basic("GPIO PULL 4 = 7\n")
    assert len(il) > 0


def test_gpio_pull_identifier_fallback():
    il = compile_basic("DIM MODE = 3\nGPIO PULL 4 = MODE\n")
    assert len(il) > 0


@pytest.mark.parametrize(
    "src, pattern",
    [
        ("CARD NOPE 1\n", "unknown CARD verb"),
        ("FIFO NOPE 1\n", "unknown FIFO verb"),
        ("DEVICE NOPE 1\n", "unknown DEVICE verb"),
        ("STREAM NOPE 1\n", "unknown STREAM verb"),
    ],
)
def test_unknown_capsule_verbs(src, pattern):
    with pytest.raises(SyntaxError, match=pattern):
        compile_basic(src)


def test_device_open_with_config():
    il = compile_basic("DIM D = DEVICE OPEN 1 CONFIG 2\n")
    assert len(il) > 0


def test_stream_setslice_and_submit():
    il = compile_basic("STREAM SETSLICE 1,2\nSTREAM SUBMIT 7 = 9\n")
    assert len(il) > 0


def test_stream_and_event_setslice_without_comma():
    il = compile_basic("STREAM SETSLICE 1 2\nEVENT SETSLICE 3 4\n")
    assert len(il) > 0


def test_unknown_capsule_head_direct():
    parser = Parser([Tok("eof", "", 1, 0)])
    with pytest.raises(SyntaxError, match="unknown DSL head"):
        parser._parse_caps_body("NOPE", False)


def test_event_value_forms_and_setslice():
    il = compile_basic(
        "DIM D = EVENT DATA 1\n"
        "DIM L = EVENT DATALEN 1\n"
        "DIM S = EVENT DATASLICE 1\n"
        "EVENT SETSLICE 3,4\n"
    )
    assert len(il) > 0


def test_unknown_event_and_ui_verbs():
    with pytest.raises(SyntaxError, match="unknown EVENT verb"):
        compile_basic("EVENT NOPE 1\n")
    with pytest.raises(SyntaxError, match="unknown UI verb"):
        compile_basic("UI NOPE 1\n")


def test_ui_panel_and_pos_single_value():
    il = compile_basic("DIM P = UI PANEL 1\nUI POS 1 = 5\n")
    assert len(il) > 0


def test_unknown_uievent_head_direct():
    parser = Parser([Tok("eof", "", 1, 0)])
    with pytest.raises(SyntaxError, match="unknown DSL head"):
        parser._parse_uievt_body("NOPE", False)


def test_do_until_and_do_loop_error():
    assert run_basic("DIM X = 0\nDO UNTIL X\nX = 1\nLOOP\nPRINT X\n") == [1]
    with pytest.raises(SyntaxError, match="DO/LOOP needs"):
        compile_basic("DO\nPRINT 1\nLOOP\n")


def test_switch_and_dispatch_parse_errors():
    with pytest.raises(SyntaxError, match="CASE/DEFAULT/ENDSWITCH"):
        compile_basic("SWITCH 1\nPRINT 1\nENDSWITCH\n")
    with pytest.raises(SyntaxError, match="CASE/DEFAULT/ENDDISPATCH"):
        compile_basic("DISPATCH 1\nPRINT 1\nENDDISPATCH\n")


def test_on_block_parses_and_lowers():
    il = compile_basic("ON Net.Close\nPRINT 1\nEND ON\n")
    assert len(il) > 0


def test_unary_minus_and_not():
    assert run_basic("DIM A = -5\nDIM B = NOT 0\nPRINT A\nPRINT B\n") == [-5, 1]


def test_store_value_atom_and_const_addition():
    il = compile_basic("CONST X = 1 + 2\nDIM C = STORE NEW CARD\nPRINT X\n")
    assert len(il) > 0


def test_raise_with_value_lowers():
    # History (see docs/DIALECT_PARITY.md / docs/EXCEPTION_ENGINE.md):
    # 1st gen: Lowerer.stmt(Raise(...)) crashed with AttributeError
    #          ('raise_sw' didn't exist) and separately misused
    #          Error.SetHandler with the raised *value* as if it were a jump
    #          target PC -- a latent bad-jump risk, not just a crash.
    # 2nd gen: fixed to a safe, side-effect-only no-op (VM's real RAISE
    #          opcode / raise_irq) -- no longer crashed, but Raise still
    #          didn't actually throw anything catchable.
    # Now: Raise is a real exception primitive -- it lowers to a genuine
    #      Error.Raise(code) host call, which jumps to the nearest
    #      Error.SetHandler'd handler (registered by an enclosing
    #      lower_try()) or propagates as an uncaught PicoFault if none is
    #      active. See tests/test_exception_engine.py for full behavioral
    #      coverage (happy path, catch, nesting, uncaught propagation, JS
    #      parity); this unit test just pins the IL shape.
    lowerer = Lowerer()
    lowerer.stmt(Raise(Num(7)))
    host_insts = [i for i in lowerer.b.insts if getattr(i, "op", None) == "host"]
    assert len(host_insts) == 1
    assert host_insts[0].ns == "Error" and host_insts[0].method == "Raise"
    assert not any(getattr(inst, "op", None) == "raise" for inst in lowerer.b.insts)


def test_basic_try_except_endtry_parses_and_lowers():
    """BASIC's TRY/EXCEPT/ENDTRY grammar (added alongside the docs/
    DIALECT_PARITY.md audit -- BASIC previously had ON blocks but not
    TryExcept/Raise, unlike Python-style). Mirrors picoscript_python.py's
    try/except/finally 1:1 at the AST level, so it must lower to the exact
    same bytecode as the equivalent Python-style source."""
    from picoscript_python import compile_python

    basic_src = (
        "LET x = 1\n"
        "TRY\n"
        "    LET x = 2\n"
        "EXCEPT\n"
        "    LET x = 3\n"
        "FINALLY\n"
        "    LET x = 4\n"
        "ENDTRY\n"
        "PRINT x\n"
    )
    python_src = (
        "x = 1\n"
        "try:\n"
        "    x = 2\n"
        "except:\n"
        "    x = 3\n"
        "finally:\n"
        "    x = 4\n"
        "print(x)\n"
    )
    basic_words = lower_to_bytecode_safe(compile_basic(basic_src))
    python_words = lower_to_bytecode_safe(compile_python(python_src))
    assert basic_words == python_words

    vm = PicoVM().run(basic_words)
    output = [int.from_bytes(b, "big") for b in vm.output]
    assert output == [4]  # try body runs, then falls through to finally


def test_basic_try_except_no_finally():
    src = "LET x = 1\nTRY\n    LET x = 2\nEXCEPT\n    LET x = 3\nENDTRY\nPRINT x\n"
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    assert [int.from_bytes(b, "big") for b in vm.output] == [2]


def test_lowerer_rejects_unknown_statement():
    with pytest.raises(SyntaxError, match="cannot lower"):
        Lowerer().stmt(object())


def test_unsupported_constant_expression():
    with pytest.raises(SyntaxError, match="unsupported constant expression"):
        Lowerer()._eval_const_expr(Str("x"))
    with pytest.raises(SyntaxError, match="unsupported constant expression"):
        Lowerer()._eval_const_expr(Bin("OR", Num(1), Num(2)))


def test_branch_true_non_comparison_emits_nz():
    lowerer = Lowerer()
    lowerer.branch_true(Num(1), "L1")
    assert any(getattr(inst, "op", None) == "cmpbr" for inst in lowerer.b.insts)


def test_break_and_skip_scope_errors_and_skip_search():
    with pytest.raises(SyntaxError, match="BREAK outside"):
        Lowerer().lower_break()
    lowerer = Lowerer()
    lowerer.scopes = [(None, "outer"), ("cont", "inner")]
    lowerer.lower_skip()
    assert any(getattr(inst, "op", None) == "jmp" for inst in lowerer.b.insts)
    lowerer = Lowerer()
    lowerer.scopes = [(None, "outer")]
    with pytest.raises(SyntaxError, match="SKIP outside"):
        lowerer.lower_skip()


def test_dispatch_case_must_be_non_negative_and_default_only_allowed():
    with pytest.raises(SyntaxError, match="constant non-negative integer"):
        compile_basic("DISPATCH 0\nCASE -1\nPRINT 1\nENDDISPATCH\n")
    il = compile_basic("DISPATCH 1\nDEFAULT\nPRINT 9\nENDDISPATCH\n")
    assert len(il) > 0


def test_eval_rejects_void_call_and_unknown_node():
    lowerer = Lowerer()
    with pytest.raises(SyntaxError, match="does not return a value"):
        lowerer.eval(Call("Net", "Close", []))
    with pytest.raises(SyntaxError, match="cannot evaluate"):
        lowerer.eval(object())


def test_radix_prefix_and_net_header_lowering():
    il = compile_basic("DIM H = hex(255)\nIo.Write(H)\nNet.Header()\n")
    assert len(il) > 0


def test_radix_branch_without_prefix_or_upper(monkeypatch):
    monkeypatch.setitem(basic_mod.BP_RADIX, "rawradix", ("ToHex", None, False))
    reg = Lowerer().lower_call(Call(None, "rawradix", [Num(255)]), want_value=True)
    assert reg is not None


@pytest.mark.parametrize("src", ["Storage.Load(1,2,3,4)\n", "Storage.Save(1,2,3,4)\n", "Storage.Pipe(1,2,3,4)\n"])
def test_storage_dotted_special_cases(src):
    il = compile_basic(src)
    assert any(getattr(inst, "op", None) in {"load", "save", "pipe"} for inst in il)


def test_lowerer_rejects_unknown_net_method():
    with pytest.raises(SyntaxError, match="unknown Net.Nope"):
        Lowerer().lower_call(Call("Net", "Nope", []), want_value=False)


def test_literal_helpers():
    assert _intlit(Num(12)) == 12
    assert _strlit(Str("ok")) == "ok"
    with pytest.raises(SyntaxError, match="expected integer literal"):
        _intlit(Str("x"))
    with pytest.raises(SyntaxError, match="expected string literal"):
        _strlit(Num(1))
