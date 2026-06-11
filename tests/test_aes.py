#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crypto.Encrypt/Decrypt = AES-256-CTR, byte-identical across Python/C/JS.

Convention (2-in/1-out host ABI): rs1 = 32-byte key span; rs2 = data span whose first
16 bytes are the IV/counter and the rest is the payload. The result is IV || (payload ^
keystream). CTR is symmetric, so Encrypt and Decrypt are the same operation.

The AES S-box, key schedule and CTR are reimplemented from scratch in all three VMs
(no library), so this test pins them to a NIST SP800-38A AES-256-CTR test vector and
checks the three runtimes agree byte-for-byte, that encrypt->decrypt round-trips, and
that the binding is capability-gated (INV-17: CAP_CRYPTO).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c              # noqa: E402
from picoscript_il import lower_to_bytecode_safe     # noqa: E402
from picoscript_vm import PicoVM, PicoFault          # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")

# NIST SP800-38A F.5.5 (CTR-AES256.Encrypt), first two blocks.
KEY = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
IV = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
PT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")
CT = bytes.fromhex("601ec313775789a5b7a7f504bbf3d228f443e3ca4d62b59aca84e990cacaf5c5")

CAP_ALL = 0x3FF
CAP_CRYPTO = 1 << 9


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def make_words(method, key, data):
    prog = setbytes(1000, key) + setbytes(2000, data)
    prog += f"int k = Span.Make(1000, {len(key)}); int d = Span.Make(2000, {len(data)});"
    prog += f"int r = Crypto.{method}(k, d); Io.Write(r);"
    return lower_to_bytecode_safe(compile_c(prog))


def run_py(words, caps=None):
    out = b"".join(PicoVM(caps=caps if caps is not None else CAP_ALL).run(words).output)
    return out


def run_vm(words, cmd, caps=None):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    env = dict(os.environ)
    if caps is not None:
        env["PICOVM_CAPS"] = str(caps)
    out = subprocess.run(cmd, input=inp, capture_output=True, text=True, env=env).stdout
    fault = None
    data = b""
    for line in out.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "OUT":
            data = bytes(int(x, 16) for x in p[1:])
        if p[0] == "FAULT" and int(p[1]) != 0:
            fault = int(p[1])
    return data, fault


def main():
    build_c_vm()
    node = ["node", os.path.join(VM_DIR, "picovm_run.js")]

    # ── Encrypt: IV || ciphertext, matching the NIST vector, byte-identical on 3 VMs ──
    ew = make_words("Encrypt", KEY, IV + PT)
    expect = IV + CT
    py = run_py(ew)
    c, _ = run_vm(ew, [VM_EXE])
    js, _ = run_vm(ew, node)
    assert py == expect, f"Python AES-256-CTR != NIST vector\n got {py.hex()}\n exp {expect.hex()}"
    assert c == expect, f"C AES != NIST: {c.hex()}"
    assert js == expect, f"JS AES != NIST: {js.hex()}"

    # ── Round-trip: Decrypt(IV || ciphertext) == IV || plaintext, on all three ──
    dw = make_words("Decrypt", KEY, IV + CT)
    rt_expect = IV + PT
    assert run_py(dw) == rt_expect, "Python decrypt round-trip failed"
    assert run_vm(dw, [VM_EXE])[0] == rt_expect, "C decrypt round-trip failed"
    assert run_vm(dw, node)[0] == rt_expect, "JS decrypt round-trip failed"

    # ── Capability gating (INV-17): without CAP_CRYPTO the binding faults (8) on all 3 ──
    no_crypto = CAP_ALL & ~CAP_CRYPTO
    try:
        PicoVM(caps=no_crypto).run(ew)
        raise AssertionError("Python: Crypto.Encrypt must fault without CAP_CRYPTO")
    except PicoFault as exc:
        assert exc.code == 8, f"Python cap fault must be 8, got {exc.code}"
    assert run_vm(ew, [VM_EXE], caps=no_crypto)[1] == 8, "C must fault 8 without CAP_CRYPTO"
    assert run_vm(ew, node, caps=no_crypto)[1] == 8, "JS must fault 8 without CAP_CRYPTO"

    # ── Granted: no fault when CAP_CRYPTO is present (default CAP_ALL already includes it) ──
    assert run_vm(ew, [VM_EXE], caps=CAP_ALL)[1] is None, "C must not fault with CAP_ALL"

    print("PASS aes: Crypto.Encrypt/Decrypt = AES-256-CTR byte-identical on Python/C/JS "
          "(NIST SP800-38A vector), encrypt/decrypt round-trips, and is CAP_CRYPTO-gated (INV-17)")


if __name__ == "__main__":
    main()
