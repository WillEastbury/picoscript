#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_textio_json_datetime.py -- coverage for Utf8Writer/Reader, Json, Xml, DateTime."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM().run(words)


def out_bytes(vm):
    return b"".join(vm.output)


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── Utf8Writer ───────────────────────────────────────────────────────────────

def test_utf8writer_new_byte_tospan():
    """Utf8Writer.New / Byte / ToSpan builds a span from bytes."""
    src = """
int w = Utf8Writer.New(64);
Utf8Writer.Byte(w, 72);
Utf8Writer.Byte(w, 105);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    assert out_bytes(run(src)) == b"Hi"


def test_utf8writer_int():
    """Utf8Writer.Int writes an integer as decimal text."""
    src = """
int w = Utf8Writer.New(32);
Utf8Writer.Int(w, 42);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    assert out_bytes(run(src)) == b"42"


def test_utf8writer_span():
    """Utf8Writer.Span appends a span's bytes."""
    src = """
int w = Utf8Writer.New(64);
int hello = "Hello";
Utf8Writer.Span(w, hello);
Utf8Writer.Byte(w, 33);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    assert out_bytes(run(src)) == b"Hello!"


def test_utf8writer_len_reset():
    """Utf8Writer.Len / Reset."""
    src = """
int w = Utf8Writer.New(64);
Utf8Writer.Byte(w, 65);
Utf8Writer.Byte(w, 66);
int n = Utf8Writer.Len(w);
print(n);
Utf8Writer.Reset(w);
int m = Utf8Writer.Len(w);
print(m);
"""
    assert out_ints(run(src)) == [2, 0]


# ── Utf8Reader ───────────────────────────────────────────────────────────────

def test_utf8reader_basic():
    """Utf8Reader.New / Next / Eof reads bytes sequentially."""
    src = """
int data = "AB";
int r = Utf8Reader.New(data);
int a = Utf8Reader.Next(r);
int b = Utf8Reader.Next(r);
int eof = Utf8Reader.Eof(r);
print(a);
print(b);
print(eof);
"""
    result = out_ints(run(src))
    assert result[0] == 65  # 'A'
    assert result[1] == 66  # 'B'
    assert result[2] != 0   # EOF after reading all


def test_utf8reader_peek():
    """Utf8Reader.Peek looks ahead without consuming."""
    src = """
int data = "XY";
int r = Utf8Reader.New(data);
int p = Utf8Reader.Peek(r);
int n = Utf8Reader.Next(r);
print(p);
print(n);
"""
    result = out_ints(run(src))
    assert result[0] == result[1]  # Peek and Next return same byte


def test_utf8reader_pos():
    """Utf8Reader.Pos reports current position."""
    src = """
int data = "ABCDE";
int r = Utf8Reader.New(data);
Utf8Reader.Next(r);
Utf8Reader.Next(r);
int pos = Utf8Reader.Pos(r);
print(pos);
"""
    assert out_ints(run(src)) == [2]


def test_utf8reader_skipws():
    """Utf8Reader.SkipWs skips whitespace."""
    src = """
int data = "   X";
int r = Utf8Reader.New(data);
Utf8Reader.SkipWs(r);
int c = Utf8Reader.Next(r);
print(c);
"""
    assert out_ints(run(src)) == [88]  # 'X'


# ── Json writer ──────────────────────────────────────────────────────────────

def test_json_object():
    """Json.BeginObject/Key/Str/Int/EndObject builds JSON."""
    src = """
int w = Utf8Writer.New(128);
Json.BeginObject(w);
int k1 = "name";
int v1 = "Alice";
Json.Key(w, k1);
Json.Str(w, v1);
int k2 = "age";
Json.Key(w, k2);
Json.Int(w, 30);
Json.EndObject(w);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    got = out_bytes(run(src))
    assert b"name" in got
    assert b"Alice" in got
    assert b"30" in got


def test_json_array():
    """Json.BeginArray/Int/EndArray builds JSON array."""
    src = """
int w = Utf8Writer.New(64);
Json.BeginArray(w);
Json.Int(w, 1);
Json.Int(w, 2);
Json.Int(w, 3);
Json.EndArray(w);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    got = out_bytes(run(src))
    assert b"1" in got and b"2" in got and b"3" in got


def test_json_bool_null():
    """Json.Bool/Null emit keywords."""
    src = """
int w = Utf8Writer.New(64);
Json.BeginArray(w);
Json.Bool(w, 1);
Json.Bool(w, 0);
Json.Null(w);
Json.EndArray(w);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    got = out_bytes(run(src))
    assert b"true" in got and b"false" in got and b"null" in got


# ── Xml writer ───────────────────────────────────────────────────────────────

def test_xml_basic():
    """Xml.Open/Text/Close builds XML elements."""
    src = """
int w = Utf8Writer.New(128);
int tag = "p";
int text = "Hello";
Xml.Open(w, tag);
Xml.OpenEnd(w);
Xml.Text(w, text);
Xml.Close(w, tag);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    got = out_bytes(run(src))
    assert b"<p>" in got and b"Hello" in got and b"</p>" in got


def test_xml_attrs():
    """Xml.AttrName/AttrValue adds attributes."""
    src = """
int w = Utf8Writer.New(128);
int tag = "div";
int an = "class";
int av = "main";
Xml.Open(w, tag);
Xml.AttrName(w, an);
Xml.AttrValue(w, av);
Xml.OpenEnd(w);
Xml.Close(w, tag);
int s = Utf8Writer.ToSpan(w);
Io.Write(s);
"""
    got = out_bytes(run(src))
    assert b"class" in got and b"main" in got and b"<div" in got


# ── DateTime ─────────────────────────────────────────────────────────────────

def test_datetime_utcnow():
    """DateTime.UtcNow returns a positive epoch-seconds value."""
    src = "int t = DateTime.UtcNow(); print(t);"
    result = out_ints(run(src))[0]
    assert result > 1700000000  # After 2023


def test_datetime_add_days():
    """DateTime.AddDays adds 86400 seconds per day."""
    src = """
int base = 1000000;
int later = DateTime.AddDays(base, 1);
int diff = later - base;
print(diff);
"""
    assert out_ints(run(src)) == [86400]


def test_datetime_add_hours():
    """DateTime.AddHours adds 3600 seconds per hour."""
    src = """
int base = 1000000;
int later = DateTime.AddHours(base, 2);
int diff = later - base;
print(diff);
"""
    assert out_ints(run(src)) == [7200]


def test_datetime_add_minutes():
    """DateTime.AddMinutes adds 60 seconds per minute."""
    src = """
int base = 1000000;
int later = DateTime.AddMinutes(base, 5);
int diff = later - base;
print(diff);
"""
    assert out_ints(run(src)) == [300]


def test_datetime_get_day_of_week():
    """DateTime.GetDayOfWeek returns 0-6."""
    src = "int t = DateTime.UtcNow(); int d = DateTime.GetDayOfWeek(t); print(d);"
    result = out_ints(run(src))[0]
    assert 0 <= result <= 6
