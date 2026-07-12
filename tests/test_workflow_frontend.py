#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_workflow_frontend.py -- the workflow frontend (visual step list ->
English PicoScript -> IL). Kept byte-aligned with baremetaljstools
BareMetal.WorkflowPico and the developercli C# PicoVm oracle."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_workflow import compile_workflow, workflow_to_english, translate_expr  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(steps):
    words = lower_to_bytecode_safe(compile_workflow(steps))
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def tail(steps):
    o = run(steps)
    return o[-1] if o else None


# ── expression translation ────────────────────────────────────────────────────

def test_translate_word_operators():
    assert translate_expr("a == 1 && b != 2 || c === 3") == "a is 1 and b is not 2 or c is 3"
    assert translate_expr("a * b - c / d % e") == "a times b minus c divided by d modulo e"
    assert translate_expr("x >= 1") == "x is at least 1"
    assert translate_expr("x <= 1") == "x is at most 1"


# ── control flow + arithmetic ─────────────────────────────────────────────────

def test_loop_sum_with_conditional():
    assert tail([
        {"type": "SET", "name": "sum", "value": 0},
        {"type": "FOR", "var": "i", "from": 1, "to": 5},
        {"type": "SET", "name": "sum", "expr": "sum + i"},
        {"type": "END"},
        {"type": "IF", "condition": "sum >= 15"},
        {"type": "SET", "name": "r", "value": 7},
        {"type": "ELSE"},
        {"type": "SET", "name": "r", "value": 9},
        {"type": "END"},
        {"type": "LOG", "message": "r"},
    ]) == 7


def test_nested_loops():
    assert tail([
        {"type": "SET", "name": "c", "value": 0},
        {"type": "FOR", "var": "i", "from": 1, "to": 2},
        {"type": "FOR", "var": "j", "from": 1, "to": 3},
        {"type": "SET", "name": "c", "expr": "c + 1"},
        {"type": "END"},
        {"type": "END"},
        {"type": "LOG", "message": "c"},
    ]) == 6


# ── integer arrays + FOREACH over values ──────────────────────────────────────

def test_array_foreach_sums_values():
    assert tail([
        {"type": "SET", "name": "data", "value": [10, 20, 30, 40]},
        {"type": "SET", "name": "sum", "value": 0},
        {"type": "FOREACH", "var": "item", "in": "data"},
        {"type": "SET", "name": "sum", "expr": "sum + item"},
        {"type": "END"},
        {"type": "LOG", "message": "sum"},
    ]) == 100


def test_array_filter_inside_foreach():
    assert tail([
        {"type": "SET", "name": "data", "value": [5, 12, 8, 20, 3]},
        {"type": "SET", "name": "sum", "value": 0},
        {"type": "FOREACH", "var": "item", "in": "data"},
        {"type": "IF", "condition": "item >= 10"},
        {"type": "SET", "name": "sum", "expr": "sum + item"},
        {"type": "END"},
        {"type": "END"},
        {"type": "LOG", "message": "sum"},
    ]) == 32


def test_inline_literal_array():
    assert tail([
        {"type": "SET", "name": "s", "value": 0},
        {"type": "FOREACH", "var": "v", "in": [3, 4, 5]},
        {"type": "SET", "name": "s", "expr": "s + v"},
        {"type": "END"},
        {"type": "LOG", "message": "s"},
    ]) == 12


def test_array_materializes_into_memory():
    src, warnings = workflow_to_english([
        {"type": "SET", "name": "data", "value": [10, 20, 30]},
    ])
    assert "Memory.Set(8192, 10)." in src
    assert "Memory.Set(8194, 30)." in src
    assert warnings == []


# ── LOAD / SAVE data ABI ──────────────────────────────────────────────────────

def test_load_variable_and_memory_roundtrip():
    assert tail([
        {"type": "SET", "name": "a", "value": 41},
        {"type": "LOAD", "name": "b", "from": "variable", "key": "a + 1"},
        {"type": "SAVE", "name": "b", "to": "memory", "key": 100},
        {"type": "LOAD", "name": "c", "from": "memory", "key": 100},
        {"type": "LOG", "message": "c"},
    ]) == 42


# ── input shapes ──────────────────────────────────────────────────────────────

def test_accepts_json_string_and_steps_object():
    j = json.dumps([{"type": "SET", "name": "x", "value": 41},
                    {"type": "SET", "name": "y", "expr": "x + 1"},
                    {"type": "LOG", "message": "y"}])
    assert tail(j) == 42
    assert tail({"steps": [{"type": "SET", "name": "z", "value": 5}, {"type": "LOG", "message": "z"}]}) == 5


# ── host-only steps warn but stay compilable ──────────────────────────────────

def test_host_only_steps_warn():
    src, warnings = workflow_to_english([
        {"type": "WEB", "method": "get", "url": "/api/x", "result": "resp"},
        {"type": "LOAD", "name": "cfg", "from": "localStorage", "key": "k"},
        {"type": "CALL", "workflow": "other"},
    ])
    assert "# WEB GET /api/x -> resp" in src
    assert "# CALL other" in src
    assert len(warnings) == 3
