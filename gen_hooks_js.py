#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_hooks_js.py -- emit vm/pico_hooks.js from picoscript_lang.py.

Keeps the browser/Node VM's host-hook table in lock-step with the Python source,
exactly like vm/pico_hooks.h does for the C VM.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picoscript_lang import (
    HOST_HOOK_CODES, HOST_HOOK_BASE, EXT_HOST_HOOK_BASE, NET_STATUS_BASE, NET_HEADER_BASE,
    NET_BODY_MARKER, NET_CLOSE_MARKER, CONTENT_TYPES,
)

L = []
L.append("// AUTO-GENERATED from picoscript_lang.py -- do not edit by hand.")
L.append("(function (root) {")
L.append("  var H = {")
L.append("    HOST_HOOK_BASE: 0x%04X," % HOST_HOOK_BASE)
L.append("    EXT_HOST_HOOK_BASE: 0x%04X," % EXT_HOST_HOOK_BASE)
L.append("    NET_STATUS_BASE: 0x%04X," % NET_STATUS_BASE)
L.append("    NET_HEADER_BASE: 0x%04X," % NET_HEADER_BASE)
L.append("    NET_BODY_MARKER: 0x%04X," % NET_BODY_MARKER)
L.append("    NET_CLOSE_MARKER: 0x%04X," % NET_CLOSE_MARKER)
L.append("    CONTENT_TYPES: {")
for k, v in CONTENT_TYPES.items():
    L.append('      0x%04X: "%s",' % (v, k))
L.append("    },")
L.append("    BY_CODE: {")
for (ns, m), code in sorted(HOST_HOOK_CODES.items(), key=lambda kv: kv[1]):
    L.append('      0x%02X: "%s.%s",' % (code, ns, m))
L.append("    }")
L.append("  };")
L.append('  if (typeof module !== "undefined" && module.exports) { module.exports = H; }')
L.append("  else { root.PV_HOOKS = H; }")
L.append("})(typeof globalThis !== \"undefined\" ? globalThis : this);")

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm", "pico_hooks.js")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("wrote", path, "with", len(HOST_HOOK_CODES), "hook codes")

# Mirror the C header (flat #define list, sorted by code) from the same table.
H = []
H.append("/* AUTO-GENERATED from picoscript_lang.py -- do not edit by hand. */")
H.append("#ifndef PICO_HOOKS_H")
H.append("#define PICO_HOOKS_H")
H.append("")
H.append("#define PV_HOST_HOOK_BASE  0x%04X" % HOST_HOOK_BASE)
H.append("#define PV_EXT_HOST_HOOK_BASE 0x%04X" % EXT_HOST_HOOK_BASE)
H.append("#define PV_NET_STATUS_BASE 0x%04X" % NET_STATUS_BASE)
H.append("#define PV_NET_HEADER_BASE 0x%04X" % NET_HEADER_BASE)
H.append("#define PV_NET_BODY_MARKER 0x%04X" % NET_BODY_MARKER)
H.append("#define PV_NET_CLOSE_MARKER 0x%04X" % NET_CLOSE_MARKER)
H.append("")
for (ns, m), code in sorted(HOST_HOOK_CODES.items(), key=lambda kv: kv[1]):
    name = ("PV_HOOK_%s_%s" % (ns, m)).upper()
    H.append("#define %-41s0x%02X" % (name, code))
H.append("")
H.append("#endif")
hpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm", "pico_hooks.h")
with open(hpath, "w", encoding="utf-8") as f:
    f.write("\n".join(H) + "\n")
print("wrote", hpath, "with", len(HOST_HOOK_CODES), "hook codes")
