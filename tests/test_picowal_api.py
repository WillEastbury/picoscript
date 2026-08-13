#!/usr/bin/env python3
"""PicoWAL C-dialect API operation-trace parity tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import HostApi, PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
API_PATH = os.path.join(ROOT, "host", "picowal", "picowal.pico")


def _source(trace: str) -> str:
    with open(API_PATH, "r", encoding="utf-8") as f:
        return f.read() + "\n" + trace


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _run_js(words):
    if shutil.which("node") is None:
        return None
    encoded = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    result = subprocess.run(
        ["node", os.path.join(VM_DIR, "picovm_run.js")],
        input=encoded,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "OUT":
            return bytes(int(value, 16) for value in fields[1:])
    return b""


def _i32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


def test_picowal_crud_scan_trace_python_js_parity():
    trace = r'''
print(pw_open());
print(pw_put(5, 42, "river bank", 1));
print(pw_put(5, 42, "duplicate", 1));
print(pw_exists(5, 42));
Io.Write(pw_get(5, 42));
Io.WriteByte(124);
print(pw_put(5, 7, "finance bank", 0));
print(pw_scan(5, -1));
print(pw_scan(5, 7));
print(pw_scan(5, 42));
print(pw_delete(5, 99));
print(pw_delete(5, 7));
print(pw_exists(5, 7));
print(pw_sync());
print(pw_recover());
print(pw_close());
'''
    words = lower_to_bytecode_safe(compile_c(_source(trace)))
    py = _run_py(words)
    js = _run_js(words)
    if js is not None:
        assert py == js
    assert py == (
        _i32(0) + _i32(0) + _i32(3) + _i32(1) + b"river bank|" +
        _i32(0) + _i32(7) + _i32(42) + _i32(-1) + _i32(2) +
        _i32(0) + _i32(0) + _i32(0) + _i32(0) + _i32(0)
    )


def test_picowal_pack_isolation_and_invalid_statuses():
    trace = r'''
print(pw_put(1, 3, "a", 0));
print(pw_put(2, 3, "b", 0));
Io.Write(pw_get(1, 3));
Io.Write(pw_get(2, 3));
print(pw_put(1024, 1, "bad", 0));
print(pw_delete(2, 4194304));
'''
    words = lower_to_bytecode_safe(compile_c(_source(trace)))
    py = _run_py(words)
    js = _run_js(words)
    expected = _i32(0) + _i32(0) + b"ab" + _i32(1) + _i32(1)
    assert py == expected
    if js is not None:
        assert js == expected
