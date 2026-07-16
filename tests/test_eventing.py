#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_eventing.py -- real ON block event dispatch.

Verifies the eventing mechanism built on OnBlock (see docs/EVENTING.md),
replacing the dead-code lowering found during the eventing investigation
(a labelled subroutine nothing ever called, followed by a bogus
`host(event_ns, "Register", (), None)` -- no runtime "Register" handling
exists anywhere).

`ON Ns.Method: body END ON` now lowers to an inline drain-and-dispatch loop
over the (already real, already working) Event.* FIFO queue, matching each
pending event's Event.Type() against a compile-time FNV-1a hash of
"NS.METHOD" (picoscript_basic.event_type_hash) -- the exact same hash
algorithm picoscript_vm.py's Map.Hash already uses at runtime, just computed
once at compile time and baked in as a plain bytecode constant (so there's no
runtime string hashing and zero cross-VM parity risk).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import (  # noqa: E402
    compile_basic, event_type_hash, Lowerer, OnBlock, Let, Bin, Var, Num,
    Print, Call, CallStmt,
)
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def test_event_type_hash_is_deterministic_and_case_insensitive():
    assert event_type_hash("Ui", "Click") == event_type_hash("UI", "CLICK")
    assert event_type_hash("Ui", "Click") == event_type_hash("ui", "click")
    assert event_type_hash("Ui", "Click") != event_type_hash("Ui", "Hover")
    # Matches picoscript_vm.py's Map.Hash fnv1a exactly (same algorithm,
    # spot-checked against the well-known FNV-1a offset basis/prime).
    assert 0 <= event_type_hash("Ui", "Click") <= 0xFFFFFFFF


def test_on_block_dispatches_matching_event_via_basic_source():
    type_code = event_type_hash("Ui", "Click")
    src = f"""
LET hits = 0
LET target = 0
EVENT POST {type_code} 5
EVENT POST 999 9
ON Ui.Click
    LET hits = hits + 1
    LET target = EVENT TARGET __event__
END ON
PRINT hits
PRINT target
"""
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    assert _out(vm) == [1, 5]  # only the matching (type_code) event fires


def test_on_block_never_fires_when_no_matching_event_posted():
    src = """
LET hits = 0
EVENT POST 999 9
ON Ui.Click
    LET hits = hits + 1
END ON
PRINT hits
"""
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    assert _out(vm) == [0]


def test_on_block_via_direct_ast_multiple_matching_events():
    """Direct AST construction (bypassing BASIC's EVENT-keyword sugar) to
    prove the *lowering* handles multiple matching events in one drain pass,
    not just the single-event happy path."""
    type_code = event_type_hash("Ui", "Click")
    lowerer = Lowerer()
    prog = [
        Let("hits", Num(0)),
        Let("total", Num(0)),
        CallStmt(Call("Event", "Post", [Num(type_code), Num(5)])),
        CallStmt(Call("Event", "Post", [Num(type_code), Num(7)])),
        CallStmt(Call("Event", "Post", [Num(999), Num(1)])),  # non-matching
        OnBlock("Ui", "Click", [
            Let("hits", Bin("+", Var("hits"), Num(1))),
            Let("total", Bin("+", Var("total"),
                              Call("Event", "Target", [Var("__event__")]))),
        ]),
        Print(Var("hits")),
        Print(Var("total")),
    ]
    il = lowerer.lower_program(prog)
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    assert _out(vm) == [2, 12]  # 2 matching events; targets 5 + 7 = 12


def test_event_raise_sugar_computes_matching_hash_and_triggers_on_block():
    """`EVENT RAISE Ns.Method target` (added alongside this test) is ergonomic
    sugar over `EVENT POST event_type_hash(ns, method) target` -- closes the
    gap docs/EVENTING.md flagged (previously an author had to compute/type
    the hash by hand to raise something an ON block would catch)."""
    src = """
LET hits = 0
LET target = 0
EVENT RAISE Ui.Click 5
EVENT RAISE Ui.Hover 9
ON Ui.Click
    LET hits = hits + 1
    LET target = EVENT TARGET __event__
END ON
PRINT hits
PRINT target
"""
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    assert _out(vm) == [1, 5]


def test_event_raise_sugar_is_byte_identical_to_manual_post():
    """`EVENT RAISE Ui.Click 5` must compile to the exact same bytecode as
    `EVENT POST <precomputed hash> 5` -- it's pure sugar, not a different
    code path."""
    hashed = event_type_hash("Ui", "Click")
    raise_words = lower_to_bytecode_safe(compile_basic("EVENT RAISE Ui.Click 5"))
    post_words = lower_to_bytecode_safe(compile_basic(f"EVENT POST {hashed} 5"))
    assert raise_words == post_words


def _node_available():
    import subprocess
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def test_event_raise_sugar_matches_js_port_byte_identical():
    """vm/picoc.js's EVENT RAISE (added alongside the Python side) must
    compute the exact same eventTypeHash and therefore emit byte-identical
    bytecode for the same source."""
    import json
    import subprocess
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    src = "DIM h = 0\nEVENT RAISE Ui.Click 5\nPRINT h"
    py_words = lower_to_bytecode_safe(compile_basic(src))
    script = f"""
    var P = require('./vm/picoc.js');
    var fs = require('fs'); eval(fs.readFileSync('./vm/pico_hooks.js','utf8'));
    var r = P.compileBasic({json.dumps(src)});
    console.log(JSON.stringify(r.words));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    js_words = json.loads(r.stdout.strip())
    assert js_words == py_words
