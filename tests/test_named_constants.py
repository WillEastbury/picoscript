#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Named HTTP constants/enums across frontends.

Verifies readable method/status constants compile and run, and that Python and JS
frontends emit byte-identical bytecode for the same source.
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
from picoscript_lang import resolve_named_constant, describe_named_constant, toLocale  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def js_compile(src, lang):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def check(src, compile_fn, lang, expected):
    words = lower_to_bytecode_safe(compile_fn(src))
    out = b"".join(PicoVM().run(words).output)
    assert out == expected, f"{lang} output {out!r} != {expected!r}"
    assert words == js_compile(src, lang), f"{lang} frontend bytecode mismatch (python/js)"
    return words


def check_status(src, compile_fn, lang, expected_status):
    words = lower_to_bytecode_safe(compile_fn(src))
    vm = PicoVM().run(words)
    assert vm.http_status == expected_status, f"{lang} status {vm.http_status!r} != {expected_status!r}"
    assert words == js_compile(src, lang), f"{lang} frontend bytecode mismatch (python/js)"
    return words


def main():
    c_prog = (
        "int m = HTTP_METHOD_POST;"
        "Resp.Status(HTTP_STATUS_CREATED);"
        "Io.WriteByte(m);"
        "Io.WriteByte(HTTP_STATUS_CREATED);"
    )
    c_enum_prog = (
        "int m = HttpMethod.POST;"
        "Resp.Status(HttpStatus.Created);"
        "Io.WriteByte(m);"
        "Io.WriteByte(HttpStatus.Created);"
    )
    b_prog = (
        "DIM M = HTTP_METHOD_POST\n"
        "Resp.Status(HTTP_STATUS_CREATED)\n"
        "Io.WriteByte(M)\n"
        "Io.WriteByte(HTTP_STATUS_CREATED)\n"
    )
    p_prog = (
        "m = HTTP_METHOD_POST\n"
        "Resp.Status(HTTP_STATUS_CREATED)\n"
        "Io.WriteByte(m)\n"
        "Io.WriteByte(HTTP_STATUS_CREATED)\n"
    )
    e_prog = (
        "Set m to HTTP_METHOD_POST.\n"
        "Resp.Status(HTTP_STATUS_CREATED).\n"
        "Io.WriteByte(m).\n"
        "Io.WriteByte(HTTP_STATUS_CREATED).\n"
    )

    expected = bytes([2, 201])
    c_words = check(c_prog, compile_c, "c", expected)
    check(c_enum_prog, compile_c, "c", expected)
    b_words = check(b_prog, compile_basic, "basic", expected)
    check(p_prog, compile_python, "python", expected)
    check(e_prog, compile_english, "english", expected)

    # Constants are compile-time sugar over the equivalent numeric literals.
    assert c_words == lower_to_bytecode_safe(compile_c(
        "int m = 2; Resp.Status(201); Io.WriteByte(m); Io.WriteByte(201);"
    )), "C constants should lower identically to numeric literals"
    assert b_words == lower_to_bytecode_safe(compile_basic(
        "DIM M = 2\nResp.Status(201)\nIo.WriteByte(M)\nIo.WriteByte(201)\n"
    )), "BASIC constants should lower identically to numeric literals"

    # Extended constant families should compile/run across frontends.
    b_ext = (
        "DIM D = DAY_MONDAY\n"
        "DIM M = MONTH_DECEMBER\n"
        "DIM Z = TZ_UTC\n"
        "DIM S = DST_ACTIVE\n"
        "DIM U = UOM_METER\n"
        "DIM C = COLOR_BLUE\n"
        "Io.WriteByte(D)\nIo.WriteByte(M)\nIo.WriteByte(Z)\n"
        "Io.WriteByte(S)\nIo.WriteByte(U)\nIo.WriteByte(C)\n"
    )
    p_ext = (
        "d = DAY_MONDAY\nm = MONTH_DECEMBER\nz = TZ_UTC\n"
        "s = DST_ACTIVE\nu = UOM_METER\nc = COLOR_BLUE\n"
        "Io.WriteByte(d)\nIo.WriteByte(m)\nIo.WriteByte(z)\n"
        "Io.WriteByte(s)\nIo.WriteByte(u)\nIo.WriteByte(c)\n"
    )
    c_ext = (
        "int d = Day.MONDAY; int m = Month.DECEMBER; int z = TimeZone.UTC;"
        "int s = DST.ACTIVE; int u = UOM_METER; int c = Color.BLUE;"
        "Io.WriteByte(d); Io.WriteByte(m); Io.WriteByte(z);"
        "Io.WriteByte(s); Io.WriteByte(u); Io.WriteByte(c);"
    )
    e_ext = (
        "Set d to DAY_MONDAY.\nSet m to MONTH_DECEMBER.\nSet z to TZ_UTC.\n"
        "Set s to DST_ACTIVE.\nSet u to UOM_METER.\nSet c to COLOR_BLUE.\n"
        "Io.WriteByte(d).\nIo.WriteByte(m).\nIo.WriteByte(z).\n"
        "Io.WriteByte(s).\nIo.WriteByte(u).\nIo.WriteByte(c).\n"
    )
    ext_expected = bytes([1, 12, 0, 2, 1, 255])
    check(c_ext, compile_c, "c", ext_expected)
    check(b_ext, compile_basic, "basic", ext_expected)
    check(p_ext, compile_python, "python", ext_expected)
    check(e_ext, compile_english, "english", ext_expected)

    # User-defined constants and enums should work in all frontends.
    c_user = (
        "const RETRY = 3;"
        "enum HttpCode { OK = 200, CREATED = 201, ACCEPTED };"
        "Io.WriteByte(RETRY);"
        "Io.WriteByte(HttpCode.OK);"
        "Io.WriteByte(HTTPCODE_CREATED);"
        "Io.WriteByte(HttpCode.ACCEPTED);"
    )
    b_user = (
        "CONST RETRY = 3\n"
        "ENUM HTTPCODE\n"
        "OK = 200\n"
        "CREATED = 201\n"
        "ACCEPTED\n"
        "ENDENUM\n"
        "Io.WriteByte(RETRY)\n"
        "Io.WriteByte(HTTPCODE_OK)\n"
        "Io.WriteByte(HTTPCODE_CREATED)\n"
        "Io.WriteByte(HTTPCODE_ACCEPTED)\n"
    )
    p_user = (
        "const RETRY = 3\n"
        "enum HttpCode:\n"
        "    OK = 200\n"
        "    CREATED = 201\n"
        "    ACCEPTED\n"
        "Io.WriteByte(RETRY)\n"
        "Io.WriteByte(HTTPCODE_OK)\n"
        "Io.WriteByte(HTTPCODE_CREATED)\n"
        "Io.WriteByte(HTTPCODE_ACCEPTED)\n"
    )
    e_user = (
        "Define constant RETRY as 3.\n"
        "Define enum HttpCode:\n"
        "    OK is 200.\n"
        "    CREATED is 201.\n"
        "    ACCEPTED.\n"
        "Io.WriteByte(RETRY).\n"
        "Io.WriteByte(HTTPCODE_OK).\n"
        "Io.WriteByte(HTTPCODE_CREATED).\n"
        "Io.WriteByte(HTTPCODE_ACCEPTED).\n"
    )
    user_expected = bytes([3, 200, 201, 202])
    check(c_user, compile_c, "c", user_expected)
    check(b_user, compile_basic, "basic", user_expected)
    check(p_user, compile_python, "python", user_expected)
    check(e_user, compile_english, "english", user_expected)

    # Net.Status accepts named constants too, including English source.
    check_status("Net.Status(STATUS_NOT_FOUND);", compile_c, "c", 404)
    check_status("Net.Status(STATUS_NOT_FOUND)\n", compile_basic, "basic", 404)
    check_status("Net.Status(STATUS_NOT_FOUND)\n", compile_python, "python", 404)
    check_status("Net.Status(STATUS_NOT_FOUND).\n", compile_english, "english", 404)

    # Larger constants still resolve to exact numeric values.
    assert resolve_named_constant("CURRENCY_USD") == 840
    assert resolve_named_constant("COUNTRY_GB") == 826
    assert resolve_named_constant("UINT32_MAX") == 4294967295

    # Pretty/localized output has built-in English and user-dictionary overrides.
    usd_meta = describe_named_constant("CURRENCY_USD")
    assert usd_meta is not None and "ISO-4217" in usd_meta["description"]
    fr_override = {
        "fr": {
           "CURRENCY_USD": {
               "label": "Dollar américain (USD)",
               "description": "Code numérique ISO 4217 840.",
           }
        }
    }
    s = toLocale("CURRENCY_USD", "fr", fr_override)
    assert s is not None and "Dollar américain (USD)" in s and "ISO 4217 840" in s
    node_check = (
        "const H=require('./vm/pico_hooks.js');"
        "const s=H.toLocale('CURRENCY_USD','fr',{fr:{CURRENCY_USD:{label:'Dollar américain (USD)',"
        "description:'Code numérique ISO 4217 840.'}}});"
        "if(!s || s.indexOf('Dollar américain (USD)')<0 || s.indexOf('ISO 4217 840')<0){process.exit(2);} "
        "process.stdout.write('ok');"
    )
    r = subprocess.run(["node", "-e", node_check], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr

    print("PASS named constants: HTTP method/status names (and C enum-style dotted names) compile "
          "across C/BASIC/Python/English, run correctly, and stay Python==JS byte-identical.")



def test_main():
    main()

if __name__ == "__main__":
    main()
