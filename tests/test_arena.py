#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arena scopes -- Arena.Mark / Arena.Rewind / Arena.Reset -- on all five runtimes.

Bump-arena allocation leaks without scoping: a long-running handler loop grows
arena_top forever. Mark() snapshots the arena, Rewind(mark) reclaims everything
allocated since (so spans and their byte storage are reused), Reset() drops back
to the base. Verified byte-identical across Python VM, JS VM, C interpreter,
toC-native and toJS-native -- the reclaimed handle is reused identically.
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

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_arena")


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


def py_out(words):
    return b"".join(PicoVM().run(words).output)


def js_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_native_out(il, slot):
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
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js');\n"
                "const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    words = lower_to_bytecode_safe(compile_c(prog))
    runs = {
        "Python VM": py_out(words),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_c(prog), slot),
        "toJS native": js_native_out(compile_c(prog), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{slot}] {label} {got!r} != {expected!r}"


def auto_scope_py(words, n):
    """Run a handler n times on ONE reused VM; return (outputs, arena_tops)."""
    host = HostApi()
    vm = PicoVM(host=host)
    outs, tops = [], []
    for _ in range(n):
        host.install_request_context(vm, path="/")
        vm.output = []
        vm.run(words)
        outs.append(b"".join(vm.output))
        tops.append(vm.arena_top)
    return outs, tops


def auto_scope_js(words, n):
    import json
    runner = os.path.join(BUILD, "autoscope_run.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(
            "const PicoVM = require('./picovm.js');\n"
            "const words = JSON.parse(process.argv[2]);\n"
            "const vm = new PicoVM();\n"
            "const out = [], tops = [];\n"
            "for (let i = 0; i < " + str(n) + "; i++) {\n"
            "  vm.setRequestContext({ path: '/' });\n"
            "  vm.output = [];\n"
            "  vm.load(words); vm.run();\n"
            "  out.push(vm.output.map(b => b.toString(16).padStart(2,'0')).join(' '));\n"
            "  tops.push(vm.arenaTop);\n"
            "}\n"
            "console.log(JSON.stringify({ out: out, tops: tops }));\n")
    r = subprocess.run(["node", runner, json.dumps(words)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    return [parse_out_bytes("OUT " + line) for line in res["out"]], res["tops"]


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js"):
        s = os.path.join(VM_DIR, dep)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(BUILD, dep))
    try:
        # Mark -> allocate -> Rewind reclaims: the post-rewind allocation reuses the
        # SAME span handle and the SAME arena bytes as the first post-mark allocation.
        rewind = ("Memory.Set(1000, 88);"                       # 'X'
                  "int s = Span.Make(1000, 1);"
                  "int m = Arena.Mark();"
                  "int a = String.Concat(s, s);"                # handle Ha, 'XX'
                  "int b = String.Concat(a, s);"                # handle Hb, 'XXX'
                  "Arena.Rewind(m);"
                  "int c = String.Concat(s, s);"                # reuses Ha -> 'XX'
                  "Io.WriteByte(a); Io.WriteByte(c); Io.WriteByte(124);"
                  "Io.Write(c);")
        check(rewind, bytes([2, 2, 124]) + b"XX", "arena_rewind")

        # Reset drops the whole arena back to base: handle numbering restarts at 1.
        reset = ("Memory.Set(1000, 89);"                        # 'Y'
                 "int s1 = Span.Make(1000, 1);"                 # handle 1
                 "int x = String.Concat(s1, s1);"               # handle 2
                 "Arena.Reset();"
                 "int s2 = Span.Make(1000, 1);"                 # handle 1 again
                 "Io.WriteByte(s2); Io.Write(s2);")
        check(reset, bytes([1, 89]), "arena_reset")

        # Automatic per-request scope: a handler that allocates spans, run 3x on ONE
        # reused VM. install_request_context auto-rewinds, so arena_top does not grow
        # and every request produces identical output -- no manual cleanup, no leak.
        # (Handle *numbers* are internal and may differ across runtimes; the observable
        # contract is span content + a stable arena, which we assert per runtime.)
        handler = lower_to_bytecode_safe(compile_c(
            "Memory.Set(1000, 72); Memory.Set(1001, 105);"   # "Hi"
            "int s = Span.Make(1000, 2);"
            "int r = String.Concat(s, s);"                    # allocates a span -> "HiHi"
            "Io.Write(r);"))
        outs, tops = auto_scope_py(handler, 3)
        assert outs == [b"HiHi", b"HiHi", b"HiHi"], f"Python per-request output: {outs}"
        assert tops[0] == tops[1] == tops[2], f"Python arena leaked across requests: {tops}"
        js_outs, js_tops = auto_scope_js(handler, 3)
        assert js_outs == [b"HiHi", b"HiHi", b"HiHi"], f"JS per-request output: {js_outs}"
        assert js_tops[0] == js_tops[1] == js_tops[2], f"JS arena leaked across requests: {js_tops}"

        print("PASS arena scopes: Arena.Mark/Rewind/Reset reclaim the bump arena byte-identically "
              "on all five runtimes; install_request_context auto-rewinds per request "
              "(3 handler runs on one VM -> no arena growth, identical output, Python & JS)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
