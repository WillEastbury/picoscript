#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_reportmodel.py -- the *report-model* frontend (visual report designer).

Compiles a declarative report model (the JSON a band/column report designer
produces) to PicoIL by lowering it to natural-English PicoScript and reusing
``compile_english`` -- the same pre-compile pattern as the workflow frontend.
This is the compiler backbone the visual report designer sits on.

The VM is a deterministic 32-bit integer machine, so a report is a numeric
detail band + aggregate footer over an integer data source. Rows are held in
``Memory`` (base + length) exactly like workflow arrays; aggregates are computed
in the detail loop and printed in the footer.

Report model (JSON):
    {
      "title":  <int?>,                        # optional banner value printed first
      "source": {"kind": "array", "values": [..]} |
                {"kind": "variable", "name": "data"},   # array already in Memory
      "row":    "item",                        # per-row expression over `item` (default "item")
      "where":  "item >= 0",                   # optional detail filter
      "aggregates": ["count", "sum", "min", "max"]   # printed in the footer, in order
    }

Lowers to (example, array [10,20,30] summed/counted):
    Memory.Set(8192, 10). ...
    Set _count to 0.
    Set _sum to 0.
    For each _r0 from 0 to 2:
        Set item to Memory.Get(8192 plus _r0).
        Set _row to item.
        Print _row.
        Set _count to _count plus 1.
        Set _sum to _sum plus _row.
    Print _count.
    Print _sum.
"""

from __future__ import annotations

import json
from typing import List, Tuple

from picoscript_english import compile_english
from picoscript_workflow import translate_expr, num_lit, sanitize_id, literal_array, emit_scalar

INT_MAX = 2147483647
INT_MIN = -2147483648
VALID_AGG = ("count", "sum", "min", "max")


def report_model_to_english(model: dict) -> Tuple[str, List[str]]:
    """Lower a report model to English PicoScript. Returns (source, warnings)."""
    out: List[str] = []
    warnings: List[str] = []
    mem_next = 8192

    # ---- title band ----
    if model.get("title") is not None:
        out.append("Print %s." % emit_scalar(model["title"], warnings, "report title"))

    # ---- data source (an integer array held in Memory) ----
    source = model.get("source") or {}
    kind = str(source.get("kind") or "array").lower()
    if kind == "array":
        values = source.get("values")
        arr = literal_array(values) if not isinstance(values, list) else values
        if arr is None:
            arr = []
        base = mem_next
        for k, el in enumerate(arr):
            out.append("Memory.Set(%d, %s)." % (base + k, emit_scalar(el, warnings, "row[%d]" % k)))
        length = len(arr)
    elif kind == "variable":
        # the array (base) + <name>_len are assumed already materialised by a prior
        # step; the designer wires this when chaining a workflow into a report.
        name = sanitize_id(source.get("name") or "data")
        base_expr = name
        length = None
        warnings.append("report: variable source %r assumed pre-materialised in Memory (base=%s, %s_len)" % (source.get("name"), name, name))
    else:
        warnings.append("report: unknown source kind %r; emitting an empty report" % kind)
        base = mem_next
        length = 0

    aggregates = [a for a in (model.get("aggregates") or []) if a in VALID_AGG]
    row_expr = model.get("row") or "item"
    where = model.get("where")

    # ---- aggregate accumulators ----
    if "count" in aggregates:
        out.append("Set _count to 0.")
    if "sum" in aggregates:
        out.append("Set _sum to 0.")
    if "min" in aggregates:
        out.append("Set _min to %s." % num_lit(INT_MAX))
    if "max" in aggregates:
        out.append("Set _max to %s." % num_lit(INT_MIN))

    # ---- detail band (loop over rows) ----
    if kind == "variable":
        upper = "%s_len minus 1" % base_expr
        get_base = base_expr
    else:
        upper = num_lit((length or 0) - 1)
        get_base = str(base)

    if kind == "variable" or (length and length > 0):
        out.append("For each _r0 from 0 to %s:" % upper)
        ind = "    "
        out.append(ind + "Set item to Memory.Get(%s plus _r0)." % get_base)
        body_ind = ind
        if where:
            out.append(ind + "If %s:" % translate_expr(where))
            body_ind = ind + "    "
        out.append(body_ind + "Set _row to %s." % translate_expr(row_expr))
        out.append(body_ind + "Print _row.")
        if "count" in aggregates:
            out.append(body_ind + "Set _count to _count plus 1.")
        if "sum" in aggregates:
            out.append(body_ind + "Set _sum to _sum plus _row.")
        if "min" in aggregates:
            out.append(body_ind + "If _row is less than _min:")
            out.append(body_ind + "    Set _min to _row.")
        if "max" in aggregates:
            out.append(body_ind + "If _row is greater than _max:")
            out.append(body_ind + "    Set _max to _row.")

    # ---- footer band (aggregates in declared order) ----
    for agg in aggregates:
        out.append("Print _%s." % agg)

    return "\n".join(out) + "\n", warnings


def _coerce_model(source):
    data = json.loads(source) if isinstance(source, str) else source
    if not isinstance(data, dict):
        raise ValueError("report model must be a JSON object")
    return data


def compile_report_model(source):
    """Compile a report model (JSON object) to PicoIL, via English PicoScript."""
    model = _coerce_model(source)
    english, _warnings = report_model_to_english(model)
    return compile_english(english)
