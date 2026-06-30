#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_coverage_push5.py -- fifth wave: build CLI cmd_native, decompile_python deep,
and remaining frontend branches.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_lang import Compiler, decompile_python, decompile_basic  # noqa: E402
from picoscript_build import cmd_native, cmd_run, main as build_main  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_cobol import compile_cobol  # noqa: E402
from picoscript_report import compile_report  # noqa: E402
from picoscript_functional import compile_functional  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


class Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _write_temp(src, ext=".pc"):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, dir=ROOT, encoding="utf-8")
    f.write(src)
    f.close()
    return f.name


def run(il):
    words = lower_to_bytecode_safe(il)
    vm = PicoVM().run(words)
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


def out_bytes(il):
    words = lower_to_bytecode_safe(il)
    return b"".join(PicoVM().run(words).output)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_build.py — cmd_native (with zig cc)
# ══════════════════════════════════════════════════════════════════════════════

def test_cmd_native_host(capsys):
    """cmd_native builds a host executable."""
    path = _write_temp("print(42);")
    out_exe = path.replace(".pc", ".exe")
    try:
        cmd_native(Args(file=path, lang=None, profile="host", target=None,
                        mcpu=None, opt="2", o=out_exe, no_opt=False))
        out = capsys.readouterr().out
        assert "wrote" in out
        assert os.path.exists(out_exe)
    finally:
        os.unlink(path)
        for f in [out_exe, out_exe + ".c"]:
            if os.path.exists(f):
                os.unlink(f)


def test_cmd_native_default_output(capsys):
    """cmd_native with no -o uses default name."""
    path = _write_temp("print(1);")
    default_out = path.replace(".pc", ".exe")
    try:
        cmd_native(Args(file=path, lang=None, profile="host", target=None,
                        mcpu=None, opt="0", o=None, no_opt=False))
        out = capsys.readouterr().out
        assert "wrote" in out
    finally:
        os.unlink(path)
        for f in [default_out, default_out + ".c"]:
            if os.path.exists(f):
                os.unlink(f)


def test_build_main_run(capsys):
    """build_main CLI dispatches 'run' subcommand."""
    path = _write_temp("print(99);")
    try:
        build_main(["run", path, "--print"])
        out = capsys.readouterr().out
        assert "99" in out
    finally:
        os.unlink(path)


def test_build_main_emit_hex(capsys):
    """build_main CLI dispatches 'emit --as bytecode --hex'."""
    path = _write_temp("print(1);")
    try:
        build_main(["emit", path, "--as", "bytecode", "--hex"])
        out = capsys.readouterr().out
        assert all(len(l) == 8 for l in out.strip().split("\n") if l)
    finally:
        os.unlink(path)


def test_build_main_emit_c(capsys):
    """build_main CLI dispatches 'emit --as c'."""
    path = _write_temp("print(42);")
    try:
        build_main(["emit", path, "--as", "c", "--func", "myfunc", "--with-main"])
        out = capsys.readouterr().out
        assert "myfunc" in out
    finally:
        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# picoscript_lang.py — decompile_python remaining branches
# ══════════════════════════════════════════════════════════════════════════════

def test_decompile_python_net_status():
    c = Compiler()
    words = c.compile("NET STATUS, 404\nNET BODY\nNET CLOSE")
    out = decompile_python(words)
    assert "404" in out and "net" in out.lower()


def test_decompile_python_net_type():
    c = Compiler()
    words = c.compile('NET TYPE, "text/html"')
    out = decompile_python(words)
    assert "net" in out.lower()


def test_decompile_python_thread():
    c = Compiler()
    words = c.compile("THREAD SKIP\nTHREAD WAIT\nTHREAD RAISE, 3")
    out = decompile_python(words)
    assert "thread" in out.lower() or "skip" in out.lower()


def test_decompile_python_math_ops():
    c = Compiler()
    words = c.compile("MATH ADD, R0, R1, 5\nMATH SUB, R2, R3, R4\nMATH MUL, R5, R6, 2\nMATH DIV, R7, R8, 3\nMATH INC, R9")
    out = decompile_python(words)
    assert len(out.strip().split("\n")) >= 5


def test_decompile_python_flow():
    c = Compiler()
    words = c.compile("10 FLOW RETURN\n20 FLOW JUMP, 10\n30 FLOW CALL, 10\n40 FLOW BRANCH, NE, R0, R1, 10")
    out = decompile_python(words)
    assert "flow" in out.lower() or "jump" in out.lower() or "ret" in out.lower()


def test_decompile_python_dsp_all():
    c = Compiler()
    words = c.compile("DSP MATMUL, R0, R1, 8\nDSP SOFTMAX, R2, R3\nDSP RELU, R4, R5\nDSP GELU, R6, R7\nDSP NORM, R8, R9")
    out = decompile_python(words)
    assert "dsp" in out.lower() or "matmul" in out.lower()


def test_decompile_python_storage_load():
    c = Compiler()
    words = c.compile("STORAGE LOAD, 1, 2, 3, R0\nSTORAGE SAVE, 0, 0, 0, R1\nSTORAGE PIPE, 0, 1, 0, R2")
    out = decompile_python(words)
    assert "storage" in out.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Remaining frontend expression paths
# ══════════════════════════════════════════════════════════════════════════════

def test_functional_le_comparison():
    src = "let x = if 3 <= 5 then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_functional_gt_comparison():
    src = "let x = if 10 > 5 then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_functional_lt_comparison():
    src = "let x = if 3 < 5 then 1 else 0\nprintfn x"
    assert run(compile_functional(src)) == [1]


def test_cobol_greater_than_keyword():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 10.
PROCEDURE DIVISION.
    IF X IS GREATER THAN 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [1]


def test_cobol_less_than_keyword():
    src = """\
IDENTIFICATION DIVISION.
PROGRAM-ID. TEST.
DATA DIVISION.
01 X PIC 9(4) VALUE 3.
PROCEDURE DIVISION.
    IF X IS LESS THAN 5
        DISPLAY 1
    END-IF.
    STOP RUN.
"""
    assert run(compile_cobol(src)) == [1]


def test_report_and_or():
    src = "DATA: x TYPE i VALUE 5.\nIF x GT 3 AND x LT 10.\n  WRITE 1.\nENDIF."
    assert run(compile_report(src)) == [1]


def test_english_and_condition():
    src = "set x to 5\nif x is greater than 3:\n    if x is less than 10:\n        display 1"
    assert run(compile_english(src)) == [1]


def test_python_nested_match():
    src = """\
x = 3
match x:
    case 1:
        print(10)
    case 2:
        print(20)
    case 3:
        print(30)
    case 4:
        print(40)
    case _:
        print(99)
"""
    assert run(compile_python(src)) == [30]


def test_c_complex_ternary():
    src = "int a = 5; int b = a > 3 ? (a > 4 ? 100 : 50) : 0; print(b);"
    assert run(compile_c(src)) == [100]
