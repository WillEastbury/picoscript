#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ui.* retained scene tree + PicoWire serialize -- Python VM == JS VM, cap-gated.

Ui.* is platform-injected (a host/PIOS client renders the wire + sources input),
so it sits outside the 5-path byte-identical gate, but the in-runtime scene tree
and serializer must behave identically on the Python and JS VMs. Crucially, the
windowing wire reuses the canonical PicoSerializer (PSC1) record format -- each
control is a PSC1 record in a pre-order node list -- so it is byte-compatible
with the card data plane, not a private format.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402
from picoserializer import deserialize_card  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

# Window "Hi" (10x20 at 0,0) with a Label "Name" and a Button "OK" (id 7).
PROG = (
    'int win = Ui.Window("Hi");\n'
    'Ui.Size(win, 655380);\n'              # (10<<16)|20
    'int lab = Ui.Label(win, "Name");\n'
    'Ui.Pos(lab, 131073);\n'               # (2<<16)|1
    'int ok = Ui.Button(win, "OK");\n'
    'Ui.SetId(ok, 7);\n'
    'Ui.Pos(ok, 327688);\n'                # (5<<16)|8
    'Io.Write(Ui.Serialize(win));\n'
)


def raw_py(words):
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    return b"".join(vm.output)


def raw_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def _parse_wire(buf):
    """Decode a PicoWire document: u16 count + that many PSC1 records (pre-order)."""
    n = int.from_bytes(buf[0:2], "big")
    pos = 2
    nodes = []
    for _ in range(n):
        # Each record is a PSC1 card; find its extent by re-encoding length.
        # deserialize_card needs the exact slice, so walk fields like the decoder.
        assert buf[pos:pos + 4] == b"PSC1", "node is not a PSC1 record"
        count = int.from_bytes(buf[pos + 4:pos + 6], "big")
        end = pos + 6
        for _f in range(count):
            nlen = buf[end]; end += 1 + nlen
            t = buf[end]; end += 1
            if t == 1:
                end += 4
            elif t == 2:
                vlen = int.from_bytes(buf[end:end + 2], "big"); end += 2 + vlen
            else:
                raise AssertionError(f"bad PSC1 type {t}")
        nodes.append(deserialize_card(buf[pos:end]))
        pos = end
    return nodes


def test_ui_tree_serialize_py_matches_expected():
    nodes = _parse_wire(raw_py(lower_to_bytecode_safe(compile_c(PROG))))
    assert len(nodes) == 3
    win, lab, ok = nodes
    assert win["c"] == 1 and win["t"] == "Hi" and win["ch"] == 2
    assert win["w"] == 10 and win["h"] == 20
    assert lab["c"] == 3 and lab["t"] == "Name" and lab["x"] == 2 and lab["y"] == 1 and lab["ch"] == 0
    assert ok["c"] == 4 and ok["t"] == "OK" and ok["id"] == 7 and ok["x"] == 5 and ok["y"] == 8


def test_ui_serialize_py_equals_js():
    words = lower_to_bytecode_safe(compile_c(PROG))
    assert raw_py(words) == raw_js(words)


def test_ui_wire_is_psc1_decodable():
    # Every node round-trips through the canonical PicoSerializer -- proving the
    # windowing wire reuses PSC1, not a private format.
    nodes = _parse_wire(raw_py(lower_to_bytecode_safe(compile_c(PROG))))
    assert all(set(n.keys()) == {"c", "ch", "h", "id", "t", "v", "w", "x", "y"} for n in nodes)


def test_ui_capability_gated():
    from picoscript_vm import CAP_ALL, CAP_UI, PicoFault
    words = lower_to_bytecode_safe(compile_c(PROG))
    host = HostApi(); vm = PicoVM(host=host, caps=CAP_ALL & ~CAP_UI); vm.load(words)
    try:
        vm.run()
        assert False, "expected a capability fault for Ui.* without CAP_UI"
    except PicoFault as e:
        assert e.code == 8, f"expected CAPABILITY fault (8), got {e.code}"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS ui: retained scene tree + PicoWire(PSC1) serialize (Python VM == JS VM, cap-gated)")


if __name__ == "__main__":
    main()
