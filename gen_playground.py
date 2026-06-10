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
     "Storage.DeleteCard(1)\nPRINT Storage.QueryCard(QRY)"),
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
    payload = json.dumps(data)

    html = PAGE.replace("/*__HOOKS__*/", hooks_js) \
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
          --text:#e6e8ef; --muted:#9aa0ad; --c:#7ee787; --b:#79c0ff; --py:#ffd866; --en:#f0a3ff; --warn:#ffd866; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:22px 28px; background:linear-gradient(120deg,#1a1d27,#232734);
           border-bottom:1px solid #2c313f; }
  header h1 { margin:0; font-size:24px; color:var(--accent); }
  header p { margin:6px 0 0; color:var(--muted); font-size:13px; }
  .wrap { display:grid; grid-template-columns:1fr 1fr; gap:0; }
  @media (max-width:1100px){ .wrap{ grid-template-columns:1fr; } }
  .col { padding:20px 24px; }
  h2.section { font-size:14px; text-transform:uppercase; letter-spacing:.08em;
               color:var(--muted); border-bottom:1px solid #2c313f; padding-bottom:8px; }
  .card { background:var(--panel); border:1px solid #2c313f; border-radius:10px;
          margin:0 0 18px; overflow:hidden; }
  .card h3 { margin:0; padding:12px 16px; font-size:15px; background:var(--panel2); }
  .card .desc { padding:8px 16px; color:var(--muted); font-size:12.5px; }
  .pair { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#2c313f; }
  .quad { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#2c313f; }
  @media (max-width:680px){ .pair{ grid-template-columns:1fr; } .quad{ grid-template-columns:1fr; } }
  .pane { background:var(--panel); }
  .pane .lbl { font-size:11px; font-weight:700; padding:6px 12px; color:#0f1117; }
  .pane.cstyle .lbl { background:var(--c); }
  .pane.bstyle .lbl { background:var(--b); }
  .pane.pystyle .lbl { background:var(--py); }
  .pane.enstyle .lbl { background:var(--en); }
  pre { margin:0; padding:12px; font-family:"SF Mono",Consolas,monospace; font-size:12px;
        line-height:1.5; white-space:pre; overflow-x:auto; }
  .cstyle pre { color:#cde9c8; } .bstyle pre { color:#cfe4ff; }
  .pystyle pre { color:#f5e6a8; } .enstyle pre { color:#f3d4ff; }
  .runbar { display:flex; align-items:center; gap:12px; padding:10px 16px; background:var(--panel2);
            border-top:1px solid #2c313f; }
  button { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:7px 14px;
           font-weight:600; cursor:pointer; font-size:12.5px; }
  button:hover { filter:brightness(1.1); } button.ghost { background:#2c313f; color:var(--text); }
  .out { font-family:"SF Mono",Consolas,monospace; font-size:12.5px; color:var(--warn); }
  /* debugger */
  .debugger { position:sticky; top:0; z-index:5; background:#11141c; border-bottom:1px solid #2c313f;
              padding:14px 24px; }
  .dbg-grid { display:grid; grid-template-columns:340px 1fr 260px; gap:16px; }
  @media (max-width:1100px){ .dbg-grid{ grid-template-columns:1fr; } }
  select, textarea { background:#0c0e14; color:var(--text); border:1px solid #2c313f;
                     border-radius:6px; padding:7px; font-family:inherit; font-size:12px; width:100%; }
  textarea { font-family:"SF Mono",Consolas,monospace; height:64px; resize:vertical; }
  .listing { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; max-height:240px;
             overflow:auto; font-family:"SF Mono",Consolas,monospace; font-size:12px; }
  .listing .row { padding:2px 10px; white-space:pre; color:#9aa0ad; }
  .listing .row.pc { background:#2d3550; color:#fff; }
  .regs { display:grid; grid-template-columns:repeat(2,1fr); gap:3px 10px; font-family:"SF Mono",monospace;
          font-size:12px; }
  .regs .r { color:var(--muted); } .regs .r b { color:var(--text); }
  .state { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:var(--muted); margin-top:8px; }
  .controls { display:flex; gap:8px; margin:10px 0; flex-wrap:wrap; }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600;
          background:#2c313f; color:var(--muted); }
  a { color:var(--b); }
</style>
</head>
<body>
<header>
  <h1>PicoScript Playground &amp; Language Guide</h1>
  <p>Every construct in both surface styles, side by side &mdash; runnable and steppable in your
     browser on the same VM that runs on bare metal. <span class="pill">case-insensitive</span>
     <span class="pill">C-style &#123;&#125;</span> <span class="pill">BASIC block</span></p>
</header>

<div class="debugger">
  <h2 class="section" style="margin-top:0">Live debugger &mdash; step the bytecode in the browser</h2>
  <div class="dbg-grid">
    <div>
      <div style="font-size:11px;color:#9aa0ad;margin-bottom:4px">compile your own source in the browser:</div>
      <select id="lang" style="margin-bottom:6px">
        <option value="c">C-style &#123; &#125;</option>
        <option value="basic">BASIC block</option>
        <option value="python">Python-style</option>
        <option value="english">Natural English</option>
      </select>
      <textarea id="src" style="height:120px" spellcheck="false"></textarea>
      <div class="controls">
        <button onclick="compileSrc(true)">Compile &amp; Run &#9654;</button>
        <button class="ghost" onclick="compileSrc(false)">Compile &amp; Step</button>
      </div>
      <div id="cerr" style="color:#ff7b72;font-size:11.5px;font-family:monospace;min-height:14px"></div>
      <hr style="border-color:#2c313f;margin:10px 0">
      <div style="font-size:11px;color:#9aa0ad;margin-bottom:4px">&hellip;or load a prebuilt example:</div>
      <select id="prog"></select>
      <div class="controls">
        <button onclick="dbgRun()">Run &#9654;</button>
        <button class="ghost" onclick="dbgStep()">Step</button>
        <button class="ghost" onclick="dbgReset()">Reset</button>
      </div>
      <div style="font-size:11px;color:#9aa0ad;margin-bottom:4px">&hellip;or paste bytecode hex
        (<code>emit &hellip; --as bytecode --hex</code>):</div>
      <textarea id="hex" placeholder="04000064&#10;48000001&hellip;"></textarea>
      <button class="ghost" style="margin-top:6px" onclick="loadHex()">Load hex</button>
    </div>
    <div>
      <div style="font-size:11px;color:#9aa0ad;margin-bottom:4px">disassembly (current PC highlighted):</div>
      <div class="listing" id="listing"></div>
      <div class="state" id="state"></div>
      <div class="out" id="out" style="margin-top:6px"></div>
    </div>
    <div>
      <div style="font-size:11px;color:#9aa0ad;margin-bottom:4px">registers R0&ndash;R15:</div>
      <div class="regs" id="regs"></div>
    </div>
  </div>
</div>

<div class="wrap" id="gallery"></div>

<script>/*__HOOKS__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
<script>/*__SER__*/</script>
<script>/*__STORE__*/</script>
<script>
const DATA = /*__DATA__*/;

// ---- gallery (side-by-side guide) -----------------------------------------
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function buildGallery(){
  const cols = [document.createElement('div'), document.createElement('div')];
  cols[0].className = 'col'; cols[1].className = 'col';
  cols[0].innerHTML = '<h2 class="section">Constructs &mdash; left half</h2>';
  cols[1].innerHTML = '<h2 class="section">Constructs &mdash; right half</h2>';
  const STYLES = [['c','C { }','cstyle'],['basic','BASIC','bstyle'],['python','PYTHON','pystyle'],['english','ENGLISH','enstyle']];
  DATA.forEach((d, i) => {
    const card = document.createElement('div');
    card.className = 'card';
    const present = STYLES.filter(s => d[s[0]]);
    const panes = present.map(s =>
      '<div class="pane '+s[2]+'"><div class="lbl">'+s[1]+'</div><pre>'+esc(d[s[0]].src)+'</pre></div>').join('');
    const dbg = present.map(s =>
      '<button class="ghost" onclick="debugIn('+i+',\''+s[0]+'\')">Debug '+s[1].split(' ')[0]+'</button>').join('');
    card.innerHTML =
      '<h3>'+(i+1)+'. '+esc(d.title)+'</h3>'+
      '<div class="desc">'+esc(d.desc)+'</div>'+
      '<div class="quad">'+panes+'</div>'+
      '<div class="runbar">'+
        '<button onclick="runCard('+i+')">Run &#9654;</button>'+
        '<span class="out" id="cardout'+i+'">output &rarr; &hellip;</span>'+
        '<span style="margin-left:auto"></span>'+dbg+
      '</div>';
    cols[i % 2].appendChild(card);
  });
  const g = document.getElementById('gallery');
  g.appendChild(cols[0]); g.appendChild(cols[1]);
}

function runWords(hexWords){
  const vm = new PicoVM();
  vm.run(hexWords.map(h => parseInt(h, 16) >>> 0));
  return vm;
}
function runCard(i){
  const d = DATA[i];
  const STYLES = ['c','basic','python','english'];
  const parts = []; let ref = null, same = true;
  STYLES.forEach(s => { if (!d[s]) return;
    const o = runWords(d[s].words).outputInts();
    if (ref === null) ref = JSON.stringify(o); else if (JSON.stringify(o) !== ref) same = false;
    parts.push(s+' &rarr; ['+o.join(', ')+']'); });
  document.getElementById('cardout'+i).innerHTML =
    parts.join('  &nbsp; ')+'  '+(same ? '&#10003; identical' : '&#9888; differ');
}

// ---- debugger -------------------------------------------------------------
let DBG = { words: [], disasm: [], vm: null };

function buildProgList(){
  const sel = document.getElementById('prog');
  const LABELS = {c:'C-style', basic:'BASIC', python:'Python', english:'English'};
  DATA.forEach((d, i) => {
    ['c','basic','python','english'].forEach(style => {
      if (!d[style]) return;
      const o = document.createElement('option');
      o.value = i+':'+style;
      o.textContent = (i+1)+'. '+d.title+'  ['+LABELS[style]+']';
      sel.appendChild(o);
    });
  });
  sel.onchange = loadSelected;
}
function loadSelected(){
  const [i, style] = document.getElementById('prog').value.split(':');
  const d = DATA[+i][style];
  DBG.words = d.words.map(h => parseInt(h, 16) >>> 0);
  DBG.disasm = d.disasm.slice();
  dbgReset();
}
function debugIn(i, style){
  document.getElementById('lang').value = style;
  document.getElementById('src').value = DATA[i][style].src;
  compileSrc(false);
  window.scrollTo({ top:0, behavior:'smooth' });
}
function loadHex(){
  const toks = document.getElementById('hex').value.trim().split(/\s+/).filter(Boolean);
  DBG.words = toks.map(h => parseInt(h, 16) >>> 0);
  DBG.disasm = DBG.words.map(jsDisasm);
  dbgReset();
}

// ---- in-browser compile (picoc.js) ----------------------------------------
function compileSrc(run){
  const lang = document.getElementById('lang').value;
  const src = document.getElementById('src').value;
  const err = document.getElementById('cerr');
  try {
    const r = PicoCompile.compile(src, lang);
    DBG.words = r.words.map(w => w >>> 0);
    DBG.disasm = DBG.words.map(jsDisasm);
    err.textContent = 'compiled ' + DBG.words.length + ' words';
    err.style.color = '#7ee787';
    dbgReset();
    if (run) dbgRun();
  } catch (e) {
    err.textContent = String(e.message || e);
    err.style.color = '#ff7b72';
  }
}

function dbgReset(){
  DBG.vm = new PicoVM();
  DBG.vm.load(DBG.words);
  render();
}
function dbgStep(){
  if (!DBG.vm) return;
  DBG.vm.step();
  render();
}
function dbgRun(){
  if (!DBG.vm) dbgReset();
  let guard = 0;
  while (DBG.vm.step() && guard++ < 200000) {}
  render();
}
function render(){
  const vm = DBG.vm; if (!vm) return;
  const L = document.getElementById('listing');
  L.innerHTML = DBG.disasm.map((t, idx) =>
    '<div class="row'+(idx===vm.pc?' pc':'')+'">'+
    String(idx).padStart(3,' ')+'  '+esc(t)+'</div>').join('');
  const pcrow = L.querySelector('.row.pc'); if (pcrow) pcrow.scrollIntoView({block:'nearest'});
  const R = document.getElementById('regs');
  R.innerHTML = Array.from(vm.regs).map((v,idx)=>
    '<div class="r">R'+idx+' <b>'+v+'</b></div>').join('');
  document.getElementById('state').textContent =
    'pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted+'  http_status='+vm.httpStatus;
  document.getElementById('out').textContent =
    'output: [' + vm.outputInts().join(', ') + ']';
}

// minimal JS disassembler for pasted bytecode
function jsDisasm(w){
  const names=["NOOP","LOAD","SAVE","PIPE","ADD","SUB","MUL","DIV","INC","JUMP","BRANCH","CALL","RETURN","WAIT","RAISE","DSP"];
  const br=["EQ","NE","LT","GT","LE","GE","Z","NZ","EOF","ERR"];
  const op=(w>>>28)&0xF, rd=(w>>>24)&0xF, rs1=(w>>>20)&0xF, rs2=(w>>>16)&0xF, imm=w&0xFFFF;
  if(op===0&&(imm&0xFF00)===0x7000) return "HOSTCALL #0x"+(imm&0xFF).toString(16);
  if(op===0&&(imm&0xF000)===0x8000) return "NET.STATUS "+(imm&0xFFF);
  if(op===0&&imm===0xC000) return "NET.CLOSE";
  if(op>=4&&op<=7) return names[op]+" R"+rd+", R"+rs1+(rs2===1?(", R"+(imm&0xF)):(", #"+imm));
  if(op===8) return "INC R"+rd;
  if(op===9) return "JUMP "+imm;
  if(op===10){ let off=imm&0x8000?imm-0x10000:imm; return "BRANCH "+(br[rs2]||rs2)+" R"+rd+", R"+rs1+", "+(off>=0?"+":"")+off; }
  if(op===11) return "CALL "+imm;
  if(op>=1&&op<=3) return names[op]+" R"+(op===1?rd:rs1)+", [0x"+imm.toString(16)+"]";
  if(op===12) return "RETURN";
  return names[op]||("?"+op);
}

buildGallery();
buildProgList();
loadSelected();
// prefill the editor with a BASIC example and compile it live
document.getElementById('lang').value = 'basic';
document.getElementById('src').value = DATA[3].basic.src;
compileSrc(false);
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
