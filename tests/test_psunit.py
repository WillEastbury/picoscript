#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PSUnit -- the PicoScript-authored unit / smoke test harness (psunit.py).

These are meta-tests: they prove the harness itself works.

  (1) Every test under tests/psunit/* passes on the reference Python VM *and*
      is byte-identical on the JS VM (--parity), across all four frontends.
  (2) The Assert.* counters actually detect a failing assertion (negative path).
  (3) Assert.Eq / Assert.True run byte-identically on the Python VM and JS VM.
  (4) The BASIC `ASSERT <cond>` keyword lowers to the same bytecode as the
      canonical Assert.True(cond) spelling, and the Python frontend matches the
      JS frontend (vm/picoc.js) byte-for-byte (guards the ASSERT mirror).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psunit  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def _node_ok():
    try:
        subprocess.run(["node", "--version"], capture_output=True)
        return True
    except FileNotFoundError:
        return False


HAVE_NODE = _node_ok()


def js_compile(src, lang="basic"):
    r = subprocess.run(["node", os.path.join(VM_DIR, "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def test_all_psunit_files_pass():
    # Every shipped PSUnit test passes on the Python VM (and JS VM when present).
    results = psunit.run_suite(parity=HAVE_NODE)
    assert results, "no PSUnit tests discovered under tests/psunit/"
    bad = [f"{r.name}: {r.status}" for r in results if not r.ok]
    assert not bad, "PSUnit failures: " + "; ".join(bad)
    # All four frontends are represented (.pc/.pbas/.ppy/.eng).
    exts = {os.path.splitext(r.name)[1] for r in results}
    assert {".pc", ".pbas", ".ppy", ".eng"} <= exts, f"missing a frontend: {exts}"


def test_assert_detects_failure():
    # A failing Assert.Eq must be counted, so the harness reports the test failed.
    prog = "Assert.Eq(1, 1);\nAssert.Eq(2, 3);\nAssert.True(0);\n"
    host = HostApi(); vm = PicoVM(host=host)
    vm.load(lower_to_bytecode_safe(compile_c(prog))); vm.run()
    assert host.assert_total == 3
    assert host.assert_failed == 2


def test_assert_reset():
    prog = "Assert.Eq(1, 2);\nAssert.Reset();\nAssert.Eq(5, 5);\n"
    host = HostApi(); vm = PicoVM(host=host)
    vm.load(lower_to_bytecode_safe(compile_c(prog))); vm.run()
    assert host.assert_total == 1
    assert host.assert_failed == 0


def test_assert_counters_py_equals_js():
    if not HAVE_NODE:
        return
    prog = ("Assert.Eq(2 + 2, 4);\nAssert.True(1);\n"
            "Assert.Eq(9, 8);\nAssert.True(0);\n")
    words = lower_to_bytecode_safe(compile_c(prog))
    pt, pf, _ = psunit.run_py(words)
    jt, jf, _ = psunit.run_js(words)
    assert (pt, pf) == (4, 2)
    assert (pt, pf) == (jt, jf)


BASIC_ASSERT = "DIM a = 5\nASSERT a = 5\nASSERT a > 0\n"
# In BASIC, ASSERT is a keyword (it shadows a dotted Assert.* call, exactly like
# GPIO shadows Gpio.*), so the canonical Assert.True spelling is written in the
# Python frontend, which shares the BASIC lowerer -> identical bytecode.
PY_CANON = "a = 5\nAssert.True(a == 5)\nAssert.True(a > 0)\n"


def test_basic_assert_keyword_runs():
    host = HostApi(); vm = PicoVM(host=host)
    vm.load(lower_to_bytecode_safe(compile_basic(BASIC_ASSERT))); vm.run()
    assert host.assert_total == 2
    assert host.assert_failed == 0


def test_basic_assert_keyword_equals_canonical():
    # `ASSERT cond` is sugar for Assert.True(cond): identical bytecode to the
    # canonical spelling in the (lowerer-sharing) Python frontend.
    from picoscript_python import compile_python
    assert lower_to_bytecode_safe(compile_basic(BASIC_ASSERT)) == \
           lower_to_bytecode_safe(compile_python(PY_CANON))


def test_basic_assert_keyword_py_equals_js_frontend():
    if not HAVE_NODE:
        return
    assert lower_to_bytecode_safe(compile_basic(BASIC_ASSERT)) == \
           js_compile(BASIC_ASSERT, "basic")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS psunit: harness runs all frontends, detects failures, "
          "Assert.* Python VM == JS VM, BASIC ASSERT byte-identical")


if __name__ == "__main__":
    main()
