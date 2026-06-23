#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text/binary encoding primitives."""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


SRC = (
    'int text = "Hello+£";'
    'Io.Write(Encoding.AsciiEncode(text)); Io.WriteByte(124);'
    'Io.Write(Encoding.Utf8Decode(Encoding.Utf8Encode(text))); Io.WriteByte(124);'
    'Io.Write(Encoding.Utf16LEDecode(Encoding.Utf16LEEncode(text))); Io.WriteByte(124);'
    'Io.Write(Encoding.Utf16BEDecode(Encoding.Utf16BEEncode(text))); Io.WriteByte(124);'
    'Io.Write(Encoding.Utf7Decode(Encoding.Utf7Encode(text))); Io.WriteByte(124);'
    'Io.Write(Encoding.HexDecode(Encoding.HexEncode(text))); Io.WriteByte(124);'
    'Io.Write(Base64.Decode(Base64.Encode(text))); Io.WriteByte(124);'
    'Io.Write(Base64.UrlDecode(Base64.UrlEncode(text)));'
)

EXPECTED = b"Hello+?|Hello+\xc2\xa3|Hello+\xc2\xa3|Hello+\xc2\xa3|Hello+\xc2\xa3|Hello+\xc2\xa3|Hello+\xc2\xa3|Hello+\xc2\xa3"


def run_py(words):
    return b"".join(PicoVM().run(words).output)


def run_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_encoding_primitives_py_js():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = run_py(words)
    js = run_js(words)
    assert py == js
    assert py == EXPECTED, py


def main():
    test_encoding_primitives_py_js()
    print("PASS encoding primitives: Base64 URL + ASCII/UTF-8/UTF-16/UTF-7/hex (Python VM == JS VM)")


if __name__ == "__main__":
    main()
