#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""psunit.py -- PSUnit: a PicoScript-authored unit / smoke test harness.

A PSUnit *test* is an ordinary PicoScript program that makes assertions through
the Assert.* host namespace:

    Assert.Eq(actual, expected)   -- records a pass iff actual == expected
    Assert.True(cond)             -- records a pass iff cond != 0

The harness compiles each test with the normal toolchain and runs it on the
reference Python VM, then reads the host assertion counters (Assert.Count() /
Assert.Failed()) to decide pass/fail -- so a test body contains *only*
assertions, with no reporting boilerplate. Tests may first seed the provider
seams (Storage.* cards, Gpio.* pins, Device.*/Stream.* rings) and assert on
them, so PSUnit doubles as a smoke test for the browser/sim runtime.

The frontend is chosen by extension (.pc = C, .pbas/.bas = BASIC, .ppy = Python,
.eng/.english = English), so a test can be written in any of the four languages.
With --parity each test is also run on the JS VM (vm/picovm_run.js) and the
assertion counters *and* program output must be byte-identical, proving
Python VM == JS VM for the code under test.

Usage:
    python psunit.py                        # run tests/psunit/*, on the Python VM
    python psunit.py --parity               # also require Python VM == JS VM
    python psunit.py path/to/test.pc ...    # run specific files
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from picoscript_build import detect_lang, to_bytecode  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
PSUNIT_DIR = os.path.join(ROOT, "tests", "psunit")
TEST_EXTS = ("pc", "pbas", "bas", "ppy", "eng", "english")


class Result:
    def __init__(self, name, total, failed, out, error=None):
        self.name = name
        self.total = total
        self.failed = failed
        self.out = out
        self.error = error

    @property
    def ok(self):
        return self.error is None and self.total > 0 and self.failed == 0

    @property
    def status(self):
        if self.error:
            return "ERROR " + self.error
        if self.total == 0:
            return "ERROR no assertions"
        if self.failed:
            return f"FAIL {self.failed}/{self.total} asserts"
        return f"ok   {self.total} asserts"


def run_py(words):
    """Run bytecode on the reference Python VM; return (total, failed, out)."""
    host = HostApi()
    vm = PicoVM(host=host)
    vm.load(words)
    vm.run()
    return host.assert_total, host.assert_failed, b"".join(vm.output)


def run_js(words):
    """Run bytecode on the JS VM via vm/picovm_run.js; return (total, failed, out)."""
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "node picovm_run.js failed")
    total = failed = 0
    out = b""
    for line in r.stdout.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "ASSERT":
            total, failed = int(p[1]), int(p[2])
        elif p[0] == "OUT":
            out = bytes(int(x, 16) for x in p[1:])
    return total, failed, out


def compile_file(path):
    source = open(path, encoding="utf-8").read()
    return to_bytecode(source, detect_lang(path, None))


def run_one(path, parity=False):
    name = os.path.relpath(path, ROOT)
    try:
        words = compile_file(path)
        pt, pf, po = run_py(words)
        if parity:
            jt, jf, jo = run_js(words)
            if (pt, pf, po) != (jt, jf, jo):
                detail = (f"Python VM != JS VM "
                          f"(py={pt}/{pf} js={jt}/{jf}, "
                          f"output {'differs' if po != jo else 'same'})")
                return Result(name, pt, pf, po, error=detail)
        return Result(name, pt, pf, po)
    except Exception as e:  # noqa: BLE001 -- report any compile/run failure as an error
        return Result(name, 0, 0, b"", error=f"{type(e).__name__}: {e}")


def discover(paths):
    if paths:
        return list(paths)
    files = []
    for ext in TEST_EXTS:
        files += glob.glob(os.path.join(PSUNIT_DIR, f"*.{ext}"))
    return sorted(files)


def run_suite(paths=None, parity=False):
    return [run_one(p, parity) for p in discover(paths)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="PSUnit -- PicoScript test harness")
    ap.add_argument("files", nargs="*", help="test files (default: tests/psunit/*)")
    ap.add_argument("--parity", action="store_true",
                    help="also require Python VM == JS VM (needs node)")
    args = ap.parse_args(argv)

    results = run_suite(args.files, args.parity)
    if not results:
        print("psunit: no tests found")
        return 1

    width = max(len(r.name) for r in results)
    failed = 0
    for r in results:
        if not r.ok:
            failed += 1
        print(f"  {r.name.ljust(width)}  {r.status}")

    asserts = sum(r.total for r in results)
    suffix = " [parity]" if args.parity else ""
    print(f"psunit: {len(results) - failed}/{len(results)} files passed, "
          f"{asserts} assertions{suffix}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
