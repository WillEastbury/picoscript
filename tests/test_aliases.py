#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-language namespace aliases (frontend sugar) -- C, BASIC and Python frontends.

Idiomatic spellings resolve at lower time to the canonical Ns.Method host call:
  C      libc:    strlen/atoi/toupper/pow/tohex/sha256 -> String/Number/Maths/Crypto
  BASIC  classic: POKE/PEEK/LEN/UCASE$/HEX$ (HEX$ -> bare UPPERCASE)
  Python builtins: len/upper/poke/peek/hex (hex -> 0x.., lowercase)
Pure sugar: identical IL, so (1) the alias program runs byte-identically on all five
runtimes, (2) its bytecode equals the canonical spelling's, (3) the Python frontend
and the JS frontend (vm/picoc.js) emit the same bytecode, and (4) a user-defined
function/sub of the same name takes precedence over the alias.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_aliases")


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


def js_compile(src, lang="c"):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


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


def check(prog, expected, slot, compile_fn=compile_c, lang="c"):
    words = lower_to_bytecode_safe(compile_fn(prog))
    runs = {
        "Python VM": b"".join(PicoVM().run(words).output),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_fn(prog), slot),
        "toJS native": js_native_out(compile_fn(prog), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{slot}] {label} {got!r} != {expected!r}"
    # Python frontend bytecode == JS frontend (picoc.js) bytecode.
    assert words == js_compile(prog, lang), f"[{slot}] Python/JS frontend bytecode diverged"


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
        check('int n = strlen("hello"); Io.WriteByte(n);', bytes([5]), "strlen")
        check('int u = toupper("hi"); Io.Write(u);', b"HI", "toupper")
        check('int x = atoi("42"); Io.WriteByte(x);', bytes([42]), "atoi")
        check('int p = pow(2, 10); int s = itoa(p); Io.Write(s);', b"1024", "pow_itoa")
        check('int h = tohex(255); Io.Write(h);', b"ff", "tohex")   # C convention: bare lowercase

        # An alias lowers to byte-identical bytecode as its canonical spelling.
        assert (lower_to_bytecode_safe(compile_c('int n=strlen("hi");Io.WriteByte(n);')) ==
                lower_to_bytecode_safe(compile_c('int n=String.Length("hi");Io.WriteByte(n);'))), \
            "alias bytecode != canonical bytecode"

        # A user-defined function shadows the alias (alias must not hijack it).
        ufn = 'void abs() { Io.WriteByte(7); } abs();'
        assert b"".join(PicoVM().run(lower_to_bytecode_safe(compile_c(ufn))).output) == bytes([7]), \
            "user function 'abs' should win over the alias"

        # ── BASIC frontend: POKE/PEEK no-parens + LEN/HEX$/UCASE$ (radix bare UPPERCASE) ──
        check('LET x = LEN("hello")\nIo.WriteByte(x)\n', bytes([5]), "b_len",
              compile_basic, "basic")
        check('LET h = HEX$(255)\nIo.Write(h)\n', b"FF", "b_hex",       # BASIC: bare uppercase
              compile_basic, "basic")
        check('POKE 1000, 65\nLET v = PEEK(1000)\nIo.WriteByte(v)\n', bytes([65]), "b_poke",
              compile_basic, "basic")
        check('LET u = UCASE$("hi")\nIo.Write(u)\n', b"HI", "b_ucase",
              compile_basic, "basic")
        assert (lower_to_bytecode_safe(compile_basic('LET n = LEN("hi")\nIo.WriteByte(n)\n')) ==
                lower_to_bytecode_safe(compile_basic('LET n = String.Length("hi")\nIo.WriteByte(n)\n'))), \
            "BASIC alias bytecode != canonical bytecode"

        # ── Python frontend: bare-name calls + hex/oct/bin (radix 0x/0o/0b lowercase) ──
        check('x = len("hello")\nIo.WriteByte(x)\n', bytes([5]), "p_len",
              compile_python, "python")
        check('h = hex(255)\nIo.Write(h)\n', b"0xff", "p_hex",          # Python: 0x prefix, lowercase
              compile_python, "python")
        check('poke(1000, 65)\nv = peek(1000)\nIo.WriteByte(v)\n', bytes([65]), "p_poke",
              compile_python, "python")
        check('u = upper("hi")\nIo.Write(u)\n', b"HI", "p_upper",
              compile_python, "python")
        assert (lower_to_bytecode_safe(compile_python('n = len("hi")\nIo.WriteByte(n)\n')) ==
                lower_to_bytecode_safe(compile_python('n = String.Length("hi")\nIo.WriteByte(n)\n'))), \
            "Python alias bytecode != canonical bytecode"
        # A user-defined sub shadows the alias (Python bare name() -> sub call, not alias).
        pufn = 'def abs():\n    Io.WriteByte(7)\nabs()\n'
        assert b"".join(PicoVM().run(lower_to_bytecode_safe(compile_python(pufn))).output) == bytes([7]), \
            "user sub 'abs' should win over the alias"

        print("PASS C aliases: strlen/toupper/atoi/itoa/pow/tohex/... resolve to canonical host "
              "calls -- byte-identical on all five runtimes, Python==JS frontend bytecode, "
              "user functions take precedence")
        print("PASS BASIC aliases: POKE/PEEK/LEN/HEX$(->FF)/UCASE$ -- 5-path byte-identical, "
              "Python==JS frontend bytecode, alias==canonical")
        print("PASS Python aliases: len/hex(->0xff)/poke/peek/upper -- 5-path byte-identical, "
              "Python==JS frontend bytecode, user subs take precedence")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)



def test_main():
    main()

if __name__ == "__main__":
    main()
