#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_layout_engine.py -- the templated layout engine (reports + forms).
Stage 2 of the 2-stage report/form model: renders a data-producer program's
output with a layout template."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picolayout as L  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _prog_output(src):
    vm = PicoVM().run(lower_to_bytecode_safe(compile_english(src)))
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


TMPL = {
    "title": "Orders",
    "columns": [{"label": "Qty", "field": 0, "width": 5},
                {"label": "Price", "field": 1, "width": 6}],
    "aggregates": [{"column": 0, "fn": "sum"}, {"column": 1, "fn": "max"}],
}


def test_render_text_report():
    out = L.render_text([2, 10, 3, 20, 1, 50], TMPL)
    lines = out.splitlines()
    assert lines[0] == "Orders"
    assert lines[1].startswith("Qty")
    assert "2" in lines[3] and "10" in lines[3]
    assert "sum=6" in lines[-1] and "max=50" in lines[-1]


def test_render_html_report_table():
    out = L.render_html([1, 2, 3], {"columns": [{"label": "V", "field": 0}],
                                    "aggregates": [{"column": 0, "fn": "sum"}]}, "report")
    assert '<table class="pico-report">' in out
    assert "<th>V</th>" in out
    assert "<td>1</td>" in out and "<td>3</td>" in out
    assert "sum=6" in out


def test_render_form_has_editable_inputs():
    out = L.render_html([2, 10], {"columns": [{"label": "Qty", "field": 0},
                                              {"label": "Price", "field": 1, "editable": False}]}, "form")
    assert '<form class="pico-form">' in out
    assert 'data-field="0"' in out and '<input' in out          # editable field -> input
    assert '<output data-field="1"' in out                       # non-editable -> output


def test_aggregate_functions():
    rows = [4, 2, 6, 8]  # single column
    t = {"columns": [{"label": "N", "field": 0}]}
    def agg(fn):
        return L.render_text(rows, dict(t, aggregates=[{"column": 0, "fn": fn}])).splitlines()[-1]
    assert agg("count") == "count=4"
    assert agg("sum") == "sum=20"
    assert agg("min") == "min=2"
    assert agg("max") == "max=8"
    assert agg("avg") == "avg=5"


def test_hex_format():
    out = L.render_text([255], {"columns": [{"label": "H", "field": 0, "format": "hex", "width": 6}]})
    assert "0xff" in out


def test_two_stage_pipeline():
    # stage 1: a PicoScript program emits the data; stage 2: render it
    data = _prog_output("Print 2.\nPrint 10.\nPrint 3.\nPrint 20.\n")
    assert data == [2, 10, 3, 20]
    out = L.render_text(data, TMPL)
    assert "sum=5" in out.splitlines()[-1]   # 2 + 3
    assert "max=20" in out.splitlines()[-1]


def test_form_writeback_roundtrips_through_memory():
    # Form (read-write): edit a value, write it back to Memory, and a program
    # reads the edited value back via Memory.Get -- the full read-write loop.
    rows = [[2, 99], [3, 20]]   # as if collected from an edited form (row0 field1 -> 99)
    assert L.flatten(rows) == [2, 99, 3, 20]
    writes = L.to_writes(rows, base=8192)     # {8192:2, 8193:99, 8194:3, 8195:20}
    assert writes[8193] == 99
    # A stage-1 program seeds Memory from the write map, then reads a field back.
    seed = "\n".join("Memory.Set(%d, %d)." % (k, v) for k, v in sorted(writes.items()))
    prog = seed + "\nPrint Memory.Get(8193).\n"
    assert _prog_output(prog)[-1] == 99          # edited value survives the round-trip
