#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Random.U32 seed injection (INV-15)."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

from picoscript_vm import PicoVM  # noqa: E402
from test_vm_safety import build_c_vm  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")

# Random.U32 -> R1, then write the low byte from R1. Repeat enough times that
# seed sensitivity is observable without asserting equality across runtimes.
WORDS = []
for _ in range(8):
    WORDS.extend([0x01007020, 0x01107072])

SEED_A = 0x12345678
SEED_B = 0x87654321


def _input(words):
    return f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"


def _parse_out(text):
    fault = None
    out = None
    for line in text.splitlines():
        if line.startswith("FAULT"):
            fault = int(line.split()[1])
        elif line.startswith("OUT"):
            out = bytes(int(p, 16) for p in line.split()[1:])
    assert fault == 0, text
    assert out is not None, text
    assert len(out) == 8, text
    return out


def py_out(seed):
    vm = PicoVM(seed=seed)
    vm.run(WORDS)
    out = b"".join(vm.output)
    assert len(out) == 8, out
    return out


def c_out(seed):
    env = dict(os.environ)
    env["PICOVM_SEED"] = hex(seed)
    r = subprocess.run([VM_EXE], input=_input(WORDS), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr or r.stdout
    return _parse_out(r.stdout)


def js_out(seed):
    env = dict(os.environ)
    env["PICOVM_SEED"] = hex(seed)
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=_input(WORDS), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr or r.stdout
    return _parse_out(r.stdout)


def assert_seeded(name, runner):
    a1 = runner(SEED_A)
    a2 = runner(SEED_A)
    b = runner(SEED_B)
    assert a1 == a2, f"{name}: same seed must replay the same Random.U32 bytes ({a1.hex()} != {a2.hex()})"
    assert a1 != b, f"{name}: different seeds must change the Random.U32 bytes ({a1.hex()})"


def main():
    build_c_vm()
    assert_seeded("Python VM", py_out)
    assert_seeded("C VM", c_out)
    assert_seeded("JS VM", js_out)
    print("PASS determinism: host-injected Random.U32 seeds replay per runtime on Python / C / JS")


if __name__ == "__main__":
    main()
