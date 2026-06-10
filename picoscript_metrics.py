#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_metrics.py -- size / instruction / cycle metrics for compiled
PicoScript programs.

Collects, for a program (in any frontend) and across backends:

  * IL instruction count (raw and after the optimizer)
  * Bytecode size (words and bytes) and a static opcode histogram
  * Estimated cycle count (an analytical model -- NOT cycle-accurate; it weights
    each opcode by a rough cost so backends/programs can be compared)
  * Emitted C and JS backend sizes (lines / bytes) for the same program
  * Optional dynamic counts from a profiled run (instructions executed + est cycles)

The cycle model is deliberately simple and documented inline so it can be tuned
against real Pi5 / RP2350 measurements later.  Treat the numbers as a comparative
signal (is change X bigger/slower than Y?), not as wall-clock truth.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import picoscript as isa
from picoscript_lang import HOST_HOOK_BASE
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js, optimize
from picoscript_vm import PicoVM

# ── analytical cycle model (estimate; tune against real silicon later) ────────
# Base cost per opcode in notional cycles.  Memory/card ops and the multiply/
# divide unit cost more; host-hook dispatch and DSP cost the most.
CYCLE_COST: Dict[int, int] = {
    isa.OP_NOOP: 1,
    isa.OP_LOAD: 3, isa.OP_SAVE: 3, isa.OP_PIPE: 3,
    isa.OP_ADD: 1, isa.OP_SUB: 1, isa.OP_MUL: 3, isa.OP_DIV: 10,
    isa.OP_INC: 1,
    isa.OP_JUMP: 2, isa.OP_BRANCH: 2, isa.OP_CALL: 3, isa.OP_RETURN: 3,
    isa.OP_WAIT: 1, isa.OP_RAISE: 2, isa.OP_DSP: 12,
}
HOSTCALL_CYCLES = 8     # NOOP carrying a host-hook marker (function-call dispatch)
NET_OP_CYCLES = 2       # NOOP carrying a Net.* transport marker
INDIRECT_JUMP_CYCLES = 3  # computed JUMP (jmptab dispatch) -- target not statically known


def classify_word(word: int):
    """Return (category_label, cycle_cost) for one bytecode word.  NOOP is split
    into HOSTCALL / NET / NOOP; an indirect/indexed JUMP is shown as JUMP* so a
    jump table is visible in the histogram."""
    d = isa.decode_instruction(word)
    op, imm, rs2 = d["opcode"], d["imm16"], d["rs2"]
    if op == isa.OP_NOOP:
        if (imm & 0xFF00) == HOST_HOOK_BASE:
            return "HOSTCALL", HOSTCALL_CYCLES
        if imm:
            return "NET", NET_OP_CYCLES
        return "NOOP", CYCLE_COST[isa.OP_NOOP]
    if op == isa.OP_JUMP and rs2 in (isa.ADDR_REGISTER, isa.ADDR_REG_OFF):
        return "JUMP*", INDIRECT_JUMP_CYCLES        # computed / jump-table dispatch
    name = isa.OPCODE_NAMES.get(op, f"OP_{op:X}")
    return name, CYCLE_COST.get(op, 1)


def static_metrics(words: List[int]) -> dict:
    hist: Dict[str, int] = {}
    cycles = 0
    for w in words:
        cat, c = classify_word(w)
        hist[cat] = hist.get(cat, 0) + 1
        cycles += c
    return {
        "bytecode_words": len(words),
        "bytecode_bytes": len(words) * 4,
        "static_instr": len(words),
        "static_cycles_est": cycles,
        "opcode_hist": hist,
    }


def _to_il(source: str, lang: str):
    if lang == "c":
        from picoscript_cfront import compile_c
        return compile_c(source)
    if lang == "basic":
        from picoscript_basic import compile_basic
        return compile_basic(source)
    if lang == "python":
        from picoscript_python import compile_python
        return compile_python(source)
    if lang == "english":
        from picoscript_english import compile_english
        return compile_english(source)
    raise ValueError(f"frontend {lang!r} has no IL metrics")


def _src_size(text: str) -> dict:
    return {"lines": text.count("\n") + 1, "bytes": len(text.encode("utf-8"))}


def measure(source: str, lang: str, backend: str = "bytecode",
            run: bool = False, opt: bool = True) -> dict:
    """Full metric bundle for `source` compiled with `lang`.  Always reports IL +
    bytecode + C/JS backend sizes; with run=True adds profiled dynamic counts."""
    il = _to_il(source, lang)
    words = lower_to_bytecode_safe(il, opt=opt)
    m: dict = {
        "lang": lang,
        "backend": backend,
        "il_insts": len(il),
        "il_insts_opt": len(optimize(il)),
    }
    m.update(static_metrics(words))
    m["c_backend"] = _src_size(lower_to_c(il, opt=opt))
    m["js_backend"] = _src_size(lower_to_js(il, opt=opt))
    if run:
        vm = PicoVM()
        vm.profile = True
        vm.run(words)
        dyn = 0
        for op, n in vm.op_hist.items():
            if op == isa.OP_NOOP:
                continue
            dyn += CYCLE_COST.get(op, 1) * n
        noop_plain = vm.op_hist.get(isa.OP_NOOP, 0) - vm.host_calls - vm.net_ops
        dyn += vm.host_calls * HOSTCALL_CYCLES + vm.net_ops * NET_OP_CYCLES + max(0, noop_plain)
        m["dynamic_instr"] = vm.steps
        m["dynamic_cycles_est"] = dyn
        m["host_calls"] = vm.host_calls
    return m


def format_metrics(m: dict, title: Optional[str] = None) -> str:
    """Render a metric bundle as a compact aligned report."""
    L: List[str] = []
    head = title or f"{m['lang']} program"
    L.append(f"PicoScript metrics  --  {head}")
    L.append(f"  IL instructions   : {m['il_insts']}  (after opt: {m['il_insts_opt']})")
    L.append(f"  Bytecode          : {m['bytecode_words']} words / {m['bytecode_bytes']} bytes"
             f"   est. {m['static_cycles_est']} cycles (static)")
    hist = "  ".join(f"{k}x{v}" for k, v in sorted(m["opcode_hist"].items(), key=lambda kv: -kv[1]))
    L.append(f"  Opcode histogram  : {hist}")
    L.append(f"  C backend         : {m['c_backend']['lines']} lines / {m['c_backend']['bytes']} bytes")
    L.append(f"  JS backend        : {m['js_backend']['lines']} lines / {m['js_backend']['bytes']} bytes")
    L.append(f"  Backend chosen    : {m['backend']}")
    if "dynamic_instr" in m:
        L.append(f"  Executed (run)    : {m['dynamic_instr']} instructions"
                 f"   est. {m['dynamic_cycles_est']} cycles (dynamic),"
                 f" {m['host_calls']} host calls")
    return "\n".join(L)
