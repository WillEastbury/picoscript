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
def tern(c, a, b): return E("tern", c=c, a=a, b=b)


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
    if e.kind == "tern":
        c, a, b = rexpr(e.c, d), rexpr(e.a, d), rexpr(e.b, d)
        if d == "c":
            return f"({c}) ? {a} : {b}"
        if d == "basic":
            return f"IIF({c}, {a}, {b})"
        if d == "python":
            return f"{a} if {c} else {b}"
        return f"{a} if {c} otherwise {b}"          # english
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
        if kind == "switch":
            _, expr, cases, dflt = s
            ex = rexpr(expr, d)
            if d == "c":
                r = [f"{pad}switch ({ex}) {{"]
                for val, body in cases:
                    r += [f"{pad}    case {val}:"] + self.block(body, ind + 2) + [f"{pad}        break;"]
                if dflt:
                    r += [f"{pad}    default:"] + self.block(dflt, ind + 2)
                return r + [f"{pad}}}"]
            if d == "basic":
                r = [f"{pad}SWITCH {ex}"]
                for val, body in cases:
                    r += [f"{pad}    CASE {val}"] + self.block(body, ind + 2)
                if dflt:
                    r += [f"{pad}    DEFAULT"] + self.block(dflt, ind + 2)
                return r + [f"{pad}ENDSWITCH"]
            if d == "python":
                r = [f"{pad}match {ex}:"]
                for val, body in cases:
                    r += [f"{pad}    case {val}:"] + self.block(body, ind + 2)
                if dflt:
                    r += [f"{pad}    case _:"] + self.block(dflt, ind + 2)
                return r
            r = [f"{pad}Choose {ex}:"]                  # english
            for val, body in cases:
                r += [f"{pad}    When {val}:"] + self.block(body, ind + 2)
            if dflt:
                r += [f"{pad}    Otherwise:"] + self.block(dflt, ind + 2)
            return r
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


def t_nested_sum(rng):
    a = rng.randint(2, 5); b = rng.randint(2, 5)
    prog = [("assign", "s", num(0)),
            ("for", "i", 1, a,
             [("for", "j", 1, b,
               [("assign", "s", bin("+", var("s"), bin("*", var("i"), var("j"))))])]),
            ("print", var("s"))]
    return f"Sum i*j for i from 1 to {a} and j from 1 to {b}, and print the total.", prog


def t_ternary_max(rng):
    a = rng.randint(1, 99); b = rng.randint(1, 99)
    while b == a:
        b = rng.randint(1, 99)
    prog = [("assign", "a", num(a)), ("assign", "b", num(b)),
            ("assign", "m", tern(bin(">", var("a"), var("b")), var("a"), var("b"))),
            ("print", var("m"))]
    return f"Print the larger of {a} and {b} using a conditional expression.", prog


def t_switch_pick(rng):
    code = rng.randint(0, 3)
    v0, v1, v2, dv = (rng.randint(10, 99) for _ in range(4))
    prog = [("assign", "code", num(code)),
            ("switch", var("code"),
             [(0, [("print", num(v0))]), (1, [("print", num(v1))]), (2, [("print", num(v2))])],
             [("print", num(dv))])]
    return (f"Set code to {code}; in a switch print {v0} for 0, {v1} for 1, {v2} for 2, "
            f"otherwise {dv}.", prog)


TEMPLATES = [t_arith, t_modulo, t_branch, t_sum_range, t_factorial,
             t_while_count, t_power, t_accumulate_evens,
             t_nested_sum, t_ternary_max, t_switch_pick]


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


# ── fine-tune-ready chat export ──────────────────────────────────────────────
DIALECT_NAME = {"c": "C-style", "basic": "BASIC", "python": "Python-style",
                "english": "natural-English"}
SYSTEM_CODEGEN = (
    "You write PicoScript, a deterministic integer-only language that compiles to a "
    "frozen 16-opcode bytecode. Values are signed 32-bit ints in one global scope; "
    "there are no strings, floats, or objects. Write the program in the {dialect} "
    "dialect and output only the code, no prose.")
SYSTEM_TRANSLATE = (
    "You translate PicoScript between its four dialects (C-style, BASIC, Python-style, "
    "natural-English) preserving exact behaviour. Output only the translated program.")


def to_chat(records):
    """Turn raw records into OpenAI-style chat-message training examples."""
    out = []
    for r in records:
        if r["task"] == "nl2code":
            out.append({"messages": [
                {"role": "system", "content": SYSTEM_CODEGEN.format(dialect=DIALECT_NAME[r["dialect"]])},
                {"role": "user", "content": r["instruction"]},
                {"role": "assistant", "content": r["code"]}],
                "meta": {"task": "nl2code", "dialect": r["dialect"], "construct": r["construct"]}})
        elif r["task"] == "translate":
            out.append({"messages": [
                {"role": "system", "content": SYSTEM_TRANSLATE},
                {"role": "user", "content": f"Translate this {DIALECT_NAME[r['from_dialect']]} "
                                            f"PicoScript to {DIALECT_NAME[r['to_dialect']]}:\n\n{r['from_code']}"},
                {"role": "assistant", "content": r["to_code"]}],
                "meta": {"task": "translate", "from": r["from_dialect"], "to": r["to_dialect"]}})
    return out


def split_train_val(items, val_frac, seed):
    items = items[:]
    random.Random(seed * 7 + 1).shuffle(items)
    n_val = max(1, int(round(len(items) * val_frac)))
    return items[n_val:], items[:n_val]


def _write_jsonl(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=400, help="distinct verified programs to emit")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--val-frac", type=float, default=0.1, help="fraction held out for validation")
    p.add_argument("--out-dir", default=os.path.join(ROOT, "data"))
    args = p.parse_args(argv)

    records, attempts = generate(args.count, args.seed)
    chat = to_chat(records)
    train, val = split_train_val(chat, args.val_frac, args.seed)

    d = args.out_dir
    _write_jsonl(os.path.join(d, "picoscript.jsonl"), records)          # raw corpus (gitignored)
    _write_jsonl(os.path.join(d, "train.chat.jsonl"), train)            # fine-tune train (gitignored)
    _write_jsonl(os.path.join(d, "val.chat.jsonl"), val)                # fine-tune val   (gitignored)
    _write_jsonl(os.path.join(d, "picoscript.chat.sample.jsonl"), chat[:40])   # committed sample

    progs = len(seen_keys(records))
    by_task = {}
    for r in records:
        by_task[r["task"]] = by_task.get(r["task"], 0) + 1
    constructs = sorted({r["construct"] for r in records if r["task"] == "nl2code"})
    print(f"verified programs : {progs} (from {attempts} attempts)")
    print(f"raw records       : {len(records)}  {by_task}")
    print(f"chat examples     : {len(chat)}  ->  train {len(train)} / val {len(val)}")
    print(f"constructs        : {len(constructs)}  {constructs}")
    print(f"wrote into {d}/ : picoscript.jsonl, train.chat.jsonl, val.chat.jsonl, picoscript.chat.sample.jsonl")


def seen_keys(records):
    return {(r["construct"], r["code"]) for r in records if r["task"] == "nl2code" and r["dialect"] == "c"}


if __name__ == "__main__":
    main()
