#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Event.* reactive event queue -- Python VM == JS VM, capability-gated.

Event.* is platform-injected (PIOS supplies real timer/IRQ/UI sources), so it
sits outside the 5-path byte-identical gate, but the in-runtime reference queue
must behave identically on the Python and JS VMs. A program posts/pulls events
(FIFO) and reads each event's type/target/data, mirroring the Stream.Next lease
pattern.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

PROG = """
int e1 = Event.Post(10, 100);
int e2 = Event.Post(20, 200);
int n = Event.Count();
int a = Event.Next();
int b = Event.Next();
int c = Event.Next();
print(n);
print(Event.Type(a)); print(Event.Target(a));
print(Event.Type(b)); print(Event.Target(b));
print(c);
print(Event.Count());
"""

EXPECTED = [2, 10, 100, 20, 200, 0, 0]

DATA_PROG = (
    'int e = Event.Post(1, 2);\n'
    'Event.SetData(e, "payload");\n'
    'int x = Event.Next();\n'
    'Io.Write(Event.Data(x));\n'
)


def _ints(b):
    out = []
    for i in range(0, len(b) - 3, 4):
        v = int.from_bytes(b[i:i + 4], "big")
        out.append(v - 0x100000000 if v & 0x80000000 else v)
    return out


def out_py(words):
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    return _ints(b"".join(vm.output))


def raw_py(words):
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    return b"".join(vm.output)


def _js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_event_queue_py_matches_expected():
    assert out_py(lower_to_bytecode_safe(compile_c(PROG))) == EXPECTED


def test_event_queue_py_equals_js():
    words = lower_to_bytecode_safe(compile_c(PROG))
    assert raw_py(words) == _js(words)


def test_event_data_roundtrip_py_equals_js():
    words = lower_to_bytecode_safe(compile_c(DATA_PROG))
    py = raw_py(words)
    assert py == b"payload"
    assert py == _js(words)


def test_event_capability_gated():
    # Event.* needs CAP_EVENT; deny it and the hook must fault (INV-17), not run.
    from picoscript_vm import CAP_ALL, CAP_EVENT, PicoFault
    words = lower_to_bytecode_safe(compile_c(PROG))
    host = HostApi(); vm = PicoVM(host=host, caps=CAP_ALL & ~CAP_EVENT); vm.load(words)
    try:
        vm.run()
        assert False, "expected a capability fault for Event.* without CAP_EVENT"
    except PicoFault as e:
        assert e.code == 8, f"expected CAPABILITY fault (8), got {e.code}"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS events: reactive queue post/next/type/target/data (Python VM == JS VM, cap-gated)")


if __name__ == "__main__":
    main()
