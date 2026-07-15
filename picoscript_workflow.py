#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_workflow.py -- the *workflow* frontend.

Compiles a visual-workflow step list (the JSON produced by the BareMetal.Workflow
designer, and the same shape BareMetal.WorkflowPico consumes) to PicoIL by first
lowering it to natural-English PicoScript, then reusing ``compile_english``. This
keeps the workflow dialect byte-for-byte aligned with the JavaScript compiler
(baremetaljstools/src/BareMetal.WorkflowPico.js) and the C# PicoVm host in
developercli/workflow -- see docs/WORKFLOW_DIALECT.md for the full contract.

A workflow is a flat list of step dicts with block markers, e.g.

    [
      {"type": "SET", "name": "sum", "value": 0},
      {"type": "FOREACH", "var": "item", "in": [10, 20, 30]},
      {"type": "SET", "name": "sum", "expr": "sum + item"},
      {"type": "END"},
      {"type": "LOG", "message": "sum"}
    ]

Step types: SET, IF/ELSE/END, FOR, FOREACH/FOREACHP, LOG, WAIT, LOAD, SAVE, WEB,
CALL. The VM is a deterministic 32-bit integer machine: arithmetic, control flow
and integer arrays (materialised into Memory) lower faithfully; genuinely
host-side steps (WEB, storage LOAD/SAVE, CALL) lower to comments and are reported
through the returned warnings.

Data ABI (identical hook codes in the JS bundle and the C# WorkflowHost):
    field/scratch  -> Context.GetScratchValue / SetScratchValue   (0xeb / 0xea)
    array/memory   -> Memory.Get / Memory.Set                     (0x37 / 0x36)
An array is a base address + length in Memory; FOREACH iterates element values
via Memory.Get(base + i).
"""

from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from picoscript_english import compile_english

UNIT = "    "
DEFAULT_ARRAY_BASE = 8192  # 0x2000 -- above workflow scratch/field keys

# JS-ish operator -> English word operator (matches BareMetal.WorkflowPico and
# developercli/tools/forge_assets/flow.js).
BINARY_WORD = {
    "==": "is", "===": "is", "!=": "is not", "!==": "is not",
    ">=": "is at least", "<=": "is at most",
    ">": "is greater than", "<": "is less than",
    "&&": "and", "||": "or",
    "+": "plus", "-": "minus", "*": "times", "/": "divided by", "%": "modulo",
}

_ID_RE = re.compile(r"[^A-Za-z0-9_]")
_INT_RE = re.compile(r"^-?\d+$")
_INTERP_EXACT = re.compile(r"^\$\{([\s\S]+)\}$")
_INTERP_ANY = re.compile(r"\$\{[\s\S]*\}")
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def _pad(n: int) -> str:
    return UNIT * n


def sanitize_id(name) -> str:
    s = _ID_RE.sub("_", "" if name is None else str(name))
    if not s:
        s = "_v"
    if s[0].isdigit():
        s = "_" + s
    return s


# ── expression translation (JS-ish subset -> English word operators) ──────────
def _tokenize_expr(s: str):
    toks = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "\"'":
            q = c
            j = i + 1
            buf = c
            while j < n:
                d = s[j]
                buf += d
                if d == "\\" and j + 1 < n:
                    buf += s[j + 1]
                    j += 2
                    continue
                if d == q:
                    j += 1
                    break
                j += 1
            toks.append(("str", buf))
            i = j
            continue
        if c.isdigit():
            j = i
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            toks.append(("num", s[i:j]))
            i = j
            continue
        if c.isalpha() or c in "_$":
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_$"):
                j += 1
            toks.append(("id", s[i:j]))
            i = j
            continue
        three = s[i:i + 3]
        if three in ("===", "!=="):
            toks.append(("op", three))
            i += 3
            continue
        two = s[i:i + 2]
        if two in ("==", "!=", ">=", "<=", "&&", "||"):
            toks.append(("op", two))
            i += 2
            continue
        if c in "+-*/%<>!":
            toks.append(("op", c))
            i += 1
            continue
        toks.append(("punct", c))
        i += 1
    return toks


def translate_expr(src) -> str:
    toks = _tokenize_expr("" if src is None else str(src))
    parts: List[str] = []
    prev_value = False
    for kind, val in toks:
        if kind == "op":
            if val in ("-", "+") and not prev_value:
                if val == "-":
                    parts.append("0")
                    parts.append("minus")
                prev_value = False
            elif val == "!" and not prev_value:
                parts.append("not")
                prev_value = False
            else:
                parts.append(BINARY_WORD.get(val, val))
                prev_value = False
        elif kind == "id":
            parts.append(sanitize_id(val))
            prev_value = True
        elif kind in ("num", "str"):
            parts.append(val)
            prev_value = True
        else:
            parts.append(val)
            prev_value = (val == ")")
    out = " ".join(parts)
    out = out.replace("( ", "(").replace(" )", ")").replace(" ,", ",")
    out = re.sub(r"\s*\.\s*", ".", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


# ── literals ──────────────────────────────────────────────────────────────────
def num_lit(n) -> str:
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return "0"
    n = int(n)
    return "(0 minus %d)" % abs(n) if n < 0 else str(n)


def quote_str(s) -> str:
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return '"' + s + '"'


def emit_scalar(value, warnings: List[str], label: str) -> str:
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return num_lit(value)
    if isinstance(value, str):
        return emit_string_value(value, warnings, label)
    warnings.append(label + ": non-scalar value is not representable on the integer VM; emitted 0")
    return "0"


def emit_string_value(v: str, warnings: List[str], label: str) -> str:
    m = _INTERP_EXACT.match(v)
    if m:
        return "(" + translate_expr(m.group(1)) + ")"
    t = v.strip()
    if _INT_RE.match(t):
        return num_lit(int(t))
    if _INTERP_ANY.search(v):
        warnings.append(label + ": string interpolation %r is not representable; emitted quoted literal" % v)
    return quote_str(v)


def emit_operand(value, warnings: List[str], label: str) -> str:
    if isinstance(value, bool):
        return emit_scalar(value, warnings, label)
    if isinstance(value, (int, float)):
        return num_lit(value)
    if isinstance(value, str):
        t = value.strip()
        if _INT_RE.match(t):
            return num_lit(int(t))
        return translate_expr(value)
    return emit_scalar(value, warnings, label)


def literal_array(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                a = json.loads(t)
                if isinstance(a, list):
                    return a
            except (ValueError, TypeError):
                pass
    return None


# ── compile context ───────────────────────────────────────────────────────────
class _Ctx:
    def __init__(self, array_base: int):
        self.out: List[str] = []
        self.warnings: List[str] = []
        self.arrays: dict = {}
        self.mem_next = array_base
        self.temp_n = 0


def _alloc(ctx: _Ctx, length: int) -> int:
    base = ctx.mem_next
    ctx.mem_next += max(1, length)
    return base


def _materialize(ctx: _Ctx, indent: int, values: list, label: str) -> Tuple[int, int]:
    base = _alloc(ctx, len(values))
    for k, el in enumerate(values):
        ctx.out.append(_pad(indent) + "Memory.Set(%d, %s)." % (base + k, emit_scalar(el, ctx.warnings, "%s[%d]" % (label, k))))
    return base, len(values)


def _step_type(step) -> str:
    return str((step or {}).get("type", "")).upper()


# ── recursive lowering ────────────────────────────────────────────────────────
def _emit_seq(steps, pos, indent, ctx: _Ctx) -> str:
    while pos[0] < len(steps):
        step = steps[pos[0]] or {}
        t = _step_type(step)
        if t == "END":
            pos[0] += 1
            return "END"
        if t == "ELSE":
            return "ELSE"
        pos[0] += 1
        _emit_step(step, t, steps, pos, indent, ctx)
    return "EOF"


def _emit_body(steps, pos, indent, ctx: _Ctx) -> str:
    start = len(ctx.out)
    term = _emit_seq(steps, pos, indent, ctx)
    if len(ctx.out) == start:
        ctx.out.append(_pad(indent) + "Set _nop to 0.")
    return term


def _close_loop(steps, pos, indent, ctx: _Ctx):
    term = _emit_body(steps, pos, indent + 1, ctx)
    if term == "ELSE":
        pos[0] += 1
        ctx.warnings.append("ELSE without a matching IF; ignored")


def _emit_step(step, t, steps, pos, indent, ctx: _Ctx):
    out, warnings = ctx.out, ctx.warnings
    if t == "SET":
        _emit_set(step, indent, ctx)
    elif t == "IF":
        out.append(_pad(indent) + "If " + translate_expr(step.get("condition") or "false") + ":")
        term = _emit_body(steps, pos, indent + 1, ctx)
        if term == "ELSE":
            pos[0] += 1
            out.append(_pad(indent) + "Otherwise:")
            term = _emit_body(steps, pos, indent + 1, ctx)
        if term == "ELSE":
            pos[0] += 1
            warnings.append("IF: multiple ELSE branches; extra ELSE ignored")
    elif t == "FOR":
        v = sanitize_id(step.get("var") or "i")
        line = _pad(indent) + "For each %s from %s to %s" % (
            v, emit_operand(step.get("from"), warnings, "FOR from"),
            emit_operand(step.get("to"), warnings, "FOR to"))
        if step.get("step") is not None:
            line += " by " + emit_operand(step.get("step"), warnings, "FOR step")
        out.append(line + ":")
        _close_loop(steps, pos, indent, ctx)
    elif t in ("FOREACH", "FOREACHP"):
        _emit_foreach(step, t, steps, pos, indent, ctx)
    elif t == "LOG":
        _emit_log(step, indent, ctx)
    elif t == "WAIT":
        out.append(_pad(indent) + "Timer.After(%s)." % emit_operand(step.get("ms", 0), warnings, "WAIT ms"))
        warnings.append("WAIT: Timer.After schedules but does not block the VM")
    elif t in ("RAISE", "EMIT"):
        ev = emit_operand(step.get("event", 0), warnings, "RAISE event")
        tgt = emit_operand(step.get("target", 0), warnings, "RAISE target") if step.get("target") is not None else "0"
        call = "Event.Post(%s, %s)" % (ev, tgt)
        if step.get("result"):
            out.append(_pad(indent) + "Set %s to %s." % (sanitize_id(step["result"]), call))
        else:
            out.append(_pad(indent) + call + ".")
    elif t in ("ON", "SUBSCRIBE"):
        _emit_on(step, steps, pos, indent, ctx)
    elif t == "LOAD":
        _emit_load(step, indent, ctx)
    elif t == "SAVE":
        _emit_save(step, indent, ctx)
    elif t == "WEB":
        out.append(_pad(indent) + "# WEB %s %s%s" % (
            str(step.get("method") or "GET").upper(), step.get("url") or "",
            (" -> " + step["result"]) if step.get("result") else ""))
        warnings.append("WEB: HTTP requests require a host transport hook and are not executed by the integer VM")
    elif t == "CALL":
        out.append(_pad(indent) + "# CALL " + (step.get("workflow") or ""))
        warnings.append("CALL: nested workflow %r must be compiled separately and is not linked" % (step.get("workflow") or ""))
    elif t == "ELSE":
        pos[0] += 1
        warnings.append("ELSE without a matching IF; ignored")
    else:
        out.append(_pad(indent) + "# %s (unsupported step type)" % t)
        warnings.append("Unsupported step type %r; emitted as comment" % t)


def _emit_set(step, indent, ctx: _Ctx):
    name = sanitize_id(step.get("name"))
    if "expr" in step:
        ctx.out.append(_pad(indent) + "Set %s to %s." % (name, translate_expr(step["expr"])))
        ctx.arrays.pop(name, None)
        return
    arr = literal_array(step.get("value"))
    if arr is not None:
        base, length = _materialize(ctx, indent, arr, "SET " + str(step.get("name")))
        ctx.arrays[name] = (base, length)
        ctx.out.append(_pad(indent) + "Set %s to %d." % (name, base))
        ctx.out.append(_pad(indent) + "Set %s_len to %s." % (name, num_lit(length)))
        return
    ctx.arrays.pop(name, None)
    ctx.out.append(_pad(indent) + "Set %s to %s." % (name, emit_scalar(step.get("value"), ctx.warnings, "SET " + str(step.get("name")))))


def _resolve_array(step, indent, ctx: _Ctx, label: str):
    in_raw = step.get("in")
    if isinstance(in_raw, str):
        key = sanitize_id(in_raw.strip())
        if key in ctx.arrays:
            return ctx.arrays[key]
    lit = literal_array(in_raw)
    if lit is not None:
        return _materialize(ctx, indent, lit, label)
    return None


def _emit_on(step, steps, pos, indent, ctx: _Ctx):
    """ON/SUBSCRIBE <event>: drain the Event.* queue and run the handler body for
    each matching event. Lowers to a bounded drain loop over the pending count."""
    ev = emit_operand(step.get("event", 0), ctx.warnings, "ON event")
    var = sanitize_id(step.get("var") or "event")
    loop = "_on%d" % ctx.temp_n
    ctx.temp_n += 1
    evid = "_ev%d" % ctx.temp_n
    ctx.temp_n += 1
    out = ctx.out
    out.append(_pad(indent) + "For each %s from 0 to (Event.Count() minus 1):" % loop)
    out.append(_pad(indent + 1) + "Set %s to Event.Next()." % evid)
    out.append(_pad(indent + 1) + "If Event.Type(%s) is %s:" % (evid, ev))
    out.append(_pad(indent + 2) + "Set %s to %s." % (var, evid))
    start = len(out)
    term = _emit_seq(steps, pos, indent + 2, ctx)
    if len(out) == start:
        out.append(_pad(indent + 2) + "Set _nop to 0.")
    if term == "ELSE":
        pos[0] += 1
        ctx.warnings.append("ELSE without a matching IF; ignored")


def _emit_foreach(step, t, steps, pos, indent, ctx: _Ctx):
    v = sanitize_id(step.get("var") or "item")
    if t == "FOREACHP":
        ctx.warnings.append("FOREACHP: parallel iteration lowered to sequential")
    info = _resolve_array(step, indent, ctx, "FOREACH " + str(step.get("var") or "item"))
    if info is not None:
        base, length = info
        idx = "_fe%d" % ctx.temp_n
        ctx.temp_n += 1
        ctx.out.append(_pad(indent) + "For each %s from 0 to %s:" % (idx, num_lit(length - 1)))
        ctx.out.append(_pad(indent + 1) + "Set %s to Memory.Get(%d plus %s)." % (v, base, idx))
        start = len(ctx.out)
        term = _emit_seq(steps, pos, indent + 1, ctx)
        if len(ctx.out) == start:
            ctx.out.append(_pad(indent + 1) + "Set _nop to 0.")
        if term == "ELSE":
            pos[0] += 1
            ctx.warnings.append("ELSE without a matching IF; ignored")
        return
    ctx.out.append(_pad(indent) + "# FOREACH %s in %s -- runtime array not resolvable; body runs once with %s = 0" % (v, step.get("in") or "", v))
    ctx.out.append(_pad(indent) + "For each %s from 0 to 0:" % v)
    ctx.warnings.append("FOREACH over %r is not representable on the integer VM; body lowered to a single iteration" % (step.get("in") or ""))
    _close_loop(steps, pos, indent, ctx)


def _emit_load(step, indent, ctx: _Ctx):
    name = sanitize_id(step.get("name"))
    frm = str(step.get("from") or "").lower()
    if frm == "variable":
        src_raw = step.get("key") or step.get("var") or step.get("source") or ""
        src_id = sanitize_id(str(src_raw).strip())
        if src_id in ctx.arrays:
            ctx.arrays[name] = ctx.arrays[src_id]
        else:
            ctx.arrays.pop(name, None)
        ctx.out.append(_pad(indent) + "Set %s to %s." % (name, translate_expr(str(src_raw))))
        return
    if frm in ("memory", "scratch"):
        hook = "Context.GetScratchValue" if frm == "scratch" else "Memory.Get"
        ctx.out.append(_pad(indent) + "Set %s to %s(%s)." % (name, hook, emit_operand(step.get("key", 0), ctx.warnings, "LOAD key")))
        ctx.arrays.pop(name, None)
        return
    ctx.out.append(_pad(indent) + "# LOAD %s <- %s%s" % (
        step.get("name") or "", step.get("from") or "", (" [%s]" % step["key"]) if step.get("key") else ""))
    ctx.warnings.append("LOAD from %r requires a host storage/transport hook and is not executed by the integer VM" % (step.get("from") or ""))


def _emit_save(step, indent, ctx: _Ctx):
    name = sanitize_id(step.get("name"))
    to = str(step.get("to") or "").lower()
    if to == "variable":
        target = sanitize_id(step.get("key") or step.get("target") or step.get("name"))
        ctx.out.append(_pad(indent) + "Set %s to %s." % (target, name))
        if name in ctx.arrays:
            ctx.arrays[target] = ctx.arrays[name]
        return
    if to in ("memory", "scratch"):
        hook = "Context.SetScratchValue" if to == "scratch" else "Memory.Set"
        ctx.out.append(_pad(indent) + "%s(%s, %s)." % (hook, emit_operand(step.get("key", 0), ctx.warnings, "SAVE key"), name))
        return
    ctx.out.append(_pad(indent) + "# SAVE %s -> %s%s" % (
        step.get("name") or "", step.get("to") or "", (" [%s]" % step["key"]) if step.get("key") else ""))
    ctx.warnings.append("SAVE to %r requires a host storage hook and is not executed by the integer VM" % (step.get("to") or ""))


def _emit_log(step, indent, ctx: _Ctx):
    msg = step.get("message")
    if isinstance(msg, bool):
        pass
    elif isinstance(msg, (int, float)):
        ctx.out.append(_pad(indent) + "Print %s." % num_lit(msg))
        return
    elif isinstance(msg, str):
        m = _INTERP_EXACT.match(msg)
        if m:
            ctx.out.append(_pad(indent) + "Print %s." % translate_expr(m.group(1)))
            return
        if _INT_RE.match(msg.strip()):
            ctx.out.append(_pad(indent) + "Print %s." % num_lit(int(msg.strip())))
            return
        if _IDENT_RE.match(msg.strip()):
            ctx.out.append(_pad(indent) + "Print %s." % sanitize_id(msg.strip()))
            return
    lvl = ("[%s] " % step["level"]) if step.get("level") else ""
    ctx.out.append(_pad(indent) + "# LOG %s%s" % (lvl, "" if msg is None else str(msg)))
    ctx.warnings.append("LOG: console strings are not printable on the integer VM; emitted as comment")


# ── public API ────────────────────────────────────────────────────────────────
def workflow_to_english(steps, array_base: int = DEFAULT_ARRAY_BASE) -> Tuple[str, List[str]]:
    """Lower a workflow step list to English PicoScript. Returns (source, warnings)."""
    ctx = _Ctx(array_base)
    pos = [0]
    n = len(steps)
    while pos[0] < n:
        term = _emit_seq(steps, pos, 0, ctx)
        if term == "ELSE":
            pos[0] += 1
            ctx.warnings.append("ELSE without a matching IF; ignored")
        elif term == "END":
            ctx.warnings.append("END without a matching block; ignored")
        else:
            break
    return "\n".join(ctx.out) + "\n", ctx.warnings


def _coerce_steps(source):
    if isinstance(source, str):
        data = json.loads(source)
    else:
        data = source
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        data = data["steps"]
    if not isinstance(data, list):
        raise ValueError("workflow source must be a JSON array of steps (or an object with a 'steps' array)")
    return data


def workflow_to_ast(source, array_base: int = DEFAULT_ARRAY_BASE):
    """Workflow (JSON step list) -> AST, via the same English rendering
    `compile_workflow` uses -- just stopping one stage earlier, before the
    Lowerer, so the tree can be inspected/edited (see picoscript_ast.py /
    docs/ast_designer_spike.html) rather than only compiled.

    Returns ``(prog, warnings)`` where ``prog`` is the same AST shape
    `picoscript_basic.Lowerer.lower_program` / `picoscript_ast.ast_to_json`
    consume. `compile_ast(json.dumps(ast_to_json(workflow_to_ast(src)[0])))`
    produces byte-identical bytecode to `compile_workflow(src)`.
    """
    from picoscript_english import tokenize, Parser
    steps = _coerce_steps(source)
    english, warnings = workflow_to_english(steps, array_base=array_base)
    prog = Parser(tokenize(english)).parse_program()
    return prog, warnings


def compile_workflow(source, array_base: int = DEFAULT_ARRAY_BASE):
    """Compile a workflow (JSON step list) to PicoIL, via English PicoScript.

    ``source`` may be a JSON string, a list of step dicts, or an object with a
    ``steps`` array. Returns the lowered PicoIL (same as the other frontends).
    """
    steps = _coerce_steps(source)
    english, _warnings = workflow_to_english(steps, array_base=array_base)
    return compile_english(english)
