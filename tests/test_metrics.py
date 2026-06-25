#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_metrics.py -- sanity checks for picoscript_metrics.

Run: python tests/test_metrics.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_metrics import measure, classify_word, format_metrics  # noqa: E402
import picoscript as isa  # noqa: E402


def main():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if cond:
            passed += 1
        else:
            failed += 1

    # Straight-line program: static == dynamic instruction count.
    LOOP = "DIM A = 0\nFOR I = 1 TO 10\n    A += I\nNEXT\nPRINT A"
    m = measure(LOOP, "basic", run=True)
    check("il count present", m["il_insts"] > 0)
    check("bytecode bytes == words*4", m["bytecode_bytes"] == m["bytecode_words"] * 4)
    check("static cycles > 0", m["static_cycles_est"] > 0)
    check("C and JS backend sizes reported", m["c_backend"]["bytes"] > 0 and m["js_backend"]["bytes"] > 0)
    # the loop body runs 10x, so dynamic instructions exceed the static program size
    check("dynamic instr > static (loop runs)", m["dynamic_instr"] > m["static_instr"])
    check("dynamic cycles >= dynamic instr", m["dynamic_cycles_est"] >= m["dynamic_instr"])

    # Host calls are counted dynamically (Memory.Set is a real host hook; Net.* is a marker).
    HOSTY = "Memory.Set(10, 65);\nMemory.Set(11, 66);\nprint(1);"
    mh = measure(HOSTY, "c", run=True)
    check("host calls counted", mh["host_calls"] >= 2)

    # classify_word: indirect/indexed JUMP is surfaced as JUMP* with a higher cost.
    E = isa.encode_instruction
    abs_jump = E(isa.OP_JUMP, imm16=5)
    idx_jump = E(isa.OP_JUMP, rs1=0, rs2=isa.ADDR_REG_OFF, imm16=5)
    check("absolute jump label", classify_word(abs_jump)[0] == "JUMP")
    check("indexed jump label JUMP*", classify_word(idx_jump)[0] == "JUMP*")
    check("indexed jump costs more", classify_word(idx_jump)[1] > classify_word(abs_jump)[1])

    # format renders without error and includes the key fields.
    text = format_metrics(m, title="loop")
    check("format mentions cycles", "cycles" in text and "Backend chosen" in text)

    print(f"\n{passed} passed, {failed} failed")
    assert failed == 0, f"{failed} test(s) failed"


def test_main():
    main()


if __name__ == "__main__":
    main()
