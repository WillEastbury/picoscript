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


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS GPIO emulator: Python VM == JS VM, values/clamping/defaults as expected")


if __name__ == "__main__":
    main()
