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


# Capsule / device / stream BASIC DSL (PACK/CARD/FIFO/DEVICE/STREAM keywords).
STREAM_DSL = (
    'DIM DEV = DEVICE OPEN "csi0"\n'
    'DIM S = STREAM OPEN DEV 196616\n'
    'DIM TOTAL = 0\n'
    'DIM L = STREAM NEXT S\n'
    'WHILE L <> 0\n'
    '  DIM SP = STREAM SPAN L\n'
    '  DIM N = Span.Len(SP)\n'
    '  FOR I = 0 TO N - 1\n'
    '    TOTAL = TOTAL + Span.Get(SP, I)\n'
    '  NEXT\n'
    '  STREAM RELEASE L\n'
    '  L = STREAM NEXT S\n'
    'ENDWHILE\n'
    'STREAM CLOSE S\n'
    'DEVICE CLOSE DEV\n'
    'PRINT TOTAL\n'
)

# Canonical Device.*/Stream.* via the Python frontend (dotted calls -- in BASIC
# DEVICE/STREAM keywords intentionally shadow the C-style Device.*/Stream.* form).
STREAM_CANON_PY = (
    'dev = Device.Open("csi0", 0)\n'
    's = Stream.Open(dev, 196616)\n'
    'total = 0\n'
    'l = Stream.Next(s)\n'
    'while l != 0:\n'
    '    sp = Stream.Span(l)\n'
    '    n = Span.Len(sp)\n'
    '    for i in range(0, n):\n'
    '        total = total + Span.Get(sp, i)\n'
    '    Stream.Release(l)\n'
    '    l = Stream.Next(s)\n'
    'Stream.Close(s)\n'
    'Device.Close(dev)\n'
    'print(total)\n'
)

CAPSULE_DSL = (
    'PACK USE 1024\n'
    'DIM QTY = "qty"\n'
    'CARD WRITE 5 = QTY\n'
    'DIM G = CARD READ 5\n'
    'DIM ADDR = CARD ADDRESS 1024 5\n'
    'DIM F = FIFO OPEN "ipc"\n'
    'FIFO SEND F = QTY\n'
    'PRINT FIFO POLL F\n'
)


def test_stream_dsl_runs():
    # RX ring csi0 buf=4 frames=3 (cfg 196616), frame n byte i=(n+i)&255 -> 6+10+14=30.
    assert run_basic(STREAM_DSL) == [30]


def test_stream_dsl_equals_canonical():
    # The BASIC DSL lowers to the same bytecode as the canonical dotted spelling
    # in the (lowerer-sharing) Python frontend.
    assert bc(STREAM_DSL) == lower_to_bytecode_safe(compile_python(STREAM_CANON_PY))


def test_stream_dsl_py_equals_js_frontend():
    assert bc(STREAM_DSL) == js_compile(STREAM_DSL, "basic")


def test_capsule_dsl_py_equals_js_frontend():
    # Pack/Card/Fifo are provider-backed (NOOP in the bare VM) but the DSL must
    # lower + mirror byte-identically across the Python and JS frontends.
    assert bc(CAPSULE_DSL) == js_compile(CAPSULE_DSL, "basic")


# UI / Event BASIC DSL (UI/EVENT keywords; shadow the dotted Ui.*/Event.* forms).
UI_DSL = (
    'DIM WIN = UI WINDOW "Login"\n'
    'UI SIZE WIN = 220, 130\n'
    'DIM NAME = UI LABEL WIN "Name:"\n'
    'UI POS NAME = 12, 16\n'
    'DIM BOX = UI TEXTBOX WIN "guest"\n'
    'UI POS BOX = 70, 12\n'
    'UI SETID BOX = 1\n'
    'DIM GO = UI BUTTON WIN "Sign in"\n'
    'UI POS GO = 70, 86\n'
    'UI SETID GO = 3\n'
    'Io.Write(UI SERIALIZE WIN)\n'
)
UI_CANON_PY = (
    'win = Ui.Window("Login")\n'
    'Ui.Size(win, 220 * 65536 + 130)\n'
    'name = Ui.Label(win, "Name:")\n'
    'Ui.Pos(name, 12 * 65536 + 16)\n'
    'box = Ui.TextBox(win, "guest")\n'
    'Ui.Pos(box, 70 * 65536 + 12)\n'
    'Ui.SetId(box, 1)\n'
    'go = Ui.Button(win, "Sign in")\n'
    'Ui.Pos(go, 70 * 65536 + 86)\n'
    'Ui.SetId(go, 3)\n'
    'Io.Write(Ui.Serialize(win))\n'
)
EVENT_DSL = (
    'DIM E1 = EVENT POST 10 100\n'
    'DIM E2 = EVENT POST 20 200\n'
    'DIM N = EVENT COUNT\n'
    'DIM A = EVENT NEXT\n'
    'EVENT SETDATA A = "x"\n'
    'PRINT N\n'
    'PRINT EVENT TYPE A\n'
    'PRINT EVENT TARGET A\n'
)


def test_ui_dsl_equals_canonical_and_runs():
    # The UI DSL lowers to the same bytecode as the canonical dotted Ui.* spelling
    # (Python frontend shares the BASIC lowerer) -- identical bytecode == identical
    # output. Also confirm it produces a non-empty serialized wire.
    assert bc(UI_DSL) == lower_to_bytecode_safe(compile_python(UI_CANON_PY))
    host = HostApi(); vm = PicoVM(host=host); vm.load(bc(UI_DSL)); vm.run()
    assert len(b"".join(vm.output)) == 300


def test_ui_dsl_py_equals_js_frontend():
    assert bc(UI_DSL) == js_compile(UI_DSL, "basic")


def test_event_dsl_runs_and_py_equals_js_frontend():
    assert run_basic(EVENT_DSL) == [2, 10, 100]
    assert bc(EVENT_DSL) == js_compile(EVENT_DSL, "basic")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS storage/GPIO/capsule/device/stream/ui/event DSL: runs, == canonical, Python==JS frontend, ext-hook encoding")


if __name__ == "__main__":
    main()
