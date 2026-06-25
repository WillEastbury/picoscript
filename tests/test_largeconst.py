#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Large integer literals (outside the 16-bit immediate range) lower correctly.

The ISA has no LOADI; a constant is loaded via a 16-bit sign-extended immediate, so a
literal outside [-32768, 32767] used to truncate. The lowering now builds any int32
literal big-endian byte-by-byte (SUB; ADD b3; MUL 256; ...; ADD b0) using only sign-safe
positive immediates -- 2 words for a small value (unchanged), 8 words otherwise.

This proves a large literal round-trips identically on all five paths (Python VM, C
interpreter, JS VM, toC-native, toJS-native), that the Python and JS compilers emit
byte-identical bytecode, and -- the ergonomic payoff -- that a Q16.16 fixed-point value
(e.g. a Maths.Sin angle) can now be written as a literal directly.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c                              # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM                                    # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_lc")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_interp(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    return out_bytes(subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout)


def js_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    return out_bytes(subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                                    input=inp, capture_output=True, text=True).stdout)


def js_bytecode(src):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), "c"],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def c_native(il, slot):
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(lower_to_c(il, func_name=f"pico_{slot}", emit_main=True))
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    return out_bytes(subprocess.run([exe], capture_output=True, text=True).stdout)


def js_native(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js');\nconst rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2,'0')).join(' '));\n")
    return out_bytes(subprocess.run(["node", runner], capture_output=True, text=True).stdout)


# int32 literals spanning the boundary, both signs, and the high bit.
VALUES = [32767, 32768, 51472, 100000, 411775, 65535, 16777216,
          -32768, -32769, -100000, 2000000000, -2000000000]


def main():
    build_c_vm()
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    try:
        for i, v in enumerate(VALUES):
            # Correctness: the bytecode VMs print the exact value back via Number.ToString.
            sw = f"int x = {v}; x = Number.ToString(x); Io.Write(x);"
            swords = lower_to_bytecode_safe(compile_c(sw))
            expected = str(v).encode()
            assert swords == js_bytecode(sw), f"{v}: Python != JS bytecode"
            assert b"".join(PicoVM().run(swords).output) == expected, f"{v}: Python VM"
            assert c_interp(swords) == expected, f"{v}: C interpreter"
            assert js_vm(swords) == expected, f"{v}: JS VM"

            # Full 5-path parity via WriteByte (works on the native backends too). The
            # divisor literals 65536 / 16777216 are themselves >16-bit, so this also
            # exercises the large-constant build. Values are compared to the Python VM
            # reference (truncating division means the bytes need only be consistent).
            bw = (f"int x = {v}; Io.WriteByte(x); Io.WriteByte(x / 256); "
                  f"Io.WriteByte(x / 65536); Io.WriteByte(x / 16777216);")
            il = compile_c(bw)
            bwords = lower_to_bytecode_safe(il)
            assert bwords == js_bytecode(bw), f"{v}: byte-prog Python != JS bytecode"
            ref = b"".join(PicoVM().run(bwords).output)
            assert len(ref) == 4, f"{v}: expected 4 bytes, got {len(ref)}"
            assert c_interp(bwords) == ref, f"{v}: C interp != Python (5-path)"
            assert js_vm(bwords) == ref, f"{v}: JS VM != Python (5-path)"
            assert c_native(il, f"lc{i}") == ref, f"{v}: toC-native != Python (5-path)"
            assert js_native(il, f"lc{i}") == ref, f"{v}: toJS-native != Python (5-path)"

        # Small literals are unchanged: the 16-bit-range path is 2 words, large is 8.
        from picoscript_il import _const_width  # noqa: E402
        assert _const_width(1000) == 2 and _const_width(-32768) == 2 and _const_width(32767) == 2
        assert _const_width(32768) == 8 and _const_width(-32769) == 8 and _const_width(100000) == 8

        # Ergonomic payoff: a Q16.16 angle (45deg = round(pi/4*65536) = 51472) as a literal.
        sin_words = lower_to_bytecode_safe(compile_c(
            "int r = Maths.Sin(51472); r = Number.ToString(r); Io.Write(r);"))
        sin_out = int(b"".join(PicoVM().run(sin_words).output).decode())
        assert abs(sin_out / 65536 - 0.7071) < 0.01, f"Sin(45deg literal) wrong: {sin_out}"
        assert js_vm(sin_words).decode() == str(sin_out), "literal Sin diverges Python vs JS VM"

        print("PASS large-const: int32 literals outside [-32768,32767] are correct on Python "
              "VM / C interp / JS VM and round-trip byte-identical on all five paths "
              "(+ toC-native / toJS-native); Python==JS bytecode; small literals unchanged; "
              "Q16.16 angles now writable as literals (Sin(51472)=sin45)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)



def test_main():
    main()

if __name__ == "__main__":
    main()
