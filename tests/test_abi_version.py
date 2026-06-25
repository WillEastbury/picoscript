#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Module-container ABI versioning (INV-23).

A persisted/shipped program is wrapped in a versioned container
[MAGIC, ABI_VERSION, HOOK_TABLE_VERSION, count, ...words]; loading refuses any
magic / ABI-version / hook-table-version / length mismatch. The hook-table version is a
content hash (FNV-1a/32) that bumps when the host hook table changes, and is computed
IDENTICALLY by the Python reference (pico_module.py) and the JS runtime (vm/picovm.js) --
so a module packed by one runtime loads on the other only when their ABIs agree.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pico_module as pm  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")


def _node(expr):
    r = subprocess.run(["node", "-e", expr], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _c_run_module(container):
    """Feed a container to the C harness in module mode; return (module_line, out_bytes)."""
    inp = f"{len(container)}\n" + "\n".join(f"{w & 0xFFFFFFFF:08x}" for w in container) + "\n"
    env = dict(os.environ, PICOVM_MODULE="1")
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True, env=env).stdout
    mod_line, out_bytes = None, b""
    for line in out.splitlines():
        if line.startswith("MODULE"):
            mod_line = int(line.split()[1])
        if line.startswith("OUT"):
            out_bytes = bytes(int(x, 16) for x in line.split()[1:])
    return mod_line, out_bytes


def main():
    words = [0x90000000, 0x4110002A, 0x02007072]

    # Python round-trip.
    container = pm.pack_module(words)
    assert container[0] == pm.MODULE_MAGIC, "magic word"
    assert container[1] == pm.MODULE_ABI_VERSION, "abi word"
    assert container[3] == len(words), "count word"
    assert pm.load_module(container) == words, "round-trip must return the original words"

    # Python refuses each kind of mismatch.
    htv = pm.hook_table_version()
    for bad, label in [
        ([0xDEADBEEF, 1, htv, 0], "magic"),
        ([pm.MODULE_MAGIC, 99, htv, 0], "abi version"),
        ([pm.MODULE_MAGIC, 1, 0x12345678, 0], "hook-table version"),
        ([pm.MODULE_MAGIC, 1, htv, 5, 0x90000000], "word count"),
        ([pm.MODULE_MAGIC, 1], "truncated header"),
    ]:
        try:
            pm.load_module(bad)
            raise AssertionError(f"load_module must refuse {label} mismatch")
        except pm.ModuleAbiError:
            pass

    # Python and JS compute the SAME hook-table version (cross-runtime ABI agreement).
    js_htv = int(_node("const P=require('./vm/picovm.js'); process.stdout.write(String(P.hookTableVersion()>>>0));"))
    assert js_htv == htv, f"hook_table_version diverged: py=0x{htv:08X} js=0x{js_htv:08X}"

    # Cross-runtime interop: a Python-packed module loads in JS and yields the same words.
    cont_csv = ",".join(str(w) for w in container)
    js_words = _node(
        "const P=require('./vm/picovm.js');"
        f"const c=[{cont_csv}];"
        "const w=P.loadModule(c);"
        "process.stdout.write(w.join(','));"
    )
    assert [int(x) for x in js_words.split(",")] == [w & 0xFFFFFFFF for w in words], \
        "Python-packed module must load identically in JS"

    # And JS refuses a tampered hook-table version too.
    bad_load = subprocess.run(
        ["node", "-e",
         "const P=require('./vm/picovm.js');"
         f"try{{P.loadModule([{pm.MODULE_MAGIC},1,305419896,0]);process.stdout.write('NOT_REFUSED');}}"
         "catch(e){process.stdout.write(/ModuleAbiError/.test(e.message)?'REFUSED':'WRONG_ERR');}"],
        cwd=ROOT, capture_output=True, text=True)
    assert bad_load.stdout.strip() == "REFUSED", f"JS must refuse bad hook-table version: {bad_load.stdout!r}"

    # The C runtime loads a Python-packed module (PV_HOOK_TABLE_VERSION generated into
    # pico_hooks.h matches) and rejects a tampered hook-table version.
    _build_c_vm()
    runnable = lower_to_bytecode_safe(compile_c("int x = 65; Io.WriteByte(x);"))   # emits 'A'
    good = pm.pack_module(runnable)
    mod_line, out_bytes = _c_run_module(good)
    assert mod_line == 0, f"C must accept a valid module (got MODULE {mod_line})"
    assert out_bytes == b"A", f"C must run the loaded module (got {out_bytes!r})"
    tampered = list(good); tampered[2] = 0x12345678            # corrupt the hook-table version
    mod_line2, _ = _c_run_module(tampered)
    assert mod_line2 == -4, f"C must reject a bad hook-table version with -4 (got MODULE {mod_line2})"

    print(f"PASS abi version: module container packs/loads + refuses magic/abi/hook-table/length "
          f"mismatch; Python, JS and C agree on hook_table_version=0x{htv:08X}, exchange modules, "
          f"and C runs a packed module + rejects a tampered one (INV-23)")



def test_main():
    main()

if __name__ == "__main__":
    main()
