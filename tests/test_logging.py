#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_logging.py -- the real Log.* tracing/audit subsystem.

Answers "do we have a decent logging / tracing / auditing subsystem?" --
verified (via a background investigation) that the answer was previously
NO: only scattered, Python/JS-internal debug logs (never exposed to
scripts) and unimplemented `Kernel.ProfileStart/ProfileEnd/TracePoint`
stubs (mapped to OP_NOOP, no actual recording anywhere). See
docs/LOGGING.md for the full writeup.

`Log.*` is a genuine, script-visible, deterministic, append-only
structured log: `Log.Write(level, messageSpan) -> id` (sequential from 1),
`Log.Count()`, `Log.Level(id)`, `Log.Message(id)`, `Log.Clear()`. No
wall-clock timestamp -- entries are ordered by sequence id, matching this
VM's established convention that non-deterministic state (clocks, entropy)
must be host-injected, not a core VM primitive (docs/NAMESPACE_STATUS.md).
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def _run(src: str):
    words = lower_to_bytecode_safe(compile_basic(src))
    return _out(PicoVM().run(words)), words


def test_log_write_returns_sequential_ids_and_count_tracks_entries():
    out, _ = _run(
        "DIM a = Log.Write(1, \"first\")\n"
        "DIM b = Log.Write(2, \"second\")\n"
        "DIM c = Log.Count()\n"
        "PRINT a\nPRINT b\nPRINT c\n"
    )
    assert out == [1, 2, 2]


def test_log_level_and_message_are_readable_back():
    out, _ = _run(
        "DIM id = Log.Write(3, \"boom\")\n"
        "DIM lvl = Log.Level(id)\n"
        "DIM msg = Log.Message(id)\n"
        "Io.Write(msg)\n"
        "PRINT lvl\n"
    )
    # Io.Write(msg) emits the span's bytes ("boom") to output before the
    # PRINT'd level; just check the level round-tripped correctly (the byte
    # stream also contains "boom" but PRINT's int framing is what _out parses).
    assert out[-1] == 3


def test_log_message_span_content_is_correct():
    words = lower_to_bytecode_safe(compile_basic(
        "DIM id = Log.Write(1, \"hello-log\")\n"
        "DIM msg = Log.Message(id)\n"
        "Io.Write(msg)\n"
    ))
    vm = PicoVM().run(words)
    assert vm.output_text() == "hello-log"


def test_log_unknown_id_reads_back_zero():
    out, _ = _run(
        "DIM lvl = Log.Level(9999)\n"
        "DIM msg = Log.Message(9999)\n"
        "PRINT lvl\nPRINT msg\n"
    )
    assert out == [0, 0]


def test_log_clear_empties_the_table():
    out, _ = _run(
        "DIM a = Log.Write(1, \"x\")\n"
        "DIM b = Log.Write(2, \"y\")\n"
        "DIM ok = Log.Clear()\n"
        "DIM c = Log.Count()\n"
        "PRINT ok\nPRINT c\n"
    )
    assert out == [1, 0]


def test_log_is_deterministic_no_wallclock_state():
    """Two independent runs of the same program must produce byte-identical
    output -- Log.* must never depend on wall-clock time or any other
    non-deterministic host state, consistent with this VM's core guarantee."""
    src = "DIM a = Log.Write(5, \"x\")\nDIM b = Log.Write(6, \"y\")\nPRINT a\nPRINT b\n"
    out1, words1 = _run(src)
    out2, words2 = _run(src)
    assert out1 == out2 == [1, 2]
    assert words1 == words2


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_log_matches_js_bytecode_vm_byte_identical():
    """vm/picovm.js's Log.* implementation (added alongside the Python side)
    must execute the SAME bytecode and produce identical results -- proves
    real Python/JS parity, not just that it doesn't crash."""
    src = (
        "DIM a = Log.Write(1, \"hi\")\n"
        "DIM b = Log.Write(2, \"lo\")\n"
        "DIM cnt = Log.Count()\n"
        "DIM lvl = Log.Level(a)\n"
        "PRINT cnt\nPRINT lvl\n"
    )
    words = lower_to_bytecode_safe(compile_basic(src))
    script = f"""
    var VM = require('./vm/picovm.js');
    var hooks = require('./vm/pico_hooks.js');
    var vm = new VM({{hooks: hooks}});
    var words = {json.dumps(words)};
    vm.run(words);
    var out = [];
    for (var i = 0; i < vm.output.length; i += 4) {{
      var v = (vm.output[i]<<24 | vm.output[i+1]<<16 | vm.output[i+2]<<8 | vm.output[i+3]) >>> 0;
      out.push(v | 0);
    }}
    console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                        cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    js_out = json.loads(r.stdout.strip())
    py_out, _ = _run(src)
    assert js_out == py_out == [2, 1]
