#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locale runtime hooks across frontends.

Checks Locale.SetLocale/GetCurrentLocale/Format* and Translate behavior in the
Python VM, plus Python-vs-JS frontend bytecode parity.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def js_compile(src, lang):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def run_text(words):
    out = b"".join(PicoVM().run(words).output)
    return out.decode("utf-8", "replace")


def check_text(src, compile_fn, lang, expected):
    words = lower_to_bytecode_safe(compile_fn(src))
    got = run_text(words)
    assert got == expected, f"{lang} output {got!r} != {expected!r}"
    assert words == js_compile(src, lang), f"{lang} frontend bytecode mismatch (python/js)"


def main():
    c_prog = (
        "int lang = \"en-GB\";"
        "int tz = \"UTC\";"
        "Locale.SetLocale(lang, tz);"
        "int current = Locale.GetCurrentLocale();"
        "int n = Locale.FormatNumber(12345, 0);"
        "int d = Locale.FormatDate(0, 0);"
        "int t = Locale.FormatTime(0, 0);"
        "Io.Write(current); Io.Write(\"|\"); Io.Write(n); Io.Write(\"|\"); Io.Write(d); Io.Write(\"|\"); Io.Write(t);"
    )
    b_prog = (
        "DIM LANG = \"en-GB\"\n"
        "DIM TZ = \"UTC\"\n"
        "Locale.SetLocale(LANG, TZ)\n"
        "DIM CURRENT = Locale.GetCurrentLocale()\n"
        "DIM N = Locale.FormatNumber(12345, 0)\n"
        "DIM D = Locale.FormatDate(0, 0)\n"
        "DIM T = Locale.FormatTime(0, 0)\n"
        "Io.Write(CURRENT)\nIo.Write(\"|\")\nIo.Write(N)\nIo.Write(\"|\")\nIo.Write(D)\nIo.Write(\"|\")\nIo.Write(T)\n"
    )
    p_prog = (
        "lang = \"en-GB\"\n"
        "tz = \"UTC\"\n"
        "Locale.SetLocale(lang, tz)\n"
        "current = Locale.GetCurrentLocale()\n"
        "n = Locale.FormatNumber(12345, 0)\n"
        "d = Locale.FormatDate(0, 0)\n"
        "t = Locale.FormatTime(0, 0)\n"
        "Io.Write(current)\nIo.Write(\"|\")\nIo.Write(n)\nIo.Write(\"|\")\nIo.Write(d)\nIo.Write(\"|\")\nIo.Write(t)\n"
    )
    e_prog = (
        "Set lang to \"en-GB\".\n"
        "Set tz to \"UTC\".\n"
        "Locale.SetLocale(lang, tz).\n"
        "Set current to Locale.GetCurrentLocale().\n"
        "Set n to Locale.FormatNumber(12345, 0).\n"
        "Set d to Locale.FormatDate(0, 0).\n"
        "Set t to Locale.FormatTime(0, 0).\n"
        "Io.Write(current).\nIo.Write(\"|\").\nIo.Write(n).\nIo.Write(\"|\").\nIo.Write(d).\nIo.Write(\"|\").\nIo.Write(t).\n"
    )
    expected = "en-GB@UTC|12345|1970-01-01 +00:00|00:00:00 +00:00"
    check_text(c_prog, compile_c, "c", expected)
    check_text(b_prog, compile_basic, "basic", expected)
    check_text(p_prog, compile_python, "python", expected)
    check_text(e_prog, compile_english, "english", expected)

    # tz enum-id input should convert UTC epoch to local offset for display.
    tz_id_prog = (
        "int lang = \"en-GB\";"
        "Locale.SetLocale(lang, TZ_EUROPE_PARIS);"
        "Io.Write(Locale.FormatDate(0, 0));"
        "Io.Write(\"|\");"
        "Io.Write(Locale.FormatTime(0, 0));"
    )
    assert run_text(lower_to_bytecode_safe(compile_c(tz_id_prog))) == "1970-01-01 +01:00|01:00:00 +01:00"

    # Translate should resolve known constant labels with value suffix.
    tr_prog = (
        "int key = \"HTTP_STATUS_NOT_FOUND\";"
        "int loc = \"en\";"
        "Io.Write(Locale.Translate(key, loc));"
    )
    tr = run_text(lower_to_bytecode_safe(compile_c(tr_prog)))
    assert "404" in tr and "Not Found" in tr

    print("PASS locale runtime: Locale.SetLocale/Format*/Translate work across C/BASIC/Python/English and keep Python==JS bytecode parity.")


if __name__ == "__main__":
    main()
