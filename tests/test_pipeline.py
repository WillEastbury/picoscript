#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_pipeline.py -- end-to-end tests for the PicoScript toolchain.

Verifies, for a battery of programs in both frontends:
  1. C-syntax and BASIC-like sources lowering to PicoIL and running on PicoVM
     produce the expected results.
  2. The portable C VM (vm/picovm_run.exe) produces *identical* register files,
     output bytes and HTTP status from the same bytecode (host/target parity).

Run:  python tests/test_pipeline.py
The C VM harness is built on demand with `python -m ziglang cc` if missing.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic                # noqa: E402
from picoscript_cfront import compile_c                   # noqa: E402
from picoscript_python import compile_python              # noqa: E402
from picoscript_english import compile_english            # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM                           # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def build_c_vm():
    if os.path.exists(VM_EXE):
        return True
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"),
           "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("C VM build failed:\n", r.stderr)
        return False
    return True


def run_c_vm(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    res = {"regs": [], "out": [], "status": -1, "steps": 0}
    for line in out.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "STEPS":
            res["steps"] = int(p[1])
        elif p[0] == "STATUS":
            res["status"] = int(p[1])
        elif p[0] == "REGS":
            res["regs"] = [int(x) for x in p[1:]]
        elif p[0] == "OUT":
            res["out"] = p[1:]
    return res


def run_js_vm(words):
    """Drive the JS bytecode VM (vm/picovm.js) via Node."""
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    runner = os.path.join(VM_DIR, "picovm_run.js")
    out = subprocess.run(["node", runner], input=inp, capture_output=True, text=True).stdout
    res = {"regs": [], "out": [], "status": -1, "steps": 0}
    for line in out.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "STEPS":
            res["steps"] = int(p[1])
        elif p[0] == "STATUS":
            res["status"] = int(p[1])
        elif p[0] == "REGS":
            res["regs"] = [int(x) for x in p[1:]]
        elif p[0] == "OUT":
            res["out"] = p[1:]
    return res


def py_state(words):
    vm = PicoVM().run(words)
    regs = [s32(vm.regs[i]) for i in range(16)]
    out = []
    for b in vm.output:
        out += [f"{x:02x}" for x in b]
    status = vm.http_status if vm.http_status is not None else -1
    return {"regs": regs, "out": out, "status": status, "steps": vm.steps, "vm": vm}


def js_compile(src, lang):
    """Compile source with the in-browser JS compiler (vm/picoc.js) via Node."""
    runner = os.path.join(VM_DIR, "picoc_compile.js")
    r = subprocess.run(["node", runner, lang], input=src, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip()
    return [int(w, 16) for w in r.stdout.split()], None


def decode_print(vm):
    def s32(v):
        return v - 0x100000000 if v & 0x80000000 else v
    return [s32(int.from_bytes(b, "big")) for b in vm.output]


# -- test programs ------------------------------------------------------------

C_LOOP = """
int acc = 0;
for (i = 0; i < 10; i = i + 1) { acc = acc + i; }
Net.Status(200);
Net.Type("application/json");
return acc;
"""

C_IF = """
int x = 7;
int r = 0;
if (x > 5) { r = 100; } else { r = 200; }
while (x > 0) { x = x - 1; r = r + 1; }
return r;
"""

C_NEST = """
int total = 0;
for (i = 1; i <= 3; i = i + 1) {
    for (j = 1; j <= 3; j = j + 1) {
        total = total + i * j;
    }
}
return total;
"""

BASIC_FULL = """
LET Y = 0
FOR I = 1 TO 5
    Y = Y + I
NEXT
LET ACC = 0
FOREACH J IN 4
    ACC = ACC + J
ENDFOREACH
GOSUB WORKER
IF Y GT 10 THEN
    PRINT Y
ELSE
    PRINT 0
ENDIF
PRINT ACC
SWITCH Y
    CASE 14
        PRINT 1400
    CASE 15
        PRINT 1500
    DEFAULT
        PRINT 9999
ENDSWITCH
PRINT Z
RETURN
SUB WORKER
    Z = 42
ENDSUB
"""

BASIC_GOTO = """
LET N = 0
TOP:
N = N + 1
IF N LT 4 THEN
    GOTO TOP
ENDIF
PRINT N
"""

BASIC_FIZZ = """
FOR N = 1 TO 15
    LET M3 = N - N / 3 * 3
    LET M5 = N - N / 5 * 5
    IF M3 EQ 0 THEN
        IF M5 EQ 0 THEN
            PRINT 0 - 3
        ELSE
            PRINT 0 - 1
        ENDIF
    ELSEIF M5 EQ 0 THEN
        PRINT 0 - 2
    ELSE
        PRINT N
    ENDIF
NEXT
RETURN
"""

BASIC_NESTED = """
LET T = 0
FOR I = 1 TO 4
    FOR J = 1 TO 4
        T = T + I * J
    NEXT
NEXT
PRINT T
"""

BASIC_DOLOOP_POST = """
LET I = 0
LET S = 0
DO
    I = I + 1
    S = S + I
LOOP UNTIL I GE 5
PRINT S
PRINT I
"""

BASIC_DOLOOP_ONCE = """
LET N = 10
DO
    N = N + 1
LOOP WHILE N LT 5
PRINT N
"""

BASIC_DOLOOP_PRE = """
LET I = 0
DO WHILE I LT 3
    I = I + 1
LOOP
PRINT I
"""

BASIC_DOLOOP_NESTED = """
LET T = 0
FOR I = 1 TO 3
    LET J = 0
    DO
        J = J + 1
        T = T + J
    LOOP UNTIL J GE I
NEXT
PRINT T
"""

BASIC_BREAK_FOR = """
LET S = 0
FOR I = 1 TO 10
    IF I GT 3 THEN
        BREAK
    ENDIF
    S = S + I
NEXT
PRINT S
"""

BASIC_SKIP_FOR = """
LET S = 0
FOR I = 1 TO 6
    LET M = I - I / 2 * 2
    IF M EQ 0 THEN
        SKIP
    ENDIF
    S = S + I
NEXT
PRINT S
"""

BASIC_SKIP_SWITCH = """
LET S = 0
FOR I = 1 TO 5
    SWITCH I
        CASE 3
            SKIP
        DEFAULT
            S = S + I
    ENDSWITCH
NEXT
PRINT S
"""

BASIC_BREAK_DO = """
LET I = 0
DO
    I = I + 1
    IF I GE 7 THEN
        BREAK
    ENDIF
LOOP WHILE I LT 100
PRINT I
"""

C_OPS = """
int x = 10;
x++;
x += 5;
int y = x % 7;
int z = (y == 2 && x > 10) ? 100 : 0;
print(x); print(y); print(z);
"""

BASIC_OPS = """
DIM X = 10
INC X
X += 5
DIM Y = X MOD 7
DIM Z = IIF(Y = 2 AND X > 10, 100, 0)
PRINT X
PRINT Y
PRINT Z
"""


SPAN_SLICE = """
DIM P = 100
FOR I = 0 TO 15
    Memory.Set(P + I, I)
NEXT
DIM S = Span.Make(100, 16)
DIM S2 = Span.Slice(S, 4)
DIM S3 = Span.Materialize(S2)
PRINT Span.Len(S2)
PRINT Span.Get(S2, 0)
PRINT Span.Get(S3, 0)
Memory.Set(104, 99)
PRINT Span.Get(S2, 0)
PRINT Span.Get(S3, 0)
"""


# Program-level card store: build the field name "qty" and the query "qty > 40"
# as UTF-8 byte-spans in arena memory, then create/fetch/update/delete/query
# cards through the Storage.* host hooks (PicoStore-backed in both VMs).
STORAGE_CRUD_BASIC = """
Memory.Set(200, 113)
Memory.Set(201, 116)
Memory.Set(202, 121)
DIM QTY = Span.Make(200, 3)
Memory.Set(210, 113)
Memory.Set(211, 116)
Memory.Set(212, 121)
Memory.Set(213, 32)
Memory.Set(214, 62)
Memory.Set(215, 32)
Memory.Set(216, 52)
Memory.Set(217, 48)
DIM QRY = Span.Make(210, 8)
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
"""

STORAGE_CRUD_C = """
Memory.Set(200, 113);
Memory.Set(201, 116);
Memory.Set(202, 121);
int qty = Span.Make(200, 3);
Memory.Set(210, 113);
Memory.Set(211, 116);
Memory.Set(212, 121);
Memory.Set(213, 32);
Memory.Set(214, 62);
Memory.Set(215, 32);
Memory.Set(216, 52);
Memory.Set(217, 48);
int qry = Span.Make(210, 8);
Storage.UsePack(1);
int a = Storage.AddCard();
Storage.SetField(qty, 42);
int b = Storage.AddCard();
Storage.SetField(qty, 7);
int c = Storage.AddCard();
Storage.SetField(qty, 99);
Storage.EditCard(b);
print(Storage.GetField(qty));
Storage.SetField(qty, 50);
print(Storage.GetField(qty));
int n = Storage.QueryCard(qry);
print(n);
print(Storage.QueryResult(0));
print(Storage.QueryResult(1));
print(Storage.QueryResult(2));
Storage.DeleteCard(1);
print(Storage.QueryCard(qry));
"""


# Python-style and English-style frontends -- both reuse the BASIC AST + Lowerer,
# so their bytecode is byte-for-byte identical to the equivalent BASIC program.
PY_CTRL = """
s = 0
for i in range(1, 11):
    s += i
n = 5
f = 1
while n > 1:
    f = f * n
    n -= 1
if s > 50:
    print(f)
else:
    print(0)
print(s)
"""

EN_CTRL = """
Set s to 0.
For each i from 1 to 10:
    Increase s by i.
Set n to 5.
Set f to 1.
While n is greater than 1:
    Set f to f times n.
    Decrease n by 1.
If s is greater than 50:
    Print f.
Otherwise:
    Print 0.
Print s.
"""

BASIC_CTRL = """
S = 0
FOR I = 1 TO 10
    S += I
NEXT
N = 5
F = 1
WHILE N > 1
    F = F * N
    N -= 1
ENDWHILE
IF S > 50 THEN
    PRINT F
ELSE
    PRINT 0
ENDIF
PRINT S
"""

# Host-hook span program in the two new surfaces (Python VM == JS VM).
PY_SPAN = """
p = 100
for i in range(0, 16):
    Memory.Set(p + i, i)
s = Span.Make(100, 16)
s2 = Span.Slice(s, 4)
print(Span.Len(s2))
print(Span.Get(s2, 0))
"""

EN_SPAN = """
Set p to 100.
For each i from 0 to 15:
    Memory.Set(p plus i, i).
Set s to Span.Make(100, 16).
Set s2 to Span.Slice(s, 4).
Print Span.Len(s2).
Print Span.Get(s2, 0).
"""


def main():
    if not build_c_vm():
        sys.exit(1)

    passed = 0
    failed = 0

    def check_pyjs(name, words, expect_print):
        """Python VM == JS VM (for programs using host hooks the C VM leaves to
        the bare-metal host, e.g. Span/Memory slice + materialize)."""
        nonlocal passed, failed
        py = py_state(words)
        jv = run_js_vm(words)
        got = decode_print(py["vm"])
        ok = (py["out"] == jv["out"] and py["regs"] == jv["regs"] and got == expect_print)
        detail = "" if ok else f"\n    py_out={py['out']} js_out={jv['out']} print={got} want={expect_print}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} Python VM == JS VM  print={got}{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    def check(name, words, expect_print=None, expect_status=None):
        nonlocal passed, failed
        py = py_state(words)
        cv = run_c_vm(words)
        jv = run_js_vm(words)
        parity = (py["regs"] == cv["regs"] and py["out"] == cv["out"]
                  and py["status"] == cv["status"]
                  and jv["regs"] == py["regs"] and jv["out"] == py["out"]
                  and jv["status"] == py["status"])
        ok = parity
        detail = ""
        if expect_print is not None:
            got = decode_print(py["vm"])
            if got != expect_print:
                ok = False
                detail += f" print={got} want={expect_print}"
        if expect_status is not None and py["status"] != expect_status:
            ok = False
            detail += f" status={py['status']} want={expect_status}"
        if not parity:
            detail += " HOST/TARGET-MISMATCH"
            detail += f"\n    py_regs={py['regs']}\n    c_regs ={cv['regs']}\n    js_regs={jv['regs']}"
            detail += f"\n    py_out ={py['out']}\n    c_out  ={cv['out']}\n    js_out ={jv['out']}"
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:22s} steps={py['steps']:>4} (C {cv['steps']:>4}) "
              f"status={py['status']} [py=c=js]{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    def check_toc(name, il, expect_status=None):
        """Lower IL -> C, compile with picovm.c, run, compare OUT+STATUS to the VM."""
        nonlocal passed, failed
        import tempfile
        words = lower_to_bytecode_safe(il)
        py = py_state(words)
        csrc = lower_to_c(il, func_name="pico_main", emit_main=True)
        tmp = tempfile.mkdtemp(prefix="pico_toc_")
        cfile = os.path.join(tmp, "prog.c")
        exe = os.path.join(tmp, "prog.exe")
        with open(cfile, "w") as f:
            f.write(csrc)
        cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
               f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [FAIL] {name:22s} emitted C did not compile:\n{r.stderr[:800]}")
            failed += 1
            return
        out = subprocess.run([exe], capture_output=True, text=True).stdout
        cv = {"out": [], "status": -1}
        for line in out.splitlines():
            p = line.split()
            if not p:
                continue
            if p[0] == "STATUS":
                cv["status"] = int(p[1])
            elif p[0] == "OUT":
                cv["out"] = p[1:]
        ok = (py["out"] == cv["out"] and py["status"] == cv["status"])
        if expect_status is not None and py["status"] != expect_status:
            ok = False
        detail = ""
        if not ok:
            detail = (f"\n    vm_out ={py['out']} vm_status={py['status']}"
                      f"\n    c_out  ={cv['out']} c_status ={cv['status']}")
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:22s} native-C out/status match VM{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    def check_tojs(name, il, expect_status=None):
        """Lower IL -> JS, run emitted module in Node, compare OUT+STATUS to VM."""
        nonlocal passed, failed
        import tempfile
        words = lower_to_bytecode_safe(il)
        py = py_state(words)
        jssrc = lower_to_js(il, module_name="pico")
        tmp = tempfile.mkdtemp(prefix="pico_tojs_")
        jsfile = os.path.join(tmp, "prog.js")
        runner = os.path.join(tmp, "run.js")
        with open(jsfile, "w") as f:
            f.write(jssrc)
        with open(runner, "w") as f:
            f.write(
                "const p = require('./prog.js');\n"
                "const rt = p.run();\n"
                "const out = [];\n"
                "for (const v of rt.outputInts()) out.push(v);\n"
                "console.log('STATUS ' + rt.httpStatus);\n"
                "console.log('OUT ' + rt.output.map(b=>b.toString(16).padStart(2,'0')).join(' '));\n"
            )
        out = subprocess.run(["node", runner], capture_output=True, text=True)
        if out.returncode != 0:
            print(f"  [FAIL] {name:22s} emitted JS did not run:\n{out.stderr[:800]}")
            failed += 1
            return
        jv = {"out": [], "status": -1}
        for line in out.stdout.splitlines():
            p = line.split()
            if not p:
                continue
            if p[0] == "STATUS":
                jv["status"] = int(p[1])
            elif p[0] == "OUT":
                jv["out"] = p[1:]
        ok = (py["out"] == jv["out"] and py["status"] == jv["status"])
        if expect_status is not None and py["status"] != expect_status:
            ok = False
        detail = ""
        if not ok:
            detail = (f"\n    vm_out ={py['out']} vm_status={py['status']}"
                      f"\n    js_out ={jv['out']} js_status ={jv['status']}")
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:22s} emitted-JS out/status match VM{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("C-syntax frontend (Python = C = JS VM parity):")
    check("c: loop-sum", lower_to_bytecode_safe(compile_c(C_LOOP)), expect_status=200)
    check("c: if+while", lower_to_bytecode_safe(compile_c(C_IF)))
    check("c: nested-for", lower_to_bytecode_safe(compile_c(C_NEST)))

    print("BASIC-like frontend (Python = C = JS VM parity):")
    check("basic: full", lower_to_bytecode_safe(compile_basic(BASIC_FULL)),
          expect_print=[15, 6, 1500, 42])
    check("basic: goto-loop", lower_to_bytecode_safe(compile_basic(BASIC_GOTO)),
          expect_print=[4])
    check("basic: fizzbuzz", lower_to_bytecode_safe(compile_basic(BASIC_FIZZ)),
          expect_print=[1, 2, -1, 4, -2, -1, 7, 8, -1, -2, 11, -1, 13, 14, -3])
    check("basic: nested-for", lower_to_bytecode_safe(compile_basic(BASIC_NESTED)),
          expect_print=[100])
    check("basic: do-loop-until", lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_POST)),
          expect_print=[15, 5])
    check("basic: do-loop once", lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_ONCE)),
          expect_print=[11])
    check("basic: do-while pre", lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_PRE)),
          expect_print=[3])
    check("basic: do-loop nested", lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_NESTED)),
          expect_print=[10])
    check("basic: break-for", lower_to_bytecode_safe(compile_basic(BASIC_BREAK_FOR)),
          expect_print=[6])
    check("basic: skip-for", lower_to_bytecode_safe(compile_basic(BASIC_SKIP_FOR)),
          expect_print=[9])
    check("basic: skip-thru-switch", lower_to_bytecode_safe(compile_basic(BASIC_SKIP_SWITCH)),
          expect_print=[12])
    check("basic: break-do", lower_to_bytecode_safe(compile_basic(BASIC_BREAK_DO)),
          expect_print=[7])

    print("Operators (++/-- ternary && || % compound, DIM, symbol/word compares):")
    check("c: operators", lower_to_bytecode_safe(compile_c(C_OPS)), expect_print=[16, 2, 100])
    check("basic: operators", lower_to_bytecode_safe(compile_basic(BASIC_OPS)), expect_print=[16, 2, 100])

    print("Span slice (zero-copy view) + materialize (copy) [Python VM == JS VM]:")
    check_pyjs("span: slice+materialize", lower_to_bytecode_safe(compile_basic(SPAN_SLICE)),
               expect_print=[12, 4, 4, 99, 4])

    print("Storage.* program-level card CRUD + query [Python VM == JS VM]:")
    check_pyjs("storage: basic crud+query", lower_to_bytecode_safe(compile_basic(STORAGE_CRUD_BASIC)),
               expect_print=[7, 50, 3, 1, 2, 3, 2])
    check_pyjs("storage: c crud+query", lower_to_bytecode_safe(compile_c(STORAGE_CRUD_C)),
               expect_print=[7, 50, 3, 1, 2, 3, 2])

    def check_equiv(name, words_a, words_b):
        """Two frontends must lower to byte-identical bytecode (AST reuse proof)."""
        nonlocal passed, failed
        ok = (words_a == words_b)
        detail = "" if ok else f"\n    a={[f'{w:08x}' for w in words_a]}\n    b={[f'{w:08x}' for w in words_b]}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} {len(words_a)} words a==b{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("Python-style + English-style frontends (3-VM parity + semantics):")
    check("python: control flow", lower_to_bytecode_safe(compile_python(PY_CTRL)), expect_print=[120, 55])
    check("english: control flow", lower_to_bytecode_safe(compile_english(EN_CTRL)), expect_print=[120, 55])
    print("New frontends lower to byte-identical bytecode vs equivalent BASIC:")
    check_equiv("python == basic", lower_to_bytecode_safe(compile_python(PY_CTRL)),
                lower_to_bytecode_safe(compile_basic(BASIC_CTRL)))
    check_equiv("english == basic", lower_to_bytecode_safe(compile_english(EN_CTRL)),
                lower_to_bytecode_safe(compile_basic(BASIC_CTRL)))
    print("New frontends with host hooks [Python VM == JS VM]:")
    check_pyjs("python: span slice", lower_to_bytecode_safe(compile_python(PY_SPAN)), expect_print=[12, 4])
    check_pyjs("english: span slice", lower_to_bytecode_safe(compile_english(EN_SPAN)), expect_print=[12, 4])

    print("toC backend (compile + run emitted C, compare to VM):")
    check_toc("toC: c nested-for", compile_c(C_NEST))
    check_toc("toC: basic full+gosub", compile_basic(BASIC_FULL))
    check_toc("toC: c loop+net", compile_c(C_LOOP), expect_status=200)

    print("toJS backend (run emitted JS in Node, compare to VM):")
    check_tojs("toJS: c nested-for", compile_c(C_NEST))
    check_tojs("toJS: basic fizzbuzz", compile_basic(BASIC_FIZZ))
    check_tojs("toJS: basic full+gosub", compile_basic(BASIC_FULL))

    def check_jscompile(name, lang, src, pywords):
        """In-browser JS compiler must produce byte-identical bytecode to Python."""
        nonlocal passed, failed
        jw, err = js_compile(src, lang)
        ok = (jw == pywords)
        detail = "" if ok else (f" ERROR {err}" if err else
                                f"\n    py={[f'{w:08x}' for w in pywords]}\n    js={[f'{w:08x}' for w in (jw or [])]}")
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} {len(pywords)} words py==js{detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("In-browser compiler (picoc.js bytecode == Python, byte-for-byte):")
    check_jscompile("jsc: c loop-sum", "c", C_LOOP, lower_to_bytecode_safe(compile_c(C_LOOP)))
    check_jscompile("jsc: c if+while", "c", C_IF, lower_to_bytecode_safe(compile_c(C_IF)))
    check_jscompile("jsc: c nested-for", "c", C_NEST, lower_to_bytecode_safe(compile_c(C_NEST)))
    check_jscompile("jsc: basic full", "basic", BASIC_FULL, lower_to_bytecode_safe(compile_basic(BASIC_FULL)))
    check_jscompile("jsc: basic fizzbuzz", "basic", BASIC_FIZZ, lower_to_bytecode_safe(compile_basic(BASIC_FIZZ)))
    check_jscompile("jsc: basic nested", "basic", BASIC_NESTED, lower_to_bytecode_safe(compile_basic(BASIC_NESTED)))
    check_jscompile("jsc: basic do-loop", "basic", BASIC_DOLOOP_POST, lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_POST)))
    check_jscompile("jsc: basic do-nested", "basic", BASIC_DOLOOP_NESTED, lower_to_bytecode_safe(compile_basic(BASIC_DOLOOP_NESTED)))
    check_jscompile("jsc: basic break-for", "basic", BASIC_BREAK_FOR, lower_to_bytecode_safe(compile_basic(BASIC_BREAK_FOR)))
    check_jscompile("jsc: basic skip-switch", "basic", BASIC_SKIP_SWITCH, lower_to_bytecode_safe(compile_basic(BASIC_SKIP_SWITCH)))
    check_jscompile("jsc: c operators", "c", C_OPS, lower_to_bytecode_safe(compile_c(C_OPS)))
    check_jscompile("jsc: basic operators", "basic", BASIC_OPS, lower_to_bytecode_safe(compile_basic(BASIC_OPS)))
    check_jscompile("jsc: span slice", "basic", SPAN_SLICE, lower_to_bytecode_safe(compile_basic(SPAN_SLICE)))
    check_jscompile("jsc: storage basic", "basic", STORAGE_CRUD_BASIC, lower_to_bytecode_safe(compile_basic(STORAGE_CRUD_BASIC)))
    check_jscompile("jsc: storage c", "c", STORAGE_CRUD_C, lower_to_bytecode_safe(compile_c(STORAGE_CRUD_C)))
    check_jscompile("jsc: python ctrl", "python", PY_CTRL, lower_to_bytecode_safe(compile_python(PY_CTRL)))
    check_jscompile("jsc: english ctrl", "english", EN_CTRL, lower_to_bytecode_safe(compile_english(EN_CTRL)))
    check_jscompile("jsc: python span", "python", PY_SPAN, lower_to_bytecode_safe(compile_python(PY_SPAN)))
    check_jscompile("jsc: english span", "english", EN_SPAN, lower_to_bytecode_safe(compile_english(EN_SPAN)))

    print(f"\n{passed} passed, {failed} failed (parity + semantics)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
