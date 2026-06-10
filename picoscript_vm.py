#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_vm.py -- PicoVM: reference runtime for the 16-opcode ISA.

Executes the frozen v1 bytecode produced by picoscript_lang.py (v1 source) and by
picoscript_il.lower_to_bytecode_safe (C-syntax & BASIC-like frontends).  This is
the deterministic interpreter the spec calls "compilation target 1" -- the same
ISA the portable C VM (vm/picovm.c) implements for bare metal.

Decode (matches picoscript.decode_instruction):

    [31:28] opcode   [27:24] rd   [23:20] rs1   [19:16] rs2/mode   [15:0] imm16

Host model: the VM owns 16 registers, a card store (dict addr16 -> int), a call
stack, an output buffer (Net.* / PIPE), and dispatches host hooks to a HostApi.
A deterministic step budget bounds execution (spec sec 11, L0).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import picoscript as isa
from picoscript_lang import (
    HOST_HOOK_BASE,
    HOST_HOOK_CODES,
    NET_STATUS_BASE,
    NET_HEADER_BASE,
    NET_BODY_MARKER,
    NET_CLOSE_MARKER,
    CONTENT_TYPES,
)

# Reverse host-hook table: hook code -> (namespace, method)
_HOOK_BY_CODE: Dict[int, tuple] = {code: key for key, code in HOST_HOOK_CODES.items()}
_CT_BY_VALUE: Dict[int, str] = {v: k for k, v in CONTENT_TYPES.items()}

MASK32 = 0xFFFFFFFF


def _sx16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _sx32(v: int) -> int:
    v &= MASK32
    return v - 0x100000000 if v & 0x80000000 else v


class HostApi:
    """Default host-hook implementation.

    Override or register handlers to model Storage/Queue/Random/Memory/etc.
    Each handler receives (vm, rd, rs1, rs2, imm16) and may read/write vm.regs.
    The default behaviour is deterministic and side-effect-light so tests are
    reproducible.
    """

    def __init__(self):
        self.queues: Dict[int, List[int]] = {}
        self.rng_state = 0x2545F4914F6CDD1D
        self.log: List[str] = []
        self.handlers: Dict[tuple, Callable] = {}
        # Card store (PicoStore) + program-level Storage.* context.
        self._store = None
        self.cur_pack = 0
        self.cur_card = 0
        self.query_results: List[int] = []

    @property
    def store(self):
        if self._store is None:
            from picostore import PicoStore  # lazy: optional dependency
            self._store = PicoStore()
        return self._store

    def register(self, ns: str, method: str, fn: Callable):
        self.handlers[(ns, method)] = fn

    def call(self, vm: "PicoVM", ns: str, method: str, rd, rs1, rs2, imm16):
        fn = self.handlers.get((ns, method))
        if fn is not None:
            return fn(vm, rd, rs1, rs2, imm16)
        # Built-in defaults for a few common hooks.
        if ns == "Random" and method == "U32":
            x = self.rng_state
            x ^= (x << 13) & MASK32
            x ^= (x >> 7)
            x ^= (x << 17) & MASK32
            self.rng_state = x & 0xFFFFFFFFFFFFFFFF
            vm.regs[rd] = x & MASK32
            return
        if ns == "Queue" and method == "Enqueue":
            self.queues.setdefault(rs1, []).append(vm.regs[rd])
            return
        if ns == "Queue" and method == "Dequeue":
            q = self.queues.get(rs1, [])
            vm.regs[rd] = q.pop(0) if q else 0
            return
        if ns == "Queue" and method == "Depth":
            vm.regs[rd] = len(self.queues.get(rs1, []))
            return
        # Memory + span / slice / materialize.
        if ns == "Memory" and method == "Set":
            vm.mem[vm.regs[rs1] & 0xFFFF] = vm.regs[rs2] & 0xFF
            return
        if ns == "Memory" and method == "Get":
            vm.regs[rd] = vm.mem[vm.regs[rs1] & 0xFFFF]
            return
        if ns == "Span" and method == "Make":
            vm.spans.append({"ptr": vm.regs[rs1] & 0xFFFF, "len": max(0, _sx32(vm.regs[rs2]))})
            vm.regs[rd] = len(vm.spans) - 1
            return
        if ns == "Span" and method == "Slice":          # zero-copy sub-span VIEW
            s = vm.spans[vm.regs[rs1]] if vm.regs[rs1] < len(vm.spans) else {"ptr": 0, "len": 0}
            off = max(0, min(_sx32(vm.regs[rs2]), s["len"]))
            vm.spans.append({"ptr": s["ptr"] + off, "len": s["len"] - off})
            vm.regs[rd] = len(vm.spans) - 1
            return
        if ns == "Span" and method == "Materialize":     # memcpy to new region (COPY)
            s = vm.spans[vm.regs[rs1]] if vm.regs[rs1] < len(vm.spans) else {"ptr": 0, "len": 0}
            dst = vm.arena_top
            vm.arena_top += s["len"]
            vm.mem[dst:dst + s["len"]] = vm.mem[s["ptr"]:s["ptr"] + s["len"]]
            vm.spans.append({"ptr": dst, "len": s["len"]})
            vm.regs[rd] = len(vm.spans) - 1
            return
        if ns == "Span" and method == "Len":
            s = vm.spans[vm.regs[rs1]] if vm.regs[rs1] < len(vm.spans) else None
            vm.regs[rd] = s["len"] if s else 0
            return
        if ns == "Span" and method == "Get":
            s = vm.spans[vm.regs[rs1]] if vm.regs[rs1] < len(vm.spans) else {"ptr": 0, "len": 0}
            idx = _sx32(vm.regs[rs2])
            vm.regs[rd] = vm.mem[s["ptr"] + idx] if 0 <= idx < s["len"] else 0
            return
        # Program-level card store: Storage.* over a PicoStore (text via byte-spans).
        if ns == "Storage":
            if self._storage(vm, method, rd, rs1, rs2):
                return
        # Unknown hook: record and continue (host-fillable primitive).
        self.log.append(f"host {ns}.{method} rd=R{rd} rs1=R{rs1} rs2=R{rs2} imm={imm16:#06x}")

    # -- Storage.* card-store helpers ---------------------------------------
    def _span_str(self, vm: "PicoVM", handle: int) -> str:
        """Decode a span (handle in rs) as a UTF-8 string from the VM arena."""
        if handle <= 0 or handle >= len(vm.spans):
            return ""
        s = vm.spans[handle]
        if not s:
            return ""
        return bytes(vm.mem[s["ptr"]:s["ptr"] + s["len"]]).decode("utf-8", "replace")

    def _str_span(self, vm: "PicoVM", text: str) -> int:
        """Write a UTF-8 string into the arena and return a new span handle."""
        b = text.encode("utf-8")
        dst = vm.arena_top
        vm.mem[dst:dst + len(b)] = b
        vm.arena_top += len(b)
        vm.spans.append({"ptr": dst, "len": len(b)})
        return len(vm.spans) - 1

    def _storage(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        """Execute a Storage.* card op. Returns True if handled.

        Context model keeps every op within the 2-in/1-out host ABI: UsePack
        selects the pack, AddCard/EditCard select the current card, then field
        ops read/write it. Field names and queries are UTF-8 byte-spans the
        program builds in arena memory (Memory.Set + Span.Make); ids/values are
        plain integers. Cards are dict records held in a PicoStore.
        """
        pack = str(self.cur_pack)
        if method == "UsePack":
            self.cur_pack = vm.regs[rs1] & MASK32
            vm.regs[rd] = self.cur_pack
            return True
        if method == "AddCard":
            cid = self.store.create(pack, {})
            self.cur_card = cid
            vm.regs[rd] = cid
            return True
        if method == "EditCard":
            cid = vm.regs[rs1] & MASK32
            ok = self.store.read(pack, cid) is not None
            self.cur_card = cid if ok else 0
            vm.regs[rd] = cid if ok else 0
            return True
        if method == "DeleteCard":
            cid = vm.regs[rs1] & MASK32
            ok = self.store.delete(pack, cid)
            if cid == self.cur_card:
                self.cur_card = 0
            vm.regs[rd] = 1 if ok else 0
            return True
        if method == "GetField":
            rec = self.store.read(pack, self.cur_card) or {}
            v = rec.get(self._span_str(vm, vm.regs[rs1]), 0)
            vm.regs[rd] = (int(v) if isinstance(v, (int, bool)) else 0) & MASK32
            return True
        if method == "SetField":
            name = self._span_str(vm, vm.regs[rs1])
            rec = self.store.read(pack, self.cur_card)
            if rec is None:
                vm.regs[rd] = 0
                return True
            rec[name] = _sx32(vm.regs[rs2])
            vm.regs[rd] = 1 if self.store.update(pack, self.cur_card, rec) else 0
            return True
        if method == "SetFieldStr":
            name = self._span_str(vm, vm.regs[rs1])
            rec = self.store.read(pack, self.cur_card)
            if rec is None:
                vm.regs[rd] = 0
                return True
            rec[name] = self._span_str(vm, vm.regs[rs2])
            vm.regs[rd] = 1 if self.store.update(pack, self.cur_card, rec) else 0
            return True
        if method == "GetFieldStr":
            rec = self.store.read(pack, self.cur_card) or {}
            v = rec.get(self._span_str(vm, vm.regs[rs1]), "")
            vm.regs[rd] = self._str_span(vm, v if isinstance(v, str) else str(v))
            return True
        if method == "QueryCard":
            q = self._span_str(vm, vm.regs[rs1])
            self.query_results = [cid for cid, _ in self.store.query(pack, q)]
            vm.regs[rd] = len(self.query_results)
            return True
        if method == "QueryResult":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self.query_results[idx] if 0 <= idx < len(self.query_results) else 0
            return True
        return False


class Halt(Exception):
    pass


class PicoVM:
    """Deterministic interpreter for the 16-opcode PicoScript ISA."""

    def __init__(self, host: Optional[HostApi] = None, max_steps: int = 1_000_000):
        self.regs: List[int] = [0] * isa_num_regs()
        self.cards: Dict[int, int] = {}
        self.call_stack: List[int] = []
        self.output: List[bytes] = []        # PIPE / Net.Body payloads
        self.http_status: Optional[int] = None
        self.http_type: Optional[str] = None
        self.mem = bytearray(65536)          # process arena (byte-addressable)
        self.arena_top = 0x8000              # bump pointer for Span.Materialize copies
        self.spans: List[Optional[dict]] = [None]   # span table; handle = 1-based index
        self.host = host or HostApi()
        self.max_steps = max_steps
        self.steps = 0
        self.pc = 0
        self.halted = False
        self.waiting = False
        self.retval = 0

    # -- public API ------------------------------------------------------
    def load(self, words: List[int]):
        self.program = list(words)
        self.pc = 0
        self.halted = False
        self.steps = 0

    def run(self, words: Optional[List[int]] = None) -> "PicoVM":
        if words is not None:
            self.load(words)
        try:
            while not self.halted:
                if self.pc >= len(self.program):
                    break
                if self.steps >= self.max_steps:
                    raise RuntimeError(f"step budget exceeded ({self.max_steps})")
                self.steps += 1
                self._step()
        except Halt:
            self.halted = True
        return self

    # -- core ------------------------------------------------------------
    def _step(self):
        word = self.program[self.pc]
        d = isa.decode_instruction(word)
        op, rd, rs1, rs2, imm16 = d["opcode"], d["rd"], d["rs1"], d["rs2"], d["imm16"]
        cur = self.pc
        self.pc += 1

        if op == isa.OP_NOOP:
            self._noop(rd, rs1, rs2, imm16)
        elif op == isa.OP_LOAD:
            self.regs[rd] = self.cards.get(imm16, 0)
        elif op == isa.OP_SAVE:
            self.cards[imm16] = self.regs[rs1] & MASK32
        elif op == isa.OP_PIPE:
            self.output.append(self._card_bytes(imm16))
        elif op in (isa.OP_ADD, isa.OP_SUB, isa.OP_MUL, isa.OP_DIV):
            self._arith(op, rd, rs1, rs2, imm16)
        elif op == isa.OP_INC:
            self.regs[rd] = (self.regs[rd] + 1) & MASK32
        elif op == isa.OP_JUMP:
            self.pc = imm16
        elif op == isa.OP_BRANCH:
            if self._cond(rs2, self.regs[rd], self.regs[rs1]):
                self.pc = cur + _sx16(imm16)
        elif op == isa.OP_CALL:
            self.call_stack.append(self.pc)
            self.pc = imm16
        elif op == isa.OP_RETURN:
            if self.call_stack:
                self.pc = self.call_stack.pop()
            else:
                raise Halt()
        elif op == isa.OP_WAIT:
            self.waiting = True
            raise Halt()
        elif op == isa.OP_RAISE:
            self.host.log.append(f"raise swirq channel={imm16}")
        elif op == isa.OP_DSP:
            self._dsp(rd, rs1, rs2, imm16)
        else:
            raise RuntimeError(f"bad opcode {op:#x} at pc={cur}")

    def _arith(self, op, rd, rs1, rs2, imm16):
        a = _sx32(self.regs[rs1])
        if rs2 == isa.ADDR_REGISTER:
            b = _sx32(self.regs[imm16 & 0xF])
        else:
            b = _sx16(imm16)
        if op == isa.OP_ADD:
            r = a + b
        elif op == isa.OP_SUB:
            r = a - b
        elif op == isa.OP_MUL:
            r = a * b
        else:
            r = a // b if b != 0 else 0
        self.regs[rd] = r & MASK32

    def _cond(self, mode, a, b):
        a = _sx32(a); b = _sx32(b)
        if mode == isa.BRANCH_EQ:
            return a == b
        if mode == isa.BRANCH_NE:
            return a != b
        if mode == isa.BRANCH_LT:
            return a < b
        if mode == isa.BRANCH_GT:
            return a > b
        if mode == isa.BRANCH_LE:
            return a <= b
        if mode == isa.BRANCH_GE:
            return a >= b
        if mode == isa.BRANCH_Z:
            return a == 0
        if mode == isa.BRANCH_NZ:
            return a != 0
        if mode == isa.BRANCH_EOF:
            return False
        if mode == isa.BRANCH_ERR:
            return False
        return False

    def _noop(self, rd, rs1, rs2, imm16):
        if (imm16 & 0xFF00) == HOST_HOOK_BASE:
            hook = imm16 & 0x00FF
            key = _HOOK_BY_CODE.get(hook)
            if key is None:
                self.host.log.append(f"unknown host hook {hook:#04x}")
                return
            self.host.call(self, key[0], key[1], rd, rs1, rs2, imm16)
        elif (imm16 & 0xF000) == NET_STATUS_BASE:
            self.http_status = imm16 & 0x0FFF
        elif (imm16 & 0xF000) == 0xA000:
            self.http_type = _CT_BY_VALUE.get(imm16, "application/octet-stream")
        elif imm16 == NET_BODY_MARKER:
            pass
        elif imm16 == NET_CLOSE_MARKER:
            raise Halt()
        elif imm16 == NET_HEADER_BASE:
            pass
        # else: genuine NOOP

    def _dsp(self, rd, rs1, rs2, imm16):
        # Reference DSP: scalars only; vectors live in cards on real hardware.
        a = _sx32(self.regs[rs1])
        if rs2 == isa.DSP_RELU:
            self.regs[rd] = max(0, a) & MASK32
        elif rs2 == isa.DSP_SCALE:
            self.regs[rd] = (a * _sx16(imm16)) & MASK32
        elif rs2 == isa.DSP_VADD:
            self.regs[rd] = (a + _sx32(self.regs[imm16 & 0xF])) & MASK32
        else:
            self.host.log.append(f"dsp subop={rs2:#x} (host-accelerated on hardware)")

    def _card_bytes(self, addr16) -> bytes:
        v = self.cards.get(addr16, 0) & MASK32
        return v.to_bytes(4, "big")

    # -- introspection ---------------------------------------------------
    def reg_dump(self) -> Dict[str, int]:
        return {f"R{i}": self.regs[i] for i in range(len(self.regs))}


def isa_num_regs() -> int:
    return 16


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: run source straight through a frontend (lazy imports to avoid
# circular deps at module import time).
# ═══════════════════════════════════════════════════════════════════════════

def run_v1(source: str, **kw) -> PicoVM:
    from picoscript_lang import Compiler
    words = Compiler().compile(source)
    return PicoVM(**kw).run(words)


def run_words(words: List[int], **kw) -> PicoVM:
    return PicoVM(**kw).run(words)
