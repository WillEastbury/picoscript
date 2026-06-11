#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INV-25 source-span / IL-op debug info + symbolication parity.

The compiler emits a side-band debug table pc -> (src_off, op, ns, method); a
fault's machine coordinates (code, pc, detail) are symbolicated against that table +
the source into a structured record {op, target, line, col, source_line, ...}. The
word stream is unchanged by the table (it is a separate symbol artifact, like a
stripped binary + symbol file), so the embedded C runtime stays lean and
symbolication happens off-device.

Parity (INV-2/INV-24): the Python compiler/symbolizer (picoscript_il) and the JS
compiler/symbolizer (vm/picoc.js) must produce byte-identical debug tables and
byte-identical symbolize() records. And a fault raised by *any* runtime (here the
Python VM and the portable C VM) at a given pc symbolicates to the same source
location -- demonstrated end-to-end below.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c                       # noqa: E402
from picoscript_basic import compile_basic                    # noqa: E402
from picoscript_python import compile_python                  # noqa: E402
from picoscript_english import compile_english                # noqa: E402
from picoscript_il import lower_to_bytecode_with_debug, symbolize  # noqa: E402
from picoscript_vm import PicoVM, PicoFault                   # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")

CAP_ALL = 0x1FF
CAP_RANDOM = 1 << 2

PROGRAMS = [
    'int s = "Hi";\nRandom.U32();\nIo.Write(s);\n',
    'int a = 2;\nint b = a + 3;\nIo.WriteByte(b);\n',
    'int x = 5;\nif (x > 1) {\n  Io.WriteByte(x);\n}\n',
    'int s = "Hi";\nMemory.Set(32766, 65);\nIo.Write(s);\n',
]


def js(src, fn):
    """Run a tiny node program against vm/picoc.js; fn is a JS expression body that
    uses `P` (the module) and `src`, and console.log(JSON.stringify(...))."""
    script = "const P = require('./vm/picoc.js'); const src = process.argv[1];\n" + fn
    r = subprocess.run(["node", "-e", script, src], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def js_words_debug(src):
    return js(src, """
const r = P.compileWithDebug(src, 'c');
const keys = Object.keys(r.debug).map(Number).sort((a,b)=>a-b);
const dbg = keys.map(pc => { const x = r.debug[pc]; return [pc, x[0], x[1], x[2], x[3]]; });
console.log(JSON.stringify({ words: r.words.map(w=>w>>>0), debug: dbg }));
""")


def js_symbolize(src, code, pc, detail):
    return js(src, """
const a = process.argv.slice(2);
const r = P.compileWithDebug(src, 'c');
console.log(JSON.stringify(P.symbolize(%d, %d, %d, r.debug, src)));
""" % (code, pc, detail))


def js_words_debug_lang(src, lang):
    return js(src, """
const r = P.compileWithDebug(src, '%s');
const keys = Object.keys(r.debug).map(Number).sort((a,b)=>a-b);
const dbg = keys.map(pc => { const x = r.debug[pc]; return [pc, x[0], x[1], x[2], x[3]]; });
console.log(JSON.stringify({ words: r.words.map(w=>w>>>0), debug: dbg }));
""" % lang)


def js_symbolize_lang(src, lang, code, pc, detail):
    return js(src, """
const r = P.compileWithDebug(src, '%s');
console.log(JSON.stringify(P.symbolize(%d, %d, %d, r.debug, src)));
""" % (lang, code, pc, detail))


def norm_debug(debug):
    return [[int(pc), rec[0], rec[1], rec[2], rec[3]] for pc, rec in sorted(debug.items())]


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def c_fault_pc(words, caps=None):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    env = dict(os.environ)
    if caps is not None:
        env["PICOVM_CAPS"] = str(caps)
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True, env=env).stdout
    for line in out.splitlines():
        if line.startswith("FAULT"):
            p = line.split()
            return int(p[1]), int(p[2]), int(p[3])   # code, pc, detail
    return 0, 0, 0


def test_debug_map_parity():
    for src in PROGRAMS:
        words, debug = lower_to_bytecode_with_debug(compile_c(src))
        j = js_words_debug(src)
        assert [w & 0xFFFFFFFF for w in words] == j["words"], f"words diverge for {src!r}"
        assert norm_debug(debug) == j["debug"], f"debug table diverges for {src!r}"


def test_symbolize_parity():
    # For every emitted pc, the Python and JS symbolizers must agree byte-for-byte.
    for src in PROGRAMS:
        _, debug = lower_to_bytecode_with_debug(compile_c(src))
        for pc in sorted(debug):
            py = symbolize(8, pc, 0, debug, src)
            j = js_symbolize(src, 8, pc, 0)
            assert py == j, f"symbolize diverges at pc={pc} for {src!r}: {py} != {j}"


def test_capability_fault_symbolicates_python_and_c():
    # Random.U32 on line 2 faults (capability) when CAP_RANDOM is revoked; the fault
    # must symbolicate to line 2, target Random.U32 -- identically whether the fault is
    # raised by the Python VM or the portable C VM (off-device symbolication).
    src = PROGRAMS[0]
    words, debug = lower_to_bytecode_with_debug(compile_c(src))
    no_random = CAP_ALL & ~CAP_RANDOM

    try:
        PicoVM(caps=no_random).run(words)
        raise AssertionError("Random.U32 must fault without CAP_RANDOM (Python)")
    except PicoFault as exc:
        rec = symbolize(exc.code, exc.pc, exc.detail, debug, src)
    assert rec["fault"] == "capability", rec
    assert rec["target"] == "Random.U32", rec
    assert rec["line"] == 2, rec
    assert rec["source_line"] == "Random.U32();", rec

    build_c_vm()
    code, pc, detail = c_fault_pc(words, caps=no_random)
    assert code == 8, f"C VM must raise capability fault (8), got {code}"
    crec = symbolize(code, pc, detail, debug, src)
    assert crec["target"] == "Random.U32" and crec["line"] == 2, crec
    # The Python-raised and C-raised faults land on the same pc -> identical record.
    assert crec == rec, f"C-path symbolication must match Python: {crec} != {rec}"


def test_const_write_fault_symbolicates():
    # INV-9 const-write (Memory.Set into the literal region) on line 2 symbolicates there.
    src = PROGRAMS[3]
    words, debug = lower_to_bytecode_with_debug(compile_c(src))
    try:
        PicoVM().run(words)
        raise AssertionError("Memory.Set into the const region must fault")
    except PicoFault as exc:
        rec = symbolize(exc.code, exc.pc, exc.detail, debug, src)
    assert rec["fault"] == "const_write", rec
    assert rec["target"] == "Memory.Set", rec
    assert rec["line"] == 2, rec


# Every frontend lowers to the same IL, so the debug table + symbolize() must be
# byte-identical Python<->JS for all four dialects, and the source offset must
# resolve to the right line (the C frontend gives line+column; the BASIC/Python/
# English frontends attribute to the statement's line).
FRONTENDS = [
    ("c", compile_c, 'int x = 5;\nIo.WriteByte(x);\n', 2),
    ("basic", compile_basic, 'LET x = 5\nIo.WriteByte(x)\n', 2),
    ("python", compile_python, 'x = 5\nIo.WriteByte(x)\n', 2),
    ("english", compile_english, 'Set x to 5.\nIo.WriteByte(x).\n', 2),
]


def test_all_frontends_debug_parity():
    for lang, comp, src, expect_line in FRONTENDS:
        words, debug = lower_to_bytecode_with_debug(comp(src))
        j = js_words_debug_lang(src, lang)
        assert [w & 0xFFFFFFFF for w in words] == j["words"], f"{lang}: words diverge"
        assert norm_debug(debug) == j["debug"], f"{lang}: debug table diverges"
        # The first host op symbolicates to the expected statement line on both runtimes.
        host_pcs = [pc for pc in sorted(debug) if debug[pc][1] == "host"]
        assert host_pcs, f"{lang}: no host op found"
        pc = host_pcs[0]
        py = symbolize(6, pc, 0, debug, src)
        je = js_symbolize_lang(src, lang, 6, pc, 0)
        assert py == je, f"{lang}: symbolize diverges: {py} != {je}"
        assert py["line"] == expect_line, f"{lang}: expected line {expect_line}, got {py}"


def main():
    tests = [
        test_debug_map_parity,
        test_symbolize_parity,
        test_capability_fault_symbolicates_python_and_c,
        test_const_write_fault_symbolicates,
        test_all_frontends_debug_parity,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"{len(tests) - failed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("PASS debuginfo: compiler pc->source debug table + symbolize() are byte-identical "
          "across Python and JS; a fault from the Python VM or the portable C VM symbolicates "
          "to the same source span + IL op (INV-25 source-span/IL-op; capsule/binding = PIOS)")


if __name__ == "__main__":
    main()
