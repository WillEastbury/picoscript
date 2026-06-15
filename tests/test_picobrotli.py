#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compress.Brotli* is the real micro-brotli codec (vendored from picoweb as
picobrotli.py / vm/picobrotli.c / vm/picobrotli.js). We verify the Python VM, the
JS VM and the native C VM each produce output byte-identical to the picobrotli
library itself, that every path round-trips, and -- crucially -- that the bytes we
emit are valid RFC 7932 Brotli that a real decoder (Node's zlib) accepts.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402
import picobrotli  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_brotli")

SAMPLES = [b"", b"a", b"abc", b"The quick brown fox jumps over the lazy dog " * 8,
           bytes(range(64)) * 3, b'{"json":"value","items":[1,2,3,4,5]} ' * 6,
           b"<html><head><title>x</title></head><body>hi</body></html>" * 3]


def _ensure_c_vm():
    if os.path.exists(VM_EXE):
        return True
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def _span_prog(data, method):
    lines = [f"Memory.Set({i}, {b});" for i, b in enumerate(data)]
    lines += [f"int z = Span.Make(0, {len(data)});",
              f"int out = Compress.{method}(z);", "Io.Write(out);"]
    return "\n".join(lines) + "\n"


def _py(words):
    h = HostApi(); vm = PicoVM(host=h); vm.load(words); vm.run()
    return b"".join(vm.output)


def _node(exe_args, words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(exe_args, input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def _js(words):
    return _node(["node", os.path.join(VM_DIR, "picovm_run.js")], words)


def _c(words):
    return _node([VM_EXE], words)


def _node_brotli_decode(enc):
    """Decode `enc` with Node's real Brotli; returns the decoded bytes or None."""
    js = ("const z=require('zlib');let c=[];process.stdin.on('data',d=>c.push(d));"
          "process.stdin.on('end',()=>{try{process.stdout.write("
          "z.brotliDecompressSync(Buffer.concat(c)));}catch(e){process.exit(3);}});")
    r = subprocess.run(["node", "-e", js], input=enc, capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout


def test_brotli_py_matches_library():
    for s in SAMPLES:
        words = lower_to_bytecode_safe(compile_c(_span_prog(s, "BrotliCompress")))
        assert _py(words) == picobrotli.encode(s), f"Python VM != library (len {len(s)})"


def test_brotli_py_equals_js_equals_c_equals_library():
    have_c = _ensure_c_vm()
    for s in SAMPLES:
        words = lower_to_bytecode_safe(compile_c(_span_prog(s, "BrotliCompress")))
        lib = picobrotli.encode(s)
        py, js = _py(words), _js(words)
        assert py == lib, f"Python VM != library (len {len(s)})"
        assert js == lib, f"JS VM != library (len {len(s)})"
        if have_c:
            assert _c(words) == lib, f"C VM != library (len {len(s)})"


def test_brotli_roundtrip_all_vms():
    have_c = _ensure_c_vm()
    for s in SAMPLES:
        rt = _span_prog(s, "BrotliCompress").replace(
            "Compress.BrotliCompress(z)", "Compress.BrotliDecompress(Compress.BrotliCompress(z))")
        words = lower_to_bytecode_safe(compile_c(rt))
        assert _py(words) == s, f"Python VM round-trip (len {len(s)})"
        assert _js(words) == s, f"JS VM round-trip (len {len(s)})"
        if have_c:
            assert _c(words) == s, f"C VM round-trip (len {len(s)})"


def test_brotli_output_is_real_brotli():
    """The bytes the VM emits must be decodable by a real Brotli decoder."""
    for s in SAMPLES:
        words = lower_to_bytecode_safe(compile_c(_span_prog(s, "BrotliCompress")))
        enc = _py(words)
        dec = _node_brotli_decode(enc)
        assert dec == s, f"Node Brotli could not decode our output (len {len(s)})"


def test_brotli_actually_compresses():
    big = b"repeat this phrase many times over and over " * 40
    assert len(picobrotli.encode(big)) < len(big) // 3


def _c_native_enc(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return _parse_out(out.stdout)


def _js_native_enc(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js');\nconst rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return _parse_out(out.stdout)


def _parse_out(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_brotli_native_transpiler_parity():
    """toC-native and toJS-native emit Brotli byte-identical to the library too,
    completing the five-path parity (Python/JS/C VMs + lower_to_c + lower_to_js)."""
    if not _ensure_c_vm():
        return
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picocompress.js", "picobrotli.js"):
        src = os.path.join(VM_DIR, dep)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(BUILD, dep))
    try:
        samples = [b"abc", b"The quick brown fox jumps over the lazy dog " * 8,
                   b'{"json":"value","items":[1,2,3,4,5]} ' * 6]
        for idx, s in enumerate(samples):
            prog = _span_prog(s, "BrotliCompress")
            il = compile_c(prog)
            lib = picobrotli.encode(s)
            cn = _c_native_enc(il, f"br{idx}")
            jn = _js_native_enc(il, f"br{idx}")
            assert cn == lib, f"toC-native != library (len {len(s)})"
            assert jn == lib, f"toJS-native != library (len {len(s)})"
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS picobrotli: Compress.Brotli* == the picobrotli library on the Python "
          "VM == JS VM == C VM (round-trip on all); output decodes under real Brotli (Node zlib)")


if __name__ == "__main__":
    main()
