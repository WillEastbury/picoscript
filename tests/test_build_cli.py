#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_build_cli.py -- coverage for picoscript_build.py CLI commands."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_build import cmd_run, cmd_emit, cmd_stats, detect_lang, to_bytecode  # noqa: E402


class Args:
    """Fake argparse namespace."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _write_temp(src, ext=".pc"):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, dir=ROOT, encoding="utf-8")
    f.write(src)
    f.close()
    return f.name


# ── cmd_run ──────────────────────────────────────────────────────────────────

def test_cmd_run_c(capsys):
    """cmd_run executes a C program and prints output."""
    path = _write_temp("print(42);")
    try:
        cmd_run(Args(file=path, lang=None, max_steps=10000, print=True, regs=False, no_opt=False))
        out = capsys.readouterr().out
        assert "42" in out
    finally:
        os.unlink(path)


def test_cmd_run_basic(capsys):
    """cmd_run executes a BASIC program."""
    path = _write_temp("PRINT 7", ext=".pbas")
    try:
        cmd_run(Args(file=path, lang=None, max_steps=10000, print=True, regs=True, no_opt=False))
        out = capsys.readouterr().out
        assert "7" in out
    finally:
        os.unlink(path)


def test_cmd_run_python(capsys):
    """cmd_run executes a Python program."""
    path = _write_temp("print(99)", ext=".ppy")
    try:
        cmd_run(Args(file=path, lang=None, max_steps=10000, print=True, regs=False, no_opt=False))
        out = capsys.readouterr().out
        assert "99" in out
    finally:
        os.unlink(path)


# ── cmd_emit ─────────────────────────────────────────────────────────────────

def test_cmd_emit_bytecode_hex(capsys):
    """cmd_emit emits bytecode in hex format."""
    path = _write_temp("print(1);")
    try:
        cmd_emit(Args(file=path, lang=None, as_="bytecode", hex=True, no_opt=False,
                      funcname=None, with_main=False, o=None))
        out = capsys.readouterr().out
        # Hex output: 8 chars per line
        assert all(len(l) == 8 for l in out.strip().split("\n") if l)
    finally:
        os.unlink(path)


def test_cmd_emit_bytecode_disasm(capsys):
    """cmd_emit emits disassembly."""
    path = _write_temp("print(1);")
    try:
        cmd_emit(Args(file=path, lang=None, as_="bytecode", hex=False, no_opt=False,
                      funcname=None, with_main=False, o=None))
        out = capsys.readouterr().out
        assert len(out.strip()) > 0
    finally:
        os.unlink(path)


def test_cmd_emit_c(capsys):
    """cmd_emit emits C source."""
    path = _write_temp("print(42);")
    try:
        cmd_emit(Args(file=path, lang=None, as_="c", hex=False, no_opt=False,
                      funcname="test_fn", with_main=True, o=None))
        out = capsys.readouterr().out
        assert "test_fn" in out
    finally:
        os.unlink(path)


def test_cmd_emit_js(capsys):
    """cmd_emit emits JavaScript source."""
    path = _write_temp("print(42);")
    try:
        cmd_emit(Args(file=path, lang=None, as_="js", hex=False, no_opt=False,
                      funcname="test_mod", with_main=False, o=None))
        out = capsys.readouterr().out
        assert "test_mod" in out
    finally:
        os.unlink(path)


def test_cmd_emit_il(capsys):
    """cmd_emit emits IL text."""
    path = _write_temp("print(42);")
    try:
        cmd_emit(Args(file=path, lang=None, as_="il", hex=False, no_opt=False,
                      funcname=None, with_main=False, o=None))
        out = capsys.readouterr().out
        assert len(out.strip()) > 0
    finally:
        os.unlink(path)


def test_cmd_emit_to_file():
    """cmd_emit writes to file when -o given."""
    path = _write_temp("print(1);")
    out_path = path + ".hex"
    try:
        cmd_emit(Args(file=path, lang=None, as_="bytecode", hex=True, no_opt=False,
                      funcname=None, with_main=False, o=out_path))
        assert os.path.exists(out_path)
        content = open(out_path).read()
        assert len(content.strip()) > 0
    finally:
        os.unlink(path)
        if os.path.exists(out_path):
            os.unlink(out_path)


# ── cmd_stats ────────────────────────────────────────────────────────────────

def test_cmd_stats(capsys):
    """cmd_stats prints metrics."""
    path = _write_temp("print(42);")
    try:
        cmd_stats(Args(file=path, lang=None, backend="auto", run=True, no_opt=False))
        out = capsys.readouterr().out
        assert "cycles" in out.lower() or "instr" in out.lower()
    finally:
        os.unlink(path)


# ── to_bytecode v1 ───────────────────────────────────────────────────────────

def test_to_bytecode_v1():
    """to_bytecode with lang='v1' uses the legacy compiler."""
    words = to_bytecode("Memory.Set(100, 42);\nNet.Close();", "v1")
    assert len(words) == 2


# ── detect_lang edge cases ───────────────────────────────────────────────────

def test_detect_lang_pico():
    """'.pico' extension returns 'v1'."""
    assert detect_lang("test.pico", None) == "v1"


def test_detect_lang_forced_overrides():
    """Forced language overrides extension."""
    assert detect_lang("test.pbas", "c") == "c"
