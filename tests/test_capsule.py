#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capsule manifest contract tests (docs/PIOS_CAPSULE_HANDOFF.md section 3).

Validates: the builder reproduces the frozen doc example byte-for-byte;
serialize() is deterministic; parse(serialize(m)) round-trips; pack/card
address parsing/formatting (canonical + typed); and the source/bytecode pairing.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from picocapsule import (  # noqa: E402
    Manifest, serialize, parse, format_address, parse_address,
    source_for, code_for, is_capsule_pack,
)

# The exact canonical manifest from docs/PIOS_CAPSULE_HANDOFF.md section 3.
DOC_EXAMPLE = (
    "capsule = on\n"
    "name = demo\n"
    "principal = app-user\n"
    "mem_kib = 4096\n"
    "cpu_ms = 1000\n"
    "fs = /var/picowal/p1024\n"
    "cards = 1001-20000\n"
    "\n"
    "process = web\n"
    "  source = 1001\n"
    "  bytecode = 10001\n"
    "  io = tcp/83\n"
    "  entry = http\n"
    "\n"
    "process = api\n"
    "  source = 1002\n"
    "  bytecode = 10002\n"
    "  io = tcp/84\n"
    "  entry = http\n"
    "\n"
    "ipc_fifo = requests\n"
    "  from = web\n"
    "  to = api\n"
    "  depth = 64\n"
    "  frame_max = 1024\n"
)


def build_demo() -> Manifest:
    m = Manifest(name="demo", cards="1001-20000", principal="app-user",
                 mem_kib=4096, cpu_ms=1000, fs="/var/picowal/p1024")
    m.process("web", source_for(1), code_for(1), entry="http").bind_tcp(83, "web")
    m.process("api", source_for(2), code_for(2), entry="http").bind_tcp(84, "api")
    m.fifo("requests", "web", "api", 64, 1024)
    return m


def test_builder_matches_doc_example():
    assert serialize(build_demo()) == DOC_EXAMPLE, "builder output drifted from the frozen doc example"


def test_serialize_is_deterministic():
    a, b = serialize(build_demo()), serialize(build_demo())
    assert a == b


def test_round_trip():
    m = parse(DOC_EXAMPLE)
    assert m.name == "demo" and m.mem_kib == 4096 and m.cpu_ms == 1000
    assert m.fs == "/var/picowal/p1024" and m.cards == "1001-20000"
    assert [p.name for p in m.processes] == ["web", "api"]
    assert m.processes[0].source == 1001 and m.processes[0].bytecode == 10001
    assert m.processes[1].io == "tcp/84"
    assert m.fifos[0].name == "requests" and m.fifos[0].depth == 64 and m.fifos[0].frame_max == 1024
    assert serialize(m) == DOC_EXAMPLE, "parse->serialize is not the identity"


def test_pairing_helpers():
    assert source_for(1) == 1001 and code_for(1) == 10001
    assert source_for(2) == 1002 and code_for(2) == 10002


def test_addresses():
    assert format_address(1024, 10001) == "1024/10001"
    assert parse_address("1024/10001") == (1024, 10001)
    assert parse_address("capsule:1024/card:10001") == (1024, 10001)
    assert is_capsule_pack(1024) and is_capsule_pack(4095)
    assert not is_capsule_pack(1023) and not is_capsule_pack(4096)


def test_minimal_manifest():
    m = Manifest(name="tiny")
    m.process("only", source_for(1), code_for(1))
    text = serialize(m)
    assert "principal" not in text and "mem_kib" not in text  # optionals omitted
    assert serialize(parse(text)) == text


def test_js_mirror_matches_python_and_doc():
    # vm/picocapsule.js must emit byte-identical canonical text to picocapsule.py
    # (so capsule card 0 is identical whoever authored it) and round-trip in JS.
    r = subprocess.run(["node", os.path.join(ROOT, "vm", "picocapsule_check.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout == DOC_EXAMPLE, "vm/picocapsule.js serialize drifted from picocapsule.py / doc"


def test_debug_map_roundtrip_and_js_parity():
    # INV-25 debug map (pc -> off,op,ns,method) serialises deterministically for a
    # capsule companion card: round-trips, and vm/picocapsule.js emits identical text.
    import json
    from picoscript_il import lower_to_bytecode_with_debug
    from picoscript_cfront import compile_c
    from picocapsule import serialize_debug_map, parse_debug_map
    _, dbg = lower_to_bytecode_with_debug(compile_c('int a = 1;\nGpio.Write(2, 1024);\nprint(a);\n'))
    text = serialize_debug_map(dbg)
    assert parse_debug_map(text) == dbg
    assert serialize_debug_map(parse_debug_map(text)) == text
    js_in = {str(pc): list(rec) for pc, rec in dbg.items()}
    node = "var C=require('./vm/picocapsule.js');process.stdout.write(C.serializeDebugMap(%s));" % json.dumps(js_in)
    r = subprocess.run(["node", "-e", node], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert r.stdout == text


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS capsule manifest contract: builder==doc, deterministic, round-trips, addresses")


if __name__ == "__main__":
    main()
