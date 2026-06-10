#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Http.* pure parsers (>0xFF dispatch via EXT base): ParseQuery / ParseForm.

A URL-encoded query/form string is decoded (%XX hex, '+' -> space) into the exact
key=value\\n "model" format that Template.Render consumes, so an HTTP request can be
rendered straight into a page: Template.Render(Compile(tmpl), Http.ParseQuery(qs)).
Python VM == JS VM, byte-exact, with known-answer checks.
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


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def run_both(prog):
    words = lower_to_bytecode_safe(compile_c(prog))
    py = b"".join(PicoVM().run(words).output)
    js = run_js_vm(words)
    assert py == js, f"parity mismatch: py={py!r} js={js!r}"
    return py


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def main():
    # ParseQuery -> key=value\n model (one trailing newline per pair).
    qs = b"name=Bob&role=admin"
    q = (setbytes(1000, qs) +
         f"int s = Span.Make(1000, {len(qs)});"
         "int m = Http.ParseQuery(s); Io.Write(m);")
    assert run_both(q) == b"name=Bob\nrole=admin\n", "ParseQuery basic"

    # URL-decoding: '+' -> space, %XX -> byte.
    qd = b"q=hello+world&x=%41"
    q2 = (setbytes(1000, qd) +
          f"int s = Span.Make(1000, {len(qd)});"
          "int m = Http.ParseQuery(s); Io.Write(m);")
    assert run_both(q2) == b"q=hello world\nx=A\n", "ParseQuery url-decode"

    # ParseForm (same engine) + a key with no '=' value.
    fm = b"a&b=2"
    f = (setbytes(1000, fm) +
         f"int s = Span.Make(1000, {len(fm)});"
         "int m = Http.ParseForm(s); Io.Write(m);")
    assert run_both(f) == b"a=\nb=2\n", "ParseForm + bare key"

    # Bad %-escape and trailing/empty pairs are preserved verbatim / skipped.
    bd = b"x=%GG&&y=1"
    b = (setbytes(1000, bd) +
         f"int s = Span.Make(1000, {len(bd)});"
         "int m = Http.ParseQuery(s); Io.Write(m);")
    assert run_both(b) == b"x=%GG\ny=1\n", "ParseQuery bad-escape + empty pair"

    # Integration: HTTP query string rendered straight through a template.
    tmpl = b"Hi {{name}} ({{role}})"
    intg = (setbytes(1000, qs) + setbytes(2000, tmpl) +
            f"int qsp = Span.Make(1000, {len(qs)});"
            "int model = Http.ParseQuery(qsp);"
            f"int tsp = Span.Make(2000, {len(tmpl)});"
            "int plan = Template.Compile(tsp);"
            "int outp = Template.Render(plan, model); Io.Write(outp);")
    assert run_both(intg) == b"Hi Bob (admin)", "ParseQuery -> Template.Render"

    # EncodeJson: key=value model -> JSON object.
    em = b"name=Bob\nrole=admin"
    e = (setbytes(1000, em) +
         f"int s = Span.Make(1000, {len(em)});"
         "int j = Http.EncodeJson(s); Io.Write(j);")
    assert run_both(e) == b'{"name":"Bob","role":"admin"}', "EncodeJson basic"

    # EncodeJson escaping: quotes + backslash.
    eq_ = b'k=a"b\\c'
    e2 = (setbytes(1000, eq_) +
          f"int s = Span.Make(1000, {len(eq_)});"
          "int j = Http.EncodeJson(s); Io.Write(j);")
    assert run_both(e2) == b'{"k":"a\\"b\\\\c"}', "EncodeJson quote/backslash"

    # EncodeJson escaping: tab + control char (\\t, \\u0001).
    ec = b"k=x\ty\x01z"
    e3 = (setbytes(1000, ec) +
          f"int s = Span.Make(1000, {len(ec)});"
          "int j = Http.EncodeJson(s); Io.Write(j);")
    assert run_both(e3) == b'{"k":"x\\ty\\u0001z"}', "EncodeJson tab/control"

    # Chain: query string -> model -> JSON response.
    cq = b"a=1&b=hello+world"
    e4 = (setbytes(1000, cq) +
          f"int s = Span.Make(1000, {len(cq)});"
          "int m = Http.ParseQuery(s); int j = Http.EncodeJson(m); Io.Write(j);")
    assert run_both(e4) == b'{"a":"1","b":"hello world"}', "ParseQuery -> EncodeJson"

    print("PASS Http.*: ParseQuery/ParseForm url-decode -> Template model + EncodeJson, "
          "Python VM == JS VM byte-exact (incl. query-string -> Template.Render integration)")


if __name__ == "__main__":
    main()
