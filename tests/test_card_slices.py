#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Partial large-card slice primitives.

Large cards (datasets, model weights, sidecars) must be slice-first: a program
should read/write byte ranges without materializing the whole card. The default
Python/JS simulators implement a tiny blob-card backend for parity; real PIOS
hosts can back the same hooks with SD/WALFS range I/O.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


SLICE_C = r'''
int data = "abcdefghijklmnopqrstuvwxyz";
Storage.UsePack(1);
Storage.SetSlice(0, Span.Len(data));
Storage.WriteSlice(7, data);
print(Storage.CardLen(7));

Storage.SetSlice(10, 5);
int mid = Storage.ReadSlice(7);
Io.Write(mid);
Io.WriteByte(124);

int patch = "XYZ";
Storage.SetSlice(5, Span.Len(patch));
Storage.WriteSlice(7, patch);

Storage.SetSlice(0, Storage.CardLen(7));
int all = Storage.ReadSlice(7);
Io.Write(all);
'''

SLICE_BASIC = r'''
DIM DATA = "abcdefghijklmnopqrstuvwxyz"
Storage.UsePack(1)
Storage.SetSlice(0, Span.Len(DATA))
Storage.WriteSlice(7, DATA)
PRINT Storage.CardLen(7)

Storage.SetSlice(10, 5)
DIM MID = Storage.ReadSlice(7)
Io.Write(MID)
Io.WriteByte(124)

DIM PATCH = "XYZ"
Storage.SetSlice(5, Span.Len(PATCH))
Storage.WriteSlice(7, PATCH)

Storage.SetSlice(0, Storage.CardLen(7))
DIM ALL = Storage.ReadSlice(7)
Io.Write(ALL)
'''

EXPECTED_TAIL = b"klmno|abcdeXYZijklmnopqrstuvwxyz"


def _py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def _check(src, compiler):
    words = lower_to_bytecode_safe(compiler(src))
    py = _py(words)
    js = _js(words)
    assert py == js, "Python VM != JS VM for Storage.* slices"
    assert int.from_bytes(py[:4], "big") == 26, py
    assert py[4:] == EXPECTED_TAIL, py[4:]


def test_card_slices_c_and_basic():
    _check(SLICE_C, compile_c)
    _check(SLICE_BASIC, compile_basic)


def main():
    test_card_slices_c_and_basic()
    print("PASS Storage.* slices: SetSlice/CardLen/ReadSlice/WriteSlice (Python VM == JS VM)")


if __name__ == "__main__":
    main()
