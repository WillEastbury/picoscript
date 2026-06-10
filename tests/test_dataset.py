#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_dataset.py -- the synthetic dataset is self-verifying, but prove it:
independently re-compile + run every emitted record and confirm its claimed output,
and that the four dialects of each program agree (Python/BASIC/English byte-identical).

Run: python tests/test_dataset.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_dataset as G   # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'' if cond else '  ' + detail}")
    passed += (1 if cond else 0)
    failed += (0 if cond else 1)


def out_of(lang, code):
    return G.run_output(G.lower_to_bytecode_safe(G.COMPILE[lang](code)))


records, attempts = G.generate(count=30, seed=7)
n2c = [r for r in records if r["task"] == "nl2code"]
tr = [r for r in records if r["task"] == "translate"]
rn = [r for r in records if r["task"] == "run"]

check("produced programs", len(n2c) // 4 >= 25, f"only {len(n2c)//4}")
check("every nl2code has all four dialects",
      all(len([r for r in n2c if r["instruction"] == ins]) >= 4
          for ins in {r["instruction"] for r in n2c}))

# 1) every nl2code record's code actually compiles, runs, and yields its claimed output.
bad = []
for r in n2c:
    try:
        if out_of(r["dialect"], r["code"]) != r["output"]:
            bad.append((r["dialect"], r["instruction"]))
    except Exception as e:
        bad.append((r["dialect"], str(e)[:40]))
check("every nl2code code recompiles+runs to its claimed output", not bad, str(bad[:3]))

# 2) translate pairs: from_code and to_code both run to the same claimed output.
tbad = []
for r in tr:
    try:
        if out_of(r["from_dialect"], r["from_code"]) != r["output"] or \
           out_of(r["to_dialect"], r["to_code"]) != r["output"]:
            tbad.append(r["construct"])
    except Exception as e:
        tbad.append(str(e)[:40])
check("translate pairs both run to the shared output", not tbad, str(tbad[:3]))

# 3) Python/BASIC/English of the same program are byte-identical (shared AST).
prog_by_instr = {}
for r in n2c:
    prog_by_instr.setdefault(r["instruction"], {})[r["dialect"]] = r["code"]
idbad = []
for ins, ds in prog_by_instr.items():
    wb = G.lower_to_bytecode_safe(G.compile_basic(ds["basic"]))
    wp = G.lower_to_bytecode_safe(G.compile_python(ds["python"]))
    we = G.lower_to_bytecode_safe(G.compile_english(ds["english"]))
    if not (wb == wp == we):
        idbad.append(ins)
check("python == basic == english byte-identical per program", not idbad, str(idbad[:2]))

# 4) run records carry a list output.
check("run records well-formed", all(isinstance(r["output"], list) for r in rn))

print(f"\n{passed} passed, {failed} failed  ({len(records)} records over {len(n2c)//4} programs)")
sys.exit(1 if failed else 0)
