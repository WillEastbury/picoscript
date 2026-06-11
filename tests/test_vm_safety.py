#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM safety / typed-fault parity (INV-10/11/12).

A malformed or runaway bytecode program must FAULT the same way on every runtime,
never silently truncate. The three bytecode VMs differ only in surface (Python/JS
raise; the C runtime sets a typed ctx->fault the harness prints as `FAULT <code>`),
so this test asserts each VM *faults* on:
  - step-budget exhaustion (INV-12) -- previously the C VM silently `break`-ed;
  - a computed/static jump target out of range (INV-11) -- previously a raw masked PC.

Fault codes (mirrored by picovm_run.js): 1 = step budget, 2 = bad opcode, 3 = bad jump.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def c_fault(words, max_steps=None, caps=None):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    env = dict(os.environ)
    if max_steps is not None:
        env["PICOVM_MAX_STEPS"] = str(max_steps)
    if caps is not None:
        env["PICOVM_CAPS"] = str(caps)
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True, env=env).stdout
    for line in out.splitlines():
        if line.startswith("FAULT"):
            return int(line.split()[1])
    return 0


def js_fault(words, max_steps=None, caps=None):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    env = dict(os.environ)
    if max_steps is not None:
        env["PICOVM_MAX_STEPS"] = str(max_steps)
    if caps is not None:
        env["PICOVM_CAPS"] = str(caps)
    out = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                         input=inp, capture_output=True, text=True, env=env).stdout
    for line in out.splitlines():
        if line.startswith("FAULT"):
            return int(line.split()[1])
    return 0


def py_faulted(words, max_steps=1_000_000, caps=None):
    try:
        PicoVM(max_steps=max_steps, caps=caps).run(words)
        return False
    except RuntimeError:
        return True


def main():
    build_c_vm()

    # ── INV-12: step-budget exhaustion. JUMP 0 is an infinite self-loop. ──
    loop = [0x90000000]   # opcode 0x9 (JUMP), imm16=0 -> PC=0 forever
    assert py_faulted(loop, max_steps=100), "Python VM must fault on step budget"
    assert c_fault(loop, max_steps=100) == 1, "C VM must fault (1=budget), not silently break"
    assert js_fault(loop, max_steps=100) == 1, "JS VM must fault (1=budget)"

    # A program that halts within budget must NOT fault on any runtime.
    # RETURN (opcode 0xC) with an empty call stack halts cleanly.
    halt = [0xC0000000]
    assert not py_faulted(halt), "clean halt must not fault (Python)"
    assert c_fault(halt) == 0, "clean halt must not fault (C)"
    assert js_fault(halt) == 0, "clean halt must not fault (JS)"

    # ── INV-11: out-of-range computed/static jump. JUMP 9999 in a 1-word program. ──
    badjump = [0x9000270F]   # JUMP imm16=0x270F (9999) >> program length (1)
    assert py_faulted(badjump), "Python VM must fault on out-of-range jump"
    assert c_fault(badjump) == 3, "C VM must fault (3=bad jump)"
    assert js_fault(badjump) == 3, "JS VM must fault (3=bad jump)"

    # ── INV-19: template render nesting beyond TPL_MAXDEPTH (32) faults (4=template). ──
    # Compile+render a 40-deep section nest on all three bytecode VMs from one source.
    sys.path.insert(0, ROOT)
    from picoscript_cfront import compile_c           # noqa: E402
    from picoscript_il import lower_to_bytecode_safe   # noqa: E402

    def setbytes(base, data):
        return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))

    tmpl = b"{{#a}}" * 40 + b"x" + b"{{/a}}" * 40
    model = b"a=1"
    src = (setbytes(1000, tmpl) + setbytes(3000, model) +
           f"int t = Span.Make(1000, {len(tmpl)}); int pl = Template.Compile(t);"
           f"int m = Span.Make(3000, {len(model)}); int o = Template.Render(pl, m); Io.Write(o);")
    tw = lower_to_bytecode_safe(compile_c(src))
    assert py_faulted(tw), "Python VM must fault on template depth > 32"
    assert c_fault(tw) == 7, "C VM must fault (7=template depth)"
    assert js_fault(tw) == 7, "JS VM must fault (7=template depth)"

    # ── INV-17 (killer): bindings are not ambient. A binding hook is denied (fault 8)
    # unless its capability class is granted; pure hooks are unaffected by the grant. ──
    CAP_ALL = 0x1FF
    CAP_RANDOM = 1 << 2
    no_random = CAP_ALL & ~CAP_RANDOM
    rand_hook = [0x00007020]   # NOOP imm16=0x7000|0x20 -> Random.U32 (RANDOM-gated)
    io_hook = [0x00007072]     # NOOP imm16=0x7000|0x72 -> Io.WriteByte (pure)

    # Granted: no fault on any VM.
    assert not py_faulted(rand_hook, caps=CAP_ALL), "Random.U32 must run when granted (Python)"
    assert c_fault(rand_hook, caps=CAP_ALL) == 0, "Random.U32 must run when granted (C)"
    assert js_fault(rand_hook, caps=CAP_ALL) == 0, "Random.U32 must run when granted (JS)"
    # Revoked CAP_RANDOM: the binding is denied identically on all three.
    assert py_faulted(rand_hook, caps=no_random), "Random.U32 must be denied without CAP_RANDOM (Python)"
    assert c_fault(rand_hook, caps=no_random) == 8, "C VM must fault (8=capability)"
    assert js_fault(rand_hook, caps=no_random) == 8, "JS VM must fault (8=capability)"
    # A pure hook (Io.WriteByte) is unaffected by the revoked binding.
    assert not py_faulted(io_hook, caps=no_random), "pure Io.WriteByte must not be gated (Python)"
    assert c_fault(io_hook, caps=no_random) == 0, "pure Io.WriteByte must not be gated (C)"
    assert js_fault(io_hook, caps=no_random) == 0, "pure Io.WriteByte must not be gated (JS)"

    print("PASS vm safety: step-budget (INV-12), out-of-range jump (INV-11), template depth "
          "(INV-19) and capability gating (INV-17: bindings are not ambient) fault identically "
          "on Python / C / JS -- pure hooks stay ungated")


if __name__ == "__main__":
    main()
