#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_lang_functions.py -- coverage for picoscript_lang.py utility functions.

Targets: resolve_named_constant, describe_named_constant, toLocale, 
encode_card_addr/decode_card_addr, disassemble, _canonical_named_constant_key.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import (  # noqa: E402
    resolve_named_constant,
    describe_named_constant,
    to_locale,
    toLocale,
    encode_card_addr,
    decode_card_addr,
    disassemble,
    NAMED_CONSTANTS,
    _canonical_named_constant_key,
)


# ── resolve_named_constant ───────────────────────────────────────────────────

def test_resolve_http_status():
    """resolve_named_constant returns numeric value for HTTP_STATUS_OK."""
    assert resolve_named_constant("HTTP_STATUS_OK") == 200


def test_resolve_http_method():
    """resolve_named_constant returns value for METHOD_GET."""
    val = resolve_named_constant("HTTP_METHOD_GET")
    assert val is not None and isinstance(val, int)


def test_resolve_case_insensitive():
    """resolve_named_constant is case-insensitive."""
    v1 = resolve_named_constant("HTTP_STATUS_OK")
    v2 = resolve_named_constant("http_status_ok")
    assert v1 == v2


def test_resolve_nonexistent():
    """resolve_named_constant returns None for unknown constants."""
    assert resolve_named_constant("NONEXISTENT_FOOBAR") is None


def test_resolve_empty():
    """resolve_named_constant returns None for empty string."""
    assert resolve_named_constant("") is None


def test_resolve_day_constant():
    """resolve_named_constant handles DAY_ prefix."""
    val = resolve_named_constant("DAY_MONDAY")
    assert val is not None


def test_resolve_month_constant():
    """resolve_named_constant handles MONTH_ prefix."""
    val = resolve_named_constant("MONTH_JANUARY")
    assert val is not None


def test_resolve_tz_constant():
    """resolve_named_constant handles TZ_ prefix."""
    val = resolve_named_constant("TZ_UTC")
    assert val is not None


def test_resolve_currency():
    """resolve_named_constant handles CURRENCY_ prefix."""
    val = resolve_named_constant("CURRENCY_USD")
    assert val is not None


def test_resolve_country():
    """resolve_named_constant handles COUNTRY_ prefix."""
    val = resolve_named_constant("COUNTRY_US")
    assert val is not None


# ── _canonical_named_constant_key ────────────────────────────────────────────

def test_canonical_method_prefix():
    """METHOD_ prefix gets HTTP_ prepended."""
    assert _canonical_named_constant_key("METHOD_GET") == "HTTP_METHOD_GET"


def test_canonical_status_prefix():
    """STATUS_ prefix gets HTTP_ prepended."""
    assert _canonical_named_constant_key("STATUS_OK") == "HTTP_STATUS_OK"


def test_canonical_dotted_httpmethod():
    """HttpMethod.GET becomes HTTP_METHOD_GET."""
    assert _canonical_named_constant_key("HTTPMETHOD.GET") == "HTTP_METHOD_GET"


def test_canonical_dotted_httpstatus():
    """HttpStatus.OK becomes HTTP_STATUS_OK."""
    assert _canonical_named_constant_key("HTTPSTATUS.OK") == "HTTP_STATUS_OK"


def test_canonical_day_dot():
    """Day.MONDAY becomes DAY_MONDAY."""
    assert _canonical_named_constant_key("DAY.MONDAY") == "DAY_MONDAY"


def test_canonical_month_dot():
    """Month.JANUARY becomes MONTH_JANUARY."""
    assert _canonical_named_constant_key("MONTH.JANUARY") == "MONTH_JANUARY"


def test_canonical_tz_dot():
    """TZ.UTC becomes TZ_UTC."""
    assert _canonical_named_constant_key("TZ.UTC") == "TZ_UTC"


def test_canonical_timezone_dot():
    """Timezone.UTC becomes TZ_UTC."""
    assert _canonical_named_constant_key("TIMEZONE.UTC") == "TZ_UTC"


def test_canonical_currency_dot():
    """Currency.USD becomes CURRENCY_USD."""
    assert _canonical_named_constant_key("CURRENCY.USD") == "CURRENCY_USD"


def test_canonical_country_dot():
    """Country.US becomes COUNTRY_US."""
    assert _canonical_named_constant_key("COUNTRY.US") == "COUNTRY_US"


def test_canonical_color_dot():
    """Color.RED becomes COLOR_RED."""
    assert _canonical_named_constant_key("COLOR.RED") == "COLOR_RED"


def test_canonical_empty():
    """Empty string returns None."""
    assert _canonical_named_constant_key("") is None


# ── describe_named_constant ──────────────────────────────────────────────────

def test_describe_returns_dict():
    """describe_named_constant returns a dict with required fields."""
    result = describe_named_constant("HTTP_STATUS_OK")
    assert result is not None
    assert "name" in result
    assert "value" in result
    assert "label" in result
    assert "description" in result
    assert "locale" in result
    assert result["value"] == 200


def test_describe_none_for_unknown():
    """describe_named_constant returns None for unknown constants."""
    assert describe_named_constant("NONEXISTENT") is None


def test_describe_none_for_empty():
    """describe_named_constant returns None for empty string."""
    assert describe_named_constant("") is None


def test_describe_with_locale():
    """describe_named_constant respects locale parameter."""
    result = describe_named_constant("HTTP_STATUS_OK", locale="fr")
    assert result is not None
    assert result["locale"] == "fr"


def test_describe_with_user_dictionary():
    """describe_named_constant uses user_dictionary overrides."""
    user = {"HTTP_STATUS_OK": {"label": "Custom OK"}}
    result = describe_named_constant("HTTP_STATUS_OK", user_dictionary=user)
    assert result["label"] == "Custom OK"


def test_describe_currency():
    """describe_named_constant handles currency metadata."""
    result = describe_named_constant("CURRENCY_USD")
    assert result is not None
    assert "USD" in result["label"]


def test_describe_tz():
    """describe_named_constant handles timezone metadata."""
    result = describe_named_constant("TZ_UTC")
    assert result is not None


# ── to_locale / toLocale ─────────────────────────────────────────────────────

def test_to_locale_basic():
    """to_locale returns a formatted string."""
    result = to_locale("HTTP_STATUS_OK")
    assert result is not None
    assert "200" in result
    assert "OK" in result.upper()


def test_to_locale_without_description():
    """to_locale with include_description=False omits description."""
    full = to_locale("HTTP_STATUS_OK", include_description=True)
    short = to_locale("HTTP_STATUS_OK", include_description=False)
    assert len(short) < len(full)
    assert "200" in short


def test_to_locale_unknown():
    """to_locale returns None for unknown constants."""
    assert to_locale("UNKNOWN_CONST") is None


def test_toLocale_alias():
    """toLocale is an alias for to_locale."""
    assert toLocale("HTTP_STATUS_OK") == to_locale("HTTP_STATUS_OK")


# ── encode_card_addr / decode_card_addr ──────────────────────────────────────

def test_card_addr_roundtrip():
    """encode_card_addr / decode_card_addr round-trips."""
    for t, p, c in [(0, 0, 0), (31, 63, 31), (5, 10, 15), (1, 1, 1)]:
        addr = encode_card_addr(t, p, c)
        assert decode_card_addr(addr) == (t, p, c)


def test_card_addr_max():
    """Max values encode correctly."""
    addr = encode_card_addr(31, 63, 31)
    assert addr == 0xFFFF


def test_card_addr_zero():
    """Zero encodes to 0."""
    assert encode_card_addr(0, 0, 0) == 0


def test_card_addr_rejects_overflow():
    """encode_card_addr rejects out-of-range values."""
    import pytest
    with pytest.raises(AssertionError):
        encode_card_addr(32, 0, 0)
    with pytest.raises(AssertionError):
        encode_card_addr(0, 64, 0)
    with pytest.raises(AssertionError):
        encode_card_addr(0, 0, 32)


# ── disassemble ──────────────────────────────────────────────────────────────

def test_disassemble_empty():
    """disassemble of empty list returns empty string."""
    result = disassemble([])
    assert result == "" or result.strip() == ""


def test_disassemble_noop():
    """disassemble handles a NOP instruction."""
    import picoscript as isa
    word = isa.encode_instruction(isa.OP_NOOP, imm16=0)
    result = disassemble([word])
    assert len(result) > 0


def test_disassemble_host_hook():
    """disassemble decodes host hook calls."""
    import picoscript as isa
    from picoscript_lang import HOST_HOOK_BASE, HOST_HOOK_CODES
    hook = HOST_HOOK_CODES[("Random", "U32")]
    word = isa.encode_instruction(isa.OP_NOOP, rd=1, imm16=HOST_HOOK_BASE | hook)
    result = disassemble([word])
    assert "Random" in result or "U32" in result


def test_disassemble_multiple_words():
    """disassemble handles a multi-word program."""
    import picoscript as isa
    from picoscript_lang import HOST_HOOK_BASE, HOST_HOOK_CODES
    words = [
        isa.encode_instruction(isa.OP_NOOP, imm16=0),
        isa.encode_instruction(isa.OP_NOOP, rd=1, imm16=HOST_HOOK_BASE | HOST_HOOK_CODES[("Io", "Write")]),
        isa.encode_instruction(isa.OP_NOOP, imm16=0xC000),  # Net.Close
    ]
    result = disassemble(words)
    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert len(lines) >= 3
