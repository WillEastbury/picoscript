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
    EXT_HOST_HOOK_BASE,
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
ARENA_BYTES = 520 * 1024                  # PicoVM data arena = RP2350 (Pico 2) 520 KB SRAM


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
        # Text/binary I/O: arena-backed writer + reader handle tables.
        self.writers: Dict[int, dict] = {}
        self.readers: Dict[int, dict] = {}
        self._next_writer = 1
        self._next_reader = 1
        # Simulated PIOS I/O binding state: one bound request context (I4) and
        # one in-flight response descriptor graph (I2) per VM invocation.
        self.request_context: Optional[dict] = None
        self.response_graph: List[dict] = []
        self.response_sealed = False
        self.response_ended = False
        # Automatic per-request arena scope: snapshot of (arena_top, span_count)
        # taken at the first handler invocation; each subsequent request rewinds
        # to it so a reused server VM never leaks (set_arena_base() can move it
        # forward after one-time setup such as Template.Compile).
        self._handler_mark: Optional[tuple] = None

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
        if ns == "Bits":
            a = vm.regs[rs1] & MASK32
            b = vm.regs[rs2] & MASK32
            sh = b & 31
            if method == "And":
                vm.regs[rd] = (a & b) & MASK32
                return
            if method == "Or":
                vm.regs[rd] = (a | b) & MASK32
                return
            if method == "Xor":
                vm.regs[rd] = (a ^ b) & MASK32
                return
            if method == "Shl":
                vm.regs[rd] = (a << sh) & MASK32
                return
            if method == "Shr":
                vm.regs[rd] = (a >> sh) & MASK32
                return
            if method == "Sar":
                vm.regs[rd] = (_sx32(a) >> sh) & MASK32
                return
            if method == "Not":
                vm.regs[rd] = (~a) & MASK32
                return
        if ns == "Dot8":
            if method == "Len":
                vm.dot_len = vm.regs[rs1] & MASK32
                return
            if method == "Of":
                n = getattr(vm, "dot_len", 0)
                size = vm.arena_bytes
                wp = vm.regs[rs1] % size
                ap = vm.regs[rs2] % size
                acc = 0
                for i in range(n):
                    w = vm.mem[(wp + i) % size]
                    a = vm.mem[(ap + i) % size]
                    acc += (w - 256 if w > 127 else w) * (a - 256 if a > 127 else a)
                vm.regs[rd] = acc & MASK32
                return
        # Memory + span / slice / materialize.
        if ns == "Memory" and method == "Set":
            vm.mem[vm.regs[rs1] % vm.arena_bytes] = vm.regs[rs2] & 0xFF
            return
        if ns == "Memory" and method == "Get":
            vm.regs[rd] = vm.mem[vm.regs[rs1] % vm.arena_bytes]
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
        # Arena scopes: Mark/Rewind/Reset the bump arena (request-scoped allocation).
        if ns == "Arena":
            if method == "Mark":
                vm.regs[rd] = ((len(vm.spans) & 0x7FF) << 20) | (vm.arena_top & 0xFFFFF)
                return
            if method == "Rewind":
                m = vm.regs[rs1] & MASK32
                vm.arena_top = m & 0xFFFFF
                cnt = (m >> 20) & 0x7FF
                if cnt < 1:
                    cnt = 1
                if cnt < len(vm.spans):
                    del vm.spans[cnt:]
                return
            if method == "Reset":
                vm.arena_top = 0x8000
                vm.spans = [None]
                return
        # EL0-facing PIOS request/response hooks over a simulated in-VM backend.
        if ns == "Req":
            if self._req(vm, method, rd, rs1, rs2):
                return
        if ns == "Resp":
            if self._resp(vm, method, rd, rs1, rs2):
                return
        # Program-level card store: Storage.* over a PicoStore (text via byte-spans).
        if ns == "Storage":
            if self._storage(vm, method, rd, rs1, rs2):
                return
        # String.* arena string library.
        if ns == "String":
            if self._stringlib(vm, method, rd, rs1, rs2):
                return
        # Number.* integer/format library.
        if ns == "Number":
            if self._numberlib(vm, method, rd, rs1, rs2):
                return
        # Template.* (AOT compile-at-save + render).
        if ns == "Template":
            if self._templatelib(vm, method, rd, rs1, rs2):
                return
        # Maths.* pure-integer ops (Power/Sqrt).
        if ns == "Maths":
            if self._mathslib(vm, method, rd, rs1, rs2):
                return
        # Compress.* (PicoCompress RLE), Crypto.* (Sha256), Html.* (entity escape).
        if ns == "Compress":
            if self._compresslib(vm, method, rd, rs1, rs2):
                return
        if ns == "Crypto":
            if self._cryptolib(vm, method, rd, rs1, rs2):
                return
        if ns == "Html":
            if self._htmllib(vm, method, rd, rs1, rs2):
                return
        if ns == "Http":
            if self._httplib(vm, method, rd, rs1, rs2):
                return
        # Io: write raw bytes (UTF-8 strings) to the output buffer.
        if ns == "Io" and method == "Write":
            h = vm.regs[rs1]
            s = vm.spans[h] if 0 < h < len(vm.spans) else None
            if s:
                vm.output.append(bytes(vm.mem[s["ptr"]:s["ptr"] + s["len"]]))
            return
        if ns == "Io" and method == "WriteByte":
            vm.output.append(bytes([vm.regs[rs1] & 0xFF]))
            return
        # Text/binary primitives: Utf8Writer / Utf8Reader / Json / Xml.
        if ns in ("Utf8Writer", "Utf8Reader", "Json", "Xml"):
            if self._textio(vm, ns, method, rd, rs1, rs2):
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

    # -- String.* arena string library (spans in / spans out) ---------------
    def _span_raw(self, vm: "PicoVM", h: int) -> bytes:
        if h <= 0 or h >= len(vm.spans) or not vm.spans[h]:
            return b""
        s = vm.spans[h]
        return bytes(vm.mem[s["ptr"]:s["ptr"] + s["len"]])

    def _new_span_bytes(self, vm: "PicoVM", data: bytes) -> int:
        dst = vm.arena_top
        vm.mem[dst:dst + len(data)] = data
        vm.arena_top += len(data)
        vm.spans.append({"ptr": dst, "len": len(data)})
        return len(vm.spans) - 1

    def _stringlib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        R = vm.regs
        a = self._span_raw(vm, R[rs1])
        if method == "Length":
            R[rd] = len(a); return True
        if method == "Concat":
            R[rd] = self._new_span_bytes(vm, a + self._span_raw(vm, R[rs2])); return True
        if method == "Substring":
            start = max(0, _sx32(R[rs2]))
            R[rd] = self._new_span_bytes(vm, a[start:]); return True
        if method == "IndexOf":
            R[rd] = a.find(self._span_raw(vm, R[rs2])) & MASK32; return True
        if method == "StartsWith":
            R[rd] = 1 if a.startswith(self._span_raw(vm, R[rs2])) else 0; return True
        if method == "EndsWith":
            R[rd] = 1 if a.endswith(self._span_raw(vm, R[rs2])) else 0; return True
        if method == "ToUpper":
            R[rd] = self._new_span_bytes(vm, bytes(c - 32 if 97 <= c <= 122 else c for c in a)); return True
        if method == "ToLower":
            R[rd] = self._new_span_bytes(vm, bytes(c + 32 if 65 <= c <= 90 else c for c in a)); return True
        if method == "Trim":
            R[rd] = self._new_span_bytes(vm, a.strip(b" \t\r\n")); return True
        if method == "SetReplace":
            vm._str_repl = a; return True
        if method == "Replace":
            repl = getattr(vm, "_str_repl", b"")
            R[rd] = self._new_span_bytes(vm, a.replace(self._span_raw(vm, R[rs2]), repl)); return True
        return False

    def _numberlib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        R = vm.regs
        if method == "Parse":
            try:
                v = int((self._span_raw(vm, R[rs1]).decode("ascii", "replace").strip()) or "0")
            except ValueError:
                v = 0
            R[rd] = v & MASK32; return True
        a, b = _sx32(R[rs1]), _sx32(R[rs2])
        if method == "Abs":
            R[rd] = abs(a) & MASK32; return True
        if method == "Min":
            R[rd] = (a if a < b else b) & MASK32; return True
        if method == "Max":
            R[rd] = (a if a > b else b) & MASK32; return True
        if method in ("Floor", "Ceiling", "Round"):   # integer values: identity
            R[rd] = a & MASK32; return True
        if method == "ToString":
            R[rd] = self._new_span_bytes(vm, str(a).encode()); return True
        if method == "ToHex":
            R[rd] = self._new_span_bytes(vm, format(a & MASK32, "x").encode()); return True
        if method == "ToOctal":
            R[rd] = self._new_span_bytes(vm, format(a & MASK32, "o").encode()); return True
        if method == "ToBinary":
            R[rd] = self._new_span_bytes(vm, format(a & MASK32, "b").encode()); return True
        return False

    def _mathslib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        # Pure-integer Maths ops (the float transcendentals Sin/Cos/Log/Exp need a
        # fixed-point convention and are left planned; see STRING_TEMPLATES.md).
        R = vm.regs
        if method == "Power":
            base, exp = _sx32(R[rs1]), _sx32(R[rs2])
            if exp <= 0:
                R[rd] = (1 if exp == 0 else 0) & MASK32
            else:
                r = 1
                for _ in range(min(exp, 0xFFFF)):
                    r = (r * base) & MASK32
                R[rd] = r
            return True
        if method == "Sqrt":
            n = _sx32(R[rs1])
            if n <= 0:
                R[rd] = 0; return True
            x, res, bit = n, 0, 1 << 30
            while bit > n:
                bit >>= 2
            while bit:
                if x >= res + bit:
                    x -= res + bit; res = (res >> 1) + bit
                else:
                    res >>= 1
                bit >>= 2
            R[rd] = res & MASK32
            return True
        return False

    def _compresslib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        # PicoCompress: a simple reversible byte-run RLE -> (count, byte) pairs.
        src = self._span_raw(vm, vm.regs[rs1])
        if method == "PicoCompress":
            out = bytearray(); i = 0
            while i < len(src):
                c = 1
                while i + c < len(src) and src[i + c] == src[i] and c < 255:
                    c += 1
                out.append(c); out.append(src[i]); i += c
            vm.regs[rd] = self._new_span_bytes(vm, bytes(out)); return True
        if method == "PicoDecompress":
            out = bytearray(); i = 0
            while i + 1 < len(src):
                out.extend(bytes([src[i + 1]]) * src[i]); i += 2
            vm.regs[rd] = self._new_span_bytes(vm, bytes(out)); return True
        return False

    def _cryptolib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        if method == "Sha256":
            import hashlib
            h = hashlib.sha256(self._span_raw(vm, vm.regs[rs1])).digest()
            vm.regs[rd] = self._new_span_bytes(vm, h); return True
        if method == "HmacSha256":
            import hashlib
            import hmac as _hmac
            key = self._span_raw(vm, vm.regs[rs1])
            msg = self._span_raw(vm, vm.regs[rs2])
            h = _hmac.new(key, msg, hashlib.sha256).digest()
            vm.regs[rd] = self._new_span_bytes(vm, h); return True
        return False

    def _htmllib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        src = self._span_raw(vm, vm.regs[rs1])
        if method == "Encode":
            out = (src.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")
                      .replace(b'"', b"&quot;").replace(b"'", b"&#39;"))
            vm.regs[rd] = self._new_span_bytes(vm, out); return True
        if method == "Decode":
            out = (src.replace(b"&lt;", b"<").replace(b"&gt;", b">").replace(b"&quot;", b'"')
                      .replace(b"&#39;", b"'").replace(b"&amp;", b"&"))
            vm.regs[rd] = self._new_span_bytes(vm, out); return True
        return False

    @staticmethod
    def _urldecode(b: bytes) -> bytes:
        out = bytearray(); i = 0
        while i < len(b):
            c = b[i]
            if c == 0x2B:                       # '+' -> space
                out.append(0x20); i += 1
            elif c == 0x25 and i + 2 < len(b):   # %XX
                try:
                    out.append(int(b[i + 1:i + 3], 16)); i += 3
                except ValueError:
                    out.append(c); i += 1
            else:
                out.append(c); i += 1
        return bytes(out)

    @staticmethod
    def _jsonesc(b: bytes) -> bytes:
        out = bytearray()
        for c in b:
            if c == 0x22:
                out += b'\\"'
            elif c == 0x5c:
                out += b'\\\\'
            elif c == 0x0a:
                out += b'\\n'
            elif c == 0x0d:
                out += b'\\r'
            elif c == 0x09:
                out += b'\\t'
            elif c < 0x20:
                out += b'\\u%04x' % c
            else:
                out.append(c)
        return bytes(out)

    @staticmethod
    def _parsejson_to_model(s: bytes) -> bytes:
        # Flatten a JSON value to dotted-path key=value model lines (the Template
        # {{#each}} model): objects -> prefix.key, arrays -> prefix.index, scalars -> leaf.
        n = len(s)
        pos = [0]
        out = bytearray()
        ws = (0x20, 0x09, 0x0a, 0x0d)

        def hx(c):
            return 0x30 <= c <= 0x39 or 0x41 <= c <= 0x46 or 0x61 <= c <= 0x66

        def skipws():
            while pos[0] < n and s[pos[0]] in ws:
                pos[0] += 1

        def parse_string():
            b = bytearray()
            pos[0] += 1
            while pos[0] < n:
                c = s[pos[0]]; pos[0] += 1
                if c == 0x22:
                    break
                if c == 0x5c and pos[0] < n:
                    e = s[pos[0]]; pos[0] += 1
                    if e == 0x6e: b.append(0x0a)
                    elif e == 0x74: b.append(0x09)
                    elif e == 0x72: b.append(0x0d)
                    elif e == 0x62: b.append(0x08)
                    elif e == 0x66: b.append(0x0c)
                    elif e == 0x75 and pos[0] + 4 <= n and all(hx(s[pos[0] + j]) for j in range(4)):
                        cp = int(s[pos[0]:pos[0] + 4], 16); pos[0] += 4
                        if cp < 0x80:
                            b.append(cp)
                        elif cp < 0x800:
                            b.append(0xC0 | (cp >> 6)); b.append(0x80 | (cp & 0x3F))
                        else:
                            b.append(0xE0 | (cp >> 12)); b.append(0x80 | ((cp >> 6) & 0x3F)); b.append(0x80 | (cp & 0x3F))
                    else:
                        b.append(e)
                else:
                    b.append(c)
            return bytes(b)

        def emit(prefix):
            skipws()
            if pos[0] >= n:
                return
            c = s[pos[0]]
            if c == 0x7b:
                pos[0] += 1; skipws()
                if pos[0] < n and s[pos[0]] == 0x7d:
                    pos[0] += 1; return
                while pos[0] < n:
                    skipws()
                    if pos[0] >= n or s[pos[0]] != 0x22:
                        break
                    key = parse_string(); skipws()
                    if pos[0] < n and s[pos[0]] == 0x3a:
                        pos[0] += 1
                    emit(key if not prefix else prefix + b"." + key); skipws()
                    if pos[0] < n and s[pos[0]] == 0x2c:
                        pos[0] += 1; continue
                    if pos[0] < n and s[pos[0]] == 0x7d:
                        pos[0] += 1
                    break
            elif c == 0x5b:
                pos[0] += 1; skipws()
                if pos[0] < n and s[pos[0]] == 0x5d:
                    pos[0] += 1; return
                idx = 0
                while pos[0] < n:
                    ik = str(idx).encode()
                    emit(ik if not prefix else prefix + b"." + ik); idx += 1; skipws()
                    if pos[0] < n and s[pos[0]] == 0x2c:
                        pos[0] += 1; continue
                    if pos[0] < n and s[pos[0]] == 0x5d:
                        pos[0] += 1
                    break
            elif c == 0x22:
                out.extend(prefix); out.append(0x3d); out.extend(parse_string()); out.append(0x0a)
            else:
                start = pos[0]
                while pos[0] < n and s[pos[0]] not in (0x2c, 0x7d, 0x5d, 0x20, 0x09, 0x0a, 0x0d):
                    pos[0] += 1
                out.extend(prefix); out.append(0x3d); out.extend(s[start:pos[0]]); out.append(0x0a)

        skipws()
        emit(b"")
        return bytes(out)

    def _httplib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        # Pure HTTP parsing: query/form -> key=value lines (the Template model format).
        src = self._span_raw(vm, vm.regs[rs1])
        if method == "ParseQuery" or method == "ParseForm":
            out = bytearray()
            for pair in src.split(b"&"):
                if not pair:
                    continue
                if b"=" in pair:
                    k, v = pair.split(b"=", 1)
                else:
                    k, v = pair, b""
                out += self._urldecode(k) + b"=" + self._urldecode(v) + b"\n"
            vm.regs[rd] = self._new_span_bytes(vm, bytes(out)); return True
        if method == "EncodeJson":
            items = []
            for line in src.split(b"\n"):
                if b"=" not in line:
                    continue
                k, v = line.split(b"=", 1)
                items.append(b'"' + self._jsonesc(k) + b'":"' + self._jsonesc(v) + b'"')
            vm.regs[rd] = self._new_span_bytes(vm, b"{" + b",".join(items) + b"}"); return True
        if method == "ParseJson":
            vm.regs[rd] = self._new_span_bytes(vm, self._parsejson_to_model(src)); return True
        return False

    def _templatelib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        # AOT-compiled template: Compile (at save time) turns a {{hole}} source
        # into a compact plan; Render walks the plan against a key=value model.
        # Plan ops: 0x01 LEN_HI LEN_LO <bytes>=literal, 0x02 KEYLEN <key>=hole.
        if method == "Compile":
            src = self._span_raw(vm, vm.regs[rs1])
            plan = bytearray()

            def lit(b):
                if b:
                    plan.extend((0x01, (len(b) >> 8) & 0xFF, len(b) & 0xFF)); plan.extend(b)

            i, n = 0, len(src)
            while i < n:
                j = src.find(b"{{", i)
                if j < 0:
                    lit(src[i:]); break
                lit(src[i:j])
                k = src.find(b"}}", j + 2)
                if k < 0:
                    lit(src[j:]); break
                inner = src[j + 2:k].strip(b" \t\r\n")
                if inner[:1] == b"#":          # section {{#k}} or iteration {{#each list}}
                    rest = inner[1:].strip(b" \t\r\n")
                    if rest[:4] == b"each" and rest[4:5] in (b" ", b"\t", b""):
                        lk = rest[4:].strip(b" \t\r\n")[:255]
                        plan.extend((0x06, len(lk))); plan.extend(lk)
                    else:
                        key = rest[:255]
                        plan.extend((0x03, len(key))); plan.extend(key)
                elif inner[:1] == b"^":        # inverted section: render if key falsy
                    key = inner[1:].strip(b" \t\r\n")[:255]
                    plan.extend((0x04, len(key))); plan.extend(key)
                elif inner[:1] == b"/":        # section / each end
                    plan.append(0x05)
                else:                          # hole
                    key = inner[:255]
                    plan.extend((0x02, len(key))); plan.extend(key)
                i = k + 2
            vm.regs[rd] = self._new_span_bytes(vm, bytes(plan))
            return True
        if method == "Render":
            plan = self._span_raw(vm, vm.regs[rs1])
            model = {}
            for line in self._span_raw(vm, vm.regs[rs2]).split(b"\n"):
                if b"=" in line:
                    key, val = line.split(b"=", 1)
                    model[key] = val

            def resolve(key, prefix):
                if key == b".":
                    return model.get(prefix, b"")
                if prefix:
                    v = model.get(prefix + b"." + key)
                    if v is not None:
                        return v
                return model.get(key, b"")

            def count_list(full):
                c = 0
                while True:
                    base = full + b"." + str(c).encode()
                    if base in model or any(kk.startswith(base + b".") for kk in model):
                        c += 1
                    else:
                        return c

            def skip_block(p):
                depth = 1
                while p < n and depth > 0:
                    o = plan[p]; p += 1
                    if o == 0x01:
                        p += 2 + ((plan[p] << 8) | plan[p + 1])
                    elif o == 0x02:
                        p += 1 + plan[p]
                    elif o in (0x03, 0x04, 0x06):
                        p += 1 + plan[p]; depth += 1
                    elif o == 0x05:
                        depth -= 1
                return p

            out = bytearray()
            prefix = b""
            stack = []                 # frames: [kind, saved_prefix, body_start, count, full, idx]
            i, n = 0, len(plan)
            while i < n:
                op = plan[i]; i += 1
                if op == 0x01:
                    ln = (plan[i] << 8) | plan[i + 1]; i += 2
                    out.extend(plan[i:i + ln]); i += ln
                elif op == 0x02:
                    kl = plan[i]; i += 1
                    out.extend(resolve(bytes(plan[i:i + kl]), prefix)); i += kl
                elif op == 0x03 or op == 0x04:           # (inverted) section
                    kl = plan[i]; i += 1
                    key = bytes(plan[i:i + kl]); i += kl
                    truthy = len(resolve(key, prefix)) > 0
                    if (truthy if op == 0x03 else (not truthy)):
                        stack.append(["sec", prefix, 0, 0, b"", 0])
                    else:
                        i = skip_block(i)
                elif op == 0x06:                         # each LIST
                    kl = plan[i]; i += 1
                    lk = bytes(plan[i:i + kl]); i += kl
                    full = (prefix + b"." + lk) if prefix else lk
                    cnt = count_list(full)
                    if cnt == 0:
                        i = skip_block(i)
                    else:
                        stack.append(["each", prefix, i, cnt, full, 0])
                        prefix = full + b".0"
                elif op == 0x05:                         # end of section / each
                    if stack:
                        fr = stack[-1]
                        if fr[0] == "each":
                            fr[5] += 1
                            if fr[5] < fr[3]:
                                prefix = fr[4] + b"." + str(fr[5]).encode(); i = fr[2]
                            else:
                                prefix = fr[1]; stack.pop()
                        else:
                            prefix = fr[1]; stack.pop()
                else:
                    break
            vm.regs[rd] = self._new_span_bytes(vm, bytes(out))
            return True
        return False

    # -- PIOS Req.*/Resp.* simulated host backend ----------------------------
    def install_request_context(self, vm: "PicoVM", *, seq=0, principal="", method="GET",
                                path="/", headers=None, body=None, body_mode=0):
        """Install the bound request context used by Req.* tests.

        String fields are materialized as VM spans; Req.* only reads this installed
        context (I4), and the response graph is reset to exactly one builder (I2).
        """
        # Automatic per-request arena scope: reclaim the previous request's spans,
        # then (re)take the post-setup base before building this request -- so the
        # server loop never relies on a human to clean up.
        if self._handler_mark is not None:
            top, cnt = self._handler_mark
            vm.arena_top = top
            if cnt < len(vm.spans):
                del vm.spans[cnt:]
        self._handler_mark = (vm.arena_top, len(vm.spans))
        headers = headers or {}
        body = body or []
        hdr = {}
        for k, v in headers.items():
            name_h = self._str_span(vm, str(k))
            value_h = self._str_span(vm, str(v))
            hdr[str(k).lower()] = {"name": name_h, "value": value_h}
        self.request_context = {
            "seq": int(seq) & MASK32,
            "principal": self._str_span(vm, str(principal)),
            "method": self._str_span(vm, str(method)),
            "path": self._str_span(vm, str(path)),
            "headers": hdr,
            "body_mode": int(body_mode) & MASK32,
            "body": [self._str_span(vm, str(chunk)) for chunk in body],
        }
        self.response_graph = []
        self.response_sealed = False
        self.response_ended = False

    set_request_context = install_request_context

    def set_arena_base(self, vm: "PicoVM"):
        """Commit the current arena top/span count as the per-request base.

        Call after one-time setup (e.g. Template.Compile at startup) so that the
        automatic per-request rewind in install_request_context preserves that
        setup while still reclaiming each handler's request-scoped allocations.
        """
        self._handler_mark = (vm.arena_top, len(vm.spans))

    def get_response_graph(self) -> List[dict]:
        """Return a copy of the simulated response descriptor graph."""
        return [dict(d) for d in self.response_graph]

    def _require_request_context(self) -> dict:
        # I4: Req.* reads are confined to the kernel-installed bound context.
        if self.request_context is None:
            raise RuntimeError("I4 violation: Req.* without installed request context")
        return self.request_context

    def _ensure_response_open(self):
        # I2: there is exactly one response graph being built; End closes it.
        if self.response_ended:
            raise RuntimeError("I2 violation: response graph already finalized")

    def _ensure_preamble_mutable(self):
        # I3: after Seal, the preamble and headers are immutable/frozen.
        if self.response_sealed:
            raise RuntimeError("I3 violation: response preamble/headers sealed")

    def _desc(self, kind: str, subtype=None, payload=None) -> dict:
        return {"kind": kind, "subtype": subtype, "payload": payload}

    def _span_payload(self, vm: "PicoVM", handle: int) -> dict:
        s = vm.spans[handle] if 0 < handle < len(vm.spans) else {"ptr": 0, "len": 0}
        return {"span": handle, "text": self._span_str(vm, handle), "ptr": s["ptr"], "len": s["len"]}

    def _resp_status(self, vm: "PicoVM", code: int):
        self._ensure_response_open()
        self._ensure_preamble_mutable()
        desc = self._desc("DESC_PREAMBLE", "STATUS", {"code": _sx32(code)})
        for i, existing in enumerate(self.response_graph):
            if existing["kind"] == "DESC_PREAMBLE" and existing["subtype"] == "STATUS":
                self.response_graph[i] = desc
                return
        self.response_graph.append(desc)

    def _resp_seal(self):
        self._ensure_response_open()
        if not self.response_sealed:
            self.response_graph.append(self._desc("DESC_COMMIT", "SEAL", None))
            self.response_sealed = True

    def _resp_end(self):
        self._ensure_response_open()
        self.response_graph.append(self._desc("DESC_COMMIT", "END", None))
        self.response_ended = True

    def _req(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        ctx = self._require_request_context()
        R = vm.regs
        if method == "Seq":
            R[rd] = ctx["seq"]; return True
        if method == "Principal":
            R[rd] = ctx["principal"]; return True
        if method == "Method":
            R[rd] = ctx["method"]; return True
        if method == "Path":
            R[rd] = ctx["path"]; return True
        if method == "Header":
            name = self._span_str(vm, R[rs1]).lower()
            R[rd] = ctx["headers"].get(name, {}).get("value", 0)
            return True
        if method == "BodyMode":
            R[rd] = ctx["body_mode"]; return True
        if method == "BodyCount":
            R[rd] = len(ctx["body"]); return True
        if method == "BodySpan":
            idx = _sx32(R[rs1])
            R[rd] = ctx["body"][idx] if 0 <= idx < len(ctx["body"]) else 0
            return True
        return False

    def _resp(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        R = vm.regs
        if method == "Status":
            self._resp_status(vm, R[rs1]); return True
        if method == "Header":
            self._ensure_response_open(); self._ensure_preamble_mutable()
            self.response_graph.append(self._desc("DESC_HEADER", None, {
                "name": self._span_payload(vm, R[rs1]),
                "value": self._span_payload(vm, R[rs2]),
            }))
            return True
        if method == "Write":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_BODY", None, self._span_payload(vm, R[rs1])))
            return True
        if method == "Trailer":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_TRAILER", None, {
                "name": self._span_payload(vm, R[rs1]),
                "value": self._span_payload(vm, R[rs2]),
            }))
            return True
        if method == "Seal":
            self._resp_seal(); return True
        if method == "End":
            self._resp_end(); return True
        if method == "Respond":
            self._resp_status(vm, R[rs1]); self._resp_seal(); self._resp_end(); return True
        if method == "Flush":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_CONTROL", "FLUSH", None)); return True
        if method == "Continue":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_CONTROL", "CONTINUE_100", None)); return True
        if method == "EndStream":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_CONTROL", "END_STREAM", None)); return True
        if method == "Upgrade":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_UPGRADE", None, self._span_payload(vm, R[rs1]))); return True
        if method == "Abort":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_ABORT", None, {"code": _sx32(R[rs1])})); self.response_ended = True; return True
        if method == "EarlyHints":
            self._ensure_response_open()
            self.response_graph.append(self._desc("DESC_CONTROL", "EARLY_HINTS_103", None)); return True
        return False

    # -- Text/binary primitives (Utf8Writer / Utf8Reader / Json / Xml) -------
    @staticmethod
    def _w_byte(vm, w, b):
        if w["pos"] < w["cap"]:
            vm.mem[w["ptr"] + w["pos"]] = b & 0xFF
            w["pos"] += 1

    def _w_text(self, vm, w, text):
        for byte in text.encode("utf-8"):
            self._w_byte(vm, w, byte)

    def _w_span(self, vm, w, span_handle):
        s = vm.spans[span_handle] if 0 < span_handle < len(vm.spans) else None
        if s:
            for i in range(s["len"]):
                self._w_byte(vm, w, vm.mem[s["ptr"] + i])

    @staticmethod
    def _json_esc(s: str) -> str:
        out = []
        for ch in s:
            o = ord(ch)
            if ch == '"':
                out.append('\\"')
            elif ch == '\\':
                out.append('\\\\')
            elif ch == '\n':
                out.append('\\n')
            elif ch == '\r':
                out.append('\\r')
            elif ch == '\t':
                out.append('\\t')
            elif o < 0x20:
                out.append('\\u%04x' % o)
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _xml_esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _json_pre(self, vm, w):
        if not w["stack"]:
            return
        st = w["stack"][-1]
        if st["afterKey"]:
            st["afterKey"] = False
        elif st["count"] > 0:
            self._w_byte(vm, w, 0x2C)               # ,

    def _json_post(self, w):
        if w["stack"]:
            w["stack"][-1]["count"] += 1

    def _textio(self, vm, ns, method, rd, rs1, rs2) -> bool:
        R = vm.regs
        if ns == "Utf8Writer":
            if method == "New":
                h = self._next_writer
                self._next_writer += 1
                self.writers[h] = {"ptr": R[rs1] & 0xFFFF, "cap": R[rs2] & 0xFFFF, "pos": 0, "stack": []}
                R[rd] = h
                return True
            w = self.writers.get(R[rs1])
            if w is None:
                R[rd] = 0
                return True
            if method == "Byte":
                self._w_byte(vm, w, R[rs2]); return True
            if method == "Int":
                self._w_text(vm, w, str(_sx32(R[rs2]))); return True
            if method == "Span":
                self._w_span(vm, w, R[rs2]); return True
            if method == "ToSpan":
                vm.spans.append({"ptr": w["ptr"], "len": w["pos"]})
                R[rd] = len(vm.spans) - 1; return True
            if method == "Len":
                R[rd] = w["pos"]; return True
            if method == "Reset":
                w["pos"] = 0; w["stack"] = []; return True
            return False
        if ns == "Utf8Reader":
            if method == "New":
                s = vm.spans[R[rs1]] if 0 < R[rs1] < len(vm.spans) else {"ptr": 0, "len": 0}
                h = self._next_reader
                self._next_reader += 1
                self.readers[h] = {"ptr": s["ptr"], "len": s["len"], "pos": 0}
                R[rd] = h
                return True
            r = self.readers.get(R[rs1])
            if r is None:
                R[rd] = 0
                return True
            cur = (lambda: vm.mem[r["ptr"] + r["pos"]] if r["pos"] < r["len"] else 0)
            if method == "Peek":
                R[rd] = cur(); return True
            if method == "Next":
                R[rd] = cur()
                if r["pos"] < r["len"]:
                    r["pos"] += 1
                return True
            if method == "SkipWs":
                while r["pos"] < r["len"] and vm.mem[r["ptr"] + r["pos"]] in (32, 9, 10, 13):
                    r["pos"] += 1
                return True
            if method == "Eof":
                R[rd] = 1 if r["pos"] >= r["len"] else 0; return True
            if method == "Pos":
                R[rd] = r["pos"]; return True
            if method == "Match":
                if r["pos"] < r["len"] and vm.mem[r["ptr"] + r["pos"]] == (R[rs2] & 0xFF):
                    r["pos"] += 1; R[rd] = 1
                else:
                    R[rd] = 0
                return True
            if method == "Int":
                while r["pos"] < r["len"] and vm.mem[r["ptr"] + r["pos"]] in (32, 9, 10, 13):
                    r["pos"] += 1
                sign = 1
                if r["pos"] < r["len"] and vm.mem[r["ptr"] + r["pos"]] == 0x2D:
                    sign = -1; r["pos"] += 1
                n = 0
                while r["pos"] < r["len"] and 0x30 <= vm.mem[r["ptr"] + r["pos"]] <= 0x39:
                    n = n * 10 + (vm.mem[r["ptr"] + r["pos"]] - 0x30); r["pos"] += 1
                R[rd] = (sign * n) & MASK32
                return True
            return False
        if ns == "Json":
            w = self.writers.get(R[rs1])
            if w is None:
                R[rd] = 0
                return True
            if method == "BeginObject" or method == "BeginArray":
                self._json_pre(vm, w)
                self._w_byte(vm, w, 0x7B if method == "BeginObject" else 0x5B)   # { or [
                if w["stack"]:
                    w["stack"][-1]["count"] += 1
                w["stack"].append({"count": 0, "afterKey": False})
                return True
            if method == "EndObject" or method == "EndArray":
                if w["stack"]:
                    w["stack"].pop()
                self._w_byte(vm, w, 0x7D if method == "EndObject" else 0x5D)     # } or ]
                return True
            if method == "Key":
                st = w["stack"][-1] if w["stack"] else None
                if st and st["count"] > 0:
                    self._w_byte(vm, w, 0x2C)
                self._w_byte(vm, w, 0x22)
                self._w_text(vm, w, self._json_esc(self._span_str(vm, R[rs2])))
                self._w_byte(vm, w, 0x22); self._w_byte(vm, w, 0x3A)             # ":
                if st:
                    st["afterKey"] = True
                return True
            if method == "Str":
                self._json_pre(vm, w)
                self._w_byte(vm, w, 0x22)
                self._w_text(vm, w, self._json_esc(self._span_str(vm, R[rs2])))
                self._w_byte(vm, w, 0x22)
                self._json_post(w); return True
            if method == "Int":
                self._json_pre(vm, w); self._w_text(vm, w, str(_sx32(R[rs2]))); self._json_post(w); return True
            if method == "Bool":
                self._json_pre(vm, w); self._w_text(vm, w, "true" if R[rs2] else "false"); self._json_post(w); return True
            if method == "Null":
                self._json_pre(vm, w); self._w_text(vm, w, "null"); self._json_post(w); return True
            if method == "Raw":
                self._json_pre(vm, w); self._w_span(vm, w, R[rs2]); self._json_post(w); return True
            return False
        if ns == "Xml":
            w = self.writers.get(R[rs1])
            if w is None:
                R[rd] = 0
                return True
            if method == "Open":
                self._w_byte(vm, w, 0x3C); self._w_span(vm, w, R[rs2]); return True          # <tag
            if method == "AttrName":
                self._w_byte(vm, w, 0x20); self._w_span(vm, w, R[rs2])
                self._w_byte(vm, w, 0x3D); self._w_byte(vm, w, 0x22); return True             # name="
            if method == "AttrValue":
                self._w_text(vm, w, self._xml_esc(self._span_str(vm, R[rs2])))
                self._w_byte(vm, w, 0x22); return True                                         # value"
            if method == "OpenEnd":
                self._w_byte(vm, w, 0x3E); return True                                         # >
            if method == "Text":
                self._w_text(vm, w, self._xml_esc(self._span_str(vm, R[rs2]))); return True
            if method == "Close":
                self._w_byte(vm, w, 0x3C); self._w_byte(vm, w, 0x2F)
                self._w_span(vm, w, R[rs2]); self._w_byte(vm, w, 0x3E); return True             # </tag>
            if method == "Empty":
                self._w_byte(vm, w, 0x2F); self._w_byte(vm, w, 0x3E); return True               # />
            return False
        return False

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

    def __init__(self, host: Optional[HostApi] = None, max_steps: int = 1_000_000,
                 arena_bytes: int = ARENA_BYTES):
        self.regs: List[int] = [0] * isa_num_regs()
        self.cards: Dict[int, int] = {}
        self.call_stack: List[int] = []
        self.output: List[bytes] = []        # PIPE / Net.Body payloads
        self.http_status: Optional[int] = None
        self.http_type: Optional[str] = None
        self.mem = bytearray(arena_bytes)    # process arena; default = RP2350 (Pico 2) 520 KB SRAM
        self.arena_bytes = arena_bytes
        self.dot_len = 0                     # active span length for Dot8.Of
        self.arena_top = 0x8000              # bump pointer for Span.Materialize copies
        self.spans: List[Optional[dict]] = [None]   # span table; handle = 1-based index
        self.host = host or HostApi()
        self.max_steps = max_steps
        self.steps = 0
        self.pc = 0
        self.halted = False
        self.waiting = False
        self.retval = 0
        # opt-in profiling (off by default; near-zero cost when disabled)
        self.profile = False
        self.op_hist: Dict[int, int] = {}
        self.host_calls = 0
        self.net_ops = 0

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

    def output_text(self) -> str:
        """Decode the output buffer (PIPE ints + Io.Write bytes) as UTF-8 text."""
        return b"".join(self.output).decode("utf-8", "replace")

    # -- core ------------------------------------------------------------
    def _step(self):
        word = self.program[self.pc]
        d = isa.decode_instruction(word)
        op, rd, rs1, rs2, imm16 = d["opcode"], d["rd"], d["rs1"], d["rs2"], d["imm16"]
        cur = self.pc
        self.pc += 1

        if self.profile:
            self.op_hist[op] = self.op_hist.get(op, 0) + 1
            if op == isa.OP_NOOP:
                if (imm16 & 0xFF00) == HOST_HOOK_BASE or (imm16 & 0xF000) == EXT_HOST_HOOK_BASE:
                    self.host_calls += 1
                elif imm16:
                    self.net_ops += 1

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
            if rs2 == isa.ADDR_REGISTER:
                tgt = self.regs[rs1] & 0xFFFF                    # PC = Rs1 (indirect)
            elif rs2 == isa.ADDR_REG_OFF:
                tgt = (self.regs[rs1] + imm16) & 0xFFFF          # PC = Rs1 + imm16 (indexed)
            else:
                tgt = imm16
            if tgt < 0 or tgt > len(self.program):              # INV-11: range-check computed jumps
                raise RuntimeError(f"bad jump target {tgt} at pc={cur}")
            self.pc = tgt
        elif op == isa.OP_BRANCH:
            if self._cond(rs2, self.regs[rd], self.regs[rs1]):
                tgt = cur + _sx16(imm16)
                if tgt < 0 or tgt > len(self.program):
                    raise RuntimeError(f"bad branch target {tgt} at pc={cur}")
                self.pc = tgt
        elif op == isa.OP_CALL:
            if imm16 < 0 or imm16 > len(self.program):
                raise RuntimeError(f"bad call target {imm16} at pc={cur}")
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
            # Signed division truncating toward zero (INV-14): matches C int32 a/b
            # and JS (a/b)|0. Python's // floors, so compute magnitude then re-sign.
            if b == 0:
                r = 0
            else:
                q = abs(a) // abs(b)
                r = -q if (a < 0) != (b < 0) else q
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
        if (imm16 & 0xFF00) == HOST_HOOK_BASE or (imm16 & 0xF000) == EXT_HOST_HOOK_BASE:
            hook = (imm16 & 0x0FFF) if (imm16 & 0xF000) == EXT_HOST_HOOK_BASE else (imm16 & 0x00FF)
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
