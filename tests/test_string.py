#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""String.* arena string library: Python VM == JS VM, byte-exact.

Exercises Length / IndexOf / StartsWith / Concat / ToUpper / Replace (with
SetReplace) on arena spans and checks the Python interpreter and the JS
interpreter (vm/picovm.js) produce identical output bytes.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

PROG = """
Memory.Set(1000, 97); Memory.Set(1001, 98); Memory.Set(1002, 99);   // "abc"
Memory.Set(1003, 98); Memory.Set(1004, 99);                          // "bc"
Memory.Set(1005, 88); Memory.Set(1006, 89);                          // "XY"
int s1 = Span.Make(1000, 3);
int s2 = Span.Make(1003, 2);
int s3 = Span.Make(1005, 2);
int r = 0;
r = String.Length(s1);        Io.WriteByte(r);    // 3
r = String.IndexOf(s1, s2);   Io.WriteByte(r);    // 1
r = String.StartsWith(s1, s2); Io.WriteByte(r);   // 0
r = String.EndsWith(s1, s2);  Io.WriteByte(r);    // 1
r = String.Concat(s1, s2);    Io.Write(r);        // "abcbc"
r = String.ToUpper(s1);       Io.Write(r);        // "ABC"
String.SetReplace(s3);
r = String.Replace(s1, s2);   Io.Write(r);        // "abc".replace("bc","XY") = "aXY"
"""

EXPECTED = [3, 1, 0, 1] + [97, 98, 99, 98, 99] + [65, 66, 67] + [97, 88, 89]


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    runner = os.path.join(VM_DIR, "picovm_run.js")
    out = subprocess.run(["node", runner], input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return [int(x, 16) for x in p[1:]]
    return []


def main():
    words = lower_to_bytecode_safe(compile_c(PROG))
    vm = PicoVM().run(words)
    py_out = list(b"".join(vm.output))
    js_out = run_js_vm(words)
    assert py_out == EXPECTED, f"Python String.* output {py_out} != expected {EXPECTED}"
    assert js_out == EXPECTED, f"JS String.* output {js_out} != expected {EXPECTED}"
    assert py_out == js_out, f"String.* parity: py={py_out} js={js_out}"
    print(f"PASS String.*: Python VM == JS VM byte-exact ({len(py_out)} bytes over "
          f"Length/IndexOf/StartsWith/EndsWith/Concat/ToUpper/Replace)")


if __name__ == "__main__":
    main()
