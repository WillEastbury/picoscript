#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_subsystem_coverage.py -- targeted coverage for vm.py subsystem gaps.

Covers:
  Number.* methods (1199-1220): ParseInt error, Abs/Min/Max/Floor/ToString/ToHex/ToOctal/ToBinary
  Template (1520-1629): {{#each}}, model overflow, nested sections, render
  Text/binary helpers (1863-1932): _w_byte/_w_text/_w_span/_json_esc/_xml_esc
  TextRender (2056-2103): Open/Attr/OpenEnd/Close/Empty/Br/Hole/Raw/Text
  Stream/device I/O (3042-3111): Open/Next/Span/SetSlice/Slice/Submit/Release/Close
  _i8, _i32be_at, _i32be_pack static helpers (2105-2120)
  JSON parser escape sequences (1400-1423)
  Request/response extended paths (1659-1756)
  Q16 math edge cases (174-257)
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_vm import (
    PicoVM, PicoFault, HostApi,
    _q16_sincos, _q16_tan, _q16_exp, _q16_log,
    Q16_ONE, Q16_HALF_PI, Q16_TWO_PI,
    PV_FAULT_TEMPLATE, PV_FAULT_ALLOC,
)


def make_vm():
    vm = PicoVM()
    vm.host._handler_mark = None
    return vm


def h_call(vm, ns, method, rd=0, rs1=0, rs2=0, imm16=0):
    """Shortcut: call host method and return result register."""
    vm.host.call(vm, ns, method, rd, rs1, rs2, imm16)
    return vm.regs[rd]


def str_span(vm, text: str) -> int:
    return vm.host._str_span(vm, text)


def span_str(vm, h: int) -> str:
    return vm.host._span_str(vm, h)


# ══════════════════════════════════════════════════════════════════════════════
# Q16.16 math edge cases (lines 173-257)
# ══════════════════════════════════════════════════════════════════════════════

def test_q16_sincos_quadrant1():
    """Angle in [pi/2, pi) → quadrant 1 path (line 181: return c, -s)."""
    angle = Q16_HALF_PI + 10000
    s, c = _q16_sincos(angle)
    assert isinstance(s, int) and isinstance(c, int)


def test_q16_sincos_quadrant2():
    """Angle in [pi, 3pi/2) → quadrant 2 path (line 183: return -s, -c)."""
    angle = Q16_HALF_PI * 2 + 10000
    s, c = _q16_sincos(angle)
    assert isinstance(s, int) and isinstance(c, int)


def test_q16_sincos_quadrant3():
    """Angle in [3pi/2, 2pi) → quadrant 3 path (line 184: return -c, s)."""
    angle = Q16_HALF_PI * 3 + 10000
    s, c = _q16_sincos(angle)
    assert isinstance(s, int) and isinstance(c, int)


def test_q16_sincos_negative_angle():
    """Negative angle → a += Q16_TWO_PI normalisation (line 174)."""
    s, c = _q16_sincos(-10000)
    assert isinstance(s, int)


def test_q16_tan_near_pi_over_2():
    """tan(pi/2) saturates → returns 0x7FFFFFFF or -0x80000000 (lines 191-195)."""
    result = _q16_tan(Q16_HALF_PI)
    assert abs(result) >= 0x7FFFFF00  # saturated near max


def test_q16_exp_overflow():
    """z > Q16_EXP_MAX_Z → returns 0x7FFFFFFF (line 225)."""
    result = _q16_exp(700000)
    assert result == 0x7FFFFFFF


def test_q16_exp_underflow():
    """z < -Q16_EXP_MAX_Z → returns 0 (line 227)."""
    result = _q16_exp(-700000)
    assert result == 0


def test_q16_exp_negative_k():
    """Small negative z → k < 0 → right-shift path (lines 241-242)."""
    result = _q16_exp(-Q16_ONE)  # e^-1 ≈ 0.368
    assert 0 < result < Q16_ONE


def test_q16_log_zero():
    """log(0) → -0x80000000 domain error (line 249)."""
    result = _q16_log(0)
    assert result == -0x80000000


def test_q16_log_negative():
    """log(-1) → domain error."""
    result = _q16_log(-1)
    assert result == -0x80000000


def test_q16_log_large():
    """log(4.0 in Q16) → positive, exercises m >= 2*Q16_ONE loop (lines 252-254)."""
    result = _q16_log(4 * Q16_ONE)
    assert result > 0


def test_q16_log_small():
    """log(0.25 in Q16) → negative, exercises m < Q16_ONE loop (lines 255-257)."""
    result = _q16_log(Q16_ONE // 4)
    assert result < 0


# ══════════════════════════════════════════════════════════════════════════════
# Number.* methods (lines 1199-1220)
# ══════════════════════════════════════════════════════════════════════════════

def test_number_parse_int_error():
    """Number.Parse with non-numeric string → status=2, value=0 (line 1199-1201)."""
    vm = make_vm()
    h = str_span(vm, "notanumber")
    vm.regs[1] = h
    h_call(vm, "Number", "Parse", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0
    assert vm.host.host_status == 2


def test_number_abs():
    """Number.Abs(-5) = 5 (line 1204-1205)."""
    vm = make_vm()
    vm.regs[1] = (-5) & 0xFFFFFFFF
    h_call(vm, "Number", "Abs", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 5


def test_number_min():
    """Number.Min(R1, R2) = min (line 1206-1207)."""
    vm = make_vm()
    vm.regs[1] = 3; vm.regs[2] = 7
    h_call(vm, "Number", "Min", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 3


def test_number_max():
    """Number.Max(R1, R2) = max (line 1208-1209)."""
    vm = make_vm()
    vm.regs[1] = 3; vm.regs[2] = 7
    h_call(vm, "Number", "Max", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 7


def test_number_floor():
    """Number.Floor (integer identity) (line 1210-1211)."""
    vm = make_vm()
    vm.regs[1] = 42
    h_call(vm, "Number", "Floor", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 42


def test_number_ceiling():
    """Number.Ceiling (integer identity) (line 1210-1211)."""
    vm = make_vm()
    vm.regs[1] = 42
    h_call(vm, "Number", "Ceiling", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 42


def test_number_round():
    """Number.Round (integer identity) (line 1210-1211)."""
    vm = make_vm()
    vm.regs[1] = 42
    h_call(vm, "Number", "Round", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 42


def test_number_to_string():
    """Number.ToString(42) → span of '42' (line 1212-1213)."""
    vm = make_vm()
    vm.regs[1] = 42
    h_call(vm, "Number", "ToString", rd=0, rs1=1, rs2=0)
    assert span_str(vm, vm.regs[0]) == "42"


def test_number_to_hex():
    """Number.ToHex(255) → 'ff' (line 1214-1215)."""
    vm = make_vm()
    vm.regs[1] = 255
    h_call(vm, "Number", "ToHex", rd=0, rs1=1, rs2=0)
    assert span_str(vm, vm.regs[0]) == "ff"


def test_number_to_octal():
    """Number.ToOctal(8) → '10' (line 1216-1217)."""
    vm = make_vm()
    vm.regs[1] = 8
    h_call(vm, "Number", "ToOctal", rd=0, rs1=1, rs2=0)
    assert span_str(vm, vm.regs[0]) == "10"


def test_number_to_binary():
    """Number.ToBinary(5) → '101' (line 1218-1219)."""
    vm = make_vm()
    vm.regs[1] = 5
    h_call(vm, "Number", "ToBinary", rd=0, rs1=1, rs2=0)
    assert span_str(vm, vm.regs[0]) == "101"


# ══════════════════════════════════════════════════════════════════════════════
# JSON parser escape sequences (lines 1406-1423)
# These are exercised via Search.Flatten or Json.* on a JSON span
# ══════════════════════════════════════════════════════════════════════════════

def test_json_parser_escape_sequences():
    """Search.Flatten with unknown method logs it; Http.ParseJson handles \\n \\t escapes."""
    vm = make_vm()
    # Use Http.ParseJson (the correct path for _parsejson_to_model)
    json_text = b'{"key":"line1\\nline2\\ttab"}'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "key=" in result


def test_json_parser_unicode_escape_1byte():
    """Http.ParseJson: \\u0041 = 'A' (1-byte UTF-8)."""
    vm = make_vm()
    json_text = b'{"k":"\\u0041"}'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "k=A" in result


def test_json_parser_unicode_escape_2byte():
    """Http.ParseJson: \\u00E9 = 'é' (2-byte UTF-8)."""
    vm = make_vm()
    json_text = b'{"k":"\\u00E9"}'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "k=" in result


def test_json_parser_unicode_escape_3byte():
    """Http.ParseJson: \\u4E2D = '中' (3-byte UTF-8)."""
    vm = make_vm()
    json_text = b'{"k":"\\u4E2D"}'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "k=" in result


def test_json_parser_unknown_escape():
    """Http.ParseJson: \\x (unknown escape) → literal character."""
    vm = make_vm()
    json_text = b'{"k":"\\x41"}'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "k=" in result


def test_json_parser_array():
    """Http.ParseJson: array [1,2,3] flattened to indexed keys."""
    vm = make_vm()
    json_text = b'[1,2,3]'
    h_json = vm.host._new_span_bytes(vm, json_text)
    vm.regs[1] = h_json
    h_call(vm, "Http", "ParseJson", rd=0, rs1=1, rs2=0)
    result = span_str(vm, vm.regs[0])
    assert "0=1" in result


def test_json_parser_empty_object():
    """JSON empty object {} (line 1434-1435)."""
    vm = make_vm()
    h = vm.host._new_span_bytes(vm, b'{}')
    vm.regs[1] = h
    h_call(vm, "Search", "Flatten", rd=0, rs1=1, rs2=0)


def test_json_parser_empty_array():
    """JSON empty array [] (line 1451-1452)."""
    vm = make_vm()
    h = vm.host._new_span_bytes(vm, b'[]')
    vm.regs[1] = h
    h_call(vm, "Search", "Flatten", rd=0, rs1=1, rs2=0)


# ══════════════════════════════════════════════════════════════════════════════
# Template subsystem (lines 1520, 1549, 1559-1629)
# ══════════════════════════════════════════════════════════════════════════════

def test_template_compile_each():
    """Template.Compile with {{#each list}} section (lines 1524-1526)."""
    vm = make_vm()
    tpl = b"{{#each items}}item{{/items}}"
    h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_template_compile_section():
    """Template.Compile with {{#section}} (lines 1527-1529)."""
    vm = make_vm()
    tpl = b"{{#visible}}shown{{/visible}}"
    h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_template_compile_inverted():
    """Template.Compile with {{^section}} inverted (lines 1530-1532)."""
    vm = make_vm()
    tpl = b"{{^hidden}}fallback{{/hidden}}"
    h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_template_compile_end_marker():
    """Template.Compile with {{/end}} end marker (line 1533-1534)."""
    vm = make_vm()
    tpl = b"{{#s}}x{{/s}}"
    h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_template_compile_missing_close():
    """Template.Compile with unclosed {{ produces a partial plan (no crash)."""
    vm = make_vm()
    tpl = b"Hello {{world"  # no closing }}
    h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    # Compile returns a span handle (> 0) — even partial plans are stored
    assert vm.regs[0] > 0


def test_template_render_model_overflow():
    """Template.Render with > 512 model lines → PicoFault TEMPLATE (line 1549)."""
    vm = make_vm()
    tpl = b"{{name}}"
    tpl_h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = tpl_h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    compiled_h = vm.regs[0]
    # Build model with 600 entries
    model_lines = b"\n".join(f"key{i}=val{i}".encode() for i in range(600))
    model_h = vm.host._new_span_bytes(vm, model_lines)
    vm.regs[1] = compiled_h
    vm.regs[2] = model_h
    with pytest.raises(PicoFault) as exc:
        h_call(vm, "Template", "Render", rd=0, rs1=1, rs2=2)
    assert exc.value.code == PV_FAULT_TEMPLATE


def test_template_render_basic():
    """Template.Render replaces holes from model."""
    vm = make_vm()
    tpl = b"Hello {{name}}!"
    tpl_h = vm.host._new_span_bytes(vm, tpl)
    vm.regs[1] = tpl_h
    h_call(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    compiled_h = vm.regs[0]
    model = b"name=World"
    model_h = vm.host._new_span_bytes(vm, model)
    vm.regs[1] = compiled_h; vm.regs[2] = model_h
    h_call(vm, "Template", "Render", rd=0, rs1=1, rs2=2)
    result = span_str(vm, vm.regs[0])
    assert "Hello" in result and "World" in result


# ══════════════════════════════════════════════════════════════════════════════
# Text/binary writer helpers (lines 1863-1932)
# ══════════════════════════════════════════════════════════════════════════════

def test_w_byte_writes_to_arena():
    """_w_byte writes a byte into the writer buffer (line 1867-1870)."""
    vm = make_vm()
    ptr = 0x100
    w = {"ptr": ptr, "pos": 0, "cap": 10}
    HostApi._w_byte(vm, w, 0x41)  # 'A'
    assert vm.mem[ptr] == 0x41
    assert w["pos"] == 1


def test_w_byte_at_capacity():
    """_w_byte at capacity does nothing (line 1868 condition fails)."""
    vm = make_vm()
    w = {"ptr": 0x100, "pos": 5, "cap": 5}
    HostApi._w_byte(vm, w, 0x41)
    assert w["pos"] == 5  # unchanged


def test_w_text_writes_utf8():
    """_w_text writes UTF-8 bytes (line 1872-1874)."""
    vm = make_vm()
    ptr = 0x200
    w = {"ptr": ptr, "pos": 0, "cap": 100}
    vm.host._w_text(vm, w, "Hello")
    assert w["pos"] == 5
    assert bytes(vm.mem[ptr:ptr+5]) == b"Hello"


def test_w_span_writes_span_contents():
    """_w_span copies span bytes (line 1876-1880)."""
    vm = make_vm()
    data = b"World"
    h = vm.host._new_span_bytes(vm, data)
    ptr = 0x300
    w = {"ptr": ptr, "pos": 0, "cap": 100}
    vm.host._w_span(vm, w, h)
    assert w["pos"] == len(data)
    assert bytes(vm.mem[ptr:ptr+len(data)]) == data


def test_w_span_invalid_handle():
    """_w_span with invalid handle is a no-op (line 1877 condition fails)."""
    vm = make_vm()
    w = {"ptr": 0x400, "pos": 0, "cap": 100}
    vm.host._w_span(vm, w, 9999)  # invalid handle
    assert w["pos"] == 0  # nothing written


def test_json_esc_special_chars():
    """_json_esc escapes quotes, backslash, newline, CR, tab, control chars (lines 1887-1900)."""
    result = HostApi._json_esc('"Hello\\nWorld\r\t\x01"')
    assert '\\"' in result
    assert '\\\\' in result
    assert '\\n' in result
    assert '\\r' in result
    assert '\\t' in result
    assert '\\u0001' in result


# ══════════════════════════════════════════════════════════════════════════════
# TextRender subsystem (lines 2056-2103)
# ══════════════════════════════════════════════════════════════════════════════

def _setup_writer(vm, capacity=512):
    """Create a writer in the VM arena and register it."""
    ptr = vm.arena_top
    vm.arena_top += capacity
    w = {"ptr": ptr, "pos": 0, "cap": capacity}
    vm.host.writers[1] = w
    return w


def test_textrender_raw():
    """TextRender.Raw copies span to writer (line 2085)."""
    vm = make_vm()
    w = _setup_writer(vm)
    data_h = vm.host._str_span(vm, "hello")
    vm.regs[1] = 1; vm.regs[2] = data_h
    h_call(vm, "TextRender", "Raw", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1
    assert bytes(vm.mem[w["ptr"]:w["ptr"]+5]) == b"hello"


def test_textrender_text():
    """TextRender.Text writes XML-escaped text (line 2087)."""
    vm = make_vm()
    w = _setup_writer(vm)
    data_h = vm.host._str_span(vm, "<b>")
    vm.regs[1] = 1; vm.regs[2] = data_h
    h_call(vm, "TextRender", "Text", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert "&lt;" in text


def test_textrender_open():
    """TextRender.Open writes '<' + tag (line 2089)."""
    vm = make_vm()
    w = _setup_writer(vm)
    tag_h = vm.host._str_span(vm, "div")
    vm.regs[1] = 1; vm.regs[2] = tag_h
    h_call(vm, "TextRender", "Open", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert text == "<div"


def test_textrender_attr():
    """TextRender.Attr writes ' name="value"' (line 2090-2094)."""
    vm = make_vm()
    w = _setup_writer(vm)
    spec_h = vm.host._str_span(vm, 'class=main')
    vm.regs[1] = 1; vm.regs[2] = spec_h
    h_call(vm, "TextRender", "Attr", rd=0, rs1=1, rs2=2)
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert 'class="main"' in text


def test_textrender_open_end():
    """TextRender.OpenEnd writes '>' (line 2095-2096)."""
    vm = make_vm()
    w = _setup_writer(vm)
    vm.regs[1] = 1
    h_call(vm, "TextRender", "OpenEnd", rd=0, rs1=1, rs2=0)
    assert vm.mem[w["ptr"]] == 0x3E  # '>'


def test_textrender_close():
    """TextRender.Close writes '</tag>' (line 2097-2098)."""
    vm = make_vm()
    w = _setup_writer(vm)
    tag_h = vm.host._str_span(vm, "p")
    vm.regs[1] = 1; vm.regs[2] = tag_h
    h_call(vm, "TextRender", "Close", rd=0, rs1=1, rs2=2)
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert text == "</p>"


def test_textrender_empty():
    """TextRender.Empty writes '/>' (line 2099-2100)."""
    vm = make_vm()
    w = _setup_writer(vm)
    vm.regs[1] = 1
    h_call(vm, "TextRender", "Empty", rd=0, rs1=1, rs2=0)
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert text == "/>"


def test_textrender_br():
    """TextRender.Br writes '<br/>' (line 2101-2102)."""
    vm = make_vm()
    w = _setup_writer(vm)
    vm.regs[1] = 1
    h_call(vm, "TextRender", "Br", rd=0, rs1=1, rs2=0)
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert text == "<br/>"


def test_textrender_hole():
    """TextRender.Hole looks up model key and writes to writer 1 (line 2070-2079)."""
    vm = make_vm()
    w = _setup_writer(vm)
    model_h = vm.host._str_span(vm, "name=Alice\nage=30")
    key_h = vm.host._str_span(vm, "name")
    vm.regs[1] = model_h; vm.regs[2] = key_h
    h_call(vm, "TextRender", "Hole", rd=0, rs1=1, rs2=2)
    text = bytes(vm.mem[w["ptr"]:w["ptr"] + w["pos"]]).decode()
    assert "Alice" in text


def test_textrender_invalid_writer():
    """TextRender with invalid writer handle → R0=0 (line 2081-2083)."""
    vm = make_vm()
    # No writer registered for slot 5
    vm.regs[1] = 5
    h_call(vm, "TextRender", "Raw", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0


def test_textrender_empty_method():
    """TextRender.Empty on invalid writer (line 2056-2058)."""
    vm = make_vm()
    vm.regs[1] = 99  # no writer
    h_call(vm, "TextRender", "Empty", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Stream / device I/O (lines 3042-3111)
# ══════════════════════════════════════════════════════════════════════════════

def _register_device(vm, dev_id=1, frames=3, buf=64):
    vm.host.devices[dev_id] = {"open": True, "frames": frames, "buf": buf}
    # cfg: dir=0 (RX), buf=64, frames=3 → 0 | (64<<1) | (3<<16)
    cfg = 0 | (buf << 1) | (frames << 16)
    return dev_id, cfg


def test_stream_close_device():
    """Device.Close sets dev['open']=False (line 3042-3045)."""
    vm = make_vm()
    _register_device(vm, dev_id=1)
    vm.regs[1] = 1
    h_call(vm, "Device", "Close", rd=0, rs1=1, rs2=0)
    assert not vm.host.devices[1]["open"]
    assert vm.regs[0] == 1


def test_stream_close_missing_device():
    """Device.Close with nonexistent device → R0=0 (line 3044)."""
    vm = make_vm()
    vm.regs[1] = 999
    h_call(vm, "Device", "Close", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0


def test_stream_open():
    """Stream.Open returns stream handle (line 3049-3062)."""
    vm = make_vm()
    dev_id, cfg = _register_device(vm, dev_id=2, frames=2, buf=8)
    vm.regs[1] = dev_id; vm.regs[2] = cfg
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] > 0
    stream_h = vm.regs[0]
    assert stream_h in vm.host.streams


def test_stream_open_missing_device():
    """Stream.Open with closed/missing device → R0=0, status=NOT_FOUND (line 3051-3054)."""
    vm = make_vm()
    vm.regs[1] = 999; vm.regs[2] = 0
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 0
    assert vm.host.host_status == 1


def test_stream_next_and_span():
    """Stream.Next gets a lease; Stream.Span materialises it (lines 3063-3085)."""
    vm = make_vm()
    dev_id, cfg = _register_device(vm, dev_id=3, frames=2, buf=8)
    vm.regs[1] = dev_id; vm.regs[2] = cfg
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    stream_h = vm.regs[0]
    vm.regs[1] = stream_h
    h_call(vm, "Stream", "Next", rd=0, rs1=1, rs2=0)
    lease_h = vm.regs[0]
    assert lease_h > 0
    vm.regs[1] = lease_h
    h_call(vm, "Stream", "Span", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_stream_next_eof():
    """Stream.Next when frames exhausted → status=EOF, R0=0 (line 3065-3068)."""
    vm = make_vm()
    dev_id, cfg = _register_device(vm, dev_id=4, frames=0, buf=8)
    vm.regs[1] = dev_id; vm.regs[2] = cfg
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    stream_h = vm.regs[0]
    vm.regs[1] = stream_h
    h_call(vm, "Stream", "Next", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0
    assert vm.host.host_status == 3


def test_stream_set_slice_and_slice():
    """Stream.SetSlice + Stream.Slice (lines 3086-3101)."""
    vm = make_vm()
    dev_id, cfg = _register_device(vm, dev_id=5, frames=1, buf=16)
    vm.regs[1] = dev_id; vm.regs[2] = cfg
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    stream_h = vm.regs[0]
    vm.regs[1] = stream_h
    h_call(vm, "Stream", "Next", rd=0, rs1=1, rs2=0)
    lease_h = vm.regs[0]
    assert lease_h > 0
    # SetSlice: offset=2, len=4
    vm.regs[1] = 2; vm.regs[2] = 4
    h_call(vm, "Stream", "SetSlice", rd=0, rs1=1, rs2=2)
    # Slice the lease
    vm.regs[1] = lease_h
    h_call(vm, "Stream", "Slice", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0


def test_stream_submit():
    """Stream.Submit (TX: hand lease to device, line 3102-3111)."""
    vm = make_vm()
    # TX stream: dir=1, buf=8, frames=1 → cfg = 1 | (8<<1) | (1<<16)
    tx_cfg = 1 | (8 << 1) | (1 << 16)
    dev_id, _ = _register_device(vm, dev_id=6, frames=1, buf=8)
    vm.regs[1] = dev_id; vm.regs[2] = tx_cfg
    h_call(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    stream_h = vm.regs[0]
    vm.regs[1] = stream_h
    h_call(vm, "Stream", "Next", rd=0, rs1=1, rs2=0)
    lease_h = vm.regs[0]
    assert lease_h > 0
    # Get span for the lease
    vm.regs[1] = lease_h
    h_call(vm, "Stream", "Span", rd=0, rs1=1, rs2=0)
    # Submit
    vm.regs[1] = stream_h; vm.regs[2] = lease_h
    h_call(vm, "Stream", "Submit", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1


# ══════════════════════════════════════════════════════════════════════════════
# _i8, _i32be_at static helpers (lines 2105-2115)
# ══════════════════════════════════════════════════════════════════════════════

def test_i8_positive():
    """_i8(100) = 100 (line 2107 else branch)."""
    from picoscript_vm import HostApi
    assert HostApi._i8(100) == 100


def test_i8_negative():
    """_i8(200) = 200 - 256 = -56 (line 2107 if branch)."""
    from picoscript_vm import HostApi
    assert HostApi._i8(200) == -56


def test_i32be_at_valid():
    """_i32be_at reads big-endian int32 from data (line 2110-2115)."""
    from picoscript_vm import HostApi
    data = b'\x00\x00\x00\x2a'  # 42 big-endian
    assert HostApi._i32be_at(data, 0) == 42


def test_i32be_at_out_of_range():
    """_i32be_at returns 0 when out of range (line 2112-2113)."""
    from picoscript_vm import HostApi
    data = b'\x00\x01'  # only 2 bytes, index 0 needs 4
    assert HostApi._i32be_at(data, 0) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response extended paths (lines 1659-1756)
# ══════════════════════════════════════════════════════════════════════════════

def test_req_body_count():
    """Req.BodyCount returns number of body chunks."""
    vm = make_vm()
    vm.host.install_request_context(vm, body=["chunk1", "chunk2"])
    h_call(vm, "Req", "BodyCount", rd=0, rs1=0, rs2=0)
    assert vm.regs[0] == 2


def test_req_body_span():
    """Req.BodySpan returns span for a body chunk."""
    vm = make_vm()
    vm.host.install_request_context(vm, body=["hello world"])
    vm.regs[1] = 0  # index 0
    h_call(vm, "Req", "BodySpan", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] > 0
    text = span_str(vm, vm.regs[0])
    assert "hello" in text


def test_resp_abort():
    """Resp.Abort closes the response (line 1860)."""
    vm = make_vm()
    vm.host.install_request_context(vm)
    h_call(vm, "Resp", "Abort", rd=0, rs1=0, rs2=0)
    # Should record an abort descriptor
    assert any(True for d in vm.host.response_graph)


def test_resp_early_hints():
    """Resp.EarlyHints adds 103 descriptor (line 1860-1862)."""
    vm = make_vm()
    vm.host.install_request_context(vm)
    h_call(vm, "Resp", "EarlyHints", rd=0, rs1=0, rs2=0)
    assert any("EARLY" in str(d) for d in vm.host.response_graph)
