#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_playground.py -- build docs/playground.html: a single doc-stack that shows
every PicoScript construct in BOTH surface styles side by side AND lets you run /
step / debug it live in the browser via the inlined JS VM (vm/picovm.js).

Each example is compiled by the real toolchain (compile_c / compile_basic ->
PicoIL -> bytecode) and verified to run on PicoVM, so the page can never drift
from the language.  picovm.js + pico_hooks.js are inlined so the file works from
file:// with no server.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_basic import compile_basic
from picoscript_python import compile_python
from picoscript_english import compile_english
from picoscript_il import lower_to_bytecode_safe, il_to_text
from picoscript_vm import PicoVM


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def out_ints(vm):
    return [s32(int.from_bytes(b, "big")) for b in vm.output]


# (title, description, C-style source, BASIC-style source)
CONSTRUCTS = [
    ("Variables & arithmetic",
     "Declare variables with DIM and evaluate expressions. Both styles are "
     "case-insensitive for keywords and variable names; one global scope.",
     "int a = 6;\nint b = 7;\nprint(a * b + 1);",
     "DIM A = 6\nDIM B = 7\nPRINT A * B + 1",
     "a = 6\nb = 7\nprint(a * b + 1)",
     "Set a to 6.\nSet b to 7.\nPrint a times b plus 1."),

    ("Conditional (if / else)",
     "Branch on a comparison. C uses braces; BASIC uses IF/THEN/ELSE/ENDIF. "
     "BASIC accepts symbol (>, =, <>) or word (GT, EQ, NE) comparators.",
     "int x = 7;\nif (x > 5) {\n    print(100);\n} else {\n    print(200);\n}",
     "DIM X = 7\nIF X > 5 THEN\n    PRINT 100\nELSE\n    PRINT 200\nENDIF",
     "x = 7\nif x > 5:\n    print(100)\nelse:\n    print(200)",
     "Set x to 7.\nIf x is greater than 5:\n    Print 100.\nOtherwise:\n    Print 200."),

    ("While loop",
     "Repeat while a condition holds (factorial of 5).",
     "int n = 5;\nint f = 1;\nwhile (n > 1) {\n    f = f * n;\n    n = n - 1;\n}\nprint(f);",
     "DIM N = 5\nDIM F = 1\nWHILE N > 1\n    F = F * N\n    DEC N\nENDWHILE\nPRINT F",
     "n = 5\nf = 1\nwhile n > 1:\n    f = f * n\n    n -= 1\nprint(f)",
     "Set n to 5.\nSet f to 1.\nWhile n is greater than 1:\n    Set f to f times n.\n    Decrease n by 1.\nPrint f."),

    ("Counted loop (for)",
     "Sum 1..10. C uses a C-style for with i++; BASIC uses FOR/TO/NEXT.",
     "int s = 0;\nfor (i = 1; i <= 10; i++) {\n    s += i;\n}\nprint(s);",
     "DIM S = 0\nFOR I = 1 TO 10\n    S += I\nNEXT\nPRINT S",
     "s = 0\nfor i in range(1, 11):\n    s += i\nprint(s)",
     "Set s to 0.\nFor each i from 1 to 10:\n    Increase s by i.\nPrint s."),

    ("Index iteration (foreach)",
     "Iterate an index 0..N-1. C expresses it as a for loop; BASIC has FOREACH.",
     "int a = 0;\nfor (j = 0; j < 5; j++) {\n    a += j;\n}\nprint(a);",
     "DIM A = 0\nFOREACH J IN 5\n    A += J\nENDFOREACH\nPRINT A",
     "a = 0\nfor j in range(5):\n    a += j\nprint(a)",
     "Set a to 0.\nRepeat 5 times with j:\n    Increase a by j.\nPrint a."),

    ("Operators (++ -- ?: && % )",
     "Increment/decrement, the ternary ?: (IIF in BASIC), short-circuit AND/OR, "
     "real modulo (% / MOD) and compound assignment.",
     "int x = 10;\nx++;\nx += 5;\nint y = x % 7;\nint z = (y == 2 && x > 10) ? 100 : 0;\nprint(x); print(y); print(z);",
     "DIM X = 10\nINC X\nX += 5\nDIM Y = X MOD 7\nDIM Z = IIF(Y = 2 AND X > 10, 100, 0)\nPRINT X\nPRINT Y\nPRINT Z",
     "x = 10\nx += 1\nx += 5\ny = x % 7\nz = 100 if y == 2 and x > 10 else 0\nprint(x)\nprint(y)\nprint(z)",
     "Set x to 10.\nIncrease x by 1.\nIncrease x by 5.\nSet y to x modulo 7.\nSet z to 100 if y is 2 and x is greater than 10 otherwise 0.\nPrint x.\nPrint y.\nPrint z."),

    ("Multi-way branch (switch)",
     "Pick a branch by value &mdash; a first-class switch in every style.",
     "int code = 2;\nswitch (code) {\n    case 1: print(10); break;\n    case 2: print(20); break;\n    default: print(0);\n}",
     "DIM CODE = 2\nSWITCH CODE\n    CASE 1\n        PRINT 10\n    CASE 2\n        PRINT 20\n    DEFAULT\n        PRINT 0\nENDSWITCH",
     "code = 2\nmatch code:\n    case 1:\n        print(10)\n    case 2:\n        print(20)\n    case _:\n        print(0)",
     "Set code to 2.\nChoose code:\n    When 1:\n        Print 10.\n    When 2:\n        Print 20.\n    Otherwise:\n        Print 0."),

    ("Jump-table dispatch (state machine)",
     "<code>dispatch</code> is a switch that compiles to a real <b>jump table</b> (an "
     "indexed jump) instead of a compare chain &mdash; O(1) dispatch on a dense integer "
     "selector. It is the primitive under switch / match / event / hook / interrupt / "
     "protocol dispatch, so you can write a state machine (or a protocol parser) directly "
     "in PicoScript. Here a 3-state ring advances 5 steps (0&rarr;1&rarr;2&rarr;0&rarr;1&rarr;2). "
     "Out-of-range selectors fall through a bounds guard to the default.",
     "int st = 0;\nint k = 0;\nwhile (k < 5) {\n    dispatch (st) {\n        case 0: st = 1; break;\n        case 1: st = 2; break;\n        case 2: st = 0; break;\n    }\n    k++;\n}\nprint(st);",
     "DIM ST = 0\nDIM K = 0\nWHILE K < 5\n    DISPATCH ST\n        CASE 0\n            ST = 1\n        CASE 1\n            ST = 2\n        CASE 2\n            ST = 0\n    ENDDISPATCH\n    INC K\nENDWHILE\nPRINT ST",
     "st = 0\nk = 0\nwhile k < 5:\n    dispatch st:\n        case 0:\n            st = 1\n        case 1:\n            st = 2\n        case 2:\n            st = 0\n    k += 1\nprint(st)",
     "Set st to 0.\nSet k to 0.\nWhile k is less than 5:\n    Dispatch on st:\n        When 0:\n            Set st to 1.\n        When 1:\n            Set st to 2.\n        When 2:\n            Set st to 0.\n    Increase k by 1.\nPrint st."),

    ("Subroutine (call / gosub)",
     "Factor shared logic. Variables are global, so the routine sees ACC.",
     "void dbl() {\n    acc = acc + acc;\n}\nint acc = 21;\ndbl();\nprint(acc);",
     "DIM ACC = 21\nGOSUB DBL\nPRINT ACC\nRETURN\nSUB DBL\n    ACC = ACC + ACC\nENDSUB",
     "def dbl():\n    acc = acc + acc\nacc = 21\ndbl()\nprint(acc)",
     "Define dbl:\n    Set acc to acc plus acc.\nSet acc to 21.\nDo dbl.\nPrint acc."),

    ("Unconditional jump (goto)",
     "A back-jump loop with a label and goto &mdash; now first-class in every style.",
     "int n = 0;\ntop:\nn++;\nif (n < 4) { goto top; }\nprint(n);",
     "DIM N = 0\nTOP:\nINC N\nIF N < 4 THEN\n    GOTO TOP\nENDIF\nPRINT N",
     "n = 0\nlabel top\nn += 1\nif n < 4:\n    goto top\nprint(n)",
     "Set n to 0.\nLabel top.\nIncrease n by 1.\nIf n is less than 4:\n    Go to top.\nPrint n."),

    ("Post-test loop (do)",
     "A loop whose body always runs at least once (condition checked at the bottom).",
     "int i = 0;\nint s = 0;\ndo {\n    i++;\n    s += i;\n} while (i < 5);\nprint(s);",
     "DIM I = 0\nDIM S = 0\nDO\n    INC I\n    S += I\nLOOP UNTIL I >= 5\nPRINT S",
     "i = 0\ns = 0\ndo:\n    i += 1\n    s += i\nuntil i >= 5\nprint(s)",
     "Set i to 0.\nSet s to 0.\nRepeat:\n    Increase i by 1.\n    Increase s by i.\nUntil i is at least 5.\nPrint s."),

    ("Early exit &amp; skip (break / skip)",
     "Sum 1..10 but skip multiples of 3 and stop once the sum passes 20.",
     "int s = 0;\nfor (i = 1; i <= 10; i++) {\n    if (i % 3 == 0) { continue; }\n    s += i;\n    if (s > 20) { break; }\n}\nprint(s);",
     "DIM S = 0\nFOR I = 1 TO 10\n    IF I MOD 3 = 0 THEN\n        SKIP\n    ENDIF\n    S += I\n    IF S > 20 THEN\n        BREAK\n    ENDIF\nNEXT\nPRINT S",
     "s = 0\nfor i in range(1, 11):\n    if i % 3 == 0:\n        continue\n    s += i\n    if s > 20:\n        break\nprint(s)",
     "Set s to 0.\nFor each i from 1 to 10:\n    If i modulo 3 is 0:\n        Skip.\n    Increase s by i.\n    If s is greater than 20:\n        Stop.\nPrint s."),

    ("HTTP response (Net.*)",
     "Set an HTTP status/type and emit a value. Namespaces are case-insensitive too.",
     "Net.Status(200);\nNet.Type(\"application/json\");\nprint(42);",
     "NET.STATUS(200)\nNET.TYPE(\"application/json\")\nPRINT 42",
     "Net.Status(200)\nNet.Type(\"application/json\")\nprint(42)",
     "Net.Status(200).\nNet.Type(\"application/json\").\nPrint 42."),

    ("Cards: CRUD &amp; query (Storage.*)",
     "Program-level card store. Field names and queries are UTF-8 byte-spans built "
     "in arena memory (Memory.Set + Span.Make); cards are serialized with the "
     "PicoBinarySerializer into a PicoStore. UsePack selects a pack, AddCard/EditCard "
     "select the current card, then SetField/GetField/QueryCard operate on it. This "
     "creates 3 cards, reads (7) and updates (50) one, queries qty&gt;40 (3 ids), then "
     "deletes one and re-queries (2).",
     "Memory.Set(200,113); Memory.Set(201,116); Memory.Set(202,121);\n"
     "int qty = Span.Make(200, 3);\n"
     "Memory.Set(210,113); Memory.Set(211,116); Memory.Set(212,121); Memory.Set(213,32);\n"
     "Memory.Set(214,62); Memory.Set(215,32); Memory.Set(216,52); Memory.Set(217,48);\n"
     "int qry = Span.Make(210, 8);\n"
     "Storage.UsePack(1);\n"
     "int a = Storage.AddCard(); Storage.SetField(qty, 42);\n"
     "int b = Storage.AddCard(); Storage.SetField(qty, 7);\n"
     "int c = Storage.AddCard(); Storage.SetField(qty, 99);\n"
     "Storage.EditCard(b); print(Storage.GetField(qty));\n"
     "Storage.SetField(qty, 50); print(Storage.GetField(qty));\n"
     "int n = Storage.QueryCard(qry); print(n);\n"
     "print(Storage.QueryResult(0)); print(Storage.QueryResult(1)); print(Storage.QueryResult(2));\n"
     "Storage.DeleteCard(1); print(Storage.QueryCard(qry));",
     "Memory.Set(200, 113)\nMemory.Set(201, 116)\nMemory.Set(202, 121)\n"
     "DIM QTY = Span.Make(200, 3)\n"
     "Memory.Set(210, 113)\nMemory.Set(211, 116)\nMemory.Set(212, 121)\nMemory.Set(213, 32)\n"
     "Memory.Set(214, 62)\nMemory.Set(215, 32)\nMemory.Set(216, 52)\nMemory.Set(217, 48)\n"
     "DIM QRY = Span.Make(210, 8)\n"
     "Storage.UsePack(1)\n"
     "DIM A = Storage.AddCard()\nStorage.SetField(QTY, 42)\n"
     "DIM B = Storage.AddCard()\nStorage.SetField(QTY, 7)\n"
     "DIM C = Storage.AddCard()\nStorage.SetField(QTY, 99)\n"
     "Storage.EditCard(B)\nPRINT Storage.GetField(QTY)\n"
     "Storage.SetField(QTY, 50)\nPRINT Storage.GetField(QTY)\n"
     "DIM N = Storage.QueryCard(QRY)\nPRINT N\n"
     "PRINT Storage.QueryResult(0)\nPRINT Storage.QueryResult(1)\nPRINT Storage.QueryResult(2)\n"
     "Storage.DeleteCard(1)\nPRINT Storage.QueryCard(QRY)",
     "Memory.Set(200, 113)\nMemory.Set(201, 116)\nMemory.Set(202, 121)\n"
     "qty = Span.Make(200, 3)\n"
     "Memory.Set(210, 113)\nMemory.Set(211, 116)\nMemory.Set(212, 121)\nMemory.Set(213, 32)\n"
     "Memory.Set(214, 62)\nMemory.Set(215, 32)\nMemory.Set(216, 52)\nMemory.Set(217, 48)\n"
     "qry = Span.Make(210, 8)\n"
     "Storage.UsePack(1)\n"
     "a = Storage.AddCard()\nStorage.SetField(qty, 42)\n"
     "b = Storage.AddCard()\nStorage.SetField(qty, 7)\n"
     "c = Storage.AddCard()\nStorage.SetField(qty, 99)\n"
     "Storage.EditCard(b)\nprint(Storage.GetField(qty))\n"
     "Storage.SetField(qty, 50)\nprint(Storage.GetField(qty))\n"
     "n = Storage.QueryCard(qry)\nprint(n)\n"
     "print(Storage.QueryResult(0))\nprint(Storage.QueryResult(1))\nprint(Storage.QueryResult(2))\n"
     "Storage.DeleteCard(1)\nprint(Storage.QueryCard(qry))",
     "Memory.Set(200, 113).\nMemory.Set(201, 116).\nMemory.Set(202, 121).\n"
     "Set qty to Span.Make(200, 3).\n"
     "Memory.Set(210, 113).\nMemory.Set(211, 116).\nMemory.Set(212, 121).\nMemory.Set(213, 32).\n"
     "Memory.Set(214, 62).\nMemory.Set(215, 32).\nMemory.Set(216, 52).\nMemory.Set(217, 48).\n"
     "Set qry to Span.Make(210, 8).\n"
     "Storage.UsePack(1).\n"
     "Set a to Storage.AddCard().\nStorage.SetField(qty, 42).\n"
     "Set b to Storage.AddCard().\nStorage.SetField(qty, 7).\n"
     "Set c to Storage.AddCard().\nStorage.SetField(qty, 99).\n"
     "Storage.EditCard(b).\nPrint Storage.GetField(qty).\n"
     "Storage.SetField(qty, 50).\nPrint Storage.GetField(qty).\n"
     "Set n to Storage.QueryCard(qry).\nPrint n.\n"
     "Print Storage.QueryResult(0).\nPrint Storage.QueryResult(1).\nPrint Storage.QueryResult(2).\n"
     "Storage.DeleteCard(1).\nPrint Storage.QueryCard(qry)."),

    ("Streaming: DMA ring (Device.* / Stream.*)",
     "Streaming hardware is a producer/consumer ring of DMA buffers, structurally "
     "like Req/Resp but over hardware. Device.Open names a device; Stream.Open starts "
     "a ring (here RX, buf=4, frames=3 -&gt; cfg 196616); then loop Next/Span/Release. "
     "Each Stream.Span is a zero-copy view into the leased buffer. The reference "
     "emulator generates frame n as bytes (n+i)&amp;255, so the three frames sum to "
     "6+10+14 = 30. PIOS injects the real DMA driver under the same hooks; the editor "
     "Stream panel renders the ring live.",
     "int dev = Device.Open(\"csi0\", 0);\n"
     "int s = Stream.Open(dev, 196616);\n"
     "int total = 0;\n"
     "int l = Stream.Next(s);\n"
     "while (l != 0) {\n"
     "  int sp = Stream.Span(l);\n"
     "  int n = Span.Len(sp);\n"
     "  for (i = 0; i < n; i = i + 1) { total = total + Span.Get(sp, i); }\n"
     "  Stream.Release(l);\n"
     "  l = Stream.Next(s);\n"
     "}\n"
     "Stream.Close(s); Device.Close(dev);\n"
     "print(total);",
     "DIM DEV = DEVICE OPEN \"csi0\"\n"
     "DIM S = STREAM OPEN DEV 196616\n"
     "DIM TOTAL = 0\n"
     "DIM L = STREAM NEXT S\n"
     "WHILE L <> 0\n"
     "  DIM SP = STREAM SPAN L\n"
     "  DIM N = Span.Len(SP)\n"
     "  FOR I = 0 TO N - 1\n"
     "    LET TOTAL = TOTAL + Span.Get(SP, I)\n"
     "  NEXT\n"
     "  STREAM RELEASE L\n"
     "  LET L = STREAM NEXT S\n"
     "ENDWHILE\n"
     "STREAM CLOSE S\nDEVICE CLOSE DEV\n"
     "PRINT TOTAL",
     "dev = Device.Open(\"csi0\", 0)\n"
     "s = Stream.Open(dev, 196616)\n"
     "total = 0\n"
     "l = Stream.Next(s)\n"
     "while l != 0:\n"
     "    sp = Stream.Span(l)\n"
     "    n = Span.Len(sp)\n"
     "    for i in range(0, n):\n"
     "        total = total + Span.Get(sp, i)\n"
     "    Stream.Release(l)\n"
     "    l = Stream.Next(s)\n"
     "Stream.Close(s)\nDevice.Close(dev)\n"
     "print(total)",
     "Set dev to Device.Open(\"csi0\", 0).\n"
     "Set s to Stream.Open(dev, 196616).\n"
     "Set total to 0.\n"
     "Set l to Stream.Next(s).\n"
     "While l is not 0:\n"
     "    Set sp to Stream.Span(l).\n"
     "    Set n to Span.Len(sp).\n"
     "    For each i from 0 to n minus 1:\n"
     "        Increase total by Span.Get(sp, i).\n"
     "    Stream.Release(l).\n"
     "    Set l to Stream.Next(s).\n"
     "Stream.Close(s).\nDevice.Close(dev).\n"
     "Print total."),

    ("Testing: PSUnit assertions (Assert.*)",
     "PSUnit is a PicoScript-authored test harness. A test makes assertions with "
     "Assert.Eq(actual, expected) and Assert.True(cond); the runtime tallies them, "
     "and the runner (psunit.py) or this editor reports pass/fail from "
     "Assert.Count()/Assert.Failed() -- so a test body is just assertions. In BASIC "
     "the idiomatic ASSERT keyword takes any condition (no dotted call).",
     "int a = 6;\nint b = 7;\n"
     "Assert.Eq(a * b, 42);\n"
     "Assert.True(a < b);\n"
     "print(a * b);",
     "DIM A = 6\nDIM B = 7\n"
     "ASSERT A * B = 42\n"
     "ASSERT A < B\n"
     "PRINT A * B",
     "a = 6\nb = 7\n"
     "Assert.Eq(a * b, 42)\n"
     "Assert.True(a < b)\n"
     "print(a * b)",
     "Set a to 6.\nSet b to 7.\n"
     "Assert.Eq(a * b, 42).\n"
     "Assert.True(a < b).\n"
     "Print a times b."),

    ("Remote UI: a window (Ui.* / Event.*)",
     "Build a window + controls as a retained scene tree, then Ui.Serialize emits "
     "a compact wire (reusing the PicoSerializer/PSC1 record format) that a thin "
     "remote client renders -- see the Remote UI tab. Pos/Size pack x,y / w,h as "
     "(x<<16)|y. User input (button click, checkbox) comes back as Event.* records "
     "keyed by control id. The program prints the wire length.",
     "int win = Ui.Window(\"Login\");\n"
     "Ui.Size(win, 220 * 65536 + 130);\n"
     "int name = Ui.Label(win, \"Name:\");\n"
     "Ui.Pos(name, 12 * 65536 + 16);\n"
     "int box = Ui.TextBox(win, \"guest\");\n"
     "Ui.Pos(box, 70 * 65536 + 12); Ui.SetId(box, 1);\n"
     "int rem = Ui.Checkbox(win, \"Remember me\");\n"
     "Ui.Pos(rem, 12 * 65536 + 52); Ui.SetId(rem, 2); Ui.SetValue(rem, 1);\n"
     "int go = Ui.Button(win, \"Sign in\");\n"
     "Ui.Pos(go, 70 * 65536 + 86); Ui.SetId(go, 3);\n"
     "print(Span.Len(Ui.Serialize(win)));",
     "DIM WIN = UI WINDOW \"Login\"\n"
     "UI SIZE WIN = 220, 130\n"
     "DIM NAME = UI LABEL WIN \"Name:\"\n"
     "UI POS NAME = 12, 16\n"
     "DIM BOX = UI TEXTBOX WIN \"guest\"\n"
     "UI POS BOX = 70, 12\n"
     "UI SETID BOX = 1\n"
     "DIM REM = UI CHECKBOX WIN \"Remember me\"\n"
     "UI POS REM = 12, 52\n"
     "UI SETID REM = 2\n"
     "UI SETVALUE REM = 1\n"
     "DIM GO = UI BUTTON WIN \"Sign in\"\n"
     "UI POS GO = 70, 86\n"
     "UI SETID GO = 3\n"
     "PRINT Span.Len(UI SERIALIZE WIN)",
     "win = Ui.Window(\"Login\")\n"
     "Ui.Size(win, 220 * 65536 + 130)\n"
     "name = Ui.Label(win, \"Name:\")\n"
     "Ui.Pos(name, 12 * 65536 + 16)\n"
     "box = Ui.TextBox(win, \"guest\")\n"
     "Ui.Pos(box, 70 * 65536 + 12)\n"
     "Ui.SetId(box, 1)\n"
     "rem = Ui.Checkbox(win, \"Remember me\")\n"
     "Ui.Pos(rem, 12 * 65536 + 52)\n"
     "Ui.SetId(rem, 2)\n"
     "Ui.SetValue(rem, 1)\n"
     "go = Ui.Button(win, \"Sign in\")\n"
     "Ui.Pos(go, 70 * 65536 + 86)\n"
     "Ui.SetId(go, 3)\n"
     "print(Span.Len(Ui.Serialize(win)))",
     "Set win to Ui.Window(\"Login\").\n"
     "Ui.Size(win, 220 * 65536 + 130).\n"
     "Set name to Ui.Label(win, \"Name:\").\n"
     "Ui.Pos(name, 12 * 65536 + 16).\n"
     "Set box to Ui.TextBox(win, \"guest\").\n"
     "Ui.Pos(box, 70 * 65536 + 12).\n"
     "Ui.SetId(box, 1).\n"
     "Set rem to Ui.Checkbox(win, \"Remember me\").\n"
     "Ui.Pos(rem, 12 * 65536 + 52).\n"
     "Ui.SetId(rem, 2).\n"
     "Ui.SetValue(rem, 1).\n"
     "Set go to Ui.Button(win, \"Sign in\").\n"
     "Ui.Pos(go, 70 * 65536 + 86).\n"
     "Ui.SetId(go, 3).\n"
     "Print Span.Len(Ui.Serialize(win))."),
]


def disasm_lines(words):
    """Readable per-word disassembly for the debugger listing (matches JS view)."""
    names = ["NOOP", "LOAD", "SAVE", "PIPE", "ADD", "SUB", "MUL", "DIV",
             "INC", "JUMP", "BRANCH", "CALL", "RETURN", "WAIT", "RAISE", "DSP"]
    br = ["EQ", "NE", "LT", "GT", "LE", "GE", "Z", "NZ", "EOF", "ERR"]
    lines = []
    for w in words:
        op = (w >> 28) & 0xF
        rd = (w >> 24) & 0xF
        rs1 = (w >> 20) & 0xF
        rs2 = (w >> 16) & 0xF
        imm = w & 0xFFFF
        nm = names[op]
        if op == 0x0 and (imm & 0xFF00) == 0x7000:
            txt = f"HOSTCALL #{imm & 0xFF:#04x}"
        elif op == 0x0 and (imm & 0xF000) == 0x8000:
            txt = f"NET.STATUS {imm & 0x0FFF}"
        elif op == 0x0 and (imm & 0xF000) == 0xA000:
            txt = f"NET.TYPE #{imm:#06x}"
        elif op == 0x0 and imm == 0xB000:
            txt = "NET.BODY"
        elif op == 0x0 and imm == 0xC000:
            txt = "NET.CLOSE"
        elif op in (0x4, 0x5, 0x6, 0x7):
            if rs2 == 0x1:
                txt = f"{nm} R{rd}, R{rs1}, R{imm & 0xF}"
            else:
                txt = f"{nm} R{rd}, R{rs1}, #{imm}"
        elif op == 0x8:
            txt = f"INC R{rd}"
        elif op == 0x9:
            txt = f"JUMP {imm}"
        elif op == 0xA:
            off = imm - 0x10000 if imm & 0x8000 else imm
            txt = f"BRANCH {br[rs2] if rs2 < len(br) else rs2} R{rd}, R{rs1}, {off:+d}"
        elif op == 0xB:
            txt = f"CALL {imm}"
        elif op in (0x1, 0x2, 0x3):
            txt = f"{nm} R{rd if op==1 else rs1}, [{imm:#06x}]"
        elif op == 0xC:
            txt = "RETURN"
        else:
            txt = nm
    
        lines.append(txt)
    return lines


def build_example(srcs):
    """srcs: dict of style -> source. Compiles each, returns per-style example data."""
    comps = {"c": compile_c, "basic": compile_basic, "python": compile_python, "english": compile_english}
    examples = {}
    for style, src in srcs.items():
        words = lower_to_bytecode_safe(comps[style](src))
        vm = PicoVM().run(words)
        examples[style] = {
            "src": src,
            "words": [f"{w:08x}" for w in words],
            "disasm": disasm_lines(words),
            "out": out_ints(vm),
            "status": vm.http_status if vm.http_status is not None else -1,
        }
    return examples


def _styles(c):
    """Unpack a CONSTRUCTS tuple into a {style: source} dict (py/en optional)."""
    title, desc = c[0], c[1]
    srcs = {"c": c[2], "basic": c[3]}
    if len(c) >= 6:
        srcs["python"] = c[4]
        srcs["english"] = c[5]
    return title, desc, srcs


def main():
    data = []
    for c in CONSTRUCTS:
        title, desc, srcs = _styles(c)
        ex = build_example(srcs)
        outs = {s: ex[s]["out"] for s in ex}
        # every provided style must produce identical output
        ref = ex["basic"]["out"]
        for s, o in outs.items():
            assert o == ref, (title, s, o, ref)
        data.append({"title": title, "desc": desc, **ex})
        print(f"  built: {title:32s} -> {ref}  [{', '.join(sorted(ex))}]")

    hooks_js = open(os.path.join(ROOT, "vm", "pico_hooks.js"), encoding="utf-8").read()
    vm_js = open(os.path.join(ROOT, "vm", "picovm.js"), encoding="utf-8").read()
    picoc_js = open(os.path.join(ROOT, "vm", "picoc.js"), encoding="utf-8").read()
    ser_js = open(os.path.join(ROOT, "vm", "picoserializer.js"), encoding="utf-8").read()
    store_js = open(os.path.join(ROOT, "vm", "picostore.js"), encoding="utf-8").read()
    pcz_js = open(os.path.join(ROOT, "vm", "picocompress.js"), encoding="utf-8").read()
    pbz_js = open(os.path.join(ROOT, "vm", "picobrotli.js"), encoding="utf-8").read()
    payload = json.dumps(data)

    html = PAGE.replace("/*__HOOKS__*/", hooks_js) \
               .replace("/*__PCZ__*/", pcz_js) \
               .replace("/*__PBZ__*/", pbz_js) \
               .replace("/*__VM__*/", vm_js) \
               .replace("/*__PICOC__*/", picoc_js) \
               .replace("/*__SER__*/", ser_js) \
               .replace("/*__STORE__*/", store_js) \
               .replace("/*__DATA__*/", payload)
    out_path = os.path.join(ROOT, "docs", "playground.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nwrote {out_path} ({len(html)//1024} KB, {len(data)} constructs)")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PicoScript Playground &amp; Language Guide</title>
<style>
  :root { --accent:#667eea; --bg:#0f1117; --panel:#1a1d27; --panel2:#232734;
          --text:#e6e8ef; --muted:#9aa0ad; --c:#7ee787; --b:#79c0ff; --py:#ffd866; --en:#f0a3ff;
          --warn:#ffd866; --err:#ff7b72; --sidebar-w:240px; }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; overflow:hidden; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--text); display:flex; flex-direction:column; }
  a { color:var(--b); }
  /* top bar */
  .topbar { display:flex; align-items:center; gap:14px; padding:8px 16px; background:var(--panel2);
            border-bottom:1px solid #2c313f; flex-shrink:0; }
  .topbar h1 { font-size:16px; color:var(--accent); white-space:nowrap; }
  .lang-toggle { display:flex; gap:2px; background:#0c0e14; border-radius:6px; padding:2px; }
  .lang-toggle button { border:none; background:none; color:var(--muted); padding:5px 12px;
    border-radius:4px; font-size:12px; font-weight:600; cursor:pointer; }
  .lang-toggle button.active { color:#fff; }
  .lang-toggle button[data-lang="c"].active { background:var(--c); color:#0f1117; }
  .lang-toggle button[data-lang="basic"].active { background:var(--b); color:#0f1117; }
  .lang-toggle button[data-lang="python"].active { background:var(--py); color:#0f1117; }
  .lang-toggle button[data-lang="english"].active { background:var(--en); color:#0f1117; }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600;
          background:#2c313f; color:var(--muted); }
  /* main layout */
  .main { display:flex; flex:1; overflow:hidden; }
  /* sidebar tree */
  .sidebar { width:var(--sidebar-w); background:var(--panel); border-right:1px solid #2c313f;
             overflow-y:auto; flex-shrink:0; padding:10px 0; }
  .sidebar .group-title { font-size:10px; text-transform:uppercase; letter-spacing:.08em;
    color:var(--muted); padding:10px 14px 4px; font-weight:700; }
  .sidebar .tree-item { padding:6px 14px 6px 20px; font-size:12px; cursor:pointer;
    color:var(--text); border-left:3px solid transparent; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }
  .sidebar .tree-item:hover { background:var(--panel2); }
  .sidebar .tree-item.active { border-left-color:var(--accent); background:var(--panel2); color:var(--accent); }
  /* content area */
  .content { flex:1; display:flex; flex-direction:column; overflow:hidden; }
  /* card view */
  .card-view { flex:1; overflow-y:auto; padding:20px 28px; }
  .card-view .card-title { font-size:18px; font-weight:700; margin-bottom:4px; }
  .card-view .card-desc { color:var(--muted); font-size:13px; margin-bottom:14px; max-width:700px; }
  .card-view pre { background:#0c0e14; border:1px solid #2c313f; border-radius:6px;
    padding:14px; font-family:"SF Mono",Consolas,monospace; font-size:12.5px; line-height:1.6;
    overflow-x:auto; max-width:700px; }
  .card-view pre.cstyle { color:#cde9c8; border-left:3px solid var(--c); }
  .card-view pre.bstyle { color:#cfe4ff; border-left:3px solid var(--b); }
  .card-view pre.pystyle { color:#f5e6a8; border-left:3px solid var(--py); }
  .card-view pre.enstyle { color:#f3d4ff; border-left:3px solid var(--en); }
  .card-view .run-area { display:flex; align-items:center; gap:10px; margin-top:12px; }
  .card-view .out { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:var(--warn); }
  button.act { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:7px 14px;
    font-weight:600; cursor:pointer; font-size:12px; }
  button.ghost { background:#2c313f; color:var(--text); border:none; border-radius:6px; padding:7px 14px;
    font-weight:600; cursor:pointer; font-size:12px; }
  button.act:hover,button.ghost:hover { filter:brightness(1.12); }
  /* debugger panels (collapsible bottom) */
  .dbg-bar { display:flex; gap:2px; background:var(--panel2); border-top:1px solid #2c313f;
             border-bottom:1px solid #2c313f; padding:0 12px; flex-shrink:0; }
  .dbg-bar button { border:none; background:none; color:var(--muted); padding:7px 12px;
    font-size:11px; font-weight:600; cursor:pointer; border-bottom:2px solid transparent; }
  .dbg-bar button.active { color:var(--accent); border-bottom-color:var(--accent); }
  .dbg-panels { background:#11141c; border-top:1px solid #2c313f; overflow:hidden;
    transition:max-height .2s; flex-shrink:0; }
  .dbg-panels.collapsed { max-height:0 !important; }
  .dbg-panel { display:none; padding:10px 16px; overflow:auto; }
  .dbg-panel.active { display:block; }
  .ui-window { background:#1b1f2a; border:1px solid #3a4150; border-radius:7px; box-shadow:0 6px 18px rgba(0,0,0,.4); overflow:hidden; }
  .ui-titlebar { background:linear-gradient(#2b3242,#222836); color:#e6edf3; font-size:12px; font-weight:600;
    padding:5px 9px; border-bottom:1px solid #3a4150; display:flex; align-items:center; }
  .ui-titlebar:before { content:''; width:9px; height:9px; border-radius:50%; background:#ff5f57; box-shadow:14px 0 #febc2e,28px 0 #28c840; margin-right:34px; }
  .ui-canvas { position:relative; background:#10131b; }
  .ui-ctl { position:absolute; font-size:12px; color:#cdd6e0; white-space:nowrap; }
  .ui-label { color:#9aa4b2; }
  .ui-button { background:#2f6feb; color:#fff; border-radius:5px; padding:4px 12px; cursor:pointer; border:1px solid #2b62cf; }
  .ui-button:hover { background:#3b7bf5; }
  .ui-textbox { background:#0c0e14; border:1px solid #3a4150; border-radius:4px; padding:3px 7px; min-width:80px; color:#e6edf3; }
  .ui-check { cursor:pointer; display:flex; align-items:center; gap:6px; }
  .ui-check .box { display:inline-block; width:14px; height:14px; line-height:13px; text-align:center;
    border:1px solid #3a4150; border-radius:3px; background:#0c0e14; color:#28c840; font-size:11px; }
  .ui-panel { border:1px dashed #3a4150; border-radius:5px; background:rgba(255,255,255,.02); }
  .listing { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; max-height:180px;
    overflow:auto; font-family:"SF Mono",Consolas,monospace; font-size:11.5px; }
  .listing .row { padding:2px 10px; white-space:pre; color:var(--muted); }
  .listing .row.pc { background:#2d3550; color:#fff; }
  .regs { display:grid; grid-template-columns:repeat(4,1fr); gap:2px 8px; font-family:"SF Mono",monospace; font-size:11.5px; }
  .regs .r { color:var(--muted); } .regs .r b { color:var(--text); }
  .state { font-family:"SF Mono",Consolas,monospace; font-size:11.5px; color:var(--muted); margin-top:6px; }
  select,textarea,input { background:#0c0e14; color:var(--text); border:1px solid #2c313f;
    border-radius:6px; padding:6px 8px; font-family:inherit; font-size:12px; }
  textarea { font-family:"SF Mono",Consolas,monospace; width:100%; resize:vertical; }
  .controls { display:flex; gap:6px; margin:8px 0; flex-wrap:wrap; align-items:center; }
  .file-sidebar { width:230px; background:var(--panel); border-right:1px solid #2c313f;
    flex-shrink:0; display:flex; flex-direction:column; overflow:hidden; }
  .file-sidebar.collapsed { width:36px; }
  .file-sidebar.collapsed .file-body { display:none; }
  .file-head { display:flex; align-items:center; gap:6px; padding:8px 10px; border-bottom:1px solid #2c313f;
    color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; font-weight:700; }
  .file-head button { margin-left:auto; background:none; border:none; color:var(--muted); cursor:pointer; }
  .file-body { padding:8px; overflow:auto; display:flex; flex-direction:column; gap:8px; }
  .file-actions { display:flex; gap:5px; flex-wrap:wrap; }
  .file-actions button { padding:4px 8px; font-size:10.5px; }
  .file-list { display:flex; flex-direction:column; gap:3px; }
  .file-item { border:1px solid transparent; border-radius:6px; padding:6px 7px; cursor:pointer; background:#0c0e14; }
  .file-item:hover { border-color:#2c313f; background:var(--panel2); }
  .file-item.active { border-color:var(--accent); background:#20263a; }
  .file-row { display:flex; align-items:center; gap:5px; min-width:0; }
  .file-name { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; font-size:12px; }
  .file-dirty { color:var(--warn); width:10px; text-align:center; font-weight:700; }
  .file-badge { font-size:9px; font-weight:700; border-radius:8px; padding:1px 5px; color:#0f1117; }
  .file-badge.c { background:var(--c); } .file-badge.basic { background:var(--b); }
  .file-badge.python { background:var(--py); } .file-badge.english { background:var(--en); }
  .file-meta { color:var(--muted); font-size:10px; margin-top:3px; }
  .file-empty,.file-status { color:var(--muted); font-size:11px; line-height:1.4; }
  .cerr { font-family:monospace; font-size:11px; min-height:14px; }
  /* GPIO header visual (wired to the injected gpioProvider) */
  .gpio-tools { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }
  .gpio-tools .muted { color:var(--muted); font-size:11px; }
  .gpio-header { display:grid; grid-template-columns:1fr 1fr; gap:4px 14px; max-width:700px; }
  .gpio-pin { display:flex; align-items:center; gap:6px; background:#0c0e14; border:1px solid #2c313f;
    border-radius:6px; padding:4px 7px; font-size:11px; }
  .gpio-pin .pn { color:var(--muted); font-family:"SF Mono",monospace; width:20px; text-align:right; }
  .gpio-pin .pl { font-weight:600; width:40px; }
  .gpio-pin .dir { font-size:9px; font-weight:700; border-radius:8px; padding:1px 5px; width:26px; text-align:center; }
  .gpio-pin .dir.out { background:var(--c); color:#0f1117; } .gpio-pin .dir.in { background:#2c313f; color:var(--muted); }
  .gpio-pin .pull { font-size:9px; color:var(--muted); width:24px; text-align:center; font-weight:700; }
  .gpio-pin .pull.up { color:#7ee787; } .gpio-pin .pull.dn { color:#79c0ff; }
  .gpio-pin .bar { flex:1; height:8px; background:#1b2030; border-radius:4px; overflow:hidden; min-width:40px; }
  .gpio-pin .bar > i { display:block; height:100%; background:var(--accent); }
  .gpio-pin .val { width:38px; text-align:right; font-family:"SF Mono",monospace; color:var(--text); }
  .gpio-pin.drivable { cursor:pointer; } .gpio-pin.drivable:hover { border-color:var(--accent); }
  /* Cards / schema designer */
  .schema-tools { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; font-size:11px; }
  .schema-tools .muted { color:var(--muted); }
  .schema-wrap { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-start; }
  .schema-col { flex:1; min-width:250px; }
  .schema-col h4 { margin:0 0 6px; font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); display:flex; align-items:center; gap:8px; }
  .sd-table { width:100%; border-collapse:collapse; font-size:11px; margin-bottom:6px; }
  .sd-table th,.sd-table td { border:1px solid #2c313f; padding:3px 6px; text-align:left; }
  .sd-table th { color:var(--muted); font-weight:600; }
  .sd-table td .x { color:var(--err); cursor:pointer; float:right; font-weight:700; }
  .field-add { display:flex; gap:5px; align-items:center; flex-wrap:wrap; }
  .field-add input,.field-add select { padding:4px 6px; font-size:11px; }
  .sd-card { border:1px solid #2c313f; border-radius:6px; padding:6px 8px; margin-bottom:5px; background:#0c0e14; }
  .sd-card .ch { display:flex; align-items:center; gap:6px; font-size:10px; color:var(--muted); margin-bottom:4px; }
  .sd-card .ch .x { margin-left:auto; color:var(--err); cursor:pointer; }
  .sd-fld { display:flex; align-items:center; gap:6px; font-size:11px; margin:2px 0; }
  .sd-fld label { width:96px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sd-fld input { flex:1; padding:3px 6px; font-size:11px; }
  .sd-empty { color:var(--muted); font-size:11px; }
  /* Stream DMA-ring visual */
  .stream-tools { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; font-size:11px; }
  .stream-tools .muted { color:var(--muted); }
  .stream-dev { border:1px solid #2c313f; border-radius:6px; padding:6px 8px; margin-bottom:6px; background:#0c0e14; }
  .stream-dev .dh { display:flex; align-items:center; gap:8px; font-size:11px; margin-bottom:5px; }
  .stream-dev .dh b { color:var(--text); } .stream-dev .dh .st { font-size:9px; font-weight:700; border-radius:8px; padding:1px 6px; }
  .stream-dev .dh .st.open { background:#7ee787; color:#0f1117; } .stream-dev .dh .st.closed { background:#2c313f; color:var(--muted); }
  .stream-dev .dh .dir { font-size:9px; font-weight:700; background:var(--accent); color:#0f1117; border-radius:8px; padding:1px 6px; }
  .frame-row { display:flex; align-items:center; gap:6px; font-size:11px; margin:2px 0; }
  .frame-row .fi { color:var(--muted); font-family:"SF Mono",monospace; width:54px; }
  .frame-row .fb { flex:1; font-family:"SF Mono",monospace; color:#cde9c8; background:#1b2030; border-radius:4px; padding:2px 6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .frame-row .fs { font-size:9px; font-weight:700; border-radius:8px; padding:1px 5px; }
  .frame-row .fs.rel { background:#2c313f; color:var(--muted); } .frame-row .fs.live { background:var(--warn); color:#0f1117; }
  @media (max-width:800px){
    .sidebar { width:180px; }
    .regs { grid-template-columns:repeat(2,1fr); }
  }
</style>
</head>
<body>
<!-- Top bar with language toggle -->
<div class="topbar">
  <h1>PicoScript</h1>
  <div class="lang-toggle" id="langToggle">
    <button data-lang="c" class="active" onclick="setLang('c')">C &#123;&#125;</button>
    <button data-lang="basic" onclick="setLang('basic')">BASIC</button>
    <button data-lang="python" onclick="setLang('python')">Python</button>
    <button data-lang="english" onclick="setLang('english')">English</button>
  </div>
  <span class="pill">case-insensitive</span>
  <span class="pill">same bytecode</span>
  <a href="index.html" style="margin-left:auto;font-size:11px;color:var(--accent);text-decoration:none">&#128214; Full language guide &amp; reference &#8599;</a>
</div>

<div class="main">
  <!-- Sidebar tree -->
  <div class="sidebar" id="tree"></div>
  <div class="file-sidebar" id="fileSidebar">
    <div class="file-head">Files <button title="Collapse files" onclick="filesToggle()">&#9664;</button></div>
    <div class="file-body">
      <div class="file-actions">
        <button class="ghost" onclick="psFilesNew()">New</button>
        <button class="ghost" onclick="psFilesOpen()">Open</button>
        <button class="act" onclick="psFilesSave()">Save</button>
        <button class="ghost" onclick="psFilesSaveAs()">Save as</button>
        <button class="ghost" onclick="psFilesRename()">Rename</button>
        <button class="ghost" onclick="psFilesDelete()">Delete</button>
      </div>
      <div class="file-list" id="fileList"></div>
      <div class="file-status" id="fileStatus"></div>
    </div>
  </div>

  <!-- Content -->
  <div class="content">
    <div class="card-view" id="cardView"></div>

    <!-- Debugger tab bar -->
    <div class="dbg-bar">
      <button class="active" onclick="toggleDbg(this,'dbg-disasm')">Disassembly</button>
      <button onclick="toggleDbg(this,'dbg-regs')">Registers</button>
      <button onclick="toggleDbg(this,'dbg-output')">Output</button>
      <button onclick="toggleDbg(this,'dbg-src')">Source Editor</button>
      <button onclick="toggleDbg(this,'dbg-gpio')">GPIO</button>
      <button onclick="toggleDbg(this,'dbg-cards')">Cards</button>
      <button onclick="toggleDbg(this,'dbg-stream')">Stream</button>
      <button onclick="toggleDbg(this,'dbg-ui')">Remote UI</button>
      <button style="margin-left:auto" onclick="collapseDbg()">&#9660; Collapse</button>
    </div>
    <div class="dbg-panels" id="dbgPanels" style="max-height:220px">
      <div class="dbg-panel active" id="dbg-disasm">
        <div class="listing" id="listing"></div>
        <div class="state" id="state"></div>
      </div>
      <div class="dbg-panel" id="dbg-regs">
        <div class="regs" id="regs"></div>
      </div>
      <div class="dbg-panel" id="dbg-output">
        <div class="out" id="out" style="font-size:13px"></div>
        <div class="out" id="psunit" style="font-size:13px;margin-top:6px"></div>
      </div>
      <div class="dbg-panel" id="dbg-src">
        <select id="lang" style="width:auto;margin-bottom:6px">
          <option value="c">C-style</option><option value="basic">BASIC</option>
          <option value="python">Python</option><option value="english">English</option>
        </select>
        <textarea id="src" style="height:100px" spellcheck="false"></textarea>
        <div class="controls">
          <button class="act" onclick="compileSrc(true)">Compile &amp; Run &#9654;</button>
          <button class="ghost" onclick="compileSrc(false)">Compile &amp; Step</button>
          <button class="ghost" onclick="dbgStep()">Step</button>
          <button class="ghost" onclick="dbgReset()">Reset</button>
        </div>
        <div id="cerr" class="cerr"></div>
      </div>
      <div class="dbg-panel" id="dbg-gpio">
        <div class="gpio-tools">
          <button class="ghost" onclick="gpioReset()">Reset pins</button>
          <span class="muted">Run a program that uses <b>Gpio.*</b> (or the <b>GPIO</b> DSL). Outputs show what it wrote (0&ndash;1024); click an <b>IN</b> pin to drive it, then re-run.</span>
        </div>
        <div class="gpio-header" id="gpioHeader"></div>
      </div>
      <div class="dbg-panel" id="dbg-cards">
        <div class="schema-tools">
          pack <input id="sdPack" type="number" value="0" min="0" style="width:64px" onchange="renderCards()">
          <button class="ghost" onclick="sdClear()">Clear store</button>
          <span class="muted">Design a typed schema and author cards. The running program shares this store via <b>Storage.*</b> / the <b>STORE</b>/<b>LOAD</b> DSL.</span>
        </div>
        <div class="schema-wrap" id="cardsView"></div>
      </div>
      <div class="dbg-panel" id="dbg-stream">
        <div class="stream-tools">
          <span class="muted">Run a program that uses <b>Device.*</b> / <b>Stream.*</b>. The reference DMA ring renders each device, its stream, and the frames read (RX) or submitted (TX) &mdash; bytes shown live, <span style="color:var(--warn)">live</span> until <b>Stream.Release</b>.</span>
        </div>
        <div id="streamView"></div>
      </div>
      <div class="dbg-panel" id="dbg-ui">
        <div class="stream-tools">
          <span class="muted">Run a program that builds a window with <b>Ui.*</b>. This panel is a reference <b>remote client</b>: it renders the <b>Ui.Serialize</b> wire (decoded as PicoSerializer/PSC1 records) as window chrome + controls. Click a <b>button</b> or <b>checkbox</b> to post an <b>Event.*</b> back to the program.</span>
        </div>
        <div id="uiView" style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start"></div>
        <div id="uiEvents" class="out" style="font-size:12px;margin-top:8px"></div>
      </div>
    </div>
  </div>
</div>

<script>/*__HOOKS__*/</script>
<script>/*__PCZ__*/</script>
<script>/*__PBZ__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
<script>/*__SER__*/</script>
<script>/*__STORE__*/</script>
<script>
var DATA = /*__DATA__*/;
var CUR_LANG = 'c';
var CUR_CARD = 0;

// pedagogical grouping
var GROUPS = [
  {name:'Basics', items:[0]},
  {name:'Control Flow', items:[1,2,3,4,10,11]},
  {name:'Operators', items:[5]},
  {name:'Dispatch & State', items:[6,7]},
  {name:'Subroutines', items:[8,9]},
  {name:'I/O & Cards', items:[12,13]},
  {name:'Devices', items:[14]},
  {name:'Testing', items:[15]},
  {name:'GUI', items:[16]}
];

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ---- language toggle -------------------------------------------------------
function setLang(lang){
  CUR_LANG = lang;
  document.querySelectorAll('#langToggle button').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-lang')===lang);
  });
  document.getElementById('lang').value = lang;
  if(typeof onLangChange==='function') onLangChange();
  showCard(CUR_CARD);
}

// ---- tree ------------------------------------------------------------------
function buildTree(){
  var html = '';
  GROUPS.forEach(function(g){
    html += '<div class="group-title">'+esc(g.name)+'</div>';
    g.items.forEach(function(idx){
      if(idx < DATA.length){
        html += '<div class="tree-item'+(idx===0?' active':'')+'" data-idx="'+idx+'" onclick="showCard('+idx+')">'+esc(DATA[idx].title)+'</div>';
      }
    });
  });
  document.getElementById('tree').innerHTML = html;
}

// ---- card view -------------------------------------------------------------
function showCard(idx){
  CUR_CARD = idx;
  var d = DATA[idx];
  var lang = CUR_LANG;
  if(!d[lang]) lang = d.c ? 'c' : 'basic';
  var STYLE_CLASS = {c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle'};
  var cv = document.getElementById('cardView');
  cv.innerHTML =
    '<div class="card-title">'+(idx+1)+'. '+esc(d.title)+'</div>'+
    '<div class="card-desc">'+d.desc+'</div>'+
    (d[lang] ? '<pre class="'+STYLE_CLASS[lang]+'">'+esc(d[lang].src)+'</pre>' : '<pre style="color:var(--muted)">(not available in this dialect)</pre>')+
    '<div class="run-area">'+
      '<button class="act" onclick="loadCard('+idx+')">Load into editor &#9654;</button>'+
      '<a class="ghost" href="index.html" style="text-decoration:none;padding:6px 10px">Full guide &#8599;</a>'+
      '<span class="out" id="cardout'+idx+'"></span>'+
    '</div>';
  document.querySelectorAll('.tree-item').forEach(function(el){
    el.classList.toggle('active', parseInt(el.getAttribute('data-idx'))===idx);
  });
  cv.scrollTop = 0;
}

// Import a guide example into the editor and run it locally (samples live in the
// full language guide; the editor screen is for writing + running your own code).
function loadCard(i){
  var d=DATA[i], lang=CUR_LANG; if(!d[lang]) lang='basic';
  document.getElementById('lang').value=lang; if(typeof onLangChange==='function') onLangChange();
  setSrc(d[lang].src);
  document.querySelectorAll('.dbg-bar button').forEach(function(b){ b.classList.toggle('active', /Source Editor/.test(b.textContent)); });
  showDbgPanel('dbg-src'); expandDbg(); compileSrc(true);
}

// ---- run / step ------------------------------------------------------------
function runWords(hex){ var vm=new PicoVM(); vm.run(hex.map(function(h){return parseInt(h,16)>>>0;})); return vm; }

function runCard(i){
  var d=DATA[i], styles=['c','basic','python','english'], parts=[], ref=null, same=true;
  styles.forEach(function(s){ if(!d[s]) return;
    var o=runWords(d[s].words).outputInts();
    if(ref===null) ref=JSON.stringify(o); else if(JSON.stringify(o)!==ref) same=false;
    parts.push(s+' \u2192 ['+o.join(', ')+']');
  });
  var el=document.getElementById('cardout'+i);
  if(el) el.innerHTML=parts.join(' &nbsp; ')+'  '+(same?'&#10003;':'&#9888;');
  // also load into debugger
  var lang=CUR_LANG; if(!d[lang]) lang='basic';
  DBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});
  DBG.disasm=d[lang].disasm.slice();
  dbgReset(); dbgRun();
}

function stepCard(i){
  var d=DATA[i], lang=CUR_LANG; if(!d[lang]) lang='basic';
  DBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});
  DBG.disasm=d[lang].disasm.slice();
  dbgReset();
  expandDbg();
}
function debugCard(i){
  var d=DATA[i], lang=CUR_LANG; if(!d[lang]) lang='basic';
  document.getElementById('lang').value = lang;
  if(typeof onLangChange==='function') onLangChange();
  setSrc(d[lang].src);
  compileSrc(false);
  showDbgPanel('dbg-src');
  expandDbg();
}

// ---- debugger panels -------------------------------------------------------
var DBG = { words:[], disasm:[], vm:null };

// ---- device panel: GPIO header visual (wired to the provider seam) ----------
var GP = { pins:{}, count:40 };
var PULL_LABEL = ['\u2014','UP','DN'];
function gpioReset(){ GP.pins={}; renderGpio(); }
function gpioDrive(pin){
  var st=GP.pins[pin]||(GP.pins[pin]={dir:0,pull:0,value:0});
  var v=(typeof prompt==='function')?prompt('Drive input pin GP'+pin+' value (0\u20131024):',String(st.value)):null;
  if(v===null) return; st.value=Math.max(0,Math.min(1024,parseInt(v,10)||0)); renderGpio();
}
function renderGpio(){
  var host=document.getElementById('gpioHeader'); if(!host) return;
  var html='';
  for(var p=0;p<GP.count;p++){
    var st=GP.pins[p]||{dir:0,pull:0,value:0}, isOut=st.dir===1, w=Math.round(st.value/1024*100);
    html+='<div class="gpio-pin'+(isOut?'':' drivable')+'"'+(isOut?'':' onclick="gpioDrive('+p+')"')+'>'+
      '<span class="pn">'+p+'</span><span class="pl">GP'+p+'</span>'+
      '<span class="dir '+(isOut?'out':'in')+'">'+(isOut?'OUT':'IN')+'</span>'+
      '<span class="pull '+(st.pull===1?'up':st.pull===2?'dn':'')+'">'+(PULL_LABEL[st.pull]||'\u2014')+'</span>'+
      '<span class="bar"><i style="width:'+w+'%"></i></span>'+
      '<span class="val">'+st.value+'</span></div>';
  }
  host.innerHTML=html;
}

// ---- device panel: Stream DMA-ring visual (observes the streamProvider) -----
var SP = { devices:{}, streams:{}, leases:{}, ds:0, ss:0, ls:0 };
function streamReset(){ SP = { devices:{}, streams:{}, leases:{}, ds:0, ss:0, ls:0 }; }
function hexBytes(a){ return (a||[]).map(function(b){return ('0'+(b&255).toString(16)).slice(-2);}).join(' '); }
function renderStream(){
  var host=document.getElementById('streamView'); if(!host) return;
  var devIds=Object.keys(SP.devices);
  if(!devIds.length){ host.innerHTML='<div class="sd-empty">no devices opened</div>'; return; }
  var html='';
  devIds.forEach(function(dh){
    var dev=SP.devices[dh];
    // streams + their leases (frames)
    var streamRows='';
    Object.keys(SP.streams).forEach(function(sh){
      var st=SP.streams[sh], dir=st.dir===1?'TX':'RX';
      var frames='';
      Object.keys(SP.leases).forEach(function(lh){
        var le=SP.leases[lh]; if(String(le.stream)!==String(sh)) return;
        frames+='<div class="frame-row"><span class="fi">frame '+le.idx+'</span>'+
          '<span class="fb">'+hexBytes(le.data)+'</span>'+
          '<span class="fs '+(le.released?'rel':'live')+'">'+(le.released?'released':'live')+'</span></div>';
      });
      streamRows+='<div style="margin-top:4px"><span class="dir">'+dir+'</span> '+
        '<span style="color:var(--muted);font-size:11px">buf='+st.buf+' frames='+st.frames+' consumed='+st.next+
        (st.tx&&st.tx.length?(' submitted='+st.tx.length):'')+'</span>'+(frames||'<div class="sd-empty">no frames yet</div>')+'</div>';
    });
    html+='<div class="stream-dev"><div class="dh"><b>'+esc(dev.id||('dev'+dh))+'</b>'+
      '<span class="st '+(dev.open?'open':'closed')+'">'+(dev.open?'open':'closed')+'</span></div>'+
      (streamRows||'<div class="sd-empty">no streams opened</div>')+'</div>';
  });
  host.innerHTML=html;
}

// ---- remote-UI panel: render the Ui.Serialize PicoWire as a window ----------
// This is a reference *remote client*: it takes the serialized wire (not the live
// tree), decodes each node as a PicoSerializer/PSC1 record, rebuilds the tree and
// renders window chrome + controls. Clicking a control posts an Event.* back.
var UI_LOG = [];
function picoWireDecode(bytes){
  if(!bytes||bytes.length<2) return null;
  var count=(bytes[0]<<8)|bytes[1], pos=2, flat=[];
  for(var i=0;i<count;i++){
    var nfields=(bytes[pos+4]<<8)|bytes[pos+5], p=pos+6;
    for(var f=0;f<nfields;f++){
      var nlen=bytes[p]; p+=1+nlen;
      var t=bytes[p]; p+=1;
      if(t===1){ p+=4; } else if(t===2){ var vlen=(bytes[p]<<8)|bytes[p+1]; p+=2+vlen; }
    }
    flat.push(PicoSerializer.deserializeCard(bytes.slice(pos,p))); pos=p;
  }
  var idx={i:0};
  function build(){ var nd=flat[idx.i++]; nd.children=[]; for(var c=0;c<nd.ch;c++) nd.children.push(build()); return nd; }
  return flat.length?build():null;
}
function uiControl(nd){
  var el=document.createElement('div'); el.className='ui-ctl';
  if(nd.c===2){ el.className+=' ui-panel'; el.style.width=Math.max(0,nd.w)+'px'; el.style.height=Math.max(0,nd.h)+'px'; }
  else if(nd.c===3){ el.className+=' ui-label'; el.textContent=nd.t||''; }
  else if(nd.c===4){ el.className+=' ui-button'; el.textContent=nd.t||''; el.onclick=function(){ onUiEvent(nd.id,1,nd.t); }; }
  else if(nd.c===5){ el.className+=' ui-textbox'; el.textContent=nd.t||''; }
  else if(nd.c===6){ el.className+=' ui-check'; el.innerHTML='<span class="box">'+(nd.v?'\u2713':'')+'</span>'+esc(nd.t||''); el.onclick=function(){ onUiEvent(nd.id,2,nd.t); }; }
  else { el.textContent=nd.t||''; }
  return el;
}
function renderUiWindow(win){
  var W=Math.max(120,win.w||220), H=Math.max(70,win.h||140);
  var wrap=document.createElement('div'); wrap.className='ui-window'; wrap.style.width=W+'px';
  var bar=document.createElement('div'); bar.className='ui-titlebar'; bar.textContent=win.t||'Window';
  var canvas=document.createElement('div'); canvas.className='ui-canvas'; canvas.style.height=H+'px';
  (function place(nodes){
    (nodes||[]).forEach(function(nd){
      var el=uiControl(nd); el.style.left=Math.max(0,nd.x)+'px'; el.style.top=Math.max(0,nd.y)+'px';
      canvas.appendChild(el);
      if(nd.children&&nd.children.length) place(nd.children);
    });
  })(win.children);
  wrap.appendChild(bar); wrap.appendChild(canvas); return wrap;
}
function renderUi(){
  var host=document.getElementById('uiView'); if(!host) return;
  var vm=DBG.vm, st=vm&&vm._uiState; host.innerHTML='';
  if(!st||!st.seq){ host.innerHTML='<span class="muted">no window built &mdash; run a program that calls <b>Ui.Window</b> + controls, then <b>Ui.Serialize</b></span>'; renderUiEvents(); return; }
  Object.keys(st.nodes).forEach(function(k){
    if(st.nodes[k].kind!==1) return;
    var tree=picoWireDecode(vm._uiWire(parseInt(k,10)));
    if(tree) host.appendChild(renderUiWindow(tree));
  });
  renderUiEvents();
}
function onUiEvent(id,type,label){
  if(!DBG.vm) return;
  var e=DBG.vm._ev||(DBG.vm._ev={recs:{},queue:[],seq:0});
  e.seq++; e.recs[e.seq]={type:type,target:(id>>>0),data:null,span:0}; e.queue.push(e.seq);
  UI_LOG.unshift('Event type='+type+' target='+id+(label?(' \u00ab'+label+'\u00bb'):'')); if(UI_LOG.length>6) UI_LOG.pop();
  renderUiEvents();
}
function renderUiEvents(){
  var host=document.getElementById('uiEvents'); if(!host) return;
  var pending=(DBG.vm&&DBG.vm._ev)?DBG.vm._ev.queue.length:0;
  host.innerHTML='<b>Event queue:</b> '+pending+' pending'+(UI_LOG.length?(' &mdash; '+UI_LOG.map(esc).join(' &middot; ')):'')+
    '<div class="muted" style="margin-top:3px">A program consumes these with <b>Event.Next()</b> / <b>Event.Target</b> / <b>Event.Type</b>.</div>';
}
var CARDSTORE = (typeof PicoStore!=='undefined') ? new PicoStore.PicoStore() : null;
var SD_TYPES = ['int','str','bool','uint8','int16','int32','uint16','uint32','utf8','latin1','blob'];
var SD_SCHEMA_KEY='picoscript.schemas.v1';
function sdSchemas(){ try{var ls=filesSafeLocalStorage();return ls?(JSON.parse(ls.getItem(SD_SCHEMA_KEY)||'{}')||{}):{};}catch(e){return {};} }
function sdSchemasWrite(s){ try{var ls=filesSafeLocalStorage();if(ls)ls.setItem(SD_SCHEMA_KEY,JSON.stringify(s));}catch(e){} }
function sdPack(){ return String((document.getElementById('sdPack')||{}).value||'0'); }
function sdSchema(){ return sdSchemas()[sdPack()]||[]; }
function sdStrType(t){ return t==='str'||t==='utf8'||t==='latin1'||t==='blob'; }
function sdAddField(){
  var el=document.getElementById('sdFName'), name=(el?el.value:'').trim();
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)){ if(typeof alert==='function')alert('field name must be a simple identifier'); return; }
  var type=document.getElementById('sdFType').value, s=sdSchemas(), p=sdPack();
  s[p]=(s[p]||[]).filter(function(f){return f.name!==name;}).concat([{name:name,type:type}]);
  sdSchemasWrite(s); renderCards();
}
function sdRemoveField(name){ var s=sdSchemas(),p=sdPack(); s[p]=(s[p]||[]).filter(function(f){return f.name!==name;}); sdSchemasWrite(s); renderCards(); }
function sdAddCard(){ if(!CARDSTORE) return; var rec={}; sdSchema().forEach(function(f){ rec[f.name]=sdStrType(f.type)?'':0; }); CARDSTORE.create(sdPack(), rec); renderCards(); }
function sdDeleteCard(id){ if(CARDSTORE){ CARDSTORE.delete(sdPack(), id); renderCards(); } }
function sdSetField(id,name,el,isStr){ if(!CARDSTORE) return; var f={}; f[name]=isStr?el.value:(parseInt(el.value,10)||0); CARDSTORE.patch(sdPack(), id, f); }
function sdClear(){ if(typeof PicoStore!=='undefined'){ CARDSTORE=new PicoStore.PicoStore(); } renderCards(); }
function renderCards(){
  var host=document.getElementById('cardsView'); if(!host) return;
  var schema=sdSchema(), p=sdPack();
  var fhtml='<table class="sd-table"><tr><th>field</th><th>type</th></tr>';
  if(!schema.length) fhtml+='<tr><td colspan="2" class="sd-empty">no fields yet</td></tr>';
  schema.forEach(function(f){ fhtml+='<tr><td>'+esc(f.name)+'<span class="x" title="remove" onclick="sdRemoveField(\''+f.name+'\')">&times;</span></td><td>'+esc(f.type)+'</td></tr>'; });
  fhtml+='</table><div class="field-add"><input id="sdFName" placeholder="field name" style="width:110px"><select id="sdFType">'+SD_TYPES.map(function(t){return '<option>'+t+'</option>';}).join('')+'</select><button class="ghost" onclick="sdAddField()">+ field</button></div>';
  var cards = CARDSTORE ? CARDSTORE.all(p) : [];
  var chtml='';
  if(!cards.length) chtml='<div class="sd-empty">no cards in pack '+esc(p)+'</div>';
  cards.forEach(function(e){ var id=e[0], rec=e[1]||{};
    var flds = schema.length?schema:Object.keys(rec).map(function(k){return {name:k,type:(typeof rec[k]==='string'?'str':'int')};});
    chtml+='<div class="sd-card"><div class="ch">card #'+id+'<span class="x" onclick="sdDeleteCard('+id+')">delete</span></div>';
    if(!flds.length) chtml+='<div class="sd-empty">empty</div>';
    flds.forEach(function(f){ var isStr=sdStrType(f.type), v=rec[f.name]; if(v===undefined)v=isStr?'':0;
      chtml+='<div class="sd-fld"><label title="'+esc(f.name)+'">'+esc(f.name)+'</label><input '+(isStr?'':'type="number"')+' value="'+esc(String(v)).replace(/"/g,'&quot;')+'" onchange="sdSetField('+id+',\''+f.name+'\',this,'+(isStr?'true':'false')+')"></div>';
    });
    chtml+='</div>';
  });
  host.innerHTML='<div class="schema-col"><h4>Schema &middot; pack '+esc(p)+'</h4>'+fhtml+'</div>'+
                 '<div class="schema-col"><h4>Cards <button class="ghost" onclick="sdAddCard()">+ card</button></h4>'+chtml+'</div>';
}

function toggleDbg(btn, panelId){
  document.querySelectorAll('.dbg-bar button').forEach(function(b){ b.classList.remove('active'); });
  btn.classList.add('active');
  showDbgPanel(panelId);
  expandDbg();
}
function showDbgPanel(id){
  document.querySelectorAll('.dbg-panel').forEach(function(p){ p.classList.remove('active'); });
  var el=document.getElementById(id); if(el) el.classList.add('active');
}
function collapseDbg(){
  document.getElementById('dbgPanels').classList.toggle('collapsed');
}
function expandDbg(){
  document.getElementById('dbgPanels').classList.remove('collapsed');
}

function getSrc(){return document.getElementById('src').value;}
function setSrc(v){document.getElementById('src').value=v;if(typeof filesRender==='function')filesRender();}
function onLangChange(){if(typeof filesRender==='function')filesRender();}

// editor language switch (PIOS feedback): if the current source is a known guide
// sample, swap to that sample's syntax in the new language and compile; otherwise
// leave the user's own text untouched and do NOT auto-compile incompatible syntax.
function sampleMatch(src){
  if(!src) return null;
  for(var i=0;i<DATA.length;i++){ var d=DATA[i];
    var langs=['c','basic','python','english'];
    for(var k=0;k<langs.length;k++){ var L=langs[k]; if(d[L]&&d[L].src===src) return {idx:i}; }
  }
  return null;
}
function editorLangChange(){
  var newLang=document.getElementById('lang').value, m=sampleMatch(getSrc());
  if(m && DATA[m.idx][newLang]){ setSrc(DATA[m.idx][newLang].src); compileSrc(true); }
  else { onLangChange(); }
}

// ---- localStorage-backed playground files ----------------------------------
var PS_FILES_KEY='picoscript.files.v1';
var PS_ACTIVE_FILE_KEY='picoscript.files.active';
var ACTIVE_FILE='';
function filesSafeLocalStorage(){try{return typeof localStorage!=='undefined'?localStorage:null;}catch(e){return null;}}
function filesRead(){
  var ls=filesSafeLocalStorage(); if(!ls) return {};
  try{
    var raw=ls.getItem(PS_FILES_KEY), parsed=raw?JSON.parse(raw):{}, out={};
    if(!parsed||typeof parsed!=='object'||Array.isArray(parsed)) return {};
    Object.keys(parsed).forEach(function(name){
      var f=parsed[name]||{};
      if(typeof name==='string'&&name.trim()&&typeof f.src==='string'&&['c','basic','python','english'].indexOf(f.lang)>=0){
        out[name]={lang:f.lang,src:f.src,updated:Number(f.updated)||0};
      }
    });
    return out;
  }catch(e){return {};}
}
function filesWrite(files){var ls=filesSafeLocalStorage(); if(!ls) return; try{ls.setItem(PS_FILES_KEY,JSON.stringify(files));}catch(e){}}
function filesSetActive(name){ACTIVE_FILE=name||'';var ls=filesSafeLocalStorage();if(!ls)return;try{if(ACTIVE_FILE)ls.setItem(PS_ACTIVE_FILE_KEY,ACTIVE_FILE);else ls.removeItem(PS_ACTIVE_FILE_KEY);}catch(e){}}
function filesEscAttr(s){return esc(String(s)).replace(/"/g,'&quot;');}
function filesStatus(msg,err){var el=document.getElementById('fileStatus');if(el){el.textContent=msg||'';el.style.color=err?'var(--err)':'var(--muted)';}}
function filesUniqueName(files){
  var lang=(document.getElementById('lang')||{}).value||'basic', base='Untitled.'+lang, name=base, n=2;
  while(files[name]) name='Untitled '+(n++)+'.'+lang;
  return name;
}
function filesIsDirty(){
  if(!ACTIVE_FILE) return false;
  var f=filesRead()[ACTIVE_FILE]; if(!f) return false;
  return f.src!==getSrc()||f.lang!==document.getElementById('lang').value;
}
function filesRender(){
  var list=document.getElementById('fileList'); if(!list) return;
  var files=filesRead(), names=Object.keys(files).sort(function(a,b){return (files[b].updated||0)-(files[a].updated||0)||a.localeCompare(b);});
  if(!names.length){list.innerHTML='<div class="file-empty">No saved files yet.</div>';return;}
  var dirty=filesIsDirty();
  list.innerHTML=names.map(function(name){
    var f=files[name], isActive=name===ACTIVE_FILE, dot=isActive&&dirty?'*':'';
    var when=f.updated?new Date(f.updated).toLocaleString():'';
    return '<div class="file-item'+(isActive?' active':'')+'" data-name="'+filesEscAttr(name)+'">'+
      '<div class="file-row"><span class="file-dirty">'+dot+'</span><span class="file-name">'+esc(name)+'</span><span class="file-badge '+f.lang+'">'+f.lang+'</span></div>'+
      '<div class="file-meta">'+(when?esc(when):'saved')+'</div></div>';
  }).join('');
  list.querySelectorAll('.file-item').forEach(function(el){el.onclick=function(){psFilesOpen(el.getAttribute('data-name'));};});
}
function filesToggle(){var el=document.getElementById('fileSidebar');if(!el)return;el.classList.toggle('collapsed');var b=el.querySelector('.file-head button');if(b)b.innerHTML=el.classList.contains('collapsed')?'&#9654;':'&#9664;';}
function psFilesList(){return filesRead();}
function psFilesNew(name){
  var files=filesRead(); name=(name||((typeof prompt==='function')?prompt('New file name',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  if(files[name]&&typeof confirm==='function'&&!confirm('Replace "'+name+'"?')) return null;
  setSrc(''); files[name]={lang:document.getElementById('lang').value||'basic',src:'',updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender(); filesStatus('New file '+name); return name;
}
function psFilesSave(name){
  var files=filesRead(); name=(name||ACTIVE_FILE||((typeof prompt==='function')?prompt('Save file as',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  files[name]={lang:document.getElementById('lang').value||'basic',src:getSrc(),updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender(); filesStatus('Saved '+name); return name;
}
function psFilesSaveAs(name){return psFilesSave(name||((typeof prompt==='function')?prompt('Save file as',ACTIVE_FILE||filesUniqueName(filesRead())):''));}
function psFilesOpen(name){
  var files=filesRead(), names=Object.keys(files).sort();
  name=(name||((typeof prompt==='function')?prompt('Open file',ACTIVE_FILE||names[0]||''):'')||'').trim();
  if(!name||!files[name]){filesStatus(name?'File not found: '+name:'Open cancelled',!!name);return null;}
  document.getElementById('lang').value=files[name].lang; onLangChange(); setSrc(files[name].src); filesSetActive(name); filesRender(); filesStatus('Opened '+name); compileSrc(false); return files[name];
}
function psFilesRename(oldName,newName){
  var files=filesRead(); oldName=(oldName||ACTIVE_FILE||'').trim(); if(!oldName||!files[oldName]){filesStatus('No active file to rename',true);return null;}
  newName=(newName||((typeof prompt==='function')?prompt('Rename file',oldName):'')||'').trim(); if(!newName||newName===oldName)return oldName;
  if(files[newName]){filesStatus('File already exists: '+newName,true);return null;}
  files[newName]=files[oldName]; files[newName].updated=Date.now(); delete files[oldName]; filesWrite(files); if(ACTIVE_FILE===oldName)filesSetActive(newName); filesRender(); filesStatus('Renamed to '+newName); return newName;
}
function psFilesDelete(name,skipConfirm){
  var files=filesRead(); name=(name||ACTIVE_FILE||'').trim(); if(!name||!files[name]){filesStatus('No file selected',true);return false;}
  if(!skipConfirm&&typeof confirm==='function'&&!confirm('Delete "'+name+'"?')) return false;
  delete files[name]; filesWrite(files); if(ACTIVE_FILE===name)filesSetActive(''); filesRender(); filesStatus('Deleted '+name); return true;
}

function compileSrc(run){
  var lang=document.getElementById('lang').value, src=getSrc();
  var err=document.getElementById('cerr');
  try {
    var r=PicoCompile.compile(src,lang);
    DBG.words=r.words.map(function(w){return w>>>0;}); DBG.disasm=DBG.words.map(jsDisasm);
    err.textContent='compiled '+DBG.words.length+' words'; err.style.color='#7ee787';
    dbgReset(); if(run) dbgRun();
  } catch(e){ err.textContent=String(e.message||e); err.style.color='#ff7b72'; }
}
function dbgReset(){ streamReset(); UI_LOG=[]; DBG.vm=new PicoVM({gpioProvider:GP, cardStore:CARDSTORE, streamProvider:SP}); DBG.vm.load(DBG.words); render(); }
function dbgStep(){ if(DBG.vm){ DBG.vm.step(); render(); } }
function dbgRun(){ if(!DBG.vm) dbgReset(); var g=0; while(DBG.vm.step()&&g++<200000){} render(); }

function render(){
  var vm=DBG.vm; if(!vm) return;
  var L=document.getElementById('listing');
  L.innerHTML=DBG.disasm.map(function(t,idx){
    return '<div class="row'+(idx===vm.pc?' pc':'')+'">'+String(idx).padStart(3,' ')+'  '+esc(t)+'</div>';
  }).join('');
  var pcrow=L.querySelector('.row.pc'); if(pcrow) pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('regs').innerHTML=Array.from(vm.regs).map(function(v,idx){
    return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  document.getElementById('state').textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted+'  http_status='+vm.httpStatus;
  document.getElementById('out').textContent='output: ['+vm.outputInts().join(', ')+']';
  var ps=document.getElementById('psunit');
  if(ps){
    var tot=(vm._asTotal||0)>>>0, fail=(vm._asFailed||0)>>>0;
    if(tot===0){ ps.textContent=''; }
    else if(fail===0){ ps.textContent='PSUnit: \u2713 '+tot+'/'+tot+' assertions passed'; ps.style.color='#7ee787'; }
    else { ps.textContent='PSUnit: \u2717 '+fail+'/'+tot+' assertions FAILED'; ps.style.color='#ff7b72'; }
  }
  renderGpio();
  renderCards();
  renderStream();
  renderUi();
}

function jsDisasm(w){
  var names=["NOOP","LOAD","SAVE","PIPE","ADD","SUB","MUL","DIV","INC","JUMP","BRANCH","CALL","RETURN","WAIT","RAISE","DSP"];
  var br=["EQ","NE","LT","GT","LE","GE","Z","NZ","EOF","ERR"];
  var op=(w>>>28)&0xF, rd=(w>>>24)&0xF, rs1=(w>>>20)&0xF, rs2=(w>>>16)&0xF, imm=w&0xFFFF;
  if(op===0&&(imm&0xFF00)===0x7000) return "HOSTCALL #0x"+(imm&0xFF).toString(16);
  if(op===0&&(imm&0xF000)===0x8000) return "NET.STATUS "+(imm&0xFFF);
  if(op===0&&imm===0xC000) return "NET.CLOSE";
  if(op>=4&&op<=7) return names[op]+" R"+rd+", R"+rs1+(rs2===1?(", R"+(imm&0xF)):(", #"+imm));
  if(op===8) return "INC R"+rd;
  if(op===9) return "JUMP "+imm;
  if(op===10){ var off=imm&0x8000?imm-0x10000:imm; return "BRANCH "+(br[rs2]||rs2)+" R"+rd+", R"+rs1+", "+(off>=0?"+":"")+off; }
  if(op===11) return "CALL "+imm;
  if(op>=1&&op<=3) return names[op]+" R"+(op===1?rd:rs1)+", [0x"+imm.toString(16)+"]";
  if(op===12) return "RETURN";
  return names[op]||("?"+op);
}

// ---- init ------------------------------------------------------------------
buildTree();
showCard(0);
document.getElementById('lang').value='basic';
setSrc(DATA[3] && DATA[3].basic ? DATA[3].basic.src : '');
document.getElementById('src').addEventListener('input',filesRender);
document.getElementById('lang').addEventListener('change',function(){editorLangChange();});
(function(){var ls=filesSafeLocalStorage(),active='';try{active=ls?ls.getItem(PS_ACTIVE_FILE_KEY)||'':'';}catch(e){} if(active&&filesRead()[active]) psFilesOpen(active); else filesRender();})();
compileSrc(false);
renderGpio();
renderCards();
renderStream();
renderUi();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
