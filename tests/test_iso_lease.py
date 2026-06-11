#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INV-7 compile-time iso-lease parity (verify_response_ownership).

`seal`/`respond`/`end` consume the `iso` response arena, so use-after-seal is a
**compile error** in the AOT compiler (docs/PIOS_IO_BINDING.md [D6]) -- zero runtime
cost. The check is a forward must-dataflow over the IL CFG: it flags a Resp.* op only
when it is illegal on *every* path to it (so a branch that seals on one arm but not
another is never falsely rejected), and is left as the runtime sim's job otherwise.

This test is the gatekeeper (INV-24): the Python gate (picoscript_il.lower_to_bytecode_safe)
and the JS gate (vm/picoc.js via vm/picoc_compile.js) must make **byte-identical
accept/reject decisions AND report the identical first violation** for every program --
a program a frontend would reject must be rejected by every frontend, or INV-1/INV-2
(same source, same semantics / lowering parity) is broken.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c          # noqa: E402
from picoscript_python import compile_python      # noqa: E402
from picoscript_il import lower_to_bytecode_safe, ResponseOwnershipError  # noqa: E402

ACCEPT = "accept"


def _tag(msg):
    # Normalise to the stable first-violation tag (text before the parenthetical).
    return msg.split(" (")[0] if "INV-7" in msg else msg


def py_decide(src, lang):
    compile_fn = compile_c if lang == "c" else compile_python
    try:
        lower_to_bytecode_safe(compile_fn(src))
        return (ACCEPT, "")
    except ResponseOwnershipError as exc:
        return ("reject", _tag(str(exc)))


def js_decide(src, lang):
    r = subprocess.run(["node", os.path.join(ROOT, "vm", "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    if r.returncode == 0:
        return (ACCEPT, "")
    err = (r.stderr or "").strip().replace("COMPILE_ERROR ", "")
    return ("reject", _tag(err))


# (lang, label, source, expected_decision)
#   expected_decision == ACCEPT, or the first-violation tag for a rejected program.
VALID = [
    ("c", "stream: status/header/seal/write/flush/end",
     'Resp.Status(200); Resp.Header("a","b"); Resp.Seal(); Resp.Write("x"); Resp.Flush(); Resp.End();'),
    ("c", "unary: build body then Respond (status last)",
     'Resp.Write("a"); Resp.Write("b"); Resp.Respond(200);'),
    ("c", "unary: status/header*2/write/trailer/end",
     'Resp.Status(201); Resp.Header("c","t"); Resp.Header("x","p"); Resp.Write("m"); Resp.Trailer("d","y"); Resp.End();'),
    ("c", "control: continue/status/seal/write/flush/endstream/upgrade/abort",
     'Resp.Continue(); Resp.Status(200); Resp.Seal(); Resp.Write("c"); Resp.Flush(); Resp.EndStream(); Resp.Upgrade("ws"); Resp.Abort(499);'),
    # Branch that starts the body on only one arm: the join point is NOT definitely
    # in the body phase, so a later header must be accepted (must-analysis soundness).
    ("python", "branch: header after one-arm body write",
     'x = 1\nif x:\n    Resp.Write("a")\nResp.Header("h","v")\nResp.End()\n'),
    ("c", "non-response program is unaffected", 'int x = 5; Io.WriteByte(x);'),
]

INVALID = [
    ("c", "header after seal", 'Resp.Status(200); Resp.Seal(); Resp.Header("late","no");',
     "INV-7: Resp.Header after Seal"),
    ("c", "double seal", 'Resp.Status(200); Resp.Seal(); Resp.Seal();',
     "INV-7: Resp.Seal after Seal"),
    ("c", "use-after-end (header)", 'Resp.Status(200); Resp.End(); Resp.Header("a","b");',
     "INV-7: Resp.Header after the response was finalized"),
    ("c", "write after end (Respond)", 'Resp.Respond(200); Resp.Write("x");',
     "INV-7: Resp.Write after the response was finalized"),
    ("c", "header after body (phase)", 'Resp.Status(200); Resp.Write("x"); Resp.Header("a","b");',
     "INV-7: Resp.Header after a body write"),
    ("c", "write after EndStream", 'Resp.Status(200); Resp.Seal(); Resp.Write("a"); Resp.EndStream(); Resp.Write("b");',
     "INV-7: Resp.Write after EndStream"),
    # Same use-after-seal expressed in the Python dialect -> the gate is frontend-agnostic
    # (it operates on the shared IL), so it must reject here too.
    ("python", "header after seal (python dialect)",
     'Resp.Status(200)\nResp.Seal()\nResp.Header("late","no")\n',
     "INV-7: Resp.Header after Seal"),
]


def main():
    failed = 0
    for lang, label, src in VALID:
        pd, jd = py_decide(src, lang), js_decide(src, lang)
        try:
            assert pd[0] == ACCEPT, f"Python gate must accept a valid handler; got {pd}"
            assert jd[0] == ACCEPT, f"JS gate must accept a valid handler; got {jd}"
            assert pd == jd, f"gate parity: Python {pd} != JS {jd}"
            print(f"[PASS] valid: {label}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] valid: {label}: {exc}")

    for lang, label, src, tag in INVALID:
        pd, jd = py_decide(src, lang), js_decide(src, lang)
        try:
            assert pd == ("reject", tag), f"Python gate must reject with {tag!r}; got {pd}"
            assert jd == ("reject", tag), f"JS gate must reject with {tag!r}; got {jd}"
            assert pd == jd, f"gate parity: Python {pd} != JS {jd}"
            print(f"[PASS] invalid: {label}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] invalid: {label}: {exc}")

    total = len(VALID) + len(INVALID)
    print(f"{total - failed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("PASS iso-lease: compile-time use-after-seal is a compile error; Python and JS "
          "gates make byte-identical accept/reject decisions with the identical first "
          "violation (INV-7 authoritative form; runtime Resp.* sim is the backstop)")


if __name__ == "__main__":
    main()
