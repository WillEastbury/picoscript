#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native C parity for the span/string namespaces: Python VM == portable C VM.

Brings the toC environment (vm/picovm.c) to parity with the interpreters for the
span model + Io.Write + String.* + Number.*. Builds vm/picovm_run.exe and checks
it emits byte-identical output to picoscript_vm.PicoVM. Because test_string /
test_number already prove PicoVM == the JS VM for these ops, byte-equality here
means all three runtimes (Python / JS / native C) now agree.
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
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"),
           "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def c_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def check(prog, expected, label):
    words = lower_to_bytecode_safe(compile_c(prog))
    py = b"".join(PicoVM().run(words).output)
    c = c_out(words)
    assert py == expected, f"[{label}] Python {py!r} != expected {expected!r}"
    assert c == expected, f"[{label}] C {c!r} != expected {expected!r}"
    assert py == c, f"[{label}] Py/C mismatch py={py!r} c={c!r}"


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def main():
    build_c_vm()

    # String.* core (the same program test_string uses to prove Py == JS).
    s = (setbytes(1000, b"abc") + setbytes(1003, b"bc") + setbytes(1005, b"XY") +
         "int s1 = Span.Make(1000, 3); int s2 = Span.Make(1003, 2); int s3 = Span.Make(1005, 2); int r = 0;"
         "r = String.Length(s1); Io.WriteByte(r);"
         "r = String.IndexOf(s1, s2); Io.WriteByte(r);"
         "r = String.StartsWith(s1, s2); Io.WriteByte(r);"
         "r = String.EndsWith(s1, s2); Io.WriteByte(r);"
         "r = String.Concat(s1, s2); Io.Write(r);"
         "r = String.ToUpper(s1); Io.Write(r);"
         "String.SetReplace(s3);"
         "r = String.Replace(s1, s2); Io.Write(r);")
    check(s, bytes([3, 1, 0, 1]) + b"abcbc" + b"ABC" + b"aXY", "String core")

    # Substring / ToLower / Trim.
    s2p = (setbytes(1000, b"Hello") + setbytes(1010, b"  hi \t") +
           "int h = Span.Make(1000, 5);"
           "int sub = String.Substring(h, 2); Io.Write(sub); Io.WriteByte(124);"
           "int up = String.ToUpper(h); int lo = String.ToLower(up); Io.Write(lo); Io.WriteByte(124);"
           "int w = Span.Make(1010, 6); int t = String.Trim(w); Io.Write(t);")
    check(s2p, b"llo|hello|hi", "Substring/ToLower/Trim")

    # Span.Slice / Materialize / Len / Get.
    sp = (setbytes(1000, b"abcdef") +
          "int h = Span.Make(1000, 6);"
          "int sl = Span.Slice(h, 2); Io.Write(sl); Io.WriteByte(124);"
          "int m = Span.Materialize(sl); int ml = Span.Len(m); Io.WriteByte(ml);"
          "int g0 = Span.Get(m, 0); Io.WriteByte(g0);")
    check(sp, b"cdef|" + bytes([4, 99]), "Span slice/materialize")

    # Number.* : Parse (with whitespace + negative), ToString, Abs.
    n1 = (setbytes(1000, b"  42 ") + setbytes(1020, b"-9") +
          "int psp = Span.Make(1000, 5); int p = Number.Parse(psp); Io.WriteByte(p);"
          "int ts = Number.ToString(p); Io.Write(ts); Io.WriteByte(124);"
          "int nsp = Span.Make(1020, 2); int n9 = Number.Parse(nsp); int a9 = Number.Abs(n9); Io.WriteByte(a9);")
    check(n1, bytes([42]) + b"42|" + bytes([9]), "Number parse/tostring/abs")

    # Number.ToHex / ToOctal / ToBinary of 255.
    n2 = (setbytes(1010, b"255") +
          "int qsp = Span.Make(1010, 3); int q = Number.Parse(qsp);"
          "int hx = Number.ToHex(q); Io.Write(hx); Io.WriteByte(124);"
          "int oc = Number.ToOctal(q); Io.Write(oc); Io.WriteByte(124);"
          "int bn = Number.ToBinary(q); Io.Write(bn);")
    check(n2, b"ff|377|11111111", "Number radix")

    # Number.Min / Max.
    n3 = "int x3 = 3; int y9 = 9; int mn = Number.Min(x3, y9); int mx = Number.Max(x3, y9); Io.WriteByte(mn); Io.WriteByte(mx);"
    check(n3, bytes([3, 9]), "Number min/max")

    print("PASS native span/String/Number: Python VM == portable C VM (vm/picovm.c) byte-exact "
          "-- spans, Io.Write, String.* (11 ops), Number.* (Parse/ToString/ToHex/Octal/Binary/Abs/Min/Max)")


if __name__ == "__main__":
    main()
