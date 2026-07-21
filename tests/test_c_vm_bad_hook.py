#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_c_vm_bad_hook.py -- the C VM's host-hook fall-through must fail
closed on a genuinely unknown hook id, and must NOT over-fault a defined
host-fillable primitive that simply has no binding in the default runtime.

Background: pv_default_host() (vm/picovm.c) used to *silently* return for any
host hook it did not handle, leaving regs[rd] stale -- an unknown/unimplemented
primitive "succeeded" with garbage. That is fixed in two tiers, matching the
cross-runtime contract (picoscript_vm.py / vm/picovm.js) exactly:

  * A DEFINED host-fillable primitive (hook code in [1, PV_HOOK_CODE_MAX]) that
    this default runtime does not implement returns the documented INV-18
    default -- 0 with host_status = NOT_FOUND -- like the reserved-namespace
    stub and like the Python / JS reference VMs. Intended host callbacks keep
    working (a real target installs its own ctx->host to supply them). This is a
    *defined* default, never a silent fall-through leaving regs[rd] stale.

  * A GENUINELY UNKNOWN hook id -- a code above PV_HOOK_CODE_MAX (0x357), which
    the compiler never emits, so it is only reachable from malformed or
    hand-crafted bytecode -- faults deterministically with PV_FAULT_BAD_HOOK (6)
    instead of silently succeeding.

Uses ziglang (a pip-installed C compiler) to build the C VM, same as
tests/test_native_toc.py and tests/test_c_vm_error_parity.py -- auto-marked
"slow" by conftest.py; run with `pytest --runslow`.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, PicoFault, isa  # noqa: E402
from picoscript_lang import HOST_HOOK_CODES  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run_bad_hook_test.exe")

PV_FAULT_BAD_HOOK = 6
HOST_HOOK_BASE = 0x7000      # PV_HOST_HOOK_BASE     -- hook codes <= 0xFF
EXT_HOST_HOOK_BASE = 0x6000  # PV_EXT_HOST_HOOK_BASE -- hook codes 0x100..0xFFF
E = isa.encode_instruction


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _imm_for(code):
    return (HOST_HOOK_BASE | code) if code <= 0xFF else (EXT_HOST_HOOK_BASE | (code & 0x0FFF))


def c_run(words):
    """Run bytecode on the C VM; return (fault_code, regs, out_bytes)."""
    words = [w & 0xFFFFFFFF for w in words]
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    fault, regs, ob = None, [], b""
    for line in out.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "FAULT":
            fault = int(p[1])
        elif p[0] == "REGS":
            regs = [int(x) for x in p[1:]]
        elif p[0] == "OUT":
            ob = bytes(int(x, 16) for x in p[1:])
    return fault, regs, ob


def hostcall(code, rd=0, rs1=0, rs2=0):
    return [E(isa.OP_NOOP, rd=rd, rs1=rs1, rs2=rs2, imm16=_imm_for(code)), E(isa.OP_RETURN)]


def load_const(reg, value):
    # reg = 0 (SUB reg, reg, <register-of-reg-itself>) then reg += value
    return [E(isa.OP_SUB, rd=reg, rs1=reg, rs2=isa.ADDR_REGISTER, imm16=0),
            E(isa.OP_ADD, rd=reg, rs1=reg, imm16=value & 0xFFFF)]


def check_unknown_hook_faults():
    """A code above PV_HOOK_CODE_MAX (0xFFF here) is genuinely unknown and must
    FAULT PV_FAULT_BAD_HOOK, not silently succeed leaving regs[rd] stale."""
    fault, _regs, _ob = c_run(hostcall(0xFFF))
    assert fault == PV_FAULT_BAD_HOOK, (
        f"unknown hook 0xFFF must fault with {PV_FAULT_BAD_HOOK}, got {fault}")


def check_defined_unbound_hook_defaults_not_faults():
    """A DEFINED host-fillable primitive with no binding in the default runtime
    (Crypto.Sha512) must NOT fault -- it returns the INV-18 default (0), exactly
    like the Python reference VM. Proves the fault does not over-reach into
    intended host callbacks."""
    code = HOST_HOOK_CODES[("Crypto", "Sha512")]
    assert code <= 0x357, code
    fault, regs, _ob = c_run(hostcall(code))
    assert fault == 0, f"defined-but-unbound Crypto.Sha512 must not fault, got FAULT {fault}"
    assert regs and regs[0] == 0, f"expected INV-18 default 0, got {regs[:1]}"

    # Python VM parity: it also does not fault for an unbound host-fillable hook.
    words = [w & 0xFFFFFFFF for w in hostcall(code)]
    vm = PicoVM(max_steps=1000)
    try:
        vm.run(list(words))
    except PicoFault as exc:  # pragma: no cover - would be a real parity break
        raise AssertionError(f"Python VM unexpectedly faulted (code={exc.code})")
    assert vm.regs[0] == 0


def check_intended_host_callback_still_works():
    """An intended host callback the default runtime DOES implement (Bits.And)
    must still work end to end: no fault, correct result in regs[rd]."""
    code = HOST_HOOK_CODES[("Bits", "And")]
    words = load_const(1, 12) + load_const(2, 10) + hostcall(code, rd=0, rs1=1, rs2=2)
    fault, regs, _ob = c_run(words)
    assert fault == 0, f"Bits.And must not fault, got FAULT {fault}"
    assert regs and regs[0] == (12 & 10), f"Bits.And(12,10) expected 8, got {regs[:1]}"


def check_arena_alloc_program_parity():
    """A compiled program that uses an unbound host-fillable primitive
    (Memory.ArenaAlloc) plus handled ones (Descriptor.*) must run without
    faulting and stay byte-identical to the Python VM -- regression guard for
    the two-tier fall-through (ArenaAlloc -> INV-18 default 0, not a fault)."""
    prog = ("int p = Memory.ArenaAlloc(8); int h = Descriptor.Make(p, 4); "
            "Descriptor.SetFlags(h, 9); int f = Descriptor.GetFlags(h); Io.WriteByte(f);")
    words = [w & 0xFFFFFFFF for w in lower_to_bytecode_safe(compile_c(prog))]
    fault, _regs, ob = c_run(words)
    assert fault == 0, f"program must not fault, got FAULT {fault}"
    py = b"".join(PicoVM(max_steps=20000).run(list(words)).output)
    assert ob == py == b"\x09", f"C {ob!r} != Python {py!r} != b'\\x09'"


def main():
    build_c_vm()
    try:
        check_unknown_hook_faults()
        check_defined_unbound_hook_defaults_not_faults()
        check_intended_host_callback_still_works()
        check_arena_alloc_program_parity()
        print("PASS: unknown hook faults (6); defined-but-unbound hooks keep the "
              "INV-18 default; intended host callbacks still work")
    finally:
        if os.path.exists(VM_EXE):
            os.remove(VM_EXE)


def test_main():
    main()


if __name__ == "__main__":
    main()
