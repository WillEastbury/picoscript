#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/vendor_baremetal.py -- vendor the canonical BareMetalJsTools browser
modules into picoscript/vm/vendor/.

Policy: BareMetalJsTools is the single source of truth for reusable browser JS.
picoscript reuses (vendors) those modules rather than maintaining parallel copies.
Re-run after upstream changes. The vendored files are committed so the generated
GitHub Pages / file:// bundles need no sibling repo.

Upstream: C:\\source\\baremetaljstools\\src (override with $BAREMETALJS_SRC).
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "vm", "vendor")
DEFAULT_SRC = os.path.join(ROOT, "..", "baremetaljstools", "src")

MODULES = [
    "BareMetal.WorkflowPico.js",   # visual workflow -> English PicoScript
    "BareMetal.Report.js",         # templated layout engine (reports + forms)
    "BareMetal.DragDrop.js",       # pointer drag & drop (designer dependency)
    "BareMetal.Workflow.js",       # workflow engine + christmas-tree Designer canvas
]

HEADER = (
    "// VENDORED from BareMetalJsTools (single source of truth). Do not edit here;\n"
    "// edit upstream in baremetaljstools/src and re-run tools/vendor_baremetal.py.\n"
    "// Upstream: {name}\n"
)


def main():
    src = os.environ.get("BAREMETALJS_SRC", DEFAULT_SRC)
    if not os.path.isdir(src):
        print(f"upstream not found: {src}", file=sys.stderr)
        return 1
    os.makedirs(DEST, exist_ok=True)
    for name in MODULES:
        s = os.path.join(src, name)
        d = os.path.join(DEST, name)
        with open(s, encoding="utf-8") as f:
            body = f.read()
        with open(d, "w", encoding="utf-8") as f:
            f.write(HEADER.format(name=name) + body)
        print(f"vendored {name} ({len(body) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
