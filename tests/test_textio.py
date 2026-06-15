#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utf8Writer / Utf8Reader / Json / Xml native in the C runtime -> full namespace
parity. These arena-backed text/binary builders ran in the Python + JS interpreters
but not the C runtime (so not in toC); toJS already had them via picovm.js. Now they
run byte-identically on all five runtimes -- the last pure-namespace gap closed.
(Req/Resp/Storage remain host-injected by design and are not portable primitives.)
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_textio")


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
    return parse_out_bytes(subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout)


def js_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    return parse_out_bytes(subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                                          input=inp, capture_output=True, text=True).stdout)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c"); exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
                        f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js"); runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js'); const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2,'0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    words = lower_to_bytecode_safe(compile_c(prog))
    runs = {
        "Python VM": b"".join(PicoVM().run(words).output),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_c(prog), slot),
        "toJS native": js_native_out(compile_c(prog), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{slot}] {label} {got!r} != {expected!r}"


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picocompress.js", "picobrotli.js"):
        s = os.path.join(VM_DIR, dep)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(BUILD, dep))
    try:
        # Json builder: object with a nested array, comma/afterKey state machine.
        json_prog = (
            "int w = Utf8Writer.New(4096, 1024);"
            "Json.BeginObject(w);"
            'Json.Key(w, "status"); Json.Str(w, "ok");'
            'Json.Key(w, "count"); Json.Int(w, 42);'
            'Json.Key(w, "items"); Json.BeginArray(w); Json.Int(w, 1); Json.Int(w, 2); Json.EndArray(w);'
            "Json.EndObject(w);"
            "Io.Write(Utf8Writer.ToSpan(w));")
        check(json_prog, b'{"status":"ok","count":42,"items":[1,2]}', "json")

        # Xml builder: element with an attribute, entity-escaped attr + text.
        xml_prog = (
            "int w = Utf8Writer.New(8192, 1024);"
            'Xml.Open(w, "a"); Xml.AttrName(w, "href"); Xml.AttrValue(w, "/x?a=1&b=2"); Xml.OpenEnd(w);'
            'Xml.Text(w, "go & see"); Xml.Close(w, "a");'
            "Io.Write(Utf8Writer.ToSpan(w));")
        check(xml_prog, b'<a href="/x?a=1&amp;b=2">go &amp; see</a>', "xml")

        # Utf8Reader: scan "12,34,-5", sum the three ints (12+34-5 = 41).
        reader_prog = (
            'int sp = "12,34,-5"; int r = Utf8Reader.New(sp);'
            "int total = 0;"
            "int x = Utf8Reader.Int(r); total = total + x; Utf8Reader.Match(r, 44);"
            "int y = Utf8Reader.Int(r); total = total + y; Utf8Reader.Match(r, 44);"
            "int z = Utf8Reader.Int(r); total = total + z;"
            "Io.WriteByte(total);")
        check(reader_prog, bytes([41]), "reader")

        print("PASS textio: Utf8Writer / Json / Xml / Utf8Reader run byte-identically on all five "
              "runtimes (Python VM == JS VM == C interp == toC-native == toJS-native) -- full "
              "pure-namespace parity in C")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
