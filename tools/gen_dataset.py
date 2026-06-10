#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/gen_dataset.py -- synthetic PicoScript dataset generator.

Emits a corpus of PicoScript programs for fine-tuning a small coding model. Each
program is built from a dialect-agnostic statement model, rendered into all four
surfaces (C / BASIC / Python / English), then **verified** by the real toolchain:

  * every dialect compiles,
  * every dialect runs on PicoVM and produces the SAME output,
  * Python/BASIC/English are byte-for-byte identical (shared AST + lowerer).

Only fully-verified programs are emitted, so the corpus cannot contain an invalid
sample. Records (JSONL):

  nl2code     : {instruction} -> {code} in a given dialect
  translate   : code in dialect A -> equivalent code in dialect B
  run         : {code} -> {output}   (execution-prediction signal)

Usage:
  python tools/gen_dataset.py --count 400 --out data/picoscript.jsonl --seed 1
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c          # noqa: E402
from picoscript_basic import compile_basic        # noqa: E402
from picoscript_python import compile_python       # noqa: E402
from picoscript_english import compile_english      # noqa: E402
from picoscript_il import lower_to_bytecode_safe    # noqa: E402
from picoscript_vm import PicoVM                     # noqa: E402

DIALECTS = ("c", "basic", "python", "english")
COMPILE = {"c": compile_c, "basic": compile_basic,
           "python": compile_python, "english": compile_english}


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def run_output(words):
    vm = PicoVM().run(words)
    return [s32(int.from_bytes(b, "big")) for b in vm.output]


# ── dialect-agnostic model ───────────────────────────────────────────────────
class E:                                  # expression
    def __init__(self, kind, **kw): self.kind = kind; self.__dict__.update(kw)


def num(v): return E("num", v=v)
def var(n): return E("var", n=n)
def bin(op, a, b): return E("bin", op=op, a=a, b=b)


# statements: ("assign", name, expr) ("print", expr) ("inc", name)
# ("if", cond, then, else_) ("while", cond, body) ("for", var, lo, hi, body)

# operator surface per dialect (BASIC differs: MOD, =, AND/OR)
OP = {
    "+":  dict(c="+",  basic="+",   python="+",   english="+"),
    "-":  dict(c="-",  basic="-",   python="-",   english="-"),
    "*":  dict(c="*",  basic="*",   python="*",   english="*"),
    "/":  dict(c="/",  basic="/",   python="/",   english="/"),
    "%":  dict(c="%",  basic="MOD", python="%",   english="%"),
    "<":  dict(c="<",  basic="<",   python="<",   english="<"),
    ">":  dict(c=">",  basic=">",   python=">",   english=">"),
    "<=": dict(c="<=", basic="<=",  python="<=",  english="<="),
    ">=": dict(c=">=", basic=">=",  python=">=",  english=">="),
    "==": dict(c="==", basic="=",   python="==",  english="=="),
}


def rexpr(e, d):
    if e.kind == "num":
        return str(e.v)
    if e.kind == "var":
        return e.n
    a, b = rexpr(e.a, d), rexpr(e.b, d)
    # parenthesize binary sub-expressions for unambiguous precedence
    if e.a.kind == "bin":
        a = "(" + a + ")"
    if e.b.kind == "bin":
        b = "(" + b + ")"
    return f"{a} {OP[e.op][d]} {b}"


class Rend:
    """Renders a statement list into one dialect's surface syntax."""
    def __init__(self, d):
        self.d = d
        self.decl = set()       # declared variables (for C int / BASIC DIM first-use)

    def block(self, stmts, indent):
        out = []
        for s in stmts:
            out += self.stmt(s, indent)
        return out

    def stmt(self, s, ind):
        d = self.d
        pad = "    " * ind
        kind = s[0]
        if kind == "assign":
            _, name, expr = s
            ex = rexpr(expr, d)
            first = name not in self.decl
            self.decl.add(name)
            if d == "c":
                return [f"{pad}{'int ' if first else ''}{name} = {ex};"]
            if d == "basic":
                return [f"{pad}{'DIM ' if first else ''}{name} = {ex}"]
            if d == "python":
                return [f"{pad}{name} = {ex}"]
            return [f"{pad}Set {name} to {ex}."]
        if kind == "inc":
            name = s[1]
            return {"c": [f"{pad}{name}++;"], "basic": [f"{pad}INC {name}"],
                    "python": [f"{pad}{name} += 1"], "english": [f"{pad}Increase {name} by 1."]}[d]
        if kind == "print":
            ex = rexpr(s[1], d)
            return {"c": [f"{pad}print({ex});"], "basic": [f"{pad}PRINT {ex}"],
                    "python": [f"{pad}print({ex})"], "english": [f"{pad}Print {ex}."]}[d]
        if kind == "if":
            _, cond, then, els = s
            c = rexpr(cond, d)
            if d == "c":
                r = [f"{pad}if ({c}) {{"] + self.block(then, ind + 1)
                if els:
                    r += [f"{pad}}} else {{"] + self.block(els, ind + 1)
                return r + [f"{pad}}}"]
            if d == "basic":
                r = [f"{pad}IF {c} THEN"] + self.block(then, ind + 1)
                if els:
                    r += [f"{pad}ELSE"] + self.block(els, ind + 1)
                return r + [f"{pad}ENDIF"]
            if d == "python":
                r = [f"{pad}if {c}:"] + self.block(then, ind + 1)
                if els:
                    r += [f"{pad}else:"] + self.block(els, ind + 1)
                return r
            r = [f"{pad}If {c}:"] + self.block(then, ind + 1)
            if els:
                r += [f"{pad}Otherwise:"] + self.block(els, ind + 1)
            return r
        if kind == "while":
            _, cond, body = s
            c = rexpr(cond, d)
            if d == "c":
                return [f"{pad}while ({c}) {{"] + self.block(body, ind + 1) + [f"{pad}}}"]
            if d == "basic":
                return [f"{pad}WHILE {c}"] + self.block(body, ind + 1) + [f"{pad}ENDWHILE"]
            if d == "python":
                return [f"{pad}while {c}:"] + self.block(body, ind + 1)
            return [f"{pad}While {c}:"] + self.block(body, ind + 1)
        if kind == "for":
            _, v, lo, hi, body = s
            self.decl.add(v)
            if d == "c":
                return [f"{pad}for ({v} = {lo}; {v} <= {hi}; {v}++) {{"] + self.block(body, ind + 1) + [f"{pad}}}"]
            if d == "basic":
                return [f"{pad}FOR {v} = {lo} TO {hi}"] + self.block(body, ind + 1) + [f"{pad}NEXT"]
            if d == "python":
                return [f"{pad}for {v} in range({lo}, {hi + 1}):"] + self.block(body, ind + 1)
            return [f"{pad}For each {v} from {lo} to {hi}:"] + self.block(body, ind + 1)
        raise ValueError(kind)


def render(prog, d):
    return "\n".join(Rend(d).block(prog, 0))


# ── templates: (name) -> (instruction, program) ──────────────────────────────
def t_arith(rng):
    a, b, c = (rng.randint(2, 20) for _ in range(3))
    ops = rng.sample(["+", "-", "*"], 2)
    prog = [("assign", "r", bin(ops[1], bin(ops[0], num(a), num(b)), num(c))),
            ("print", var("r"))]
    instr = f"Compute ({a} {ops[0]} {b}) {ops[1]} {c} and print the result."
    return instr, prog


def t_modulo(rng):
    a = rng.randint(20, 99); b = rng.randint(2, 9)
    prog = [("assign", "x", num(a)), ("print", bin("%", var("x"), num(b)))]
    return f"Print the remainder of {a} divided by {b}.", prog


def t_branch(rng):
    x = rng.randint(1, 20); thr = rng.randint(5, 15)
    hi, lo = rng.randint(50, 99), rng.randint(1, 49)
    prog = [("assign", "x", num(x)),
            ("if", bin(">", var("x"), num(thr)), [("print", num(hi))], [("print", num(lo))])]
    return f"Set x to {x}; if x is greater than {thr} print {hi}, otherwise print {lo}.", prog


def t_sum_range(rng):
    lo = rng.randint(1, 5); hi = rng.randint(6, 15)
    prog = [("assign", "s", num(0)),
            ("for", "i", lo, hi, [("assign", "s", bin("+", var("s"), var("i")))]),
            ("print", var("s"))]
    return f"Sum the integers from {lo} to {hi} and print the total.", prog


def t_factorial(rng):
    n = rng.randint(2, 7)
    prog = [("assign", "f", num(1)),
            ("for", "i", 2, n, [("assign", "f", bin("*", var("f"), var("i")))]),
            ("print", var("f"))]
    return f"Compute the factorial of {n} and print it.", prog


def t_while_count(rng):
    n = rng.randint(3, 9)
    prog = [("assign", "n", num(n)), ("assign", "c", num(0)),
            ("while", bin(">", var("n"), num(0)),
             [("assign", "c", bin("+", var("c"), num(1))),
              ("assign", "n", bin("-", var("n"), num(1)))]),
            ("print", var("c"))]
    return f"Count down from {n} to 0 and print how many steps it took.", prog


def t_power(rng):
    base = rng.randint(2, 5); exp = rng.randint(2, 5)
    prog = [("assign", "p", num(1)),
            ("for", "i", 1, exp, [("assign", "p", bin("*", var("p"), num(base)))]),
            ("print", var("p"))]
    return f"Raise {base} to the power {exp} and print the result.", prog


def t_accumulate_evens(rng):
    hi = rng.randint(6, 14)
    prog = [("assign", "s", num(0)),
            ("for", "i", 1, hi,
             [("if", bin("==", bin("%", var("i"), num(2)), num(0)),
               [("assign", "s", bin("+", var("s"), var("i")))], [])]),
            ("print", var("s"))]
    return f"Sum the even numbers from 1 to {hi} and print the total.", prog


TEMPLATES = [t_arith, t_modulo, t_branch, t_sum_range, t_factorial,
             t_while_count, t_power, t_accumulate_evens]


# ── generation pipeline ──────────────────────────────────────────────────────
def make_sample(name, instr, prog):
    """Render to 4 dialects, compile+run+verify. Returns dict or None if invalid."""
    srcs, words, outs = {}, {}, {}
    for d in DIALECTS:
        src = render(prog, d)
        try:
            w = lower_to_bytecode_safe(COMPILE[d](src))
        except Exception:
            return None
        srcs[d], words[d] = src, w
        outs[d] = run_output(w)
    ref = outs["c"]
    if any(outs[d] != ref for d in DIALECTS):
        return None                                     # outputs must agree
    if not (words["basic"] == words["python"] == words["english"]):
        return None                                     # shared-AST byte identity
    return {"name": name, "instruction": instr, "srcs": srcs, "output": ref,
            "words": len(words["c"])}


def generate(count, seed):
    rng = random.Random(seed)
    records, seen, attempts = [], set(), 0
    while len(seen) < count and attempts < count * 60:
        attempts += 1
        tmpl = rng.choice(TEMPLATES)
        instr, prog = tmpl(rng)
        sample = make_sample(tmpl.__name__, instr, prog)
        if not sample:
            continue
        key = (tmpl.__name__, sample["srcs"]["c"])
        if key in seen:
            continue
        seen.add(key)
        for d in DIALECTS:
            records.append({"task": "nl2code", "construct": sample["name"], "dialect": d,
                            "instruction": sample["instruction"], "code": sample["srcs"][d],
                            "output": sample["output"]})
            records.append({"task": "run", "construct": sample["name"], "dialect": d,
                            "code": sample["srcs"][d], "output": sample["output"]})
        ds = DIALECTS
        a = rng.choice(ds); b = rng.choice([x for x in ds if x != a])
        records.append({"task": "translate", "construct": sample["name"],
                        "from_dialect": a, "to_dialect": b,
                        "from_code": sample["srcs"][a], "to_code": sample["srcs"][b],
                        "output": sample["output"]})
    return records, attempts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=200, help="distinct verified programs to emit")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default=os.path.join(ROOT, "data", "picoscript.jsonl"))
    args = p.parse_args(argv)

    records, attempts = generate(args.count, args.seed)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    progs = len({(r["construct"], r["code"]) for r in records if r["task"] == "nl2code"}) // len(DIALECTS)
    by_task = {}
    for r in records:
        by_task[r["task"]] = by_task.get(r["task"], 0) + 1
    print(f"wrote {args.out}")
    print(f"  verified programs : {progs} (from {attempts} attempts)")
    print(f"  records           : {len(records)}  {by_task}")
    print(f"  dialects          : {', '.join(DIALECTS)}")


if __name__ == "__main__":
    main()
