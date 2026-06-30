#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted coverage push for remaining picoscript_vm.py branches."""

import datetime as _dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picoscript_vm as pvm  # noqa: E402
from picoscript_vm import (  # noqa: E402
    HostApi,
    Halt,
    PicoFault,
    PicoVM,
    Q16_ONE,
    _aes256_ctr,
    _aes256_encrypt_block,
    _aes256_key_expand,
    _default_timezone_name,
    _dist_sym,
    _inflate,
    _len_sym,
    _q16_exp,
)


def make_vm(**kw):
    vm = PicoVM(**kw)
    vm.host._handler_mark = None
    return vm


def h(vm, ns, method, rd=0, rs1=0, rs2=0):
    vm.host.call(vm, ns, method, rd, rs1, rs2, 0)
    return vm.regs[rd]


def sspan(vm, text: str) -> int:
    return vm.host._str_span(vm, text)


def bspan(vm, data: bytes) -> int:
    return vm.host._new_span_bytes(vm, data)


def span_text(vm, handle: int) -> str:
    return vm.host._span_str(vm, handle)


def span_bytes(vm, handle: int) -> bytes:
    return vm.host._span_raw(vm, handle)


def i32be(*vals: int) -> bytes:
    return b"".join(int(v).to_bytes(4, "big", signed=True) for v in vals)


def s32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def new_writer(vm, ptr=0x200, cap=256) -> int:
    vm.regs[1] = ptr
    vm.regs[2] = cap
    h(vm, "Utf8Writer", "New", rd=0, rs1=1, rs2=2)
    return vm.regs[0]


def new_reader(vm, text: bytes) -> int:
    vm.regs[1] = bspan(vm, text)
    h(vm, "Utf8Reader", "New", rd=0, rs1=1, rs2=0)
    return vm.regs[0]


def compile_tpl(vm, tpl: bytes) -> int:
    vm.regs[1] = bspan(vm, tpl)
    h(vm, "Template", "Compile", rd=0, rs1=1, rs2=0)
    return vm.regs[0]


def render_tpl(vm, plan_h: int, model: bytes) -> str:
    vm.regs[1] = plan_h
    vm.regs[2] = bspan(vm, model)
    h(vm, "Template", "Render", rd=0, rs1=1, rs2=2)
    return span_text(vm, vm.regs[0])


def test_default_timezone_name_prefers_key_then_name(monkeypatch):
    class FakeAware:
        def __init__(self, tzinfo):
            self.tzinfo = tzinfo

        def astimezone(self):
            return self

    class FakeDateTime:
        tzinfo_obj = None

        @classmethod
        def now(cls, tz=None):
            return FakeAware(cls.tzinfo_obj)

    class KeyTz:
        key = "Europe/London"

        def tzname(self, _dt_obj):
            return "ignored"

    class NameTz:
        key = None

        def tzname(self, _dt_obj):
            return "BST"

    monkeypatch.setattr(_dt, "datetime", FakeDateTime)
    FakeDateTime.tzinfo_obj = KeyTz()
    assert _default_timezone_name() == "Europe/London"
    FakeDateTime.tzinfo_obj = NameTz()
    assert _default_timezone_name() == "BST"


def test_q16_exp_positive_loop_and_aes_ctr_carry_chain():
    assert _q16_exp(Q16_ONE * 2) > Q16_ONE

    key = bytes(32)
    iv = bytes([0] * 15 + [0xFF])
    data = bytes(32)
    got = _aes256_ctr(key, iv, data)
    w = _aes256_key_expand(key)
    expected = (
        _aes256_encrypt_block(iv, w)
        + _aes256_encrypt_block(bytes([0] * 14 + [1, 0]), w)
    )
    assert got == expected


def test_deflate_symbol_helpers_and_malformed_inputs():
    assert _dist_sym(0) == 0
    assert _dist_sym(1) == 0
    assert _len_sym(2) == 257
    with pytest.raises(ValueError, match="truncated compressed data"):
        _inflate(b"")
    with pytest.raises(ValueError, match="bad compressed data"):
        _inflate(b'"\xfbO\xe5')


def test_memory_const_guards_and_init_flags():
    vm = make_vm(seed=123, no_alloc=True)
    assert vm.host.rng_state == 123
    assert vm.host.no_alloc is True

    vm = make_vm()
    vm.host.const_floor = 0
    vm.regs[1] = 0x7000
    vm.regs[2] = 42
    with pytest.raises(PicoFault) as exc:
        h(vm, "Memory", "Set", rd=0, rs1=1, rs2=2)
    assert exc.value.code == pvm.PV_FAULT_CONST_WRITE

    vm = make_vm()
    vm.regs[1] = 0x7100
    vm.regs[2] = 65
    h(vm, "Memory", "SetConst", rd=0, rs1=1, rs2=2)
    vm.regs[2] = 66
    with pytest.raises(PicoFault) as exc2:
        h(vm, "Memory", "SetConst", rd=0, rs1=1, rs2=2)
    assert exc2.value.code == pvm.PV_FAULT_CONST_WRITE


def test_dispatch_unknown_methods_fall_through_to_log():
    vm = make_vm(caps=pvm.CAP_ALL)
    vm.host.install_request_context(vm, path="/a/b")

    writer_h = new_writer(vm)
    reader_h = new_reader(vm, b"-12")
    text_h = sspan(vm, "x")

    cases = [
        ("Resp", lambda: None),
        ("Req", lambda: None),
        ("Query", lambda: None),
        ("Search", lambda: None),
        ("Gpio", lambda: None),
        ("Device", lambda: None),
        ("Stream", lambda: None),
        ("Assert", lambda: None),
        ("Event", lambda: None),
        ("Ui", lambda: None),
        ("String", lambda: vm.regs.__setitem__(1, text_h)),
        ("Number", lambda: None),
        ("Template", lambda: None),
        ("Maths", lambda: None),
        ("Compress", lambda: None),
        ("Crypto", lambda: None),
        ("Html", lambda: None),
        ("Http", lambda: None),
        ("Utf8Writer", lambda: vm.regs.__setitem__(1, writer_h)),
        ("Utf8Reader", lambda: vm.regs.__setitem__(1, reader_h)),
        ("Json", lambda: vm.regs.__setitem__(1, writer_h)),
        ("Xml", lambda: vm.regs.__setitem__(1, writer_h)),
        ("TextRender", lambda: vm.regs.__setitem__(1, writer_h)),
        ("Process", lambda: None),
        ("Env", lambda: None),
        ("Timer", lambda: None),
        ("Scheduler", lambda: None),
        ("Principal", lambda: None),
        ("Capability", lambda: None),
        ("Sandbox", lambda: None),
        ("Base64", lambda: None),
        ("Encoding", lambda: None),
        ("DateTime", lambda: None),
        ("Locale", lambda: None),
    ]

    for ns, setup in cases:
        setup()
        before = len(vm.host.log)
        h(vm, ns, "Missing", rd=0, rs1=1, rs2=2)
        assert len(vm.host.log) == before + 1
        assert f"host {ns}.Missing" in vm.host.log[-1]


def test_string_library_methods():
    vm = make_vm()
    hello = sspan(vm, "  Hello ")
    world = sspan(vm, "World")
    ell = sspan(vm, "ell")
    repl = sspan(vm, "i")

    vm.regs[1] = hello
    h(vm, "String", "Length", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 8

    vm.regs[1] = hello
    vm.regs[2] = world
    h(vm, "String", "Concat", rd=0, rs1=1, rs2=2)
    assert span_text(vm, vm.regs[0]) == "  Hello World"

    h(vm, "String", "Substring", rd=0, rs1=1, rs2=2)
    assert span_text(vm, vm.regs[0]) == "Hello "

    vm.regs[2] = ell
    h(vm, "String", "IndexOf", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 3
    assert vm.host.host_status == 0

    h(vm, "String", "StartsWith", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 0
    vm.regs[2] = world
    h(vm, "String", "EndsWith", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 0

    h(vm, "String", "Eq", rd=0, rs1=1, rs2=1)
    assert vm.regs[0] == 1

    h(vm, "String", "ToUpper", rd=0, rs1=1, rs2=0)
    assert span_text(vm, vm.regs[0]) == "  HELLO "
    h(vm, "String", "ToLower", rd=0, rs1=1, rs2=0)
    assert span_text(vm, vm.regs[0]) == "  hello "
    h(vm, "String", "Trim", rd=0, rs1=1, rs2=0)
    assert span_text(vm, vm.regs[0]) == "Hello"

    vm.regs[1] = repl
    h(vm, "String", "SetReplace", rd=0, rs1=1, rs2=0)
    vm.regs[1] = hello
    vm.regs[2] = ell
    h(vm, "String", "Replace", rd=0, rs1=1, rs2=2)
    assert span_text(vm, vm.regs[0]) == "  Hio "


def test_json_parser_and_template_edge_cases():
    flat = HostApi._parsejson_to_model(
        br'{"esc":"\n\t\r\b\f\x","ascii":"\u0041","latin":"\u00e9","euro":"\u20ac","obj":{"k":"v","n":1},"arr":["x",2]}'
    ).decode("utf-8", "replace")
    assert "esc=" in flat and "ascii=A" in flat and "latin=é" in flat and "arr.0=x" in flat
    assert HostApi._parsejson_to_model(b'{"a":') == b""
    assert HostApi._parsejson_to_model(b"[]") == b""
    assert HostApi._parsejson_to_model(b"[1").startswith(b"0=1")

    vm = make_vm()
    plan = compile_tpl(vm, b"{{#each users}}{{name}}{{/users}} {{#each items}}{{.}}{{#flag}}!{{/flag}}{{/items}}")
    rendered = render_tpl(vm, plan, b"users.0.name=Alice\nitems.0=A\nitems.1=B\nflag=1")
    assert rendered == "Alice A!B!"

    vm2 = make_vm()
    plan2 = compile_tpl(vm2, b"X{{#each items}}{{#flag}}Y{{/flag}}{{.}}{{/items}}Z")
    assert render_tpl(vm2, plan2, b"") == "XZ"


def test_request_context_rewind_set_base_and_response_lifecycle():
    vm = make_vm()
    keep_h = sspan(vm, "keep")
    vm.host.set_arena_base(vm)
    base_top = vm.arena_top
    base_count = len(vm.spans)
    vm.host.install_request_context(vm, path="/aaaaa")
    after_first_install = vm.arena_top
    transient = sspan(vm, "temp")
    assert transient >= keep_h
    vm.host.install_request_context(vm, path="/bbbbb")
    assert vm.arena_top == after_first_install
    assert len(vm.spans) == base_count + 3  # keep + path/method/principal request spans

    vm = make_vm()
    vm.host.install_request_context(vm)
    name_h = sspan(vm, "X-Test")
    value_h = sspan(vm, "yes")
    body_h = sspan(vm, "body")
    vm.regs[1] = 200
    h(vm, "Resp", "Status", rd=0, rs1=1, rs2=0)
    vm.regs[1] = 201
    h(vm, "Resp", "Status", rd=0, rs1=1, rs2=0)
    vm.regs[1] = name_h
    vm.regs[2] = value_h
    h(vm, "Resp", "Header", rd=0, rs1=1, rs2=2)
    vm.regs[1] = body_h
    h(vm, "Resp", "Write", rd=0, rs1=1, rs2=0)
    h(vm, "Resp", "Flush", rd=0, rs1=0, rs2=0)
    vm.regs[1] = name_h
    vm.regs[2] = value_h
    h(vm, "Resp", "Trailer", rd=0, rs1=1, rs2=2)
    h(vm, "Resp", "Seal", rd=0, rs1=0, rs2=0)
    before = len(vm.host.response_graph)
    vm.host._resp_seal(explicit=False)
    assert len(vm.host.response_graph) == before
    h(vm, "Resp", "End", rd=0, rs1=0, rs2=0)
    assert vm.host.response_graph[0]["payload"]["code"] == 201

    vm2 = make_vm()
    vm2.host.install_request_context(vm2)
    h(vm2, "Resp", "Continue", rd=0, rs1=0, rs2=0)
    h(vm2, "Resp", "EarlyHints", rd=0, rs1=0, rs2=0)
    vm2.regs[1] = 204
    h(vm2, "Resp", "Respond", rd=0, rs1=1, rs2=0)


def test_utf8_json_xml_and_text_render_edges():
    vm = make_vm()
    writer_h = new_writer(vm)
    hello_h = sspan(vm, "Hi")
    vm.regs[1] = writer_h
    vm.regs[2] = hello_h
    h(vm, "Utf8Writer", "Span", rd=0, rs1=1, rs2=2)
    h(vm, "Utf8Writer", "ToSpan", rd=0, rs1=1, rs2=0)
    assert span_text(vm, vm.regs[0]) == "Hi"

    vm.regs[1] = 999
    h(vm, "Utf8Writer", "Len", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0

    reader_h = new_reader(vm, b"-12x")
    vm.regs[1] = reader_h
    h(vm, "Utf8Reader", "Int", rd=0, rs1=1, rs2=0)
    assert s32(vm.regs[0]) == -12
    vm.regs[2] = ord("x")
    h(vm, "Utf8Reader", "Match", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 1
    vm.regs[1] = 999
    h(vm, "Utf8Reader", "Peek", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 0

    vm2 = make_vm()
    writer2 = new_writer(vm2)
    raw_h = sspan(vm2, "7")
    msg_h = sspan(vm2, "line\n")
    key_h = sspan(vm2, "k")
    vm2.regs[1] = writer2
    vm2.regs[2] = msg_h
    h(vm2, "Json", "Str", rd=0, rs1=1, rs2=2)  # _json_post with empty stack
    h(vm2, "Json", "BeginObject", rd=0, rs1=1, rs2=0)
    vm2.regs[2] = key_h
    h(vm2, "Json", "Key", rd=0, rs1=1, rs2=2)
    vm2.regs[2] = raw_h
    h(vm2, "Json", "Raw", rd=0, rs1=1, rs2=2)
    h(vm2, "Json", "EndObject", rd=0, rs1=1, rs2=0)
    h(vm2, "Utf8Writer", "ToSpan", rd=0, rs1=1, rs2=0)
    assert b'"k":7' in span_bytes(vm2, vm2.regs[0])

    vm3 = make_vm()
    writer3 = new_writer(vm3)
    tag_h = sspan(vm3, "br")
    vm3.regs[1] = writer3
    vm3.regs[2] = tag_h
    h(vm3, "Xml", "Open", rd=0, rs1=1, rs2=2)
    h(vm3, "Xml", "Empty", rd=0, rs1=1, rs2=0)
    h(vm3, "Utf8Writer", "ToSpan", rd=0, rs1=1, rs2=0)
    assert span_text(vm3, vm3.regs[0]) == "<br/>"

    vm4 = make_vm()
    model_h = sspan(vm4, "name=Alice")
    key2_h = sspan(vm4, "name")
    vm4.regs[1] = model_h
    vm4.regs[2] = key2_h
    h(vm4, "TextRender", "Hole", rd=0, rs1=1, rs2=2)
    assert vm4.regs[0] == 0

    vm5 = make_vm()
    writer5 = new_writer(vm5)
    vm5.regs[1] = writer5
    vm5.regs[2] = key2_h
    assert vm5.host._textrender(vm5, "Missing", 0, 1, 2) is False


def test_tensor_model_quant_and_tokenizer_edges():
    vm = make_vm()
    vm.host.tensor_rows = 1
    vm.host.tensor_cols = 3
    vm.regs[1] = bspan(vm, bytes([1, 2]))
    vm.regs[2] = bspan(vm, bytes([10]))
    assert vm.host._tensor(vm, "MatVecI8", 0, 1, 2) is True
    assert span_bytes(vm, vm.regs[0]) == i32be(10)

    vm.regs[1] = bspan(vm, b"")
    assert vm.host._tensor(vm, "SoftmaxI32", 0, 1, 0) is True
    assert span_bytes(vm, vm.regs[0]) == b""

    assert vm.host._decode_row_spec(0, -5, 2, 4) == (0, 2)
    assert vm.host._base3_weight(bytes([2, 0, 0, 0]), 0, 4, 5) == -1

    vm.host.model_tensors[7] = {"pack": 0, "card": 1, "offset": 0, "rows": 1, "cols": 5, "format": 3}
    vm.host.blob_cards[("0", 1)] = bytearray([2, 0, 0, 0])
    vec_h = bspan(vm, bytes([3]))
    assert vm.host._model_block_matvec(vm, 0, 7, vec_h, "base3") is True
    assert span_bytes(vm, vm.regs[0]) == i32be(0)

    vm.host.bitlinear_rows = 1
    vm.host.bitlinear_cols = 3
    vm.regs[1] = bspan(vm, bytes([1]))
    vm.regs[2] = bspan(vm, bytes([5]))
    assert vm.host._bitlinear(vm, "MatVecTernary", 0, 1, 2) is True
    assert vm.host._bitlinear(vm, "MatVecBitmap", 0, 1, 2) is True

    vm.regs[1] = bspan(vm, i32be(-1000, 1000))
    vm.regs[2] = 1
    assert vm.host._quant(vm, "QuantI8", 0, 1, 2) is True
    assert span_bytes(vm, vm.regs[0]) == bytes([0, 255])

    vm.regs[1] = bspan(vm, b"ok=1\nbad\nskip=oops")
    assert vm.host._tokenizer(vm, "SetVocab", 0, 1, 0) is True
    assert vm.regs[0] == 1

    vm.regs[1] = bspan(vm, i32be(100, 10, 5))
    assert vm.host._argmax_span(vm, 0, vm.regs[1]) is True
    assert vm.regs[0] == 0


def test_attention_query_helpers_search_terms_and_search_ops():
    vm = make_vm()
    vm.host.attn_shape["dim"] = 3
    vm.regs[1] = bspan(vm, bytes([1]))
    vm.regs[2] = bspan(vm, bytes([2, 3, 4, 5, 6, 7]))
    assert vm.host._attention(vm, "Scores", 0, 1, 2) is True
    assert span_bytes(vm, vm.regs[0]) == i32be(2, 5)

    pack_h = sspan(vm, "pack")
    spec_h = sspan(vm, "display||||")
    vm.regs[1] = pack_h
    vm.regs[2] = spec_h
    h(vm, "Query", "BuildLookupFilter", rd=0, rs1=1, rs2=2)
    assert span_text(vm, vm.regs[0]) == "S:display\nF:pack"

    spec2_h = sspan(vm, "src|42|dst")
    vm.regs[2] = spec2_h
    h(vm, "Query", "BuildManyToManyMap", rd=0, rs1=1, rs2=2)
    assert "W:src|==|42" in span_text(vm, vm.regs[0])

    assert vm.host._search_key("bad-pack", 7) == 7
    assert vm.host._search_terms("A,b! c") == ["a", "b", "c"]

    h(vm, "Search", "Configure", rd=0, rs1=pack_h, rs2=0)
    h(vm, "Search", "Rebuild", rd=0, rs1=0, rs2=0)
    vm.host.cur_pack = 9
    vm.regs[1] = 1
    vm.regs[2] = sspan(vm, "hello world")
    h(vm, "Search", "UpsertText", rd=0, rs1=1, rs2=2)
    vm.regs[1] = 2
    vm.regs[2] = sspan(vm, "hello semantic")
    h(vm, "Search", "UpsertText", rd=0, rs1=1, rs2=2)
    vm.regs[1] = 1
    vm.regs[2] = sspan(vm, "kind|alpha")
    h(vm, "Search", "JournalFacet", rd=0, rs1=1, rs2=2)
    vm.regs[1] = 2
    vm.regs[2] = sspan(vm, "score|oops")
    h(vm, "Search", "JournalNumber", rd=0, rs1=1, rs2=2)
    vm.regs[1] = sspan(vm, "kind")
    h(vm, "Search", "Facets", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1
    vm.regs[1] = sspan(vm, "score|x|y")
    h(vm, "Search", "Range", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1

    empty = make_vm()
    h(empty, "Search", "Load", rd=0, rs1=0, rs2=0)
    assert empty.regs[0] == 0


def test_storage_device_stream_event_and_ui_edges():
    vm = make_vm()
    h(vm, "Storage", "UsePack", rd=0, rs1=0, rs2=0)
    h(vm, "Storage", "AddCard", rd=0, rs1=0, rs2=0)
    card = vm.regs[0]
    vm.regs[1] = card
    h(vm, "Storage", "DeleteCard", rd=0, rs1=1, rs2=0)
    assert vm.host.cur_card == 0

    vm.regs[1] = sspan(vm, "name")
    vm.regs[2] = sspan(vm, "value")
    h(vm, "Storage", "SetFieldStr", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 0

    vm.host.slice_offset = 3
    vm.regs[1] = 99
    vm.regs[2] = bspan(vm, b"A")
    h(vm, "Storage", "WriteSlice", rd=0, rs1=1, rs2=2)
    assert vm.host.blob_cards[("0", 99)] == b"\x00\x00\x00A"

    dev_name = sspan(vm, "uart0")
    vm.regs[1] = dev_name
    h(vm, "Device", "Open", rd=0, rs1=1, rs2=0)
    dev = vm.regs[0]
    vm.regs[1] = dev
    h(vm, "Device", "Close", rd=0, rs1=1, rs2=0)
    assert vm.regs[0] == 1

    vm.regs[1] = dev
    vm.regs[2] = (4 << 16) | (4 << 1)  # RX, buf=4, frames=4
    h(vm, "Stream", "Open", rd=0, rs1=1, rs2=2)
    assert vm.regs[0] == 0  # closed device

    vm2 = make_vm()
    vm2.regs[1] = dev_name = sspan(vm2, "uart1")
    h(vm2, "Device", "Open", rd=0, rs1=1, rs2=0)
    dev2 = vm2.regs[0]
    vm2.regs[1] = dev2
    vm2.regs[2] = (1 | (4 << 1) | (1 << 16))  # TX, buf=4, frames=1
    h(vm2, "Stream", "Open", rd=0, rs1=1, rs2=2)
    stream = vm2.regs[0]
    vm2.regs[1] = stream
    vm2.regs[2] = 999
    h(vm2, "Stream", "Submit", rd=0, rs1=1, rs2=2)
    assert vm2.regs[0] == 0

    vm3 = make_vm()
    vm3.regs[1] = 5
    vm3.regs[2] = 9
    h(vm3, "Event", "Post", rd=0, rs1=1, rs2=2)
    ev = vm3.regs[0]
    vm3.regs[1] = ev
    h(vm3, "Event", "Data", rd=0, rs1=1, rs2=0)
    assert vm3.regs[0] == 0
    vm3.regs[2] = bspan(vm3, b"hello")
    h(vm3, "Event", "SetData", rd=0, rs1=1, rs2=2)
    assert vm3.regs[0] == 1
    h(vm3, "Event", "Data", rd=0, rs1=1, rs2=0)
    assert span_text(vm3, vm3.regs[0]) == "hello"
    vm3.regs[1] = 999
    h(vm3, "Event", "SetData", rd=0, rs1=1, rs2=2)
    assert vm3.regs[0] == 0

    vm4 = make_vm()
    title_h = sspan(vm4, "Root")
    vm4.regs[1] = title_h
    h(vm4, "Ui", "Window", rd=0, rs1=1, rs2=0)
    root = vm4.regs[0]
    for method, text in [("Panel", ""), ("Label", "L"), ("Button", "B"), ("Checkbox", "C"), ("TextBox", "T")]:
        vm4.regs[1] = root
        vm4.regs[2] = sspan(vm4, text)
        h(vm4, "Ui", method, rd=0, rs1=1, rs2=2)
    vm4.regs[1] = 999
    vm4.regs[2] = (1 << 16) | 2
    h(vm4, "Ui", "Pos", rd=0, rs1=1, rs2=2)
    assert vm4.regs[0] == 0
    for method in ("Size", "SetText", "SetId", "SetValue"):
        h(vm4, "Ui", method, rd=0, rs1=1, rs2=2)
        assert vm4.regs[0] == 0
    vm4.regs[1] = 999
    h(vm4, "Ui", "Serialize", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm4, vm4.regs[0])[:2] == b"\x00\x00"
    vm4.regs[1] = root
    h(vm4, "Ui", "Serialize", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm4, vm4.regs[0])[:2] != b"\x00\x00"


def test_process_timer_principal_error_capsule_base64_encoding_datetime_locale_and_misc():
    vm = make_vm()
    vm.regs[1] = (-7) & 0xFFFFFFFF
    with pytest.raises(Halt):
        h(vm, "Process", "Exit", rd=0, rs1=1, rs2=0)
    assert vm.host._process_table[vm.host._process_self]["exit_code"] == -7

    vm2 = make_vm()
    vm2.regs[1] = 999
    h(vm2, "Process", "Kill", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] == 0
    h(vm2, "Env", "Count", rd=0, rs1=0, rs2=0)
    vm2.regs[1] = 1
    h(vm2, "Env", "Key", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] == 0

    vm3 = make_vm()
    vm3.regs[1] = 5
    h(vm3, "Timer", "After", rd=0, rs1=1, rs2=0)
    after = vm3.regs[0]
    vm3.regs[1] = 3
    h(vm3, "Timer", "Every", rd=0, rs1=1, rs2=0)
    vm3.regs[1] = 9
    h(vm3, "Scheduler", "Tick", rd=0, rs1=1, rs2=0)
    assert vm3.regs[0] >= 3
    assert after in [vm3.host.events[e]["target"] for e in vm3.host.event_queue]

    vm4 = make_vm()
    vm4.host._principal_claims = {"a": "1"}
    h(vm4, "Principal", "Claims", rd=0, rs1=0, rs2=0)
    assert span_text(vm4, vm4.regs[0]) == "a=1"
    vm4.regs[1] = 0x10
    h(vm4, "Capability", "Drop", rd=0, rs1=1, rs2=0)
    assert vm4.regs[0] == 1
    h(vm4, "Sandbox", "Deny", rd=0, rs1=1, rs2=0)
    assert vm4.regs[0] == 1

    vm5 = make_vm()
    vm5.pc = 0
    vm5.host._error_resume_pc = 12
    h(vm5, "Error", "Resume", rd=0, rs1=0, rs2=0)
    assert vm5.pc == 12
    h(vm5, "Error", "Clear", rd=0, rs1=0, rs2=0)
    assert vm5.regs[0] == 1

    vm6 = make_vm()
    vm6.regs[1] = 1
    vm6.regs[2] = 2
    with pytest.raises(Halt):
        h(vm6, "Capsule", "Jump", rd=0, rs1=1, rs2=2)
    vm6.regs[1] = 999
    h(vm6, "Capsule", "RunModule", rd=0, rs1=1, rs2=0)
    assert vm6.regs[0] == 0

    vm7 = make_vm()
    vm7.regs[1] = sspan(vm7, "A")
    h(vm7, "Base64", "Decode", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm7, vm7.regs[0]) == b""
    vm7.regs[1] = sspan(vm7, "A")
    h(vm7, "Base64", "UrlDecode", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm7, vm7.regs[0]) == b""

    vm8 = make_vm()
    vm8.regs[1] = sspan(vm8, "abc")
    h(vm8, "Encoding", "HexDecode", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm8, vm8.regs[0]) == bytes.fromhex("0abc")
    vm8.regs[1] = sspan(vm8, "zz")
    h(vm8, "Encoding", "HexDecode", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm8, vm8.regs[0]) == b""

    vm9 = make_vm()
    h(vm9, "DateTime", "UnixTimestamp", rd=0, rs1=0, rs2=0)
    assert vm9.regs[0] > 0
    vm9.regs[1] = sspan(vm9, "")
    h(vm9, "DateTime", "Parse", rd=0, rs1=1, rs2=0)
    assert vm9.regs[0] == 0 and vm9.host.host_status == 2
    vm9.regs[1] = sspan(vm9, "2024-01-02T03:04:05")
    h(vm9, "DateTime", "Parse", rd=0, rs1=1, rs2=0)
    assert vm9.host.host_status == 0
    vm9.regs[1] = (-2147483648) & 0xFFFFFFFF
    h(vm9, "DateTime", "Format", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm9, vm9.regs[0]) == b""
    h(vm9, "DateTime", "GetDayOfWeek", rd=0, rs1=1, rs2=0)
    assert vm9.regs[0] == 0
    h(vm9, "DateTime", "GetDayOfYear", rd=0, rs1=1, rs2=0)
    assert vm9.regs[0] == 0
    h(vm9, "DateTime", "Year", rd=0, rs1=1, rs2=0)
    assert vm9.regs[0] == 0

    vm10 = make_vm()
    vm10.regs[3] = 0
    vm10.regs[1] = sspan(vm10, "en-GB@UTC")
    h(vm10, "Locale", "SetLocale", rd=0, rs1=1, rs2=3)
    assert vm10.regs[0] == 1
    vm10.regs[1] = sspan(vm10, "en-US")
    h(vm10, "Locale", "SetLocale", rd=0, rs1=1, rs2=3)
    assert vm10.regs[0] == 1
    vm10.regs[1] = sspan(vm10, "en-US@")
    h(vm10, "Locale", "SetLocale", rd=0, rs1=1, rs2=3)
    assert vm10.regs[0] == 1
    h(vm10, "Locale", "GetCurrentLocale", rd=0, rs1=0, rs2=0)
    assert "UTC" in span_text(vm10, vm10.regs[0])
    vm10.regs[1] = 12345
    vm10.regs[2] = sspan(vm10, "EUR")
    h(vm10, "Locale", "FormatCurrency", rd=0, rs1=1, rs2=2)
    assert span_text(vm10, vm10.regs[0]).startswith("EUR ")
    vm10.regs[1] = sspan(vm10, "no-such-key")
    vm10.regs[2] = 0
    h(vm10, "Locale", "Translate", rd=0, rs1=1, rs2=2)
    assert span_text(vm10, vm10.regs[0]) == "no-such-key"
    vm10.regs[1] = sspan(vm10, "en-US")
    vm10.regs[2] = sspan(vm10, "No/Such_Zone")
    h(vm10, "Locale", "SetLocale", rd=0, rs1=1, rs2=2)
    assert vm10.regs[0] == 0

    assert make_vm()._cond(999, 1, 2) is False

    vm11 = make_vm()
    vm11._noop(0, 0, 0, pvm.HOST_HOOK_BASE | 0xFF)
    assert "unknown host hook" in vm11.host.log[-1]


def test_misc_remaining_branches():
    vm = make_vm()
    vm.regs[1] = bspan(vm, b"not-pico")
    h(vm, "Compress", "PicoDecompress", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm, vm.regs[0]) == b""
    vm.regs[1] = bspan(vm, b"not-brotli")
    h(vm, "Compress", "BrotliDecompress", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm, vm.regs[0]) == b""
    assert HostApi._jsonesc(b"\n\r") == b"\\n\\r"
    assert HostApi._parsejson_to_model(b" \n\t{\"a\":\"b\"}") == b"a=b\n"

    vm2 = make_vm()
    vm2.host.cur_pack = 7
    h(vm2, "Storage", "UsePack", rd=0, rs1=0, rs2=0)
    h(vm2, "Storage", "AddCard", rd=0, rs1=0, rs2=0)
    card = vm2.regs[0]
    vm2.regs[1] = sspan(vm2, "name")
    vm2.regs[2] = sspan(vm2, "hello semantic")
    h(vm2, "Storage", "SetFieldStr", rd=0, rs1=1, rs2=2)
    vm2.regs[1] = 0
    h(vm2, "Search", "IndexPack", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] == 1
    assert "hello semantic" in vm2.host._record_text({"name": "hello semantic"})

    vm2.host.search_vector_sig = 5
    vm2.host.search_semantic_weight = 3
    vm2.host.search_docs[vm2.host._search_key("0", card)]["vector"] = 5
    vm2.regs[1] = sspan(vm2, "hello")
    h(vm2, "Search", "QueryHybrid", rd=0, rs1=1, rs2=0)
    vm2.regs[1] = 0
    h(vm2, "Search", "Plan", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] >= 1
    vm2.regs[1] = 1
    h(vm2, "Search", "Plan", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] >= 1
    vm2.regs[1] = 3
    h(vm2, "Search", "Plan", rd=0, rs1=1, rs2=0)
    assert vm2.regs[0] >= 1

    spec_h = sspan(vm2, "only-source")
    vm2.regs[1] = sspan(vm2, "pack")
    vm2.regs[2] = spec_h
    h(vm2, "Query", "BuildManyToManyMap", rd=0, rs1=1, rs2=2)
    assert "W:only-source|==|" in span_text(vm2, vm2.regs[0])

    vm3 = make_vm()
    vm3.regs[1] = 999
    h(vm3, "Timer", "Cancel", rd=0, rs1=1, rs2=0)
    assert vm3.regs[0] == 0
    vm3.host.ui_nodes[1] = {"kind": 1, "id": 0, "x": 0, "y": 0, "w": 0, "h": 0, "value": 0, "text": b"", "children": [999]}
    vm3.regs[1] = 1
    h(vm3, "Ui", "Serialize", rd=0, rs1=1, rs2=0)
    assert span_bytes(vm3, vm3.regs[0])[:2] == b"\x00\x01"

    vm4 = make_vm()
    vm4.host.install_request_context(vm4, path="/x/y")
    h(vm4, "Req", "ParamCount", rd=0, rs1=0, rs2=0)
    assert vm4.regs[0] == 2
    assert vm4.host._datetime_ext(vm4, "Missing", 0, 0, 0) is False
    assert vm4.host._req_param(vm4, "Missing", 0, 0, 0) is False
