#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed active-record card sugar over existing Storage.* hooks.

This is the first authoring layer above the low-level PicoStore API:

    Order ord = Storage.GetCard(pack, card);
    ord.qty = 42;
    Storage.SaveCard(ord);

The compiler lowers it to UsePack/EditCard/SetField/GetField/QueryCard, so no VM
record-object ABI is introduced yet. SaveCard is currently a flush/no-op because
SetField is eager; it keeps the source model ready for dirty-buffered records.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

SRC = r'''
Storage.UsePack(1);
int a = Storage.AddCard();
Order ord = Storage.GetCard(1, a);
ord.qty = 42;
Storage.SaveCard(ord);

int b = Storage.AddCard();
Order other = Storage.GetCard(1, b);
other.qty = 7;
Storage.SaveCard(other);

print(ord.qty);
int n = Storage.QueryCards(1, "qty > 40");
print(n);

for (i = 0; i < n; i++) {
    Order hit = Storage.GetCard(1, Storage.QueryResult(i));
    hit.qty--;
    Storage.SaveCard(hit);
    print(hit.qty);
}
'''

EXPECTED = [42, 1, 41]


def _py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return [int.from_bytes(o, "big") for o in vm.output]


def _js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            raw = bytes(int(x, 16) for x in p[1:])
            return [int.from_bytes(raw[i:i+4], "big") for i in range(0, len(raw), 4)]
    return []


def _js_compile_words(src):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), "c"],
                       cwd=ROOT, input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(x.strip(), 16) for x in r.stdout.splitlines() if x.strip()]


def test_active_record_cards_c_frontend():
    words = lower_to_bytecode_safe(compile_c(SRC))
    assert _py(words) == EXPECTED
    assert _js(words) == EXPECTED
    assert _js_compile_words(SRC) == words


def main():
    test_active_record_cards_c_frontend()
    print("PASS active-record cards: typed handles + dot fields lower to Storage.* (Python VM == JS VM == JS compiler)")


if __name__ == "__main__":
    main()
