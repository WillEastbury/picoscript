#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for browser/portal display of mixed text + numeric output.

The VM raw output is still bytes: strings append their UTF-8 bytes and numeric
PRINT/PIPE appends 4-byte big-endian integers. The portal must not show those
mixed bytes as packed integer arrays for text/html or text/plain responses; it
uses picovm.js outputDisplayText() to format typed output chunks for humans.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402


HTML_COUNTER = r'''
Net.Status(200).
Net.Type("text/html").
Set a to 16.
Print "<html><body>".
While a is greater than 1:
    Print a.
    Print "<br/>".
    Decrease a by 2.
Print "</body></html>".
'''

EXPECTED_BODY = "<html><body>16<br/>14<br/>12<br/>10<br/>8<br/>6<br/>4<br/>2<br/></body></html>"


def run_js_display(words):
    script = r'''
const PicoVM = require("./vm/picovm.js");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const vm = new PicoVM();
vm.load(input.words);
vm.run();
process.stdout.write(JSON.stringify({
  status: vm.httpStatus,
  type: vm.httpType,
  ints: vm.outputInts(),
  display: vm.outputDisplayText(),
  rawText: vm.outputText()
}));
'''
    r = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        input=json.dumps({"words": words}),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_text_html_mixed_output_display_is_html_not_packed_ints():
    words = lower_to_bytecode_safe(compile_english(HTML_COUNTER))
    out = run_js_display(words)

    assert out["status"] == 200
    assert out["type"] == "text/html"
    assert out["display"] == EXPECTED_BODY
    assert out["display"].startswith("<html><body>16<br/>14<br/>12")
    assert out["ints"] != [875692032, 917504, 786432, 655360, 524288, 393216, 262144, 131072]


def main():
    test_text_html_mixed_output_display_is_html_not_packed_ints()
    print("PASS HTTP display output: text/html mixed string+number PRINT renders as HTML, not packed ints")


if __name__ == "__main__":
    main()
