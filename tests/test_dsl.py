#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Readable storage / device DSL for the BASIC frontend.

STORE = writes, LOAD = reads, GPIO = pins. Pure frontend sugar lowering to the
canonical Storage.*/Gpio.* host hooks, so:
  (1) the DSL runs correctly on the VM,
  (2) its bytecode equals the canonical Ns.Method spelling,
  (3) the Python frontend and the JS frontend (vm/picoc.js) emit identical
      bytecode -- which also guards the ext-hook (code > 0xFF) encoding that was
      previously only handled in the Python lowerer (EXT_HOST_HOOK_BASE).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def bc(src, comp=compile_basic):
    return lower_to_bytecode_safe(comp(src))


def js_compile(src, lang="basic"):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def run_basic(src):
    host = HostApi(); vm = PicoVM(host=host); vm.load(bc(src)); vm.run()

    def s32(b):
        v = int.from_bytes(b, "big")
        return v - 0x100000000 if v & 0x80000000 else v
    return [s32(b) for b in vm.output]


STORE_DSL = '''DIM QTY = "qty"
DIM QRY = "qty > 40"
STORE USE PACK 1
DIM A NEW CARD
STORE SET QTY = 42
DIM B NEW CARD
STORE SET QTY = 7
DIM C NEW CARD
STORE SET QTY = 99
LOAD CARD B
PRINT LOAD QTY
STORE SET QTY = 50
PRINT LOAD QTY
DIM N = LOAD QUERY QRY
PRINT N
PRINT LOAD RESULT 0
PRINT LOAD RESULT 1
PRINT LOAD RESULT 2
STORE DELETE CARD 1
PRINT LOAD QUERY QRY
'''

STORE_CANON = '''DIM QTY = "qty"
DIM QRY = "qty > 40"
Storage.UsePack(1)
DIM A = Storage.AddCard()
Storage.SetField(QTY, 42)
DIM B = Storage.AddCard()
Storage.SetField(QTY, 7)
DIM C = Storage.AddCard()
Storage.SetField(QTY, 99)
Storage.EditCard(B)
PRINT Storage.GetField(QTY)
Storage.SetField(QTY, 50)
PRINT Storage.GetField(QTY)
DIM N = Storage.QueryCard(QRY)
PRINT N
PRINT Storage.QueryResult(0)
PRINT Storage.QueryResult(1)
PRINT Storage.QueryResult(2)
Storage.DeleteCard(1)
PRINT Storage.QueryCard(QRY)
'''

GPIO_DSL = '''GPIO DIR 2 = OUT
GPIO WRITE 2 = 1024
DIM v = GPIO READ 2
PRINT v
GPIO PULL 3 = UP
DIM d = GPIO DIR 2
PRINT d
DIM n = GPIO COUNT
PRINT n
'''

# Canonical GPIO via the Python frontend (Gpio.* dotted calls -- in BASIC the
# GPIO keyword intentionally shadows the C-style Gpio.* form).
GPIO_CANON = '''Gpio.SetDir(2, 1)
Gpio.Write(2, 1024)
v = Gpio.Read(2)
print(v)
Gpio.SetPull(3, 1)
d = Gpio.GetDir(2)
print(d)
n = Gpio.Count()
print(n)
'''


def test_storage_dsl_runs():
    # 3 cards qty=42/7/99; edit B, read 7, set 50, query qty>40 -> {42,50,99}=3,
    # ids 1,2,3; delete card 1; query again -> 2.
    assert run_basic(STORE_DSL) == [7, 50, 3, 1, 2, 3, 2]


def test_storage_dsl_equals_canonical():
    assert bc(STORE_DSL) == bc(STORE_CANON)


def test_gpio_dsl_equals_canonical():
    assert bc(GPIO_DSL) == bc(GPIO_CANON, compile_python)


def test_storage_dsl_py_equals_js():
    assert bc(STORE_DSL) == js_compile(STORE_DSL, "basic")


def test_gpio_dsl_py_equals_js():
    assert bc(GPIO_DSL) == js_compile(GPIO_DSL, "basic")


def test_ext_hook_encoding_py_equals_js():
    # Regression: 2-byte host hooks (code > 0xFF) must ENCODE identically in the
    # Python lowerer and vm/picoc.js (EXT_HOST_HOOK_BASE), not merely decode the
    # same. Html.Encode is 0x0146; the GPIO hooks above are 0x0150-0x0156.
    src = 'int r = Html.Encode("hi");\nprint(r);\n'
    assert bc(src, compile_c) == js_compile(src, "c")


def test_server_entry_transparent_and_parity():
    # Server.Main { ... } (C) / SERVER ... ENDSERVER (BASIC) are transparent
    # wrappers: the body is the program entry, byte-identical to the bare body and
    # across the Python and JS frontends. The two surface forms agree too.
    cwrap = "Server.Main {\n  Net.Status(200);\n  Io.WriteByte(52);\n  Io.WriteByte(50);\n}\n"
    cbare = "Net.Status(200);\nIo.WriteByte(52);\nIo.WriteByte(50);\n"
    bwrap = "SERVER\nNet.Status(200)\nIo.WriteByte(52)\nIo.WriteByte(50)\nENDSERVER\n"
    bbare = "Net.Status(200)\nIo.WriteByte(52)\nIo.WriteByte(50)\n"
    assert bc(cwrap, compile_c) == bc(cbare, compile_c)
    assert bc(bwrap) == bc(bbare)
    assert bc(bwrap) == bc(cwrap, compile_c)
    assert bc(cwrap, compile_c) == js_compile(cwrap, "c")
    assert bc(bwrap) == js_compile(bwrap, "basic")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS storage/GPIO DSL: runs, == canonical, Python==JS frontend, ext-hook encoding")


if __name__ == "__main__":
    main()
