#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picolayout.py -- the templated layout engine (PicoScript reports + forms).

Stage 2 of the 2-stage report/form model: stage 1 is an ordinary PicoScript
data-producer program (any frontend) whose output is a flat list of integer
values; this engine renders that data with a layout *template*.

One engine serves both:
  * mode "report" -> read-only rendering (text or an HTML table + aggregate footer)
  * mode "form"   -> read-write rendering (an HTML form of labelled inputs bound to
                     the data; editable fields become <input>, others stay static)

A report is simply a data program with a read-only layout attached; a form is the
same with a read-write layout. The template and this engine are shared; only field
editability (+ a write-back path, handled by the caller) differs.

Template (JSON):
    {
      "title":   "Sales",                 # optional band printed first
      "mode":    "report" | "form",       # default "report"
      "columns": [                          # data is chunked into rows of len(columns)
        {"label": "Qty", "field": 0, "width": 6, "format": "int"|"hex"|"raw",
         "align": "left"|"right", "editable": true}   # editable only matters in form mode
      ],
      "aggregates": [                       # report footer, per column
        {"column": 0, "fn": "count"|"sum"|"min"|"max"|"avg"}
      ]
    }
"""

from __future__ import annotations

import html as _html
from typing import List, Optional


def _rows(data: List[int], ncols: int) -> List[List[int]]:
    ncols = max(1, ncols)
    return [data[i:i + ncols] for i in range(0, len(data), ncols)]


def _fmt(value, fmt: str) -> str:
    if value is None:
        return ""
    if fmt == "hex":
        try:
            return "0x%x" % (int(value) & 0xFFFFFFFF)
        except (TypeError, ValueError):
            return str(value)
    if fmt == "raw":
        return str(value)
    # "int" (default)
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _agg(rows: List[List[int]], col: int, fn: str):
    vals = [r[col] for r in rows if col < len(r) and isinstance(r[col], (int, float))]
    if fn == "count":
        return len(vals)
    if not vals:
        return 0
    if fn == "sum":
        return sum(vals)
    if fn == "min":
        return min(vals)
    if fn == "max":
        return max(vals)
    if fn == "avg":
        return sum(vals) // len(vals)   # integer average (VM is integer)
    return 0


def _columns(template: dict) -> List[dict]:
    cols = template.get("columns") or template.get("fields") or []
    return [c if isinstance(c, dict) else {"label": str(c)} for c in cols]


def _col_field(col: dict, idx: int) -> int:
    f = col.get("field")
    return f if isinstance(f, int) else idx


def render_text(data: List[int], template: dict) -> str:
    """Render a read-only text report. Deterministic; used for both preview + tests."""
    cols = _columns(template)
    if not cols:
        return ""
    widths = [max(3, int(c.get("width") or 8)) for c in cols]
    lines: List[str] = []
    if template.get("title"):
        lines.append(str(template["title"]))
    header = "  ".join(str(c.get("label", "")).ljust(widths[i]) for i, c in enumerate(cols))
    lines.append(header)
    lines.append("  ".join("-" * widths[i] for i in range(len(cols))))
    rows = _rows(list(data), len(cols))
    for row in rows:
        cells = []
        for i, c in enumerate(cols):
            v = row[_col_field(c, i)] if _col_field(c, i) < len(row) else None
            s = _fmt(v, str(c.get("format") or "int"))
            cells.append(s.rjust(widths[i]) if (c.get("align") == "right") else s.ljust(widths[i]))
        lines.append("  ".join(cells))
    aggs = template.get("aggregates") or []
    if aggs:
        lines.append("  ".join("-" * widths[i] for i in range(len(cols))))
        parts = []
        for a in aggs:
            col = int(a.get("column", 0))
            fn = str(a.get("fn", "sum"))
            parts.append("%s=%s" % (fn, _agg(rows, col, fn)))
        lines.append("  ".join(parts))
    return "\n".join(lines) + "\n"


def render_html(data: List[int], template: dict, mode: Optional[str] = None) -> str:
    """Render an HTML report (table) or form (labelled inputs). `mode` overrides
    template["mode"] (default "report")."""
    cols = _columns(template)
    mode = (mode or template.get("mode") or "report").lower()
    rows = _rows(list(data), len(cols) or 1)
    esc = _html.escape

    if mode == "form":
        out = ['<form class="pico-form">']
        if template.get("title"):
            out.append('<h3 class="pico-form-title">%s</h3>' % esc(str(template["title"])))
        for ri, row in enumerate(rows):
            out.append('<div class="pico-form-row" data-row="%d">' % ri)
            for i, c in enumerate(cols):
                fi = _col_field(c, i)
                v = row[fi] if fi < len(row) else 0
                label = esc(str(c.get("label", "")))
                sval = _fmt(v, str(c.get("format") or "int"))
                editable = c.get("editable", True)
                if editable:
                    out.append(
                        '<label class="pico-field"><span>%s</span>'
                        '<input name="c%d" data-field="%d" data-row="%d" value="%s"></label>'
                        % (label, i, fi, ri, esc(sval)))
                else:
                    out.append(
                        '<label class="pico-field"><span>%s</span>'
                        '<output data-field="%d" data-row="%d">%s</output></label>'
                        % (label, fi, ri, esc(sval)))
            out.append('</div>')
        out.append('</form>')
        return "\n".join(out) + "\n"

    # report mode -> table
    out = ['<table class="pico-report">']
    if template.get("title"):
        out.append('<caption>%s</caption>' % esc(str(template["title"])))
    out.append('<thead><tr>' + "".join('<th>%s</th>' % esc(str(c.get("label", ""))) for c in cols) + '</tr></thead>')
    out.append('<tbody>')
    for row in rows:
        cells = ""
        for i, c in enumerate(cols):
            fi = _col_field(c, i)
            v = row[fi] if fi < len(row) else None
            cells += '<td>%s</td>' % esc(_fmt(v, str(c.get("format") or "int")))
        out.append('<tr>' + cells + '</tr>')
    out.append('</tbody>')
    aggs = template.get("aggregates") or []
    if aggs:
        cellmap = {}
        for a in aggs:
            col = int(a.get("column", 0))
            cellmap[col] = "%s=%s" % (str(a.get("fn", "sum")), _agg(rows, col, str(a.get("fn", "sum"))))
        tf = ""
        for i in range(len(cols)):
            tf += '<td>%s</td>' % esc(cellmap.get(i, ""))
        out.append('<tfoot><tr>' + tf + '</tr></tfoot>')
    out.append('</table>')
    return "\n".join(out) + "\n"


def render(data, template, mode: Optional[str] = None) -> str:
    """Render `data` (a flat list of ints) with `template`. Text when
    template["output"]=="text", else HTML. `mode` overrides report/form."""
    import json as _json
    if isinstance(template, str):
        template = _json.loads(template)
    out_kind = str(template.get("output") or "html").lower()
    if out_kind == "text":
        return render_text(data, template)
    return render_html(data, template, mode)


def flatten(rows) -> List[int]:
    """Flatten rows (row-major) back to the flat int list the engine consumed."""
    out = []
    for row in rows or []:
        for v in row or []:
            out.append(int(v))
    return out


def to_writes(rows, base: int = 0, stride: int = 0) -> dict:
    """Write-back into the data ABI: rows -> { key: value } keyed by
    (base + rowIndex*stride + field), so a stage-1 program can read each field
    back via Context.GetScratchValue / Memory.Get. `stride` defaults to the
    widest row."""
    if not stride:
        stride = max([len(r or []) for r in (rows or [])] + [1])
    out = {}
    for ri, row in enumerate(rows or []):
        for fi, v in enumerate(row or []):
            out[base + ri * stride + fi] = int(v)
    return out
