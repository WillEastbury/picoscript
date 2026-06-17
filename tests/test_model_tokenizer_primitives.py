#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-bound tensor views and trie tokenizer primitives."""

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


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


SRC = (
    # Longest-prefix trie vocab. "hello" should beat "he".
    'int vocab = "hello=100;he=10; =20;world=101";'
    'Tokenizer.SetVocab(vocab);'
    'int text = "hello world!";'
    'print(Tokenizer.EncodeTrie(text));'
    'print(Tokenizer.Token(0)); print(Tokenizer.Token(1)); print(Tokenizer.Token(2));'
    'Io.Write(Tokenizer.DecodeTrie()); Io.WriteByte(124);'
    # Bind a tensor view to a blob card and read all/row slices.
    'Storage.UsePack(2);'
    'int blob = "XXXXabcdef";'
    'Storage.SetSlice(0, Span.Len(blob));'
    'Storage.WriteSlice(7, blob);'
    'int spec = "2|7|4|2|3|1";'
    'Model.TensorView(9, spec);'
    'print(Model.TensorOffset(9)); print(Model.TensorRows(9)); print(Model.TensorCols(9)); print(Model.TensorFormat(9));'
    'Io.Write(Model.ReadTensor(9)); Io.WriteByte(124);'
    'Io.Write(Model.ReadTensorRow(9, 1));'
)

EXPECTED = (
    (4).to_bytes(4, "big") +
    (100).to_bytes(4, "big") +
    (20).to_bytes(4, "big") +
    (101).to_bytes(4, "big") +
    b"hello world!|" +
    (4).to_bytes(4, "big") +
    (2).to_bytes(4, "big") +
    (3).to_bytes(4, "big") +
    (1).to_bytes(4, "big") +
    b"abcdef|def"
)


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _run_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_model_bound_tensor_and_trie_tokenizer():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py == EXPECTED, py


def main():
    test_model_bound_tensor_and_trie_tokenizer()
    print("PASS Model/Tokenizer primitives: longest-prefix vocab + storage-bound tensor views")


if __name__ == "__main__":
    main()
