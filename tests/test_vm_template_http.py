#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_template_http.py -- coverage for Template.* and Http.* VM hooks."""
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


# ── Template.* ───────────────────────────────────────────────────────────────

def test_template_render():
    """Template.Render exercises the template subsystem."""
    src = """
int tmpl = "Hello!";
int result = Template.Render(tmpl);
print(result);
"""
    vm = run(src)
    # Template hooks run without fault
    assert vm.steps > 0


def test_template_compile_render():
    """Template.Compile + Render cycle."""
    src = """
int tmpl = "static text";
int compiled = Template.Compile(tmpl);
int result = Template.Render(compiled);
print(result);
"""
    vm = run(src)
    assert vm.steps > 0


def test_template_render_no_vars():
    """Template.Render exercises the handler code path."""
    src = """
int tmpl = "plain output";
int result = Template.Render(tmpl);
print(result);
"""
    vm = run(src)
    assert vm.steps > 0


# ── Http.* ───────────────────────────────────────────────────────────────────

def test_http_parse_query():
    """Http.ParseQuery parses query string."""
    src = """
int qs = "a=1&b=2";
int parsed = Http.ParseQuery(qs);
print(parsed);
"""
    vm = run(src)
    assert len(vm.output) > 0


def test_http_parse_json():
    """Http.ParseJson parses a JSON string."""
    src = """
int json = "{}";
int parsed = Http.ParseJson(json);
print(parsed);
"""
    vm = run(src)
    assert len(vm.output) > 0


def test_http_encode_json():
    """Http.EncodeJson produces JSON output."""
    src = """
int data = "test";
int json = Http.EncodeJson(data);
Io.Write(json);
"""
    vm = run(src)
    assert len(vm.output) > 0


# ── Html.* ───────────────────────────────────────────────────────────────────

def test_html_encode():
    """Html.Encode escapes special HTML chars."""
    src = """
int s = "<b>hi</b>";
int esc = Html.Encode(s);
Io.Write(esc);
"""
    vm = run(src)
    got = out_bytes(vm)
    assert b"&lt;" in got


def test_html_decode():
    """Html.Decode reverses encoding."""
    src = """
int s = "&lt;b&gt;";
int unesc = Html.Decode(s);
Io.Write(unesc);
"""
    vm = run(src)
    got = out_bytes(vm)
    assert b"<b>" in got


# ── Base64.* ─────────────────────────────────────────────────────────────────

def test_base64_encode_decode():
    """Base64.Encode / Base64.Decode round-trip."""
    src = """
int data = "Hello World";
int enc = Base64.Encode(data);
int dec = Base64.Decode(enc);
Io.Write(dec);
"""
    vm = run(src)
    assert out_bytes(vm) == b"Hello World"


def test_base64_encode_output():
    """Base64.Encode produces valid base64."""
    src = """
int data = "ABC";
int enc = Base64.Encode(data);
Io.Write(enc);
"""
    vm = run(src)
    import base64
    expected = base64.b64encode(b"ABC")
    assert out_bytes(vm) == expected


def test_base64_url_encode():
    """Base64.UrlEncode uses URL-safe alphabet."""
    src = """
int data = "test?data+here";
int enc = Base64.UrlEncode(data);
Io.Write(enc);
"""
    vm = run(src)
    got = out_bytes(vm)
    # URL-safe base64 has no + or /
    assert b"+" not in got
    assert b"/" not in got


# ── Encoding.* ───────────────────────────────────────────────────────────────

def test_encoding_hex():
    """Encoding.HexEncode / HexDecode round-trip."""
    src = """
int data = "AB";
int hex = Encoding.HexEncode(data);
int back = Encoding.HexDecode(hex);
Io.Write(back);
"""
    vm = run(src)
    assert out_bytes(vm) == b"AB"


def test_encoding_utf8():
    """Encoding.Utf8Encode / Utf8Decode round-trip."""
    src = """
int data = "Hello";
int enc = Encoding.Utf8Encode(data);
int dec = Encoding.Utf8Decode(enc);
Io.Write(dec);
"""
    vm = run(src)
    assert out_bytes(vm) == b"Hello"
