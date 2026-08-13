#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-language intrinsic smoke matrix.

This starts closing the gap called out in gaps.md: we did not have a single
mechanical suite that drives the same intrinsic behaviours through every major
language surface.

Current slice:
- frontends: C, BASIC, Python, English, COBOL, Report, Workflow
- runtimes: Python VM, JS VM, C VM, native C
- intrinsic families: String, Number, Bits, Span, Crypto
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_vm import HostApi, PicoVM  # noqa: E402
from picoscript_workflow import compile_workflow  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD_DIR = os.path.join(ROOT, ".test_build_intrinsic_matrix")
NODE_EXE = shutil.which("node") or shutil.which("node.exe") or r"C:\Program Files\nodejs\node.exe"
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def _find_c_compiler():
    """Return a usable hosted C compiler command prefix."""
    zig = shutil.which("zig")
    if zig:
        return [zig, "cc"]
    cl = shutil.which("cl")
    if cl:
        return [cl]
    candidates = [
        r"C:\Program Files\Microsoft Visual Studio\18\Enterprise\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64\cl.exe",
        r"C:\Program Files\Microsoft Visual Studio\17\BuildTools\VC\Tools\MSVC\14.3\bin\Hostx64\x64\cl.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return [candidate]
    return None

FRONTENDS = {
    "c": compile_c,
    "basic": compile_basic,
    "python": compile_python,
    "english": compile_english,
    "cobol": compile_cobol,
    "report": compile_report,
    "workflow": compile_workflow,
}


def _s32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def _decode_output_chunks(chunks):
    return [_s32(int.from_bytes(chunk, "big")) for chunk in chunks]


def _decode_hex_bytes(raw):
    assert len(raw) % 4 == 0, f"unexpected output byte count: {len(raw)}"
    out = []
    for i in range(0, len(raw), 4):
        out.append(_s32((raw[i] << 24) | (raw[i + 1] << 16) | (raw[i + 2] << 8) | raw[i + 3]))
    return out


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return _decode_output_chunks(vm.output)


def _run_js(words):
    assert NODE_EXE and os.path.exists(NODE_EXE), "Node.js is unavailable for JS VM parity"
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    runner = os.path.join(VM_DIR, "picovm_run.js")
    r = subprocess.run([NODE_EXE, runner], input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    raw = []
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            raw.extend(int(x, 16) for x in p[1:])
    return _decode_hex_bytes(raw)


def _run_c_compile(args):
    if os.path.basename(args[0]).lower().startswith('cl'):
        dev = r"C:\Program Files\Microsoft Visual Studio\18\Enterprise\Common7\Tools\VsDevCmd.bat"
        quoted = ' '.join(('"'+a+'"') if ' ' in a else a for a in args)
        return subprocess.run(['cmd', '/d', '/c', f'call "{dev}" -arch=x64 >nul && {quoted}'], capture_output=True, text=True)
    return subprocess.run(args, capture_output=True, text=True)

def _build_c_vm():
    if os.path.exists(VM_EXE):
        return
    compiler = _find_c_compiler()
    assert compiler, 'No hosted C compiler is available'
    if os.path.basename(compiler[0]).lower().startswith('cl'):
        cmd = compiler + ['/nologo', '/O2', '/std:c11', os.path.join(VM_DIR, 'picovm.c'), os.path.join(VM_DIR, 'picovm_run.c'), f'/Fe:{VM_EXE}']
    else:
        cmd = compiler + ['-std=c99', '-O2', os.path.join(VM_DIR, 'picovm.c'), os.path.join(VM_DIR, 'picovm_run.c'), '-o', VM_EXE]
    r = _run_c_compile(cmd)
    if r.returncode != 0:
        pytest.skip('hosted C compiler unavailable: ' + (r.stderr or r.stdout))


def _run_c_vm(words):
    _build_c_vm()
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    raw = []
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            raw.extend(int(x, 16) for x in p[1:])
    return _decode_hex_bytes(raw)


def _run_native_c(il, slot):
    os.makedirs(BUILD_DIR, exist_ok=True)
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD_DIR, f"{slot}.c")
    exe = os.path.join(BUILD_DIR, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = _run_c_compile(cmd)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    raw = []
    for line in out.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            raw.extend(int(x, 16) for x in p[1:])
    return _decode_hex_bytes(raw)


def _compile(lang: str, source):
    return FRONTENDS[lang](source)


CASES = [
    {
        "name": "string_length",
        "expected": [5],
        "sources": {
            "c": 'int r = String.Length("hello"); print(r);',
            "basic": 'DIM R = String.Length("hello")\nPRINT R\n',
            "python": 'r = String.Length("hello")\nprint(r)\n',
            "english": 'Set r to String.Length("hello").\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = String.Length("hello").\n    DISPLAY R.\n    STOP RUN.\n',
            "report": "DATA: r TYPE i VALUE 0.\nr = String.Length('hello').\nWRITE r.\n",
            "workflow": [{"type": "SET", "name": "r", "expr": 'String.Length("hello")'}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "number_abs",
        "expected": [7],
        "sources": {
            "c": 'int r = Number.Abs(0 - 7); print(r);',
            "basic": 'DIM R = Number.Abs(0 - 7)\nPRINT R\n',
            "python": 'r = Number.Abs(0 - 7)\nprint(r)\n',
            "english": 'Set r to Number.Abs(0 minus 7).\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = Number.Abs(0 - 7).\n    DISPLAY R.\n    STOP RUN.\n',
            "report": 'DATA: r TYPE i VALUE 0.\nr = Number.Abs(0 - 7).\nWRITE r.\n',
            "workflow": [{"type": "SET", "name": "r", "expr": "Number.Abs(0 - 7)"}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "bits_and",
        "expected": [8],
        "sources": {
            "c": 'int r = Bits.And(12, 10); print(r);',
            "basic": 'DIM R = Bits.And(12, 10)\nPRINT R\n',
            "python": 'r = Bits.And(12, 10)\nprint(r)\n',
            "english": 'Set r to Bits.And(12, 10).\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = Bits.And(12, 10).\n    DISPLAY R.\n    STOP RUN.\n',
            "report": 'DATA: r TYPE i VALUE 0.\nr = Bits.And(12, 10).\nWRITE r.\n',
            "workflow": [{"type": "SET", "name": "r", "expr": "Bits.And(12, 10)"}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "number_parse",
        "expected": [123],
        "sources": {
            "c": 'int r = Number.Parse("123"); print(r);',
            "basic": 'DIM R = Number.Parse("123")\nPRINT R\n',
            "python": 'r = Number.Parse("123")\nprint(r)\n',
            "english": 'Set r to Number.Parse("123").\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = Number.Parse("123").\n    DISPLAY R.\n    STOP RUN.\n',
            "report": "DATA: r TYPE i VALUE 0.\nr = Number.Parse('123').\nWRITE r.\n",
            "workflow": [{"type": "SET", "name": "r", "expr": 'Number.Parse("123")'}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "span_len",
        "expected": [5],
        "sources": {
            "c": 'int r = Span.Len("abcde"); print(r);',
            "basic": 'DIM R = Span.Len("abcde")\nPRINT R\n',
            "python": 'r = Span.Len("abcde")\nprint(r)\n',
            "english": 'Set r to Span.Len("abcde").\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = Span.Len("abcde").\n    DISPLAY R.\n    STOP RUN.\n',
            "report": "DATA: r TYPE i VALUE 0.\nr = Span.Len('abcde').\nWRITE r.\n",
            "workflow": [{"type": "SET", "name": "r", "expr": 'Span.Len("abcde")'}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "number_tohex_len",
        "expected": [2],
        "sources": {
            "c": 'int r = String.Length(Number.ToHex(255)); print(r);',
            "basic": 'DIM R = String.Length(Number.ToHex(255))\nPRINT R\n',
            "python": 'r = String.Length(Number.ToHex(255))\nprint(r)\n',
            "english": 'Set r to String.Length(Number.ToHex(255)).\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = String.Length(Number.ToHex(255)).\n    DISPLAY R.\n    STOP RUN.\n',
            "report": "DATA: r TYPE i VALUE 0.\nr = String.Length(Number.ToHex(255)).\nWRITE r.\n",
            "workflow": [{"type": "SET", "name": "r", "expr": 'String.Length(Number.ToHex(255))'}, {"type": "LOG", "message": "r"}],
        },
    },
    {
        "name": "crypto_sha256_len",
        "expected": [32],
        "sources": {
            "c": 'int r = Span.Len(Crypto.Sha256("abc")); print(r);',
            "basic": 'DIM R = Span.Len(Crypto.Sha256("abc"))\nPRINT R\n',
            "python": 'r = Span.Len(Crypto.Sha256("abc"))\nprint(r)\n',
            "english": 'Set r to Span.Len(Crypto.Sha256("abc")).\nPrint r.\n',
            "cobol": 'IDENTIFICATION DIVISION.\nPROGRAM-ID. TEST.\nDATA DIVISION.\n01 R PIC 9(4) VALUE 0.\nPROCEDURE DIVISION.\n    COMPUTE R = Span.Len(Crypto.Sha256("abc")).\n    DISPLAY R.\n    STOP RUN.\n',
            "report": "DATA: r TYPE i VALUE 0.\nr = Span.Len(Crypto.Sha256('abc')).\nWRITE r.\n",
            "workflow": [{"type": "SET", "name": "r", "expr": 'Span.Len(Crypto.Sha256("abc"))'}, {"type": "LOG", "message": "r"}],
        },
    },
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
@pytest.mark.parametrize("lang", tuple(FRONTENDS.keys()))
def test_intrinsic_smoke_matrix_py_js(case, lang):
    il = _compile(lang, case["sources"][lang])
    words = lower_to_bytecode_safe(il)
    assert _run_py(words) == case["expected"]
    assert _run_js(words) == case["expected"]

@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
@pytest.mark.parametrize("lang", tuple(FRONTENDS.keys()))
def test_intrinsic_smoke_matrix_all_frontends(case, lang):
    il = _compile(lang, case["sources"][lang])
    words = lower_to_bytecode_safe(il)
    py_out = _run_py(words)
    js_out = _run_js(words)
    c_out = _run_c_vm(words)
    native_c_out = _run_native_c(il, f"{lang}_{case['name']}")
    assert py_out == case["expected"], f"{lang} python-vm mismatch for {case['name']}: {py_out!r}"
    assert js_out == case["expected"], f"{lang} js-vm mismatch for {case['name']}: {js_out!r}"
    assert c_out == case["expected"], f"{lang} c-vm mismatch for {case['name']}: {c_out!r}"
    assert native_c_out == case["expected"], f"{lang} native-c mismatch for {case['name']}: {native_c_out!r}"
    assert py_out == js_out == c_out == native_c_out







