#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_report_model.py -- the report-model frontend (visual report designer
JSON -> English PicoScript -> IL)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_reportmodel import compile_report_model, report_model_to_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


REPORT_MODEL_JS = Path(ROOT) / "vm" / "picoreportmodel.js"


def run(model):
    words = lower_to_bytecode_safe(compile_report_model(model))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def test_detail_band_and_aggregates():
    assert run({
        "source": {"kind": "array", "values": [10, 20, 30, 40]},
        "row": "item",
        "aggregates": ["count", "sum", "min", "max"],
    }) == [10, 20, 30, 40, 4, 100, 10, 40]


def test_title_and_where_filter():
    assert run({
        "title": 999,
        "source": {"kind": "array", "values": [5, 12, 8, 20, 3]},
        "where": "item >= 10",
        "aggregates": ["count", "sum"],
    }) == [999, 12, 20, 2, 32]


def test_row_expression():
    # per-row computed column: double each value
    assert run({
        "source": {"kind": "array", "values": [1, 2, 3]},
        "row": "item * 2",
        "aggregates": ["sum"],
    }) == [2, 4, 6, 12]


def test_aggregate_order_preserved():
    # footer prints aggregates in declared order
    assert run({
        "source": {"kind": "array", "values": [4, 2, 6]},
        "aggregates": ["max", "min", "sum"],
    }) == [4, 2, 6, 6, 2, 12]


def test_accepts_json_string():
    j = json.dumps({"source": {"kind": "array", "values": [7, 7]}, "aggregates": ["count"]})
    assert run(j) == [7, 7, 2]


def test_empty_report():
    assert run({"source": {"kind": "array", "values": []}, "aggregates": ["count", "sum"]}) == [0, 0]


def test_english_shape():
    src, warnings = report_model_to_english({
        "source": {"kind": "array", "values": [1, 2]},
        "aggregates": ["sum"],
    })
    assert "Memory.Set(8192, 1)." in src
    assert "For each _r0 from 0 to 1:" in src
    assert "Print _sum." in src
    assert warnings == []


def test_browser_lowering_matches_python():
    model = {
        "title": 42,
        "source": {"kind": "array", "values": [5, 12, 8]},
        "where": "item >= 10",
        "row": "item * 2",
        "aggregates": ["count", "sum", "min", "max"],
    }
    expected_source, expected_warnings = report_model_to_english(model)
    script = (
        "const report = require(process.argv[1]);"
        "const model = JSON.parse(process.argv[2]);"
        "process.stdout.write(JSON.stringify(report.toEnglish(model)));"
    )
    completed = subprocess.run(
        ["node", "-e", script, str(REPORT_MODEL_JS), json.dumps(model)],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = json.loads(completed.stdout)
    assert actual["source"] == expected_source
    assert actual["warnings"] == expected_warnings
