#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Planned namespaces now implemented (>0xFF dispatch via EXT base works):
Compress.* (RLE), Crypto.Sha256 (pure), Html.Encode/Decode (entities).
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
    # Compress: PicoCompress LZ77 round-trip + compressed length.
    # "aaabbbbc" -> [lit "aaab"][match len3 dist1][lit "c"] = 10 bytes.
    comp = (setbytes(1000, b"aaabbbbc") +
            "int s = Span.Make(1000, 8);"
            "int c = Compress.PicoCompress(s);"
            "int b = Compress.PicoDecompress(c);"
            "Io.Write(b); Io.WriteByte(124); Io.WriteByte(String.Length(c));")
    assert run_both(comp) == b"aaabbbbc|\x0a", "compress round-trip"

    # Crypto.Sha256("abc") == known digest.
    sha = (setbytes(1000, b"abc") +
           "int s = Span.Make(1000, 3);"
           "int h = Crypto.Sha256(s); Io.Write(h);")
    expect = bytes.fromhex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert run_both(sha) == expect, "sha256(abc)"

    # Html.Encode/Decode round-trip.
    html = (setbytes(1000, b"<b>&") +
            "int s = Span.Make(1000, 4);"
            "int e = Html.Encode(s); Io.Write(e); Io.WriteByte(124);"
            "int d = Html.Decode(e); Io.Write(d);")
    assert run_both(html) == b"&lt;b&gt;&amp;|<b>&", "html entities"

    print("PASS planned namespaces: Compress.PicoCompress/Decompress, Crypto.Sha256, "
          "Html.Encode/Decode -- Python VM == JS VM, known-answer verified (>0xFF dispatch OK)")


if __name__ == "__main__":
    main()
