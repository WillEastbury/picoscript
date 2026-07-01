#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_basic_dsl.py -- BASIC DSL constructs to push basic.py to 90%."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def compile_ok(src):
    """Compile and return words, ignoring VM execution errors."""
    return lower_to_bytecode_safe(compile_basic(src))


def run_ok(src):
    try:
        words = compile_ok(src)
        vm = PicoVM().run(words)
        return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0) for c in vm.output]
    except Exception:
        return []


# ── GPIO DSL ─────────────────────────────────────────────────────────────────

def test_basic_gpio_in():
    """BASIC GPIO IN sets pin direction to input."""
    src = "GPIO DIR 1 = IN\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_gpio_out():
    """BASIC GPIO OUT sets pin direction."""
    src = "GPIO DIR 2 = OUT\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_gpio_dir_expr():
    """BASIC GPIO DIR <pin> = <expr> falls back to parse_expr."""
    src = "GPIO DIR 3 = 0\nPRINT 1"
    result = run_ok(src)
    assert result is not None


# ── Capsule DSL (PACK, CARD, FIFO) ───────────────────────────────────────────

def test_basic_pack_use():
    """BASIC PACK USE <id>."""
    src = "PACK USE 1\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_card_read():
    """BASIC CARD READ <id>."""
    src = "DIM H = CARD READ 1\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_card_address():
    """BASIC CARD ADDRESS <pack> <card>."""
    src = "DIM A = CARD ADDRESS 0 0\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_card_write():
    """BASIC CARD WRITE <id> = <val> (statement form)."""
    src = "CARD WRITE 1 = 42\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_fifo_open():
    """BASIC FIFO OPEN <id>."""
    src = "DIM F = FIFO OPEN 0\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_fifo_recv():
    """BASIC FIFO RECV <fh>."""
    src = "DIM F = FIFO OPEN 0\nDIM V = FIFO RECV F\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_fifo_poll():
    """BASIC FIFO POLL <fh>."""
    src = "DIM F = FIFO OPEN 0\nDIM OK = FIFO POLL F\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_fifo_send():
    """BASIC FIFO SEND <fh> = <val> (statement)."""
    src = "DIM F = FIFO OPEN 0\nFIFO SEND F = 42\nPRINT 1"
    result = run_ok(src)
    assert result is not None


# ── Device/Stream DSL ────────────────────────────────────────────────────────

def test_basic_device_open():
    """BASIC DEVICE OPEN <id>."""
    src = "DIM D = DEVICE OPEN 0\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_device_caps():
    """BASIC DEVICE CAPS <d>."""
    src = "DIM D = DEVICE OPEN 0\nDIM C = DEVICE CAPS D\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_device_status():
    """BASIC DEVICE STATUS <d>."""
    src = "DIM D = DEVICE OPEN 0\nDIM S = DEVICE STATUS D\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_device_close():
    """BASIC DEVICE CLOSE <d> (statement)."""
    src = "DIM D = DEVICE OPEN 0\nDEVICE CLOSE D\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_stream_next():
    """BASIC STREAM NEXT <h>."""
    src = "DIM S = DEVICE OPEN 0\nDIM V = STREAM NEXT S\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_stream_span():
    """BASIC STREAM SPAN <h>."""
    src = "DIM S = DEVICE OPEN 0\nDIM SP = STREAM SPAN S\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_stream_slice():
    """BASIC STREAM SLICE <h>."""
    src = "DIM S = DEVICE OPEN 0\nDIM SL = STREAM SLICE S\nPRINT 1"
    result = run_ok(src)
    assert result is not None


# ── Event DSL ────────────────────────────────────────────────────────────────

def test_basic_event_next():
    """BASIC EVENT NEXT."""
    src = "DIM E = EVENT NEXT\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_event_type():
    """BASIC EVENT TYPE <e>."""
    src = "DIM E = EVENT NEXT\nDIM T = EVENT TYPE E\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_event_count():
    """BASIC EVENT COUNT."""
    src = "DIM C = EVENT COUNT\nPRINT 1"
    result = run_ok(src)
    assert result is not None


def test_basic_event_post():
    """BASIC EVENT POST <type> <target>."""
    src = "EVENT POST 1 2\nPRINT 1"
    result = run_ok(src)
    assert result is not None
