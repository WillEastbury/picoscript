#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reference GPIO emulator (Gpio.*) -- Python VM == JS VM.

GPIO is platform-injected: PIOS supplies the real per-pin driver, so it sits
outside the 5-path byte-identical gate. But the browser/sim reference emulator
must behave identically on the Python and JS VMs. Pins carry an analog value in
[0,1024] (writes saturate); dir 0=in/1=out; pull 0=none/1=up/2=down.
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
Gpio.SetDir(2, 1);
Gpio.Write(2, 1024);
int a = Gpio.Read(2);
Gpio.Write(2, 5000);
int b = Gpio.Read(2);
Gpio.Write(2, -7);
int c = Gpio.Read(2);
Gpio.SetPull(3, 1);
int d = Gpio.GetPull(3);
Gpio.SetPull(4, 9);
int e = Gpio.GetPull(4);
int f = Gpio.GetDir(2);
int g = Gpio.GetDir(99);
int n = Gpio.Count();
print(a); print(b); print(c); print(d); print(e); print(f); print(g); print(n);
"""

EXPECTED = [1024, 1024, 0, 1, 0, 1, 0, 40]


def _ints(b):
    out = []
    for i in range(0, len(b) - 3, 4):
        v = int.from_bytes(b[i:i + 4], "big")
        out.append(v - 0x100000000 if v & 0x80000000 else v)
    return out


def out_py(words):
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    return _ints(b"".join(vm.output))


def out_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return _ints(bytes(int(x, 16) for x in p[1:]))
    return []


def test_gpio_emulator_py_matches_expected():
    assert out_py(lower_to_bytecode_safe(compile_c(PROG))) == EXPECTED


def test_gpio_emulator_py_equals_js():
    words = lower_to_bytecode_safe(compile_c(PROG))
    assert out_py(words) == out_js(words)


def _raw_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


SCHEMA_PROG = (
    'int schema = "id:int;qty:int";\n'
    'Storage.SetSchemaForPack(1024, schema);\n'
    'int got = Storage.GetSchemaForPack(1024);\n'
    'Io.Write(got);\n'
)


def test_schema_hooks_roundtrip_py_equals_js():
    # Storage.SetSchemaForPack/GetSchemaForPack store + return the schema span
    # bytes per pack (were NOOP). Round-trips exactly and identically Python<->JS.
    words = lower_to_bytecode_safe(compile_c(SCHEMA_PROG))
    host = HostApi(); vm = PicoVM(host=host); vm.load(words); vm.run()
    py = b"".join(vm.output)
    assert py == _raw_js(words)
    assert py == b"id:int;qty:int"


# RX DMA ring: device "csi0", buf=4, frames=3 -> cfg=(4<<1)|(3<<16)=196616.
# Frame n byte i=(n+i)&0xFF, so sums 6+10+14 = 30 over the three frames.
STREAM_PROG = (
    'int dev = Device.Open("csi0", 0);\n'
    'int s = Stream.Open(dev, 196616);\n'
    'int total = 0;\n'
    'int l = Stream.Next(s);\n'
    'while (l != 0) {\n'
    '  int sp = Stream.Span(l);\n'
    '  int n = Span.Len(sp);\n'
    '  for (i = 0; i < n; i = i + 1) { total = total + Span.Get(sp, i); }\n'
    '  Stream.Release(l);\n'
    '  l = Stream.Next(s);\n'
    '}\n'
    'Stream.Close(s);\n'
    'Device.Close(dev);\n'
    'print(total);\n'
)


def test_stream_ring_emulator_py_matches_expected():
    words = lower_to_bytecode_safe(compile_c(STREAM_PROG))
    assert out_py(words) == [30]


def test_stream_ring_emulator_py_equals_js():
    words = lower_to_bytecode_safe(compile_c(STREAM_PROG))
    assert out_py(words) == out_js(words)


def test_stream_capability_gated():
    # Stream.* needs CAP_DMA; deny it and the hook must fault (INV-17), not run.
    from picoscript_vm import CAP_ALL, CAP_DMA, PicoFault
    words = lower_to_bytecode_safe(compile_c(STREAM_PROG))
    host = HostApi(); vm = PicoVM(host=host, caps=CAP_ALL & ~CAP_DMA); vm.load(words)
    try:
        vm.run()
        assert False, "expected a capability fault for Stream.* without CAP_DMA"
    except PicoFault as e:
        assert e.code == 8, f"expected CAPABILITY fault (8), got {e.code}"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS devices: GPIO emulator + schema hooks + Stream DMA-ring (Python VM == JS VM, cap-gated)")


if __name__ == "__main__":
    main()
