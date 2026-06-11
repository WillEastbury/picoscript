#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""First-class toC parity: Python VM == C interpreter == toC-compiled native.

The span/string namespaces lower to first-class native C (a direct pv_host2 call,
no bytecode VM, no string-keyed dispatch). For each construct this builds THREE
runtimes from one source -- the Python reference VM, the portable C interpreter
(vm/picovm_run.exe running bytecode), and a standalone native binary emitted by
lower_to_c (emit_main) and compiled with the toolchain -- and asserts all three
emit byte-identical output plus the known answer.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_toc")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def parse_out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    py = b"".join(PicoVM().run(lower_to_bytecode_safe(compile_c(prog))).output)
    ci = c_interp_out(lower_to_bytecode_safe(compile_c(prog)))
    cn = c_native_out(compile_c(prog), slot)
    assert py == expected, f"[{slot}] Python {py!r} != {expected!r}"
    assert ci == expected, f"[{slot}] C-interp {ci!r} != {expected!r}"
    assert cn == expected, f"[{slot}] toC-native {cn!r} != {expected!r}"


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    try:
        # String.* core.
        s = (setbytes(1000, b"abc") + setbytes(1003, b"bc") + setbytes(1005, b"XY") +
             "int s1 = Span.Make(1000, 3); int s2 = Span.Make(1003, 2); int s3 = Span.Make(1005, 2); int r = 0;"
             "r = String.Length(s1); Io.WriteByte(r);"
             "r = String.IndexOf(s1, s2); Io.WriteByte(r);"
             "r = String.Concat(s1, s2); Io.Write(r);"
             "r = String.ToUpper(s1); Io.Write(r);"
             "String.SetReplace(s3);"
             "r = String.Replace(s1, s2); Io.Write(r);")
        check(s, bytes([3, 1]) + b"abcbc" + b"ABC" + b"aXY", "string_core")

        # Substring / ToLower / Trim.
        s2p = (setbytes(1000, b"Hello") + setbytes(1010, b"  hi \t") +
               "int h = Span.Make(1000, 5);"
               "int sub = String.Substring(h, 2); Io.Write(sub); Io.WriteByte(124);"
               "int up = String.ToUpper(h); int lo = String.ToLower(up); Io.Write(lo); Io.WriteByte(124);"
               "int w = Span.Make(1010, 6); int t = String.Trim(w); Io.Write(t);")
        check(s2p, b"llo|hello|hi", "substr_trim")

        # Span.Slice / Materialize / Len / Get.
        sp = (setbytes(1000, b"abcdef") +
              "int h = Span.Make(1000, 6);"
              "int sl = Span.Slice(h, 2); Io.Write(sl); Io.WriteByte(124);"
              "int m = Span.Materialize(sl); int ml = Span.Len(m); Io.WriteByte(ml);"
              "int g0 = Span.Get(m, 0); Io.WriteByte(g0);")
        check(sp, b"cdef|" + bytes([4, 99]), "span_ops")

        # Number.* : Parse / ToString / ToHex / Abs.
        nm = (setbytes(1000, b"255") + setbytes(1020, b"-9") +
              "int qsp = Span.Make(1000, 3); int q = Number.Parse(qsp); Io.WriteByte(Number.Abs(q));"
              "int hx = Number.ToHex(q); Io.Write(hx); Io.WriteByte(124);"
              "int ts = Number.ToString(q); Io.Write(ts);")
        check(nm, bytes([255]) + b"ff|255", "number")

        # Maths.Power / Sqrt.
        ma = ("int b = 2; int e = 10; int p = Maths.Power(b, e); Io.WriteByte(Number.Abs(0));"
              "int n = 144; int s = Maths.Sqrt(n); Io.WriteByte(s);"
              "int ts = Number.ToString(p); Io.Write(ts);")
        # 2^10 = 1024 -> ToString "1024"; sqrt(144)=12
        check(ma, bytes([0, 12]) + b"1024", "maths")

        # Compress (RLE round-trip) + Html (entities, incl. tricky &amp;lt;).
        cz = (setbytes(1000, b"aaabbbbc") +
              "int s = Span.Make(1000, 8);"
              "int c = Compress.PicoCompress(s); Io.WriteByte(String.Length(c));"
              "int d = Compress.PicoDecompress(c); Io.Write(d);")
        check(cz, bytes([6]) + b"aaabbbbc", "compress")

        hz = (setbytes(1000, b"<b>&'\"") + setbytes(1100, b"&amp;lt;") +
              "int s = Span.Make(1000, 6);"
              "int e = Html.Encode(s); Io.Write(e); Io.WriteByte(124);"
              "int x = Span.Make(1100, 8); int d = Html.Decode(x); Io.Write(d);")
        check(hz, b"&lt;b&gt;&amp;&#39;&quot;|&lt;", "html")

        # Http.ParseQuery (url-decode -> model).
        qsrc = b"q=hello+world&x=%41"
        hq = (setbytes(1000, qsrc) +
              f"int s = Span.Make(1000, {len(qsrc)}); int m = Http.ParseQuery(s); Io.Write(m);")
        check(hq, b"q=hello world\nx=A\n", "http_query")

        # Http.EncodeJson (model -> JSON).
        ejsrc = b"name=Bob\nrole=admin"
        ej = (setbytes(1000, ejsrc) +
              f"int s = Span.Make(1000, {len(ejsrc)}); int j = Http.EncodeJson(s); Io.Write(j);")
        check(ej, b'{"name":"Bob","role":"admin"}', "http_encodejson")

        # Http.ParseJson (nested JSON -> dotted {{#each}} model).
        pjsrc = b'{"items":[{"name":"A"},{"name":"B"}]}'
        pj = (setbytes(1000, pjsrc) +
              f"int s = Span.Make(1000, {len(pjsrc)}); int m = Http.ParseJson(s); Io.Write(m);")
        check(pj, b"items.0.name=A\nitems.1.name=B\n", "http_parsejson")

        print("PASS first-class toC: Python VM == C interpreter == toC-compiled native, byte-exact "
              "-- Span/String/Number/Maths/Compress/Html/Http (compiled programs skip the bytecode VM)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
