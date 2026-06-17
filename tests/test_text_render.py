#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TextRender.* streaming HTML/template helpers."""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

SRC = r'''
int w = Utf8Writer.New(3000, 512);
TextRender.Open(w, "html"); TextRender.OpenEnd(w);
TextRender.Open(w, "body"); TextRender.Attr(w, "class=main & hot"); TextRender.OpenEnd(w);
TextRender.Text(w, "<safe>");
TextRender.Br(w);
TextRender.Raw(w, "<b>");
TextRender.Text(w, "raw & escaped");
TextRender.Raw(w, "</b>");
int model = "name=Ada <admin>";
TextRender.Hole(model, "name");
TextRender.Close(w, "body");
TextRender.Close(w, "html");
Io.Write(Utf8Writer.ToSpan(w));
'''

EXPECTED = b'<html><body class="main &amp; hot">&lt;safe&gt;<br/><b>raw &amp; escaped</b>Ada &lt;admin&gt;</body></html>'


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _run_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_text_render_html_streaming():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py == EXPECTED


def main():
    test_text_render_html_streaming()
    print("PASS TextRender.*: streaming HTML tags/text/attrs/hole (Python VM == JS VM)")


if __name__ == "__main__":
    main()
