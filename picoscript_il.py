#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_il.py -- PicoIL: the shared intermediate language.

Both frontends (C-syntax and BASIC-like) lower their AST to PicoIL.  PicoIL is a
small, typed, three-address representation over *virtual registers* (vregs).  It is
then lowered to a concrete backend:

    PicoIL --opt--> PicoIL --regalloc--> [physical R0..R15] --> bytecode words   (PicoVM)
                                                              --> portable C       (toC: Thumb/AArch64)

The IL is the single place where we trade footprint for throughput ("lowering
shape closer to the metal", per LANGUAGE_SPEC.md sec 10):

  * opt=True       constant folding, redundant-move elimination, INC fusion
  * regalloc       linear-scan over conservative live intervals; optional card spill
  * backend        'bytecode' (compact ISA) or 'c' (native toolchain)

This module deliberately re-uses the *frozen* v1 ISA encoding from picoscript.py and
the host-hook / Net markers from picoscript_lang.py so every backend stays
bit-compatible with the existing v1 compiler and decompilers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union, Dict

import picoscript as isa
from picoscript_lang import (
    HOST_HOOK_BASE,
    EXT_HOST_HOOK_BASE,
    HOST_HOOK_CODES,
    NET_STATUS_BASE,
    NET_HEADER_BASE,
    NET_BODY_MARKER,
    NET_CLOSE_MARKER,
    CONTENT_TYPES,
    encode_card_addr,
)

NUM_REGS = 16

# Condition codes (match picoscript BRANCH_* values exactly).
COND = {
    "EQ": isa.BRANCH_EQ, "NE": isa.BRANCH_NE, "LT": isa.BRANCH_LT,
    "GT": isa.BRANCH_GT, "LE": isa.BRANCH_LE, "GE": isa.BRANCH_GE,
    "Z": isa.BRANCH_Z, "NZ": isa.BRANCH_NZ, "EOF": isa.BRANCH_EOF,
    "ERR": isa.BRANCH_ERR,
}
# Comparison negation, used by frontends to fall through on the false edge.
COND_NEGATE = {
    "EQ": "NE", "NE": "EQ", "LT": "GE", "GE": "LT", "GT": "LE", "LE": "GT",
}

ARITH = {"add": isa.OP_ADD, "sub": isa.OP_SUB, "mul": isa.OP_MUL, "div": isa.OP_DIV}

# Case-folded host-hook index so frontends can be fully case-insensitive:
# (ns_lower, method_lower) -> canonical (Ns, Method) as the host ABI expects.
HOOK_CANON = {(ns.lower(), m.lower()): (ns, m) for (ns, m) in HOST_HOOK_CODES}


def canon_host(ns: str, method: str):
    """Resolve a possibly mixed-case namespace.method to its canonical host-ABI
    spelling.  Unknown names pass through unchanged (errors surface at lowering)."""
    return HOOK_CANON.get((ns.lower(), method.lower()), (ns, method))


# ═══════════════════════════════════════════════════════════════════════════
# Operand model
# ═══════════════════════════════════════════════════════════════════════════

class VReg:
    """A virtual register.  Frontends allocate these freely; the allocator maps
    them onto physical R0..R15 (spilling to scratch cards under pressure).

    `pinned` marks a named *variable* (as opposed to a short-lived temporary).
    Pinned vregs are given a whole-program live interval so they never share a
    physical register -- this is required for correctness across OP_CALL/GOSUB,
    where a subroutine body placed textually after `main` actually executes
    *during* it and must not clobber a still-live variable.
    """
    __slots__ = ("id", "name", "pinned")
    _counter = 0

    def __init__(self, name: str = "", pinned: bool = False):
        self.id = VReg._counter
        VReg._counter += 1
        self.name = name or f"v{self.id}"
        self.pinned = pinned

    def __repr__(self):
        return f"%{self.name}"

    def __hash__(self):
        return self.id

    def __eq__(self, other):
        return isinstance(other, VReg) and other.id == self.id


@dataclass(frozen=True)
class Imm:
    """An immediate integer operand."""
    value: int

    def __repr__(self):
        return f"#{self.value}"


Operand = Union[VReg, Imm]


# ═══════════════════════════════════════════════════════════════════════════
# IL instructions
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Inst:
    """A single PicoIL instruction.  `op` selects the variant; fields are reused
    per-op (documented in build helpers below)."""
    op: str
    dst: Optional[VReg] = None
    a: Optional[Operand] = None
    b: Optional[Operand] = None
    cond: Optional[str] = None
    label: Optional[str] = None
    ns: Optional[str] = None
    method: Optional[str] = None
    args: Tuple[Operand, ...] = ()
    imm: int = 0
    text: str = ""        # carried comment / string literal (e.g. Net.Type)
    targets: Tuple[str, ...] = ()   # ordered case labels for a jump table (jmptab)

    def __repr__(self):
        if self.op == "label":
            return f"{self.label}:"
        parts = [self.op]
        if self.dst is not None:
            parts.append(repr(self.dst))
        if self.a is not None:
            parts.append(repr(self.a))
        if self.b is not None:
            parts.append(repr(self.b))
        if self.op in ("const", "load", "save", "pipe", "raise") or (self.op == "net" and self.imm):
            parts.append(f"#{self.imm}")
        if self.cond:
            parts.append(self.cond)
        if self.label and self.op != "label":
            parts.append(f"-> {self.label}")
        if self.ns:
            parts.append(f"{self.ns}.{self.method}({', '.join(map(repr, self.args))})")
        if self.text:
            parts.append(repr(self.text))
        return "  " + " ".join(parts)


class ILBuilder:
    """Convenience builder used by frontends to emit PicoIL."""

    def __init__(self):
        self.insts: List[Inst] = []
        self._label_n = 0

    # -- operand helpers -------------------------------------------------
    def vreg(self, name: str = "") -> VReg:
        return VReg(name)

    def new_label(self, hint: str = "L") -> str:
        self._label_n += 1
        return f"{hint}{self._label_n}"

    # -- emit ------------------------------------------------------------
    def const(self, dst: VReg, value: int):
        self.insts.append(Inst("const", dst=dst, imm=value))

    def mov(self, dst: VReg, src: Operand):
        self.insts.append(Inst("mov", dst=dst, a=src))

    def arith(self, op: str, dst: VReg, a: Operand, b: Operand):
        assert op in ARITH, op
        self.insts.append(Inst(op, dst=dst, a=a, b=b))

    def inc(self, dst: VReg):
        self.insts.append(Inst("inc", dst=dst))

    def cmpbr(self, cond: str, a: Operand, b: Operand, label: str):
        self.insts.append(Inst("cmpbr", cond=cond, a=a, b=b, label=label))

    def jmp(self, label: str):
        self.insts.append(Inst("jmp", label=label))

    def jmptab(self, selector: VReg, targets: Tuple[str, ...], default_label: str):
        """Indexed jump table: PC dispatches on `selector` (assumed in [0, len(targets)))
        to targets[selector].  `default_label` is the out-of-range target used by the
        C/JS backends' switch default; callers must emit a bounds guard for bytecode."""
        self.insts.append(Inst("jmptab", a=selector, targets=tuple(targets), label=default_label))

    def label(self, name: str):
        self.insts.append(Inst("label", label=name))

    def call(self, label: str):
        self.insts.append(Inst("call", label=label))

    def ret(self):
        self.insts.append(Inst("ret"))

    def host(self, ns: str, method: str, args: Tuple[Operand, ...] = (),
             dst: Optional[VReg] = None):
        self.insts.append(Inst("host", ns=ns, method=method, args=tuple(args), dst=dst))

    def load(self, dst: VReg, addr16: int):
        self.insts.append(Inst("load", dst=dst, imm=addr16))

    def save(self, src: VReg, addr16: int):
        self.insts.append(Inst("save", a=src, imm=addr16))

    def pipe(self, src: VReg, addr16: int):
        self.insts.append(Inst("pipe", a=src, imm=addr16))

    def net(self, kind: str, value: Union[int, str] = 0):
        self.insts.append(Inst("net", method=kind, imm=value if isinstance(value, int) else 0,
                               text=value if isinstance(value, str) else ""))

    def dsp(self, subop: int, dst: VReg, a: Optional[VReg] = None, b: Optional[Operand] = None):
        self.insts.append(Inst("dsp", dst=dst, a=a, b=b, imm=subop))

    def wait(self, mask: Optional[VReg] = None):
        self.insts.append(Inst("wait", a=mask))

    def raise_irq(self, channel: int):
        self.insts.append(Inst("raise", imm=channel))


# ═══════════════════════════════════════════════════════════════════════════
# Optimizer (peephole on the linear IL)
# ═══════════════════════════════════════════════════════════════════════════

def _is_imm(x) -> bool:
    return isinstance(x, Imm)


def optimize(insts: List[Inst]) -> List[Inst]:
    """Constant folding + redundant-move elimination + INC fusion.

    Conservative: only rewrites within obviously-safe local windows.  Keeps
    the IL semantically identical while shrinking the lowered footprint.
    """
    out: List[Inst] = []
    for ins in insts:
        # Constant-fold pure arithmetic on two immediates.
        if ins.op in ARITH and _is_imm(ins.a) and _is_imm(ins.b):
            av, bv = ins.a.value, ins.b.value
            if ins.op == "add":
                r = av + bv
            elif ins.op == "sub":
                r = av - bv
            elif ins.op == "mul":
                r = av * bv
            else:
                r = av // bv if bv != 0 else 0
            out.append(Inst("const", dst=ins.dst, imm=r))
            continue
        # x = x + 1  ->  inc x
        if ins.op == "add" and isinstance(ins.a, VReg) and ins.dst == ins.a \
                and _is_imm(ins.b) and ins.b.value == 1:
            out.append(Inst("inc", dst=ins.dst))
            continue
        # mov x, x  -> drop
        if ins.op == "mov" and isinstance(ins.a, VReg) and ins.dst == ins.a:
            continue
        out.append(ins)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Register allocation (linear scan over conservative live intervals)
# ═══════════════════════════════════════════════════════════════════════════

class RegisterPressureError(Exception):
    pass


def _operand_vregs(ins: Inst):
    for x in (ins.dst, ins.a, ins.b, *ins.args):
        if isinstance(x, VReg):
            yield x


def allocate(insts: List[Inst], spill: bool = False) -> Dict[int, int]:
    """Map each VReg.id to a physical register R0..R15.

    Uses conservative live intervals [first def/use, last use] so a vreg is never
    freed while a later (possibly back-edge) use remains -- correct in the presence
    of loops without a full CFG.  When `spill` is set, R14/R15 are reserved as
    shuttle scratch and overflow vregs are spilled to scratch cards.
    """
    first: Dict[int, int] = {}
    last: Dict[int, int] = {}
    order: List[int] = []
    vregs: Dict[int, VReg] = {}
    for i, ins in enumerate(insts):
        for v in _operand_vregs(ins):
            if v.id not in first:
                first[v.id] = i
                order.append(v.id)
                vregs[v.id] = v
            last[v.id] = i

    # Loop-aware liveness: a value defined before a loop header and still live at
    # that header is live across the whole loop because of the back-edge.  Extend
    # its interval to the back-edge so the loop body cannot reuse its register
    # (otherwise a loop-invariant like a FOR bound gets clobbered mid-loop).
    label_pos: Dict[str, int] = {}
    for i, ins in enumerate(insts):
        if ins.op == "label":
            label_pos[ins.label] = i
    back_edges: List[Tuple[int, int]] = []   # (header_index, backedge_index)
    for i, ins in enumerate(insts):
        if ins.op in ("jmp", "cmpbr") and ins.label in label_pos:
            t = label_pos[ins.label]
            if t <= i:
                back_edges.append((t, i))
    for (t, i) in back_edges:
        for vid in list(first.keys()):
            if first[vid] < t <= last[vid] and last[vid] < i:
                last[vid] = i

    # Pinned (named-variable) vregs persist across calls: give them a
    # whole-program interval so they get a dedicated register and are never
    # clobbered by a subroutine body that runs mid-flow (GOSUB/CALL).
    n = max(1, len(insts))
    call_idx = [i for i, ins in enumerate(insts) if ins.op == "call"]
    for vid, v in vregs.items():
        spans_call = any(first[vid] <= ci <= last[vid] for ci in call_idx)
        if v.pinned or spans_call:
            first[vid] = 0
            last[vid] = n


    usable = NUM_REGS - 2 if spill else NUM_REGS
    free = list(range(usable))
    active: List[Tuple[int, int]] = []   # (end_index, vreg_id)
    mapping: Dict[int, int] = {}
    spilled: Dict[int, int] = {}         # vreg_id -> scratch card slot
    next_slot = 0

    def expire(at: int):
        nonlocal active
        keep = []
        for end, vid in active:
            if end < at:
                free.append(mapping[vid])
            else:
                keep.append((end, vid))
        active = sorted(keep)

    for vid in sorted(order, key=lambda v: first[v]):
        expire(first[vid])
        if free:
            free.sort()
            reg = free.pop(0)
            mapping[vid] = reg
            active.append((last[vid], vid))
            active.sort()
        elif spill:
            spilled[vid] = next_slot
            next_slot += 1
        else:
            raise RegisterPressureError(
                f"register pressure exceeds {NUM_REGS} live values; "
                f"re-run with spill=True or simplify the program")
    # Expose spill info via attribute on the dict for the lowerer.
    mapping_meta = dict(mapping)
    mapping_meta["__spilled__"] = spilled            # type: ignore
    mapping_meta["__shuttle__"] = (NUM_REGS - 2, NUM_REGS - 1)  # type: ignore
    return mapping_meta


# ═══════════════════════════════════════════════════════════════════════════
# Backend 1: lower to bytecode words (PicoVM / picovm.c)
# ═══════════════════════════════════════════════════════════════════════════

def _phys(mapping: Dict[int, int], v: VReg) -> int:
    reg = mapping.get(v.id)
    if reg is None:
        raise RegisterPressureError(f"vreg {v} was spilled; spill lowering path required")
    return reg


def lower_to_bytecode(insts: List[Inst], opt: bool = True) -> List[int]:
    """Lower PicoIL to a list of 32-bit instruction words (the frozen v1 ISA).

    Two passes: resolve label PCs, then emit.  Immediates that cannot live in a
    register slot (arith with a non-register first operand) are materialized with
    a CONST first by the frontends, so here `a` is always a VReg for arithmetic.
    """
    if opt:
        insts = optimize(insts)
    mapping = allocate(insts)

    # Pass 1: assign a PC to every real (non-label) instruction; record labels.
    labels: Dict[str, int] = {}
    pc = 0
    for ins in insts:
        if ins.op == "label":
            labels[ins.label] = pc
        else:
            pc += 1

    words: List[int] = []
    pc = 0
    for ins in insts:
        if ins.op == "label":
            continue
        words.append(_emit_word(ins, mapping, labels, pc))
        pc += 1
    return words


def _emit_word(ins: Inst, mapping, labels, pc: int) -> int:
    op = ins.op
    E = isa.encode_instruction

    if op == "const":
        # Materialize via ADD Rd, Rd(=0 assumed)… instead use SUB then ADD imm.
        # The ISA has no LOADI, so CONST lowers to: Rd = 0 + imm  (ADD with imm, rs1=Rd)
        # We rely on the VM treating ADD rd, rs1, imm where rs1 reads current Rd.
        # To make it independent of Rd's prior value, emit SUB Rd,Rd,Rd then ADD imm.
        rd = _phys(mapping, ins.dst)
        # SUB rd, rd, rd  -> 0 ; encoded as register-mode sub of rd by itself.
        # but simpler & deterministic: use a dedicated CONST lowering = XOR-free:
        # ADD rd, ZEROREG, imm   — we reserve no zero reg, so do two steps via VM.
        # Here we emit a single ADDI from a freshly-zeroed rd using SUB self.
        # (two words)
        raise _ConstExpansion(rd, ins.imm)

    if op == "mov":
        rd = _phys(mapping, ins.dst)
        if isinstance(ins.a, Imm):
            raise _ConstExpansion(rd, ins.a.value)
        rs1 = _phys(mapping, ins.a)
        # MOV rd, rs1  ->  ADD rd, rs1, #0
        return E(isa.OP_ADD, rd=rd, rs1=rs1, imm16=0)

    if op in ARITH:
        rd = _phys(mapping, ins.dst)
        rs1 = _phys(mapping, ins.a)
        if isinstance(ins.b, Imm):
            return E(ARITH[op], rd=rd, rs1=rs1, imm16=ins.b.value & 0xFFFF)
        rs2 = _phys(mapping, ins.b)
        return E(ARITH[op], rd=rd, rs1=rs1, rs2=isa.ADDR_REGISTER, imm16=rs2)

    if op == "inc":
        return E(isa.OP_INC, rd=_phys(mapping, ins.dst))

    if op == "cmpbr":
        ra = _phys(mapping, ins.a)
        rb = _phys(mapping, ins.b)
        target = labels[ins.label]
        offset = (target - pc) & 0xFFFF
        return E(isa.OP_BRANCH, rd=ra, rs1=rb, rs2=COND[ins.cond], imm16=offset)

    if op == "jmp":
        return E(isa.OP_JUMP, imm16=labels[ins.label])

    if op == "jmptab":
        raise ValueError("jmptab requires lower_to_bytecode_safe (multi-word expansion)")

    if op == "call":
        return E(isa.OP_CALL, imm16=labels[ins.label])

    if op == "ret":
        return E(isa.OP_RETURN)

    if op == "load":
        return E(isa.OP_LOAD, rd=_phys(mapping, ins.dst), imm16=ins.imm)

    if op == "save":
        return E(isa.OP_SAVE, rs1=_phys(mapping, ins.a), imm16=ins.imm)

    if op == "pipe":
        return E(isa.OP_PIPE, rs1=_phys(mapping, ins.a), imm16=ins.imm)

    if op == "dsp":
        rd = _phys(mapping, ins.dst)
        rs1 = _phys(mapping, ins.a) if isinstance(ins.a, VReg) else 0
        imm = ins.b.value if isinstance(ins.b, Imm) else (_phys(mapping, ins.b) if isinstance(ins.b, VReg) else 0)
        return E(isa.OP_DSP, rd=rd, rs1=rs1, rs2=ins.imm, imm16=imm & 0xFFFF)

    if op == "wait":
        if isinstance(ins.a, VReg):
            return E(isa.OP_WAIT, rs1=_phys(mapping, ins.a), rs2=isa.ADDR_REGISTER)
        return E(isa.OP_WAIT)

    if op == "raise":
        return E(isa.OP_RAISE, imm16=ins.imm)

    if op == "net":
        kind = ins.method
        if kind == "status":
            return E(isa.OP_NOOP, imm16=NET_STATUS_BASE | (ins.imm & 0x0FFF))
        if kind == "type":
            return E(isa.OP_NOOP, imm16=CONTENT_TYPES.get(ins.text, 0xA000))
        if kind == "header":
            return E(isa.OP_NOOP, imm16=NET_HEADER_BASE)
        if kind == "body":
            return E(isa.OP_NOOP, imm16=NET_BODY_MARKER)
        if kind == "close":
            return E(isa.OP_NOOP, imm16=NET_CLOSE_MARKER)
        raise ValueError(f"unknown net kind {kind}")

    if op == "host":
        hook = HOST_HOOK_CODES.get((ins.ns, ins.method))
        if hook is None:
            raise ValueError(f"unknown host hook {ins.ns}.{ins.method}")
        imm16 = (HOST_HOOK_BASE | hook) if hook <= 0xFF else (EXT_HOST_HOOK_BASE | (hook & 0x0FFF))
        rd = _phys(mapping, ins.dst) if isinstance(ins.dst, VReg) else 0
        rs1 = _phys(mapping, ins.args[0]) if len(ins.args) >= 1 and isinstance(ins.args[0], VReg) else 0
        rs2 = _phys(mapping, ins.args[1]) if len(ins.args) >= 2 and isinstance(ins.args[1], VReg) else 0
        return E(isa.OP_NOOP, rd=rd, rs1=rs1, rs2=rs2, imm16=imm16)

    raise ValueError(f"cannot lower IL op {op!r}")


class _ConstExpansion(Exception):
    """Signals that a CONST/MOV-imm needs a 2-word expansion (no LOADI in ISA)."""
    def __init__(self, rd: int, value: int):
        self.rd = rd
        self.value = value


def lower_to_bytecode_safe(insts: List[Inst], opt: bool = True) -> List[int]:
    """Like lower_to_bytecode but expands CONST/MOV-imm into 2 words
    (SUB rd,rd,rd ; ADD rd,rd,#imm) so a register can be set to any constant
    without a LOADI opcode.  This recomputes label PCs accounting for expansion."""
    if opt:
        insts = optimize(insts)
    mapping = allocate(insts)

    # Determine which non-label insts expand to 2 words.
    def width(ins: Inst) -> int:
        if ins.op == "label":
            return 0
        if ins.op == "const":
            return 2
        if ins.op == "mov" and isinstance(ins.a, Imm):
            return 2
        if ins.op == "jmptab":
            return len(ins.targets) + 1     # 1 computed jump + N inline table entries
        return 1

    labels: Dict[str, int] = {}
    pc = 0
    for ins in insts:
        if ins.op == "label":
            labels[ins.label] = pc
        else:
            pc += width(ins)

    words: List[int] = []
    pc = 0
    E = isa.encode_instruction
    for ins in insts:
        if ins.op == "label":
            continue
        if ins.op == "const" or (ins.op == "mov" and isinstance(ins.a, Imm)):
            rd = _phys(mapping, ins.dst)
            value = ins.imm if ins.op == "const" else ins.a.value
            words.append(E(isa.OP_SUB, rd=rd, rs1=rd, rs2=isa.ADDR_REGISTER, imm16=rd))  # rd = rd - rd = 0
            words.append(E(isa.OP_ADD, rd=rd, rs1=rd, imm16=value & 0xFFFF))             # rd = 0 + imm
            pc += 2
            continue
        if ins.op == "jmptab":
            sel = _phys(mapping, ins.a)
            # computed jump: PC = sel + (table base); table starts at the next word.
            words.append(E(isa.OP_JUMP, rs1=sel, rs2=isa.ADDR_REG_OFF, imm16=(pc + 1) & 0xFFFF))
            for tgt in ins.targets:                       # inline table: one absolute JUMP per case
                words.append(E(isa.OP_JUMP, imm16=labels[tgt]))
            pc += len(ins.targets) + 1
            continue
        try:
            words.append(_emit_word(ins, mapping, labels, pc))
        except _ConstExpansion as ce:
            words.append(E(isa.OP_SUB, rd=ce.rd, rs1=ce.rd, rs2=isa.ADDR_REGISTER, imm16=ce.rd))
            words.append(E(isa.OP_ADD, rd=ce.rd, rs1=ce.rd, imm16=ce.value & 0xFFFF))
            pc += 2
            continue
        pc += 1
    return words


# ═══════════════════════════════════════════════════════════════════════════
# Backend 2: lower to portable C (toC -- native Thumb / AArch64 via host cc)
# ═══════════════════════════════════════════════════════════════════════════

def _c_ident(label: str) -> str:
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in label)


def lower_to_c(insts: List[Inst], func_name: str = "pico_main", opt: bool = True,
               emit_main: bool = False) -> str:
    """Emit portable C implementing the IL using int64 storage and the picovm.h
    host ABI.  The native toolchain lowers this to Thumb / AArch64.

    Subroutines (OP_CALL/GOSUB) are emitted as separate C functions; pinned
    variables become module-level statics so they persist across calls exactly
    like the shared register file does on the bytecode target.  Local labels
    become C labels; cmpbr becomes `if (...) goto Lx;`.

    If `emit_main` is set, a `main()` is appended that runs `func_name` and prints
    STATUS/OUT in the same format as vm/picovm_run.c (for host validation).
    """
    if opt:
        insts = optimize(insts)

    module = func_name
    call_targets = {ins.label for ins in insts if ins.op == "call"}

    # Split the linear IL into functions at call-target labels.
    functions: List[Tuple[str, List[Inst]]] = []
    cur_name = module
    cur: List[Inst] = []
    for ins in insts:
        if ins.op == "label" and ins.label in call_targets:
            functions.append((cur_name, cur))
            cur_name = f"{module}__{_c_ident(ins.label)}"
            cur = []
        else:
            cur.append(ins)
    functions.append((cur_name, cur))

    label_to_func = {lbl: f"{module}__{_c_ident(lbl)}" for lbl in call_targets}

    # Pinned vregs are shared global state; temporaries are function-local.
    pinned_ids: Dict[int, VReg] = {}
    for ins in insts:
        for v in _operand_vregs(ins):
            if v.pinned:
                pinned_ids.setdefault(v.id, v)

    def name_of(v: VReg) -> str:
        return f"g{v.id}" if v.pinned else f"v{v.id}"

    def opnd(x: Operand) -> str:
        return str(x.value) if isinstance(x, Imm) else name_of(x)

    out: List[str] = []
    out.append('#include "picovm.h"')
    out.append("")
    if pinned_ids:
        for vid, v in sorted(pinned_ids.items()):
            out.append(f"static int64_t g{vid} = 0;   /* {v.name} */")
        out.append("")
    # forward declarations
    for fname, _seg in functions:
        out.append(f"int64_t {fname}(pv_ctx *ctx);")
    out.append("")

    for fname, seg in functions:
        is_main = (fname == module)
        local_ids: Dict[int, VReg] = {}
        for ins in seg:
            for v in _operand_vregs(ins):
                if not v.pinned:
                    local_ids.setdefault(v.id, v)
        out.append(f"int64_t {fname}(pv_ctx *ctx) {{")
        if local_ids:
            decls = ", ".join(f"v{vid} = 0" for vid in sorted(local_ids))
            out.append(f"    int64_t {decls};")
        out.append("    (void)ctx;")
        for ins in seg:
            out.append(_emit_c(ins, opnd, name_of, label_to_func, is_main))
        out.append("    return ctx->retval;")
        out.append("}")
        out.append("")

    if emit_main:
        out.append("#include <stdio.h>")
        out.append("int main(void) {")
        out.append("    pv_ctx ctx; pv_init(&ctx);")
        out.append(f"    {module}(&ctx);")
        out.append('    printf("STEPS %ld\\n", ctx.steps);')
        out.append('    printf("STATUS %d\\n", ctx.http_status);')
        out.append('    printf("REGS");')
        out.append('    for (int i = 0; i < PV_NUM_REGS; i++) printf(" %d", ctx.regs[i]);')
        out.append('    printf("\\n");')
        out.append('    printf("OUT");')
        out.append('    for (int i = 0; i < ctx.out_len; i++) printf(" %02x", ctx.out[i]);')
        out.append('    printf("\\n");')
        out.append("    return 0;")
        out.append("}")
    return "\n".join(out)


def _emit_c(ins: Inst, opnd, name_of, label_to_func, is_main: bool) -> str:
    op = ins.op
    if op == "label":
        return f"{_c_ident(ins.label)}:;"
    if op == "const":
        return f"    {name_of(ins.dst)} = {ins.imm};"
    if op == "mov":
        return f"    {name_of(ins.dst)} = {opnd(ins.a)};"
    if op in ARITH:
        sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[op]
        if op == "div":
            return (f"    {name_of(ins.dst)} = ({opnd(ins.b)} != 0) ? "
                    f"({opnd(ins.a)} / {opnd(ins.b)}) : 0;")
        return f"    {name_of(ins.dst)} = {opnd(ins.a)} {sym} {opnd(ins.b)};"
    if op == "inc":
        return f"    {name_of(ins.dst)} += 1;"
    if op == "cmpbr":
        csym = {"EQ": "==", "NE": "!=", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}
        tgt = _c_ident(ins.label)
        if ins.cond in csym:
            return f"    if ({opnd(ins.a)} {csym[ins.cond]} {opnd(ins.b)}) goto {tgt};"
        if ins.cond == "Z":
            return f"    if ({opnd(ins.a)} == 0) goto {tgt};"
        if ins.cond == "NZ":
            return f"    if ({opnd(ins.a)} != 0) goto {tgt};"
        return f"    if (pv_cond(ctx, {COND[ins.cond]})) goto {tgt};"
    if op == "jmp":
        return f"    goto {_c_ident(ins.label)};"
    if op == "jmptab":
        sel = opnd(ins.a)
        arms = "".join(f" case {k}: goto {_c_ident(t)};" for k, t in enumerate(ins.targets))
        return f"    switch ((int)({sel})) {{{arms} default: goto {_c_ident(ins.label)}; }}"
    if op == "call":
        return f"    {label_to_func[ins.label]}(ctx);"
    if op == "ret":
        return "    return ctx->retval;"
    if op == "host":
        dst = f"{name_of(ins.dst)} = " if isinstance(ins.dst, VReg) else ""
        a = opnd(ins.args[0]) if len(ins.args) >= 1 else "0"
        b = opnd(ins.args[1]) if len(ins.args) >= 2 else "0"
        if ins.ns == "Bits" and isinstance(ins.dst, VReg):
            da = f"(uint32_t)({a})"
            db = f"(uint32_t)({b})"
            sh = f"({db} & 31)"
            if ins.method == "And":
                expr = f"(int64_t)(int32_t)(({da} & {db}) & 0xFFFFFFFFu)"
            elif ins.method == "Or":
                expr = f"(int64_t)(int32_t)(({da} | {db}) & 0xFFFFFFFFu)"
            elif ins.method == "Xor":
                expr = f"(int64_t)(int32_t)(({da} ^ {db}) & 0xFFFFFFFFu)"
            elif ins.method == "Not":
                expr = f"(int64_t)(int32_t)((~{da}) & 0xFFFFFFFFu)"
            elif ins.method == "Shl":
                expr = f"(int64_t)(int32_t)(({da} << {sh}) & 0xFFFFFFFFu)"
            elif ins.method == "Shr":
                expr = f"(int64_t)(int32_t)({da} >> {sh})"
            elif ins.method == "Sar":
                expr = f"(int64_t)((int32_t){da} >> {sh})"
            else:
                expr = None
            if expr is not None:
                return f"    {name_of(ins.dst)} = {expr};"
        if ins.ns == "Memory" and ins.method == "Get" and isinstance(ins.dst, VReg):
            return f"    {name_of(ins.dst)} = pv_mem_get(ctx, (uint32_t)({a}));"
        if ins.ns == "Memory" and ins.method == "Set":
            return f"    pv_mem_set(ctx, (uint32_t)({a}), (int32_t)({b}));"
        if ins.ns == "Io" and ins.method == "WriteByte":
            return f"    pv_io_write(ctx, (int32_t)({a}));"
        if ins.ns == "Dot8" and ins.method == "Len":
            return f"    pv_dot8_setlen(ctx, (int)({a}));"
        if ins.ns == "Dot8" and ins.method == "Of" and isinstance(ins.dst, VReg):
            return f"    {name_of(ins.dst)} = pv_dot8(ctx, (uint32_t)({a}), (uint32_t)({b}));"
        return f"    {dst}pv_host(ctx, \"{ins.ns}\", \"{ins.method}\", {a}, {b});"
    if op == "load":
        return f"    {name_of(ins.dst)} = pv_load(ctx, {ins.imm});"
    if op == "save":
        return f"    pv_save(ctx, {ins.imm}, {opnd(ins.a)});"
    if op == "pipe":
        return f"    pv_pipe(ctx, {ins.imm}, {opnd(ins.a)});"
    if op == "net":
        if ins.method == "status":
            return f"    pv_net_status(ctx, {ins.imm});"
        if ins.method == "type":
            return f"    pv_net_type(ctx, \"{ins.text}\");"
        if ins.method == "body":
            return "    pv_net_body(ctx);"
        if ins.method == "close":
            return "    pv_net_close(ctx);"
        if ins.method == "header":
            return "    pv_net_header(ctx);"
    if op == "dsp":
        a = opnd(ins.a) if isinstance(ins.a, VReg) else "0"
        b = opnd(ins.b) if ins.b is not None else "0"
        return f"    {name_of(ins.dst)} = pv_dsp(ctx, {ins.imm}, {a}, {b});"
    if op == "wait":
        return "    pv_wait(ctx);"
    if op == "raise":
        return f"    pv_raise(ctx, {ins.imm});"
    return f"    /* unhandled IL op {op} */"


# ═══════════════════════════════════════════════════════════════════════════
# Backend 3: lower to JavaScript (toJS -- compile + debug in the browser)
# ═══════════════════════════════════════════════════════════════════════════

def lower_to_js(insts: List[Inst], module_name: str = "pico", opt: bool = True) -> str:
    """Emit a self-contained JavaScript module implementing the IL.

    JS has no `goto`, so each routine is lowered to a basic-block state machine
    (`while(true) switch(_b){...}`); subroutines (OP_CALL/GOSUB) become real JS
    functions and pinned variables become closure-scope globals.  int32 semantics
    are preserved with `|0` / `Math.imul` so output matches the bytecode VM.

    The emitted module exposes `{ run(rt?), main, makeRuntime }` and works in both
    Node (`module.exports`) and the browser (`root.<module>Program`).
    """
    if opt:
        insts = optimize(insts)

    call_targets = {ins.label for ins in insts if ins.op == "call"}
    # split into functions at call-target labels (entry = main)
    functions: List[Tuple[str, List[Inst]]] = []
    cur_name = module_name
    cur: List[Inst] = []
    for ins in insts:
        if ins.op == "label" and ins.label in call_targets:
            functions.append((cur_name, cur))
            cur_name = f"{module_name}__{_c_ident(ins.label)}"
            cur = []
        else:
            cur.append(ins)
    functions.append((cur_name, cur))
    label_to_func = {lbl: f"{module_name}__{_c_ident(lbl)}" for lbl in call_targets}

    pinned_ids: Dict[int, VReg] = {}
    for ins in insts:
        for v in _operand_vregs(ins):
            if v.pinned:
                pinned_ids.setdefault(v.id, v)

    def jname(v: VReg) -> str:
        return f"g{v.id}" if v.pinned else f"v{v.id}"

    def jop(x: Operand) -> str:
        return str(x.value) if isinstance(x, Imm) else jname(x)

    out: List[str] = []
    out.append("// AUTO-GENERATED from PicoScript IL (toJS backend).")
    out.append("(function (root) {")
    out.append("  'use strict';")
    out.append("  function makeRuntime() {")
    out.append("    return {")
    out.append("      cards: {}, output: [], httpStatus: -1, httpType: null, closed: false,")
    out.append("      load: function (a) { return this.cards[a] | 0; },")
    out.append("      save: function (a, v) { this.cards[a] = v | 0; },")
    out.append("      pipe: function (a, v) { var x = v >>> 0; this.output.push((x>>>24)&255,(x>>>16)&255,(x>>>8)&255,x&255); },")
    out.append("      netStatus: function (c) { this.httpStatus = c & 0xFFF; },")
    out.append("      netType: function (t) { this.httpType = t; }, netBody: function () {}, netHeader: function () {},")
    out.append("      netClose: function () { this.closed = true; },")
    out.append("      host: function (ns, m, a, b) { return 0; },")
    out.append("      mem: new Uint8Array(520 * 1024), dotLen: 0,")
    out.append("      memGet: function (a) { return this.mem[(a >>> 0) % this.mem.length]; },")
    out.append("      memSet: function (a, v) { this.mem[(a >>> 0) % this.mem.length] = v & 255; },")
    out.append("      ioWrite: function (b) { this.output.push(b & 255); },")
    out.append("      dotLenSet: function (n) { this.dotLen = n >>> 0; },")
    out.append("      dot8: function (w, a) { var n=this.dotLen|0, sz=this.mem.length, s=0, i=0; for(;i<n;i++){var x=this.mem[(w+i)%sz]; if(x>127)x-=256; var y=this.mem[(a+i)%sz]; if(y>127)y-=256; s=(s+x*y)|0;} return s; },")
    out.append("      dsp: function (s, a, b) { return s===4?(a<0?0:a):(s===3?Math.imul(a,b):(s===9?(a+b|0):0)); },")
    out.append("      outputInts: function () { var o=[]; for (var i=0;i+3<this.output.length;i+=4){o.push(((this.output[i]<<24)|(this.output[i+1]<<16)|(this.output[i+2]<<8)|this.output[i+3])|0);} return o; }")
    out.append("    };")
    out.append("  }")
    out.append("")
    if pinned_ids:
        gdecl = ", ".join(f"g{vid} = 0" for vid in sorted(pinned_ids))
        out.append(f"  var {gdecl};")
        out.append("")

    for fname, seg in functions:
        out.extend(_emit_js_function(fname, seg, jname, jop, label_to_func, pinned_ids))
        out.append("")

    out.append(f"  function _reset() {{ {'; '.join(f'g{vid} = 0' for vid in sorted(pinned_ids)) or '/* no globals */'}; }}")
    out.append("  var api = {")
    out.append(f"    main: {module_name},")
    out.append("    makeRuntime: makeRuntime,")
    out.append(f"    run: function (rt) {{ _reset(); rt = rt || makeRuntime(); {module_name}(rt); return rt; }}")
    out.append("  };")
    out.append("  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }")
    out.append(f"  else {{ root.{module_name}Program = api; }}")
    out.append("})(typeof globalThis !== 'undefined' ? globalThis : this);")
    return "\n".join(out)


def _emit_js_function(fname, seg, jname, jop, label_to_func, pinned_ids) -> List[str]:
    # local (temp) vregs declared per function
    local_ids: Dict[int, VReg] = {}
    for ins in seg:
        for v in _operand_vregs(ins):
            if not v.pinned:
                local_ids.setdefault(v.id, v)

    # basic blocks: a new block starts at each label; block 0 is the entry.
    label_block: Dict[str, int] = {}
    blocks: List[List[Inst]] = [[]]
    for ins in seg:
        if ins.op == "label":
            label_block[ins.label] = len(blocks)
            blocks.append([])
        else:
            blocks[-1].append(ins)

    L: List[str] = []
    L.append(f"  function {fname}(rt) {{")
    if local_ids:
        L.append("    var " + ", ".join(f"v{vid} = 0" for vid in sorted(local_ids)) + ";")
    L.append("    var _b = 0;")
    L.append("    while (_b >= 0) {")
    L.append("      switch (_b) {")
    for bi, block in enumerate(blocks):
        L.append(f"        case {bi}:")
        terminated = False
        for ins in block:
            line, term = _emit_js_inst(ins, jop, jname, label_block, label_to_func)
            L.append("          " + line)
            if term:
                terminated = True
                break
        if not terminated:
            nxt = bi + 1
            if nxt < len(blocks):
                L.append(f"          _b = {nxt}; continue;")
            else:
                L.append("          _b = -1; continue;")
    L.append("      }")
    L.append("    }")
    L.append("    return rt;")
    L.append("  }")
    return L


def _emit_js_inst(ins: Inst, jop, jname, label_block, label_to_func) -> Tuple[str, bool]:
    """Return (js_source, is_terminator)."""
    op = ins.op
    if op == "const":
        return f"{jname(ins.dst)} = {ins.imm};", False
    if op == "mov":
        return f"{jname(ins.dst)} = {jop(ins.a)};", False
    if op in ARITH:
        a, b = jop(ins.a), jop(ins.b)
        if op == "add":
            return f"{jname(ins.dst)} = ({a} + {b}) | 0;", False
        if op == "sub":
            return f"{jname(ins.dst)} = ({a} - {b}) | 0;", False
        if op == "mul":
            return f"{jname(ins.dst)} = Math.imul({a}, {b});", False
        return f"{jname(ins.dst)} = ({b} !== 0 ? (({a} / {b}) | 0) : 0);", False
    if op == "inc":
        return f"{jname(ins.dst)} = ({jname(ins.dst)} + 1) | 0;", False
    if op == "cmpbr":
        csym = {"EQ": "===", "NE": "!==", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}
        tgt = label_block[ins.label]
        a, b = jop(ins.a), jop(ins.b)
        if ins.cond in csym:
            cond = f"({a} {csym[ins.cond]} {b})"
        elif ins.cond == "Z":
            cond = f"({a} === 0)"
        elif ins.cond == "NZ":
            cond = f"({a} !== 0)"
        else:
            cond = "false"
        return f"if ({cond}) {{ _b = {tgt}; continue; }}", False
    if op == "jmp":
        return f"_b = {label_block[ins.label]}; continue;", True
    if op == "call":
        return f"{label_to_func[ins.label]}(rt);", False
    if op == "ret":
        return "return rt;", True
    if op == "host":
        dst = f"{jname(ins.dst)} = " if isinstance(ins.dst, VReg) else ""
        a = jop(ins.args[0]) if len(ins.args) >= 1 else "0"
        b = jop(ins.args[1]) if len(ins.args) >= 2 else "0"
        if ins.ns == "Bits" and isinstance(ins.dst, VReg):
            if ins.method == "And":
                expr = f"(({a} & {b}) | 0)"
            elif ins.method == "Or":
                expr = f"(({a} | {b}) | 0)"
            elif ins.method == "Xor":
                expr = f"(({a} ^ {b}) | 0)"
            elif ins.method == "Not":
                expr = f"((~{a}) | 0)"
            elif ins.method == "Shl":
                expr = f"(({a} << ({b} & 31)) | 0)"
            elif ins.method == "Shr":
                expr = f"(({a} >>> ({b} & 31)) | 0)"
            elif ins.method == "Sar":
                expr = f"(({a} >> ({b} & 31)) | 0)"
            else:
                expr = None
            if expr is not None:
                return f"{jname(ins.dst)} = {expr};", False
        if ins.ns == "Memory" and ins.method == "Get" and isinstance(ins.dst, VReg):
            return f"{jname(ins.dst)} = rt.memGet({a});", False
        if ins.ns == "Memory" and ins.method == "Set":
            return f"rt.memSet({a}, {b});", False
        if ins.ns == "Io" and ins.method == "WriteByte":
            return f"rt.ioWrite({a});", False
        if ins.ns == "Dot8" and ins.method == "Len":
            return f"rt.dotLenSet({a});", False
        if ins.ns == "Dot8" and ins.method == "Of" and isinstance(ins.dst, VReg):
            return f"{jname(ins.dst)} = rt.dot8({a}, {b});", False
        return f'{dst}rt.host("{ins.ns}", "{ins.method}", {a}, {b});', False
    if op == "load":
        return f"{jname(ins.dst)} = rt.load({ins.imm});", False
    if op == "save":
        return f"rt.save({ins.imm}, {jop(ins.a)});", False
    if op == "pipe":
        return f"rt.pipe({ins.imm}, {jop(ins.a)});", False
    if op == "net":
        if ins.method == "status":
            return f"rt.netStatus({ins.imm});", False
        if ins.method == "type":
            return f'rt.netType("{ins.text}");', False
        if ins.method == "body":
            return "rt.netBody();", False
        if ins.method == "close":
            return "rt.netClose(); return rt;", True
        if ins.method == "header":
            return "rt.netHeader();", False
    if op == "dsp":
        a = jop(ins.a) if isinstance(ins.a, VReg) else "0"
        b = jop(ins.b) if ins.b is not None else "0"
        return f"{jname(ins.dst)} = rt.dsp({ins.imm}, {a}, {b});", False
    if op == "wait":
        return "return rt;", True
    if op == "raise":
        return f"/* raise {ins.imm} */", False
    if op == "jmptab":
        sel = jop(ins.a)
        arms = " ".join(f"case {k}: {{ _b = {label_block[t]}; continue; }}"
                        for k, t in enumerate(ins.targets))
        return f"switch (({sel})|0) {{ {arms} default: {{ _b = {label_block[ins.label]}; continue; }} }}", True
    return f"/* unhandled {op} */", False


def il_to_text(insts: List[Inst]) -> str:
    """Human-readable IL dump (for -S / debugging)."""
    return "\n".join(repr(i) for i in insts)
