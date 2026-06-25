#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template.* engine: AOT Compile-at-save + Render, Python VM == JS VM.

A template is compiled once (Template.Compile -> a compact plan you store in a
walfs card) and rendered many times against a key=value model (Template.Render)
with no re-parsing. Verifies holes substitute correctly and the Python and JS
interpreters render byte-identically.
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


def build_prog(tmpl: bytes, model: bytes) -> str:
    lines = [f"Memory.Set({1000 + i}, {b});" for i, b in enumerate(tmpl)]
    lines += [f"Memory.Set({2000 + i}, {b});" for i, b in enumerate(model)]
    lines += [
        f"int tmpl = Span.Make(1000, {len(tmpl)});",
        f"int model = Span.Make(2000, {len(model)});",
        "int plan = Template.Compile(tmpl);",
        "int outp = Template.Render(plan, model);",
        "Io.Write(outp);",
    ]
    return "\n".join(lines) + "\n"


def run_js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def check(tmpl, model, expected):
    words = lower_to_bytecode_safe(compile_c(build_prog(tmpl, model)))
    py = b"".join(PicoVM().run(words).output)
    js = run_js_vm(words)
    assert py == expected, f"Python render {py!r} != {expected!r} (tmpl={tmpl!r})"
    assert js == expected, f"JS render {js!r} != {expected!r} (tmpl={tmpl!r})"
    return len(words)


def main():
    check(b"Hi {{name}}!", b"name=Bob", b"Hi Bob!")
    check(b"{{a}}-{{ b }}-{{x}}", b"a=1\nb=22", b"1-22-")        # whitespace-trimmed key, missing -> empty
    check(b"<p>{{title}}</p>", b"title=Hello & Bye", b"<p>Hello & Bye</p>")
    # sections (truthy), inverted, nesting, mixed with holes
    check(b"{{#show}}yes{{/show}}", b"show=1", b"yes")
    check(b"{{#show}}yes{{/show}}", b"show=", b"")
    check(b"{{^show}}no{{/show}}", b"show=", b"no")
    check(b"{{^show}}no{{/show}}", b"show=1", b"")
    check(b"{{#a}}A{{#b}}B{{/b}}C{{/a}}", b"a=1\nb=1", b"ABC")     # nested, both true
    check(b"{{#a}}A{{#b}}B{{/b}}C{{/a}}", b"a=1\nb=", b"AC")       # nested, inner false
    check(b"{{#a}}A{{/a}}", b"a=", b"")                           # outer false skips body
    check(b"Hi {{#vip}}*{{/vip}}{{name}}", b"vip=1\nname=Bob", b"Hi *Bob")
    # iteration: object lists, scalar lists ({{.}}), nesting, empty
    check(b"{{#each items}}<li>{{name}}</li>{{/each}}", b"items.0.name=A\nitems.1.name=B",
          b"<li>A</li><li>B</li>")
    check(b"{{#each xs}}[{{.}}]{{/each}}", b"xs.0=1\nxs.1=2\nxs.2=3", b"[1][2][3]")
    check(b"{{#each none}}X{{/each}}", b"other=1", b"")
    check(b"{{#each rows}}{{#each cols}}{{.}}{{/each}};{{/each}}",
          b"rows.0.cols.0=a\nrows.0.cols.1=b\nrows.1.cols.0=c", b"ab;c;")
    print("PASS Template.*: AOT compile-at-save + render, Python VM == JS VM byte-exact "
          "(holes, sections, inverted, nesting, {{#each}} object/scalar/nested lists, missing-key)")



def test_main():
    main()

if __name__ == "__main__":
    main()
