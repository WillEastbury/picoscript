#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_cfront_exception_eventing.py -- C-style (picoscript_cfront.py)
TryCatch/Raise/OnBlock support.

Part of the full-language-equivalence pass. Unlike the BASIC-family
frontends (BASIC/Python/English/COBOL/Report/Functional), C-style is a
fully independent frontend -- its own AST dataclasses and its own
`Lowerer`, never importing from `picoscript_basic`. It DOES share the same
`picoscript_il.ILBuilder` (including `label_addr`) and the same
`Error.SetHandler`/`PopHandler`/`Raise`/`Clear` host ops as the BASIC
family, so the underlying exception/eventing mechanism (see
docs/EXCEPTION_ENGINE.md, docs/EVENTING.md) is identical -- just
re-expressed against cfront's own `TryCatch`/`Raise`/`OnBlock` node classes
and its own `lower_try`/`lower_on_block` methods.

Verified here as byte-identical bytecode vs. the equivalent BASIC source,
proving the mechanism really is the same despite the independent AST.

Scope note: the JS mirror of C-style (`CParser`/`CLowerer` in
`vm/picoc.js`) does NOT have this yet -- porting it would mean a THIRD,
independent implementation of the same mechanism (BLowerer's JS port was
the second), which was deliberately not attempted in this pass to avoid
rushing a third from-scratch implementation. See docs/DIALECT_PARITY.md.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, PicoFault  # noqa: E402

import pytest  # noqa: E402


def _out(vm: PicoVM):
    return [int.from_bytes(b, "big") for b in vm.output]


def test_c_try_catch_finally_raise_byte_identical_to_basic():
    c_src = (
        "int x = 0;\n"
        "try {\n"
        "    x = 1;\n"
        "    raise 42;\n"
        "    x = 999;\n"
        "} catch {\n"
        "    x = x + 100;\n"
        "} finally {\n"
        "    x = x + 1000;\n"
        "}\n"
        "print(x);\n"
    )
    basic_src = (
        "LET X = 0\n"
        "TRY\n"
        "    LET X = 1\n"
        "    RAISE 42\n"
        "    LET X = 999\n"
        "EXCEPT\n"
        "    LET X = X + 100\n"
        "FINALLY\n"
        "    LET X = X + 1000\n"
        "ENDTRY\n"
        "PRINT X\n"
    )
    c_words = lower_to_bytecode_safe(compile_c(c_src))
    basic_words = lower_to_bytecode_safe(compile_basic(basic_src))
    assert c_words == basic_words
    assert _out(PicoVM().run(c_words)) == [1101]


def test_c_try_catch_happy_path_no_finally():
    src = "int x = 0;\ntry {\n    x = 1;\n} catch {\n    x = 999;\n}\nprint(x);\n"
    words = lower_to_bytecode_safe(compile_c(src))
    assert _out(PicoVM().run(words)) == [1]


def test_c_uncaught_raise_propagates_as_picofault():
    words = lower_to_bytecode_safe(compile_c("raise 7;\nprint(1);\n"))
    with pytest.raises(PicoFault) as exc:
        PicoVM().run(words)
    assert exc.value.code == 7


def test_c_on_block_dispatches_matching_event():
    from picoscript_basic import event_type_hash
    type_code = event_type_hash("Ui", "Click")
    src = (
        f"int hits = 0;\n"
        f"int target = 0;\n"
        f"Event.Post({type_code}, 5);\n"
        f"Event.Post(999, 9);\n"
        f"on Ui.Click {{\n"
        f"    hits = hits + 1;\n"
        f"    target = Event.Target(__event__);\n"
        f"}}\n"
        f"print(hits);\n"
        f"print(target);\n"
    )
    words = lower_to_bytecode_safe(compile_c(src))
    assert _out(PicoVM().run(words)) == [1, 5]


def test_c_native_transpile_rejects_try_catch_clearly():
    """lower_to_c (native transpile) has no PC-addressable model for
    label_addr -- must reject clearly rather than silently mis-compile,
    same as for the BASIC family (see docs/EXCEPTION_ENGINE.md)."""
    from picoscript_il import lower_to_c
    src = "int x = 0;\ntry {\n    raise 1;\n} catch {\n    x = 1;\n}\n"
    il = compile_c(src)
    with pytest.raises(ValueError, match="laddr"):
        lower_to_c(il)
