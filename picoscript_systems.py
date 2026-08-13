#!/usr/bin/env python3
"""PicoScript fixed two-word systems super-ISA.

The base ISA remains the frozen 16-op, 32-bit machine.  A previously unassigned
NOOP immediate range (0x5xxx) is the escape for a systems instruction; the next
word is always its payload.  Existing hook (0x6/0x7) and HTTP (0x8..0xA)
encodings are therefore unchanged.

Header word:
    31..28  base OP_NOOP / EXT
    27..24  X destination (or store value)
    23..20  X source (or memory base)
    19..16  family
    15..12  0x5 escape marker
    11..4   opcode
     3..0   type/flags

Payload word is an immediate, displacement, target PC, or a register operand
when bit 31 is set.  This intentionally preserves one addressing/fusion intent
until the native backend.
"""

from dataclasses import dataclass

import picoscript as isa
from picoscript_vm import PicoVM, PicoFault, Halt, MASK32

SUPER_MASK = 0xF000
SUPER_TAG = 0x5000
SUPER_REG = 0x80000000
SUPER_REG_MASK = 0xFFFFFFF0
NUM_XREGS = 16

# Families: BLOCK is reserved here; providers/bindings live elsewhere.
F_INT64 = 0x1
F_MEMORY = 0x2
F_POINTER = 0x3
F_FRAME = 0x4
F_ATOMIC = 0x5
F_BLOCK = 0x6
F_SIMD = 0x7
F_TENSOR = 0x8
F_GRAPH = 0x9
F_STORAGE = 0xA

T_I64, T_U64, T_PTR, T_SIZE, T_OFFSET, T_WAL, T_INDEX, T_POSTINGS, T_GRAPH, T_NODE = range(10)

# INT64
S_MOV64 = 0x01
S_MOVI64 = 0x02       # zero/sign-extended 32-bit payload according to type
S_ADD64 = 0x03
S_SUB64 = 0x04
S_MUL64 = 0x05
S_DIV64 = 0x06
S_AND64 = 0x07
S_OR64 = 0x08
S_XOR64 = 0x09
S_SHL64 = 0x0A
S_SHR64 = 0x0B
S_FROM_R32 = 0x0C     # Xdst = zero/sign-extended Rsrc
S_TO_R32 = 0x0D       # Rdst = low32(Xsrc)
S_EQ64, S_NE64, S_LT64, S_GT64, S_LE64, S_GE64 = range(0x20, 0x26)

# MEMORY
S_LOAD8, S_LOAD16, S_LOAD32, S_LOAD64 = 0x01, 0x02, 0x03, 0x04
S_STORE8, S_STORE16, S_STORE32, S_STORE64 = 0x11, 0x12, 0x13, 0x14
S_MEMCPY, S_MEMSET = 0x20, 0x21
S_LOAD_FIELD64, S_STORE_FIELD32 = 0x30, 0x31
S_LOAD_INDEX32, S_STORE_INDEX32 = 0x32, 0x33
S_BOUNDS_LOAD = 0x34

# POINTER / FRAME / ATOMIC.  BLOCK opcodes are deliberately only reserved.
S_LEA, S_PTR_ADD, S_PTR_DIFF, S_PTR_INDEX = 0x01, 0x02, 0x03, 0x04
S_CALL, S_RET = 0x01, 0x02
S_FRAME_ENTER, S_FRAME_LEAVE = 0x10, 0x11
S_LD_LOCAL32, S_ST_LOCAL32 = 0x12, 0x13
S_LD_LOCAL64, S_ST_LOCAL64, S_ADDR_LOCAL = 0x14, 0x15, 0x16
S_ATOMIC_CAS64 = 0x01
S_BLOCK_READ, S_BLOCK_WRITE, S_MAP_RANGE = 0x01, 0x02, 0x03

# STORAGE: provider-backed PicoWAL and derived index operations.
S_WAL_OPEN, S_WAL_PACK = 0x01, 0x02
S_WAL_PUT, S_WAL_CREATE, S_WAL_GET = 0x03, 0x04, 0x05
S_WAL_DELETE, S_WAL_EXISTS, S_WAL_SCAN = 0x06, 0x07, 0x08
S_WAL_SYNC, S_WAL_RECOVER = 0x09, 0x0A
S_INDEX_OPEN, S_INDEX_UPSERT, S_INDEX_DELETE = 0x20, 0x21, 0x22
S_INDEX_EXACT, S_INDEX_REVERSE, S_INDEX_RESULT = 0x23, 0x24, 0x25
S_FTS_OPEN, S_FTS_UPSERT, S_FTS_DELETE = 0x40, 0x41, 0x42
S_FTS_FIND, S_FTS_RESULT = 0x43, 0x44

# GRAPH: weighted adjacency and bounded shortest-path search.
S_GRAPH_OPEN, S_GRAPH_SET_WEIGHT = 0x01, 0x02
S_GRAPH_ADD, S_GRAPH_DELETE, S_GRAPH_WEIGHT = 0x03, 0x04, 0x05
S_GRAPH_OUT, S_GRAPH_IN = 0x06, 0x07
S_GRAPH_RESULT_NODE, S_GRAPH_RESULT_WEIGHT = 0x08, 0x09
S_GRAPH_SHORTEST_PATH = 0x0A

FAMILY_NAMES = {F_INT64: "INT64", F_MEMORY: "MEMORY", F_POINTER: "POINTER",
                F_FRAME: "FRAME", F_ATOMIC: "ATOMIC", F_BLOCK: "BLOCK",
                F_SIMD: "SIMD", F_TENSOR: "TENSOR", F_GRAPH: "GRAPH",
                F_STORAGE: "STORAGE"}
TYPE_NAMES = {T_I64: "I64", T_U64: "U64", T_PTR: "PTR", T_SIZE: "SIZE", T_OFFSET: "OFFSET",
              T_WAL: "WAL", T_INDEX: "INDEX", T_POSTINGS: "POSTINGS",
              T_GRAPH: "GRAPH", T_NODE: "NODE"}
OP_NAMES = {
    (F_INT64, S_MOV64): "MOV64", (F_INT64, S_MOVI64): "MOVI64",
    (F_INT64, S_ADD64): "ADD64", (F_INT64, S_SUB64): "SUB64",
    (F_INT64, S_MUL64): "MUL64", (F_INT64, S_DIV64): "DIV64",
    (F_INT64, S_AND64): "AND64", (F_INT64, S_OR64): "OR64",
    (F_INT64, S_XOR64): "XOR64", (F_INT64, S_SHL64): "SHL64",
    (F_INT64, S_SHR64): "SHR64", (F_INT64, S_FROM_R32): "FROM_R32",
    (F_INT64, S_TO_R32): "TO_R32", (F_INT64, S_EQ64): "EQ64",
    (F_INT64, S_NE64): "NE64", (F_INT64, S_LT64): "LT64",
    (F_INT64, S_GT64): "GT64", (F_INT64, S_LE64): "LE64",
    (F_INT64, S_GE64): "GE64",
    (F_MEMORY, S_LOAD8): "LOAD8", (F_MEMORY, S_LOAD16): "LOAD16",
    (F_MEMORY, S_LOAD32): "LOAD32", (F_MEMORY, S_LOAD64): "LOAD64",
    (F_MEMORY, S_STORE8): "STORE8", (F_MEMORY, S_STORE16): "STORE16",
    (F_MEMORY, S_STORE32): "STORE32", (F_MEMORY, S_STORE64): "STORE64",
    (F_MEMORY, S_MEMCPY): "MEMCPY", (F_MEMORY, S_MEMSET): "MEMSET",
    (F_POINTER, S_LEA): "LEA", (F_POINTER, S_PTR_ADD): "PTR_ADD",
    (F_POINTER, S_PTR_DIFF): "PTR_DIFF", (F_POINTER, S_PTR_INDEX): "PTR_INDEX",
    (F_FRAME, S_CALL): "CALL", (F_FRAME, S_RET): "RET",
    (F_FRAME, S_FRAME_ENTER): "FRAME_ENTER", (F_FRAME, S_FRAME_LEAVE): "FRAME_LEAVE",
    (F_FRAME, S_ADDR_LOCAL): "ADDR_LOCAL",
    (F_BLOCK, S_BLOCK_READ): "BLOCK_READ", (F_BLOCK, S_BLOCK_WRITE): "BLOCK_WRITE",
    (F_BLOCK, S_MAP_RANGE): "MAP_RANGE",
    (F_STORAGE, S_WAL_OPEN): "WAL_OPEN", (F_STORAGE, S_WAL_PACK): "WAL_PACK",
    (F_STORAGE, S_WAL_PUT): "WAL_PUT", (F_STORAGE, S_WAL_CREATE): "WAL_CREATE",
    (F_STORAGE, S_WAL_GET): "WAL_GET", (F_STORAGE, S_WAL_DELETE): "WAL_DELETE",
    (F_STORAGE, S_WAL_EXISTS): "WAL_EXISTS", (F_STORAGE, S_WAL_SCAN): "WAL_SCAN",
    (F_STORAGE, S_WAL_SYNC): "WAL_SYNC", (F_STORAGE, S_WAL_RECOVER): "WAL_RECOVER",
    (F_STORAGE, S_INDEX_OPEN): "INDEX_OPEN", (F_STORAGE, S_INDEX_UPSERT): "INDEX_UPSERT",
    (F_STORAGE, S_INDEX_DELETE): "INDEX_DELETE", (F_STORAGE, S_INDEX_EXACT): "INDEX_EXACT",
    (F_STORAGE, S_INDEX_REVERSE): "INDEX_REVERSE", (F_STORAGE, S_INDEX_RESULT): "INDEX_RESULT",
    (F_STORAGE, S_FTS_OPEN): "FTS_OPEN", (F_STORAGE, S_FTS_UPSERT): "FTS_UPSERT",
    (F_STORAGE, S_FTS_DELETE): "FTS_DELETE", (F_STORAGE, S_FTS_FIND): "FTS_FIND",
    (F_STORAGE, S_FTS_RESULT): "FTS_RESULT",
    (F_GRAPH, S_GRAPH_OPEN): "GRAPH_OPEN", (F_GRAPH, S_GRAPH_SET_WEIGHT): "GRAPH_SET_WEIGHT",
    (F_GRAPH, S_GRAPH_ADD): "GRAPH_ADD", (F_GRAPH, S_GRAPH_DELETE): "GRAPH_DELETE",
    (F_GRAPH, S_GRAPH_WEIGHT): "GRAPH_WEIGHT", (F_GRAPH, S_GRAPH_OUT): "GRAPH_OUT",
    (F_GRAPH, S_GRAPH_IN): "GRAPH_IN", (F_GRAPH, S_GRAPH_RESULT_NODE): "GRAPH_RESULT_NODE",
    (F_GRAPH, S_GRAPH_RESULT_WEIGHT): "GRAPH_RESULT_WEIGHT",
    (F_GRAPH, S_GRAPH_SHORTEST_PATH): "GRAPH_SHORTEST_PATH",
}


@dataclass(frozen=True)
class SuperInstruction:
    family: int
    opcode: int
    dst: int = 0
    src: int = 0
    typecode: int = T_U64
    payload: int = 0


def encode_super(family, opcode, dst=0, src=0, payload=0, typecode=T_U64):
    if not (0 <= family < 16 and 0 <= opcode < 256):
        raise ValueError("super family/opcode out of range")
    if not (0 <= dst < NUM_XREGS and 0 <= src < NUM_XREGS):
        raise ValueError("X register out of range")
    imm = SUPER_TAG | ((opcode & 0xFF) << 4) | (typecode & 0xF)
    return [isa.encode_instruction(isa.OP_NOOP, rd=dst, rs1=src,
                                   rs2=family, imm16=imm), payload & 0xFFFFFFFF]


def reg_operand(xreg):
    if not 0 <= xreg < NUM_XREGS: raise ValueError("X register out of range")
    return SUPER_REG | xreg


def is_super_word(word):
    op, _d, _s, _f, imm = isa.decode_instruction_fast(word)
    return op == isa.OP_NOOP and (imm & SUPER_MASK) == SUPER_TAG


def decode_super(header, payload):
    op, dst, src, family, imm = isa.decode_instruction_fast(header)
    if op != isa.OP_NOOP or (imm & SUPER_MASK) != SUPER_TAG:
        raise ValueError("not a systems superinstruction")
    return SuperInstruction(family, (imm >> 4) & 0xFF, dst, src,
                            imm & 0xF, payload & 0xFFFFFFFF)


def disassemble_systems(words):
    """Disassemble a mixed base/systems word stream without decoding payloads."""
    lines, pc = [], 0
    while pc < len(words):
        if is_super_word(words[pc]):
            if pc + 1 >= len(words):
                lines.append(f"  {pc:3d}: <truncated SUPER>"); break
            i = decode_super(words[pc], words[pc + 1])
            family = FAMILY_NAMES.get(i.family, f"F{i.family:X}")
            name = OP_NAMES.get((i.family, i.opcode), f"OP_{i.opcode:02X}")
            typ = TYPE_NAMES.get(i.typecode, f"T{i.typecode:X}")
            payload = (f"X{i.payload & 0xF}"
                       if (i.payload & SUPER_REG_MASK) == SUPER_REG else f"0x{i.payload:08x}")
            lines.append(f"  {pc:3d}: {family}.{name}.{typ} X{i.dst}, X{i.src}, {payload}")
            pc += 2
        else:
            rendered = isa.disassemble([words[pc]]).strip()
            rendered = rendered.split(":", 1)[1].strip() if ":" in rendered else rendered
            lines.append(f"  {pc:3d}: {rendered}"); pc += 1
    return "\n".join(lines)


def _sx(value, bits):
    sign = 1 << (bits - 1); value &= (1 << bits) - 1
    return value - (1 << bits) if value & sign else value


class SystemsPicoVM(PicoVM):
    """Reference VM for base PicoScript plus native-width systems instructions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xregs = [0] * NUM_XREGS
        self.xregs[15] = self.arena_bytes       # X15 = systems stack pointer
        self.xregs[14] = self.arena_bytes       # X14 = frame pointer
        self.frames = []

    def reset_for_request(self):
        super().reset_for_request()
        self.xregs[:] = [0] * NUM_XREGS
        self.xregs[15] = self.xregs[14] = self.arena_bytes
        self.frames.clear()
        return self

    def _verify(self):
        # Verify base control flow, skipping payload words (which are data and may
        # coincidentally resemble a JUMP/CALL/BRANCH instruction).
        n, pc = len(self.program), 0
        while pc < n:
            word = self.program[pc]
            if is_super_word(word):
                if pc + 1 >= n: raise PicoFault(2, pc, 0, "truncated systems instruction")
                pc += 2; continue
            op, _rd, _rs1, rs2, imm = isa.decode_instruction_fast(word)
            target = None
            if op == isa.OP_JUMP and rs2 == 0: target = imm
            elif op == isa.OP_CALL: target = imm
            elif op == isa.OP_BRANCH: target = pc + _sx(imm, 16)
            if target is not None and not 0 <= target <= n:
                raise PicoFault(3, pc, target, f"bad static target {target} at pc={pc}")
            pc += 1

    def _step(self):
        if is_super_word(self.program[self.pc]):
            cur = self.pc
            if cur + 1 >= len(self.program):
                raise PicoFault(2, cur, 0, "truncated systems instruction")
            ins = decode_super(self.program[cur], self.program[cur + 1])
            self.cur_pc = cur; self.pc += 2
            self._execute_super(ins, cur)
            return
        super()._step()

    def _operand(self, payload, signed=False):
        if (payload & SUPER_REG_MASK) == SUPER_REG: return self.xregs[payload & 0xF]
        return _sx(payload, 32) if signed else payload

    def _bounds(self, ptr, width, pc):
        if ptr < 0 or width < 0 or ptr + width > self.arena_bytes:
            raise PicoFault(3, pc, ptr, "systems memory access out of range")

    def _load(self, ptr, width, pc):
        self._bounds(ptr, width, pc)
        return int.from_bytes(self.mem[ptr:ptr + width], "little")

    def _store(self, ptr, width, value, pc):
        self._bounds(ptr, width, pc)
        self.mem[ptr:ptr + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")

    def _r_operand(self, payload):
        if (payload & SUPER_REG_MASK) == SUPER_REG:
            return self.regs[payload & 0xF] & MASK32
        return payload & MASK32

    @staticmethod
    def _cap_handle(kind, pack=0, selector=0):
        return ((kind & 0xF) << 60) | ((selector & 0xFFFFFFFF) << 16) | (pack & 0x3FF)

    @staticmethod
    def _cap_parts(handle):
        return ((handle >> 60) & 0xF, handle & 0x3FF, (handle >> 16) & 0xFFFFFFFF)

    def _storage_call(self, method, a=0, b=0):
        """Call the existing capability-checked Storage provider without
        exposing scratch-register mutations to the program."""
        saved = list(self.regs)
        self.regs[13], self.regs[14], self.regs[12] = a & MASK32, b & MASK32, 0
        self.host.call(self, "Storage", method, 12, 13, 14, 0)
        result = self.regs[12] & MASK32
        self.regs[:] = saved
        return result

    def _select_storage(self, handle, selector_method=None):
        _kind, pack, selector = self._cap_parts(handle)
        self._storage_call("UsePack", pack)
        if selector_method is not None:
            self._storage_call(selector_method, selector)
        return pack, selector

    def _execute_super(self, i, pc):
        X, mask = self.xregs, 0xFFFFFFFFFFFFFFFF
        if i.family == F_INT64:
            b = self._operand(i.payload, i.typecode == T_I64)
            a = X[i.src]
            if i.opcode in (S_EQ64, S_NE64, S_LT64, S_GT64, S_LE64, S_GE64):
                aa, bb = ((_sx(a, 64), _sx(b, 64)) if i.typecode == T_I64 else (a, b))
                result = {S_EQ64: aa == bb, S_NE64: aa != bb, S_LT64: aa < bb,
                          S_GT64: aa > bb, S_LE64: aa <= bb, S_GE64: aa >= bb}[i.opcode]
                self.regs[i.dst] = int(result); return
            if i.opcode == S_FROM_R32:
                r = self.regs[i.src] & MASK32
                if i.typecode == T_I64: r = _sx(r, 32)
            elif i.opcode == S_TO_R32:
                self.regs[i.dst] = a & MASK32; return
            elif i.opcode == S_MOV64: r = a
            elif i.opcode == S_MOVI64: r = b
            elif i.opcode == S_ADD64: r = a + b
            elif i.opcode == S_SUB64: r = a - b
            elif i.opcode == S_MUL64: r = a * b
            elif i.opcode == S_DIV64:
                if b == 0: r = 0
                elif i.typecode == T_I64: r = int(_sx(a, 64) / _sx(b, 64))
                else: r = a // b
            elif i.opcode == S_AND64: r = a & b
            elif i.opcode == S_OR64: r = a | b
            elif i.opcode == S_XOR64: r = a ^ b
            elif i.opcode == S_SHL64: r = a << (b & 63)
            elif i.opcode == S_SHR64:
                r = (_sx(a, 64) >> (b & 63)) if i.typecode == T_I64 else (a >> (b & 63))
            else: raise PicoFault(2, pc, i.opcode, "bad INT64 superinstruction")
            X[i.dst] = r & mask; return
        if i.family == F_MEMORY:
            widths = {S_LOAD8: 1, S_LOAD16: 2, S_LOAD32: 4, S_LOAD64: 8,
                      S_LOAD_FIELD64: 8, S_STORE8: 1, S_STORE16: 2,
                      S_STORE32: 4, S_STORE64: 8, S_STORE_FIELD32: 4}
            if i.opcode in widths:
                ptr = X[i.src] + _sx(i.payload, 32); width = widths[i.opcode]
                if i.opcode in (S_LOAD8, S_LOAD16, S_LOAD32, S_LOAD64, S_LOAD_FIELD64):
                    X[i.dst] = self._load(ptr, width, pc)
                else: self._store(ptr, width, X[i.dst], pc)
                return
            length = self._operand(i.payload)
            if i.opcode == S_MEMCPY:
                self._bounds(X[i.dst], length, pc); self._bounds(X[i.src], length, pc)
                self.mem[X[i.dst]:X[i.dst] + length] = self.mem[X[i.src]:X[i.src] + length]
            elif i.opcode == S_MEMSET:
                self._bounds(X[i.dst], length, pc)
                self.mem[X[i.dst]:X[i.dst] + length] = bytes([X[i.src] & 0xFF]) * length
            else: raise PicoFault(2, pc, i.opcode, "bad MEMORY superinstruction")
            return
        if i.family == F_POINTER:
            operand = self._operand(i.payload, signed=True)
            if i.opcode in (S_LEA, S_PTR_ADD): X[i.dst] = (X[i.src] + operand) & mask
            elif i.opcode == S_PTR_DIFF: X[i.dst] = (X[i.src] - operand) & mask
            elif i.opcode == S_PTR_INDEX:
                index = (X[i.payload & 0xF]
                         if (i.payload & SUPER_REG_MASK) == SUPER_REG else i.payload)
                stride = 1 << (i.typecode & 3)
                X[i.dst] = (X[i.src] + index * stride) & mask
            else: raise PicoFault(2, pc, i.opcode, "bad POINTER superinstruction")
            return
        if i.family == F_FRAME:
            if i.opcode == S_FRAME_ENTER:
                size = i.payload; newsp = X[15] - size
                self._bounds(newsp, size, pc)
                self.frames.append((list(self.regs), list(X), X[14], X[15]))
                X[15] = X[14] = newsp
            elif i.opcode == S_FRAME_LEAVE:
                if not self.frames: raise PicoFault(5, pc, 0, "frame underflow")
                result_r = self.regs[i.dst]
                result_x = X[i.src]
                heap_idx = ((i.payload & 0xF)
                            if (i.payload & SUPER_REG_MASK) == SUPER_REG else None)
                heap_value = X[heap_idx] if heap_idx is not None else 0
                old_r, old_x, old_fp, old_sp = self.frames.pop()
                self.regs[:] = old_r; X[:] = old_x
                X[14], X[15] = old_fp, old_sp
                if i.typecode & 1: self.regs[i.dst] = result_r
                if i.typecode & 2: X[i.src] = result_x
                if heap_idx is not None: X[heap_idx] = heap_value
            elif i.opcode in (S_LD_LOCAL32, S_LD_LOCAL64):
                X[i.dst] = self._load(X[14] + _sx(i.payload, 32), 4 if i.opcode == S_LD_LOCAL32 else 8, pc)
            elif i.opcode in (S_ST_LOCAL32, S_ST_LOCAL64):
                self._store(X[14] + _sx(i.payload, 32), 4 if i.opcode == S_ST_LOCAL32 else 8, X[i.dst], pc)
            elif i.opcode == S_ADDR_LOCAL: X[i.dst] = X[14] + _sx(i.payload, 32)
            elif i.opcode == S_CALL:
                if not 0 <= i.payload <= len(self.program): raise PicoFault(3, pc, i.payload, "bad systems call")
                self.call_stack.append(self.pc); self.pc = i.payload
            elif i.opcode == S_RET:
                if self.call_stack: self.pc = self.call_stack.pop()
                else: raise Halt()
            else: raise PicoFault(2, pc, i.opcode, "bad FRAME superinstruction")
            return
        if i.family == F_STORAGE:
            rv = self._r_operand(i.payload)
            if i.opcode == S_WAL_OPEN:
                ready = self._storage_call("Ready")
                X[i.dst] = self._cap_handle(1) if ready else 0
                return
            if i.opcode == S_WAL_PACK:
                X[i.dst] = self._cap_handle(1, rv)
                return
            if i.opcode in (S_WAL_PUT, S_WAL_CREATE, S_WAL_GET, S_WAL_DELETE,
                            S_WAL_EXISTS, S_WAL_SCAN):
                self._select_storage(X[i.src])
                method = {S_WAL_PUT: "PutCard", S_WAL_CREATE: "PutCard",
                          S_WAL_GET: "ReadExact", S_WAL_DELETE: "DeleteExact",
                          S_WAL_EXISTS: "Exists", S_WAL_SCAN: "ScanNext"}[i.opcode]
                record = self.regs[i.dst] & MASK32
                if i.opcode == S_WAL_CREATE and self._storage_call("Exists", record):
                    result = 3
                else:
                    result = self._storage_call(method, record, rv)
                self.regs[i.dst] = result
                return
            if i.opcode in (S_WAL_SYNC, S_WAL_RECOVER):
                self.regs[i.dst] = self._storage_call(
                    "Sync" if i.opcode == S_WAL_SYNC else "Recover")
                return
            if i.opcode == S_INDEX_OPEN:
                _kind, pack, _sel = self._cap_parts(X[i.src])
                X[i.dst] = self._cap_handle(2, pack, rv)
                return
            if i.opcode in (S_INDEX_UPSERT, S_INDEX_DELETE, S_INDEX_EXACT,
                            S_INDEX_REVERSE, S_INDEX_RESULT):
                if not hasattr(self, "_sys_exact_indexes"):
                    self._sys_exact_indexes, self._sys_index_results = {}, []
                handle = X[i.src]
                table = self._sys_exact_indexes.setdefault(handle, {})
                record = self.regs[i.dst] & MASK32
                if i.opcode == S_INDEX_UPSERT:
                    for ids in table.values(): ids.discard(record)
                    table.setdefault(rv, set()).add(record); self.regs[i.dst] = 1
                elif i.opcode == S_INDEX_DELETE:
                    for ids in table.values(): ids.discard(record)
                    self.regs[i.dst] = 1
                elif i.opcode in (S_INDEX_EXACT, S_INDEX_REVERSE):
                    self._sys_index_results = sorted(table.get(record, ()))
                    self.regs[i.dst] = len(self._sys_index_results)
                else:
                    pos = record
                    self.regs[i.dst] = (self._sys_index_results[pos]
                                        if pos < len(self._sys_index_results) else MASK32)
                return
            if i.opcode == S_FTS_OPEN:
                _kind, pack, _sel = self._cap_parts(X[i.src])
                X[i.dst] = self._cap_handle(3, pack, rv)
                return
            if i.opcode in (S_FTS_UPSERT, S_FTS_DELETE, S_FTS_FIND, S_FTS_RESULT):
                self._select_storage(X[i.src], "FullTextField")
                first = self.regs[i.dst] & MASK32
                if i.opcode == S_FTS_UPSERT: method, second = "FullTextUpsert", rv
                elif i.opcode == S_FTS_DELETE: method, second = "FullTextDelete", 0
                elif i.opcode == S_FTS_FIND:
                    self._storage_call("FullTextMode", rv)
                    method, second = "FullTextFind", 0
                else: method, second = "FullTextResult", 0
                self.regs[i.dst] = self._storage_call(method, first, second)
                return
            raise PicoFault(2, pc, i.opcode, "bad STORAGE superinstruction")
        if i.family == F_GRAPH:
            rv = self._r_operand(i.payload)
            if i.opcode == S_GRAPH_OPEN:
                _kind, pack, _sel = self._cap_parts(X[i.src])
                X[i.dst] = self._cap_handle(4, pack, rv)
                return
            self._select_storage(X[i.src], "GraphRelation")
            first = self.regs[i.dst] & MASK32
            if i.opcode == S_GRAPH_SET_WEIGHT:
                self.regs[i.dst] = self._storage_call("GraphWeightSet", first); return
            methods = {S_GRAPH_ADD: "GraphAdd", S_GRAPH_DELETE: "GraphDelete",
                       S_GRAPH_WEIGHT: "GraphWeight", S_GRAPH_OUT: "GraphOut",
                       S_GRAPH_IN: "GraphOut", S_GRAPH_RESULT_NODE: "GraphResultNode",
                       S_GRAPH_RESULT_WEIGHT: "GraphResultWeight"}
            if i.opcode in methods:
                second = 1 if i.opcode == S_GRAPH_IN else (0 if i.opcode == S_GRAPH_OUT else rv)
                self.regs[i.dst] = self._storage_call(methods[i.opcode], first, second)
                return
            if i.opcode == S_GRAPH_SHORTEST_PATH:
                # The provider owns adjacency and installs the resulting path in
                # the same result cursor consumed by Graph.ResultNode/Weight.
                self.regs[i.dst] = self._storage_call("GraphPath", first, rv)
                return
            raise PicoFault(2, pc, i.opcode, "bad GRAPH superinstruction")
        if i.family == F_BLOCK:
            raise PicoFault(6, pc, i.opcode, "BLOCK superinstruction requires a provider")
        raise PicoFault(2, pc, i.family, "unsupported systems family")
