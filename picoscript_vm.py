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
import picocompress
import picobrotli
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

PV_FAULT_NONE = 0
PV_FAULT_STEP_BUDGET = 1
PV_FAULT_BAD_OPCODE = 2
PV_FAULT_BAD_JUMP = 3
PV_FAULT_CALL_OVERFLOW = 4
PV_FAULT_RET_UNDERFLOW = 5
PV_FAULT_BAD_HOOK = 6
PV_FAULT_TEMPLATE = 7
PV_FAULT_CAPABILITY = 8
PV_FAULT_ALLOC = 9
PV_FAULT_CONST_WRITE = 10


class PicoFault(RuntimeError):
    """Structured VM trap carrying the fault code, bytecode PC, and fault detail."""

    def __init__(self, code: int, pc: int = 0, detail: int = 0, message: Optional[str] = None):
        self.code = int(code)
        self.pc = int(pc)
        self.detail = int(detail)
        super().__init__(message or f"VM fault {self.code} at pc={self.pc} detail={self.detail}")


def _sx16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _sx32(v: int) -> int:
    v &= MASK32
    return v - 0x100000000 if v & 0x80000000 else v


# ── Q16.16 fixed-point CORDIC (Maths.Sin/Cos/Tan, ...) ───────────────────────
# All-integer; the constants/iteration count below are shared verbatim with
# vm/picovm.c and vm/picovm.js so the result is byte-identical on every path.
# A value v is represented as round(v * 65536); angles are radians in Q16.16.
Q16_ONE = 1 << 16
Q16_HALF_PI = 102944
Q16_PI = 205887
Q16_TWO_PI = 411775
Q16_CORDIC_GAIN_INV = 39797     # 1/prod(sqrt(1+2^-2i)) in Q16.16, pre-cancels CORDIC gain
Q16_ATAN = (51472, 30386, 16055, 8150, 4091, 2047, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2)


def _q16_cordic_quad(r: int):
    """CORDIC rotation for r in [0, HALF_PI); returns (sin, cos) in Q16.16."""
    x = Q16_CORDIC_GAIN_INV
    y = 0
    z = r
    for i in range(16):
        dx = x >> i
        dy = y >> i
        if z >= 0:
            x = _sx32(x - dy); y = _sx32(y + dx); z -= Q16_ATAN[i]
        else:
            x = _sx32(x + dy); y = _sx32(y - dx); z += Q16_ATAN[i]
    return y, x


def _q16_sincos(angle: int):
    """(sin, cos) in Q16.16 for a Q16.16 radian angle, via quadrant reduction."""
    a = angle % Q16_TWO_PI
    if a < 0:
        a += Q16_TWO_PI
    q = a // Q16_HALF_PI
    r = a - q * Q16_HALF_PI
    s, c = _q16_cordic_quad(r)
    if q == 0:
        return s, c
    if q == 1:
        return c, _sx32(-s)
    if q == 2:
        return _sx32(-s), _sx32(-c)
    return _sx32(-c), s


def _q16_tan(angle: int) -> int:
    """tan in Q16.16 = sin/cos (trunc-toward-zero divide); saturates when cos == 0."""
    s, c = _q16_sincos(angle)
    if c == 0:
        return 0x7FFFFFFF if s >= 0 else -0x80000000
    num = s * Q16_ONE
    q = abs(num) // abs(c)
    if (num < 0) != (c < 0):
        q = -q
    return _sx32(q)


# Q16.16 exp/log helpers. fixmul uses arithmetic >>16 (floor); the series divisions
# use trunc-toward-zero to match the C/JS signed-division convention (INV-2).
Q16_LN2 = 45426
Q16_INV_LN2 = 94548
Q16_INV_LN10 = 28462
Q16_EXP_MAX_Z = 681300       # ~ln(32767) in Q16.16; above this exp overflows int32


def _q16_fixmul(a: int, b: int) -> int:
    return _sx32((a * b) >> 16)


def _q16_idiv(a: int, n: int) -> int:
    q = abs(a) // abs(n)
    return -q if (a < 0) != (n < 0) else q


def _q16_fixdiv(a: int, b: int) -> int:
    num = a * Q16_ONE
    q = abs(num) // abs(b)
    return _sx32(-q if (num < 0) != (b < 0) else q)


def _q16_exp(z: int) -> int:
    """e^z in Q16.16 (range-reduced by ln2, Taylor on the remainder)."""
    if z >= Q16_EXP_MAX_Z:
        return 0x7FFFFFFF
    if z <= -Q16_EXP_MAX_Z:
        return 0
    k = (_q16_fixmul(z, Q16_INV_LN2) + (Q16_ONE >> 1)) >> 16
    r = _sx32(z - k * Q16_LN2)
    term = Q16_ONE
    acc = Q16_ONE
    for n in range(1, 8):
        term = _q16_idiv(_q16_fixmul(term, r), n)
        acc = _sx32(acc + term)
    if k >= 0:
        for _ in range(k):
            acc *= 2
            if acc > 0x7FFFFFFF:
                return 0x7FFFFFFF
    else:
        for _ in range(-k):
            acc >>= 1
    return _sx32(acc)


def _q16_log(x: int) -> int:
    """ln(x) in Q16.16 (x>0); x = m*2^e with m in [1,2), ln(m)=2*atanh((m-1)/(m+1))."""
    if x <= 0:
        return -0x80000000
    e = 0
    m = x
    while m >= 2 * Q16_ONE:
        m >>= 1
        e += 1
    while m < Q16_ONE:
        m <<= 1
        e -= 1
    u = _q16_fixdiv(m - Q16_ONE, m + Q16_ONE)
    u2 = _q16_fixmul(u, u)
    term = u
    acc = 0
    for n in range(6):
        acc = _sx32(acc + _q16_idiv(term, 2 * n + 1))
        term = _q16_fixmul(term, u2)
    return _sx32(_sx32(2 * acc) + e * Q16_LN2)


# ── AES-256-CTR (Crypto.Encrypt/Decrypt). All-byte ops; the S-box/Rcon tables and
# the algorithm are shared verbatim with vm/picovm.c and vm/picovm.js so ciphertext is
# byte-identical on every path. CTR is symmetric, so Encrypt == Decrypt. ───────────────
_AES_SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
)
_AES_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d)


def _aes_xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _aes_gmul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        a = _aes_xtime(a)
        b >>= 1
    return r & 0xFF


def _aes256_key_expand(key: bytes):
    w = [list(key[4 * i:4 * i + 4]) for i in range(8)]
    for i in range(8, 60):
        t = list(w[i - 1])
        if i % 8 == 0:
            t = t[1:] + t[:1]
            t = [_AES_SBOX[x] for x in t]
            t[0] ^= _AES_RCON[i // 8 - 1]
        elif i % 8 == 4:
            t = [_AES_SBOX[x] for x in t]
        w.append([w[i - 8][j] ^ t[j] for j in range(4)])
    return w


def _aes256_encrypt_block(blk: bytes, w):
    state = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_rk(rnd):
        for c in range(4):
            for r in range(4):
                state[r][c] ^= w[rnd * 4 + c][r]

    add_rk(0)
    for rnd in range(1, 14):
        for r in range(4):
            for c in range(4):
                state[r][c] = _AES_SBOX[state[r][c]]
        for r in range(1, 4):
            state[r] = state[r][r:] + state[r][:r]
        for c in range(4):
            col = [state[r][c] for r in range(4)]
            state[0][c] = _aes_gmul(col[0], 2) ^ _aes_gmul(col[1], 3) ^ col[2] ^ col[3]
            state[1][c] = col[0] ^ _aes_gmul(col[1], 2) ^ _aes_gmul(col[2], 3) ^ col[3]
            state[2][c] = col[0] ^ col[1] ^ _aes_gmul(col[2], 2) ^ _aes_gmul(col[3], 3)
            state[3][c] = _aes_gmul(col[0], 3) ^ col[1] ^ col[2] ^ _aes_gmul(col[3], 2)
        add_rk(rnd)
    for r in range(4):
        for c in range(4):
            state[r][c] = _AES_SBOX[state[r][c]]
    for r in range(1, 4):
        state[r] = state[r][r:] + state[r][:r]
    add_rk(14)
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _aes256_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    w = _aes256_key_expand(key)
    out = bytearray()
    ctr = bytearray(iv)
    for off in range(0, len(data), 16):
        ks = _aes256_encrypt_block(bytes(ctr), w)
        for j in range(min(16, len(data) - off)):
            out.append(data[off + j] ^ ks[j])
        for j in range(15, -1, -1):
            ctr[j] = (ctr[j] + 1) & 0xFF
            if ctr[j]:
                break
    return bytes(out)


# ── DEFLATE (RFC 1951) + gzip (RFC 1952), built into the runtime ────────────
# A canonical compressor so the bytes are identical on every path: one final
# fixed-Huffman block, greedy LZ77 with a deterministic hash-chain match finder.
# inflate is spec-deterministic (decompresses real zlib/gzip output too). Mirror
# in vm/picovm.js + vm/picovm.c must stay byte-for-byte identical.
_LEN_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51,
             59, 67, 83, 99, 115, 131, 163, 195, 227, 258)
_LEN_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4,
              4, 4, 5, 5, 5, 5, 0)
_DIST_BASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385,
              513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577)
_DIST_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9,
               10, 10, 11, 11, 12, 12, 13, 13)
_CLEN_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)

_CRC32_TABLE = []
for _n in range(256):
    _c = _n
    for _k in range(8):
        _c = (0xEDB88320 ^ (_c >> 1)) if (_c & 1) else (_c >> 1)
    _CRC32_TABLE.append(_c & 0xFFFFFFFF)


def _crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _fixed_lit_lengths():
    return [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8


def _codes_from_lengths(lengths):
    maxbits = max(lengths) if lengths else 0
    bl_count = [0] * (maxbits + 1)
    for L in lengths:
        if L:
            bl_count[L] += 1
    code = 0
    next_code = [0] * (maxbits + 1)
    for bits in range(1, maxbits + 1):
        code = (code + bl_count[bits - 1]) << 1
        next_code[bits] = code
    out = {}
    for sym, L in enumerate(lengths):
        if L:
            out[sym] = (next_code[L], L)
            next_code[L] += 1
    return out


def _tree_from_lengths(lengths):
    return {(c, L): sym for sym, (c, L) in _codes_from_lengths(lengths).items()}


def _deflate(data: bytes) -> bytes:
    lit = _codes_from_lengths(_fixed_lit_lengths())
    out = bytearray()
    bitbuf = 0
    bitcnt = 0

    def put(value, n):
        nonlocal bitbuf, bitcnt
        bitbuf |= (value & ((1 << n) - 1)) << bitcnt
        bitcnt += n
        while bitcnt >= 8:
            out.append(bitbuf & 0xFF)
            bitbuf >>= 8
            bitcnt -= 8

    def huff(code, n):
        r = 0
        for _ in range(n):
            r = (r << 1) | (code & 1)
            code >>= 1
        put(r, n)

    put(1, 1)        # BFINAL
    put(1, 2)        # BTYPE = fixed Huffman
    n = len(data)
    head = {}
    prev = [0] * (n + 1)
    i = 0
    while i < n:
        match_len = 0
        match_dist = 0
        if i + 3 <= n:
            h = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
            j = head.get(h, 0) - 1
            chain = 0
            maxlen = min(258, n - i)
            while j >= 0 and i - j <= 32768 and chain < 256:
                length = 0
                while length < maxlen and data[j + length] == data[i + length]:
                    length += 1
                if length > match_len:
                    match_len = length
                    match_dist = i - j
                    if length >= maxlen:
                        break
                j = prev[j] - 1
                chain += 1
        if match_len >= 3:
            ls = _len_sym(match_len)
            code, clen = lit[ls]
            huff(code, clen)
            put(match_len - _LEN_BASE[ls - 257], _LEN_EXTRA[ls - 257])
            ds = _dist_sym(match_dist)
            huff(ds, 5)
            put(match_dist - _DIST_BASE[ds], _DIST_EXTRA[ds])
            end = i + match_len
            while i < end:
                if i + 3 <= n:
                    h = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
                    prev[i] = head.get(h, 0)
                    head[h] = i + 1
                i += 1
        else:
            code, clen = lit[data[i]]
            huff(code, clen)
            if i + 3 <= n:
                h = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
                prev[i] = head.get(h, 0)
                head[h] = i + 1
            i += 1
    code, clen = lit[256]
    huff(code, clen)
    if bitcnt > 0:
        out.append(bitbuf & 0xFF)
    return bytes(out)


def _len_sym(length):
    for i in range(len(_LEN_BASE) - 1, -1, -1):
        if length >= _LEN_BASE[i]:
            return 257 + i
    return 257


def _dist_sym(dist):
    for i in range(len(_DIST_BASE) - 1, -1, -1):
        if dist >= _DIST_BASE[i]:
            return i
    return 0


def _inflate(data: bytes) -> bytes:
    pos = 0
    bitbuf = 0
    bitcnt = 0
    out = bytearray()
    fixed_lit = _tree_from_lengths(_fixed_lit_lengths())
    fixed_dist = _tree_from_lengths([5] * 30)

    def take(n):
        nonlocal pos, bitbuf, bitcnt
        while bitcnt < n:
            if pos >= len(data):
                raise ValueError("truncated compressed data")
            b = data[pos]
            pos += 1
            bitbuf |= b << bitcnt
            bitcnt += 8
        v = bitbuf & ((1 << n) - 1)
        bitbuf >>= n
        bitcnt -= n
        return v

    def sym(tree):
        code = 0
        length = 0
        while True:
            code = (code << 1) | take(1)
            length += 1
            s = tree.get((code, length))
            if s is not None:
                return s
            if length > 15:
                raise ValueError("bad compressed data")

    while True:
        bfinal = take(1)
        btype = take(2)
        if btype == 0:
            take(bitcnt & 7)               # skip to the next byte boundary
            ln = take(16); take(16)
            for _ in range(ln):
                out.append(take(8))
        else:
            if btype == 1:
                lit_tree, dist_tree = fixed_lit, fixed_dist
            else:
                lit_tree, dist_tree = _read_dynamic(take)
            while True:
                s = sym(lit_tree)
                if s == 256:
                    break
                if s < 256:
                    out.append(s)
                else:
                    li = s - 257
                    length = _LEN_BASE[li] + take(_LEN_EXTRA[li])
                    dsym = sym(dist_tree)
                    dist = _DIST_BASE[dsym] + take(_DIST_EXTRA[dsym])
                    start = len(out) - dist
                    for k in range(length):
                        out.append(out[start + k])
        if bfinal:
            break
    return bytes(out)


def _read_dynamic(take):
    hlit = take(5) + 257
    hdist = take(5) + 1
    hclen = take(4) + 4
    clen_lengths = [0] * 19
    for i in range(hclen):
        clen_lengths[_CLEN_ORDER[i]] = take(3)
    clen_tree = _tree_from_lengths(clen_lengths)

    def csym():
        code = 0
        length = 0
        while True:
            code = (code << 1) | take(1)
            length += 1
            s = clen_tree.get((code, length))
            if s is not None:
                return s
            if length > 15:
                raise ValueError("bad compressed data")

    lengths = []
    while len(lengths) < hlit + hdist:
        s = csym()
        if s < 16:
            lengths.append(s)
        elif s == 16:
            lengths.extend([lengths[-1]] * (take(2) + 3))
        elif s == 17:
            lengths.extend([0] * (take(3) + 3))
        else:
            lengths.extend([0] * (take(7) + 11))
    return _tree_from_lengths(lengths[:hlit]), _tree_from_lengths(lengths[hlit:hlit + hdist])


def _gzip_compress(data: bytes) -> bytes:
    hdr = bytes([0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 0xFF])
    tail = (_crc32(data) & 0xFFFFFFFF).to_bytes(4, "little") + \
           (len(data) & 0xFFFFFFFF).to_bytes(4, "little")
    return hdr + _deflate(data) + tail


def _gzip_decompress(data: bytes) -> bytes:
    if len(data) < 18 or data[0] != 0x1F or data[1] != 0x8B:
        raise ValueError("bad compressed data")
    flg = data[3]
    pos = 10
    if flg & 4:                              # FEXTRA
        xlen = data[pos] | (data[pos + 1] << 8); pos += 2 + xlen
    if flg & 8:                              # FNAME
        while data[pos] != 0:
            pos += 1
        pos += 1
    if flg & 16:                             # FCOMMENT
        while data[pos] != 0:
            pos += 1
        pos += 1
    if flg & 2:                              # FHCRC
        pos += 2
    return _inflate(data[pos:-8])



# Binding capability classes (INV-17: "bindings are not ambient"). Bit values are shared
# verbatim with vm/picovm.h (PV_CAP_*) and vm/picovm.js so a denied hook faults identically
# on every path. Pure computation needs no capability (class 0, always allowed).
CAP_KERNEL  = 1 << 0
CAP_QUEUE   = 1 << 1
CAP_RANDOM  = 1 << 2
CAP_STORAGE = 1 << 3
CAP_TIME    = 1 << 4
CAP_NET     = 1 << 5
CAP_CONTEXT = 1 << 6
CAP_AUTH    = 1 << 7
CAP_ENV     = 1 << 8
CAP_CRYPTO  = 1 << 9
CAP_GPIO    = 1 << 10           # Gpio.* (device pins; OS/emulator-backed)
CAP_CAPSULE = 1 << 11           # Pack/Card/Fifo (capsule store + intra-capsule IPC)
CAP_DEVICE  = 1 << 12           # Device.* (enumerate/open a streaming device)
CAP_DMA     = 1 << 13           # Stream.* (DMA-ring buffers)
CAP_EVENT   = 1 << 14           # Event.* (reactive event queue; UI/async dispatch)
CAP_UI      = 1 << 15           # Ui.* (retained scene tree / remote windowing)
CAP_ALL     = 0xFFFF            # default grant: every binding (host restricts to gate)

_CAP_BY_NS = {
    "Kernel": CAP_KERNEL, "Queue": CAP_QUEUE, "Random": CAP_RANDOM,
    "Req": CAP_NET, "Resp": CAP_NET, "Net": CAP_NET,
    "Storage": CAP_STORAGE, "DateTime": CAP_TIME, "Context": CAP_CONTEXT,
    "Auth": CAP_AUTH, "X509": CAP_AUTH, "Environment": CAP_ENV, "Locale": CAP_ENV,
    "Gpio": CAP_GPIO,
    "Pack": CAP_CAPSULE, "Card": CAP_CAPSULE, "Fifo": CAP_CAPSULE,
    "Device": CAP_DEVICE, "Stream": CAP_DMA,
    "Event": CAP_EVENT, "Ui": CAP_UI,
}


def hook_cap(ns: str, method: str) -> int:
    """Capability class a host hook needs (0 = pure). Mirrors vm/picovm.c pv_hook_cap;
    handles the mixed namespaces (Maths/Crypto have both pure and binding members)."""
    if ns == "Maths" and method in ("Random", "RandomRange"):
        return CAP_RANDOM
    if ns == "Crypto" and method == "RandomBytes":
        return CAP_RANDOM
    if ns == "Crypto" and method in ("Encrypt", "Decrypt"):
        return CAP_CRYPTO
    if ns == "Http" and method in ("ReadHeader", "ReadBody", "GenerateHeaders", "GenerateResponse"):
        return CAP_NET
    return _CAP_BY_NS.get(ns, 0)


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
        self.caps = CAP_ALL          # granted binding capabilities (INV-17); host restricts to gate
        self.no_alloc = False        # INV-5: when True, arena allocation in a hook raises PicoFault
        self.host_status = 0         # INV-18: typed status of the last fallible hook (0=OK)
        self.const_floor = 0x8000    # INV-9: lowest literal const-pool address; [floor,0x8000) is RO
        self.log: List[str] = []
        self.handlers: Dict[tuple, Callable] = {}
        # Card store (PicoStore) + program-level Storage.* context.
        self._store = None
        self.cur_pack = 0
        self.cur_card = 0
        self.query_results: List[int] = []
        self.search_docs: Dict[int, dict] = {}
        self.search_results: List[tuple] = []
        self.search_plan: Dict[str, int] = {"lexical": 0, "vector": 0, "hybrid": 0, "semantic": 0}
        self.search_vector_sig = 0
        self.search_semantic_weight = 0
        self.tensor_rows = 0
        self.tensor_cols = 0
        self.bitlinear_rows = 0
        self.bitlinear_cols = 0
        self.gpio: Dict[int, dict] = {}   # reference GPIO emulator: pin -> {dir,pull,value}
        self.schemas: Dict[int, bytes] = {}   # per-pack typed-field schema span bytes (0x60/0x61)
        self.blob_cards: Dict[tuple, bytearray] = {}  # (pack, card) -> large-card bytes for slice tests/sim
        self.slice_offset = 0
        self.slice_len = 0
        # Reference DMA-ring emulator (Device.*/Stream.*): deterministic fake ring.
        self.devices: Dict[int, dict] = {}
        self.streams: Dict[int, dict] = {}
        self.leases: Dict[int, dict] = {}
        self.stream_slice_offset = 0
        self.stream_slice_len = 0
        self._dev_seq = 0
        self._stream_seq = 0
        self._lease_seq = 0
        # PSUnit assertion counters (Assert.*): the test-harness facility.
        self.assert_total = 0
        self.assert_failed = 0
        # Event.* reactive queue: pending FIFO of event ids + a record table.
        self.events: Dict[int, dict] = {}     # eventId -> {type, target, data(bytes|None), span}
        self.event_queue: List[int] = []      # pending eventIds (FIFO)
        self._event_seq = 0
        self.event_slice_offset = 0
        self.event_slice_len = 0
        # Ui.* retained scene tree: nodeId -> {kind,id,x,y,w,h,value,text,children}.
        self.ui_nodes: Dict[int, dict] = {}
        self._ui_seq = 0
        # Text/binary I/O: arena-backed writer + reader handle tables.
        self.writers: Dict[int, dict] = {}
        self.readers: Dict[int, dict] = {}
        self._next_writer = 1
        self._next_reader = 1
        # Simulated PIOS I/O binding state: one bound request context (I4) and
        # one in-flight response descriptor graph (I2) per VM invocation.
        self.request_context: Optional[dict] = None
        self.req_slice_offset = 0
        self.req_slice_len = 0
        self.response_graph: List[dict] = []
        self.response_sealed = False
        self.response_ended = False
        self.response_mode: Optional[str] = None   # 'unary' | 'stream' (set at Seal / terminal verb)
        self.response_body_started = False          # first Resp.Write opens the body phase
        self.response_stream_closed = False         # Resp.EndStream closes the stream/body phase
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
        # INV-17: bindings are not ambient -- a hook touching the outside world is
        # denied unless its capability class has been granted to this capsule.
        need = hook_cap(ns, method)
        if need and not (self.caps & need):
            hook = HOST_HOOK_CODES.get((ns, method), 0)
            raise PicoFault(PV_FAULT_CAPABILITY, getattr(vm, "cur_pc", 0), hook,
                            f"capability denied: {ns}.{method} requires an ungranted binding")
        if ns == "Status" and method == "Last":      # INV-18: read out-of-band fallible-hook status
            vm.regs[rd] = self.host_status & MASK32
            return
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
            self.host_status = 0 if q else 3       # INV-18: EMPTY
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
        if ns == "Tensor":
            if self._tensor(vm, method, rd, rs1, rs2):
                return
        if ns == "BitLinear":
            if self._bitlinear(vm, method, rd, rs1, rs2):
                return
        # Memory + span / slice / materialize.
        if ns == "Memory" and method == "Set":
            a = vm.regs[rs1] % vm.arena_bytes
            if self.const_floor <= a < 0x8000:        # INV-9: literal const region is read-only
                raise PicoFault(PV_FAULT_CONST_WRITE, getattr(vm, "cur_pc", 0), a,
                                "write to read-only literal const region")
            vm.mem[a] = vm.regs[rs2] & 0xFF
            return
        if ns == "Memory" and method == "SetConst":   # INV-9: compiler-only literal write
            a = vm.regs[rs1] % vm.arena_bytes
            vm.mem[a] = vm.regs[rs2] & 0xFF
            if a < self.const_floor:
                self.const_floor = a
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
        if ns == "Query":
            if self._query_helpers(vm, method, rd, rs1, rs2):
                return
        if ns == "Search":
            if self._search(vm, method, rd, rs1, rs2):
                return
        if ns == "Storage":
            if self._storage(vm, method, rd, rs1, rs2):
                return
        if ns == "Gpio":
            if self._gpio(vm, method, rd, rs1, rs2):
                return
        if ns == "Device":
            if self._device(vm, method, rd, rs1, rs2):
                return
        if ns == "Stream":
            if self._stream(vm, method, rd, rs1, rs2):
                return
        if ns == "Assert":
            if self._assert(vm, method, rd, rs1, rs2):
                return
        if ns == "Event":
            if self._event(vm, method, rd, rs1, rs2):
                return
        if ns == "Ui":
            if self._ui(vm, method, rd, rs1, rs2):
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
        if self.no_alloc:                                   # INV-5: hot-path allocation is a fault
            raise PicoFault(code=9, pc=getattr(vm, "pc", 0), detail=len(data),
                            message="arena allocation in no-alloc mode")
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
            idx = a.find(self._span_raw(vm, R[rs2]))
            self.host_status = 0 if idx >= 0 else 1     # INV-18: NOT_FOUND
            R[rd] = idx & MASK32; return True
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
            raw = self._span_raw(vm, R[rs1]).decode("ascii", "replace").strip()
            try:
                v = int(raw)                  # empty/non-numeric -> ValueError (status 2), value 0
                self.host_status = 0
            except ValueError:
                v = 0
                self.host_status = 2          # INV-18: PARSE_ERROR
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
        # Pure-integer Maths ops; the transcendentals are fixed-point Q16.16 (CORDIC,
        # byte-identical across Python/C/JS -- see _q16_* above and vm/picovm.{c,js}).
        R = vm.regs
        if method == "Sin":
            R[rd] = _q16_sincos(_sx32(R[rs1]))[0] & MASK32; return True
        if method == "Cos":
            R[rd] = _q16_sincos(_sx32(R[rs1]))[1] & MASK32; return True
        if method == "Tan":
            R[rd] = _q16_tan(_sx32(R[rs1])) & MASK32; return True
        if method == "Exp":
            R[rd] = _q16_exp(_sx32(R[rs1])) & MASK32; return True
        if method == "Log":
            R[rd] = _q16_log(_sx32(R[rs1])) & MASK32; return True
        if method == "Log10":
            R[rd] = _q16_fixmul(_q16_log(_sx32(R[rs1])), Q16_INV_LN10) & MASK32; return True
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
        # PicoCompress: the real picocompress codec (vendored picocompress.py),
        # byte-identical with the C/JS/Arduino/... ports of the same library.
        src = self._span_raw(vm, vm.regs[rs1])
        if method == "PicoCompress":
            vm.regs[rd] = self._new_span_bytes(vm, picocompress.compress(src)); return True
        if method == "PicoDecompress":
            try:
                res = picocompress.decompress(src); self.host_status = 0
            except Exception:
                res = b""; self.host_status = 2
            vm.regs[rd] = self._new_span_bytes(vm, res); return True
        # Brotli: the real micro-brotli codec (vendored picobrotli.py from picoweb),
        # byte-identical with vm/picobrotli.c and vm/picobrotli.js. Output is valid
        # RFC 7932 decodable by any browser / zlib / Node.
        if method == "BrotliCompress":
            vm.regs[rd] = self._new_span_bytes(vm, picobrotli.encode(src)); return True
        if method == "BrotliDecompress":
            try:
                res = picobrotli.decode(src); self.host_status = 0
            except Exception:
                res = b""; self.host_status = 2
            vm.regs[rd] = self._new_span_bytes(vm, res); return True
        # Real DEFLATE (RFC 1951) + gzip (RFC 1952), built into the runtime.
        if method in ("DeflateCompress", "DeflateDecompress", "GzipCompress", "GzipDecompress"):
            try:
                if method == "DeflateCompress":
                    res = _deflate(src)
                elif method == "DeflateDecompress":
                    res = _inflate(src)
                elif method == "GzipCompress":
                    res = _gzip_compress(src)
                else:
                    res = _gzip_decompress(src)
                self.host_status = 0
            except (ValueError, IndexError):
                self.host_status = 2                 # malformed input
                res = b""
            vm.regs[rd] = self._new_span_bytes(vm, res); return True
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
        if method in ("Encrypt", "Decrypt"):
            # AES-256-CTR. rs1 = 32-byte key span; rs2 = data span whose first 16 bytes are
            # the IV/counter and the rest is the payload. Returns IV || (payload ^ keystream);
            # CTR is symmetric so Encrypt and Decrypt are the same operation.
            key = self._span_raw(vm, vm.regs[rs1])
            data = self._span_raw(vm, vm.regs[rs2])
            if len(key) != 32 or len(data) < 16:
                self.host_status = 2          # INV-18: bad key length / missing IV
                vm.regs[rd] = 0; return True
            self.host_status = 0
            iv = data[:16]
            body = _aes256_ctr(key, iv, data[16:])
            vm.regs[rd] = self._new_span_bytes(vm, iv + body); return True
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

        def emit(prefix, depth):
            if depth > 64:          # INV-20: bound JSON nesting depth (matches C pjs_emit depth>64)
                return
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
                    emit(key if not prefix else prefix + b"." + key, depth + 1); skipws()
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
                    emit(ik if not prefix else prefix + b"." + ik, depth + 1); idx += 1; skipws()
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
        emit(b"", 0)
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
            _mcount = 0
            for line in self._span_raw(vm, vm.regs[rs2]).split(b"\n"):
                if b"=" in line:
                    _mcount += 1
                    if _mcount > 512:            # INV-19: bound model entries (matches C TPL_MAXMODEL)
                        raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), _mcount,
                                        "template model exceeded")
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
                if len(out) > 262144:                    # INV-19: bound total rendered output (256 KB)
                    raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), len(out),
                                    "template output exceeded")
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
                        if len(stack) >= 32:             # INV-19: bound nesting (matches C TPL_MAXDEPTH)
                            raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), 0,
                                            "template depth exceeded")
                        stack.append(["sec", prefix, 0, 0, b"", 0])
                    else:
                        i = skip_block(i)
                elif op == 0x06:                         # each LIST
                    kl = plan[i]; i += 1
                    lk = bytes(plan[i:i + kl]); i += kl
                    full = (prefix + b"." + lk) if prefix else lk
                    cnt = count_list(full)
                    if cnt > 100000:                     # INV-19: bound {{#each}} iteration count
                        raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), cnt,
                                        "template each-count exceeded")
                    if cnt == 0:
                        i = skip_block(i)
                    else:
                        if len(stack) >= 32:
                            raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), 0,
                                            "template depth exceeded")
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
        def body_span(chunk):
            if isinstance(chunk, (bytes, bytearray)):
                return self._new_span_bytes(vm, bytes(chunk))
            return self._str_span(vm, str(chunk))

        self.request_context = {
            "seq": int(seq) & MASK32,
            "principal": self._str_span(vm, str(principal)),
            "method": self._str_span(vm, str(method)),
            "path": self._str_span(vm, str(path)),
            "headers": hdr,
            "body_mode": int(body_mode) & MASK32,
            "body": [body_span(chunk) for chunk in body],
        }
        self.response_graph = []
        self.response_sealed = False
        self.response_ended = False
        self.response_mode = None
        self.response_body_started = False
        self.response_stream_closed = False

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

    def _ensure_header_phase(self):
        # I6: headers belong to the preamble/header phase, which precedes the body
        # phase. Status may still be set last, but a header may not follow a body write.
        if self.response_body_started:
            raise RuntimeError("I6 violation: header after body phase started")

    def _ensure_stream_open(self):
        # I6: body writes are illegal once the stream phase is closed (Resp.EndStream).
        if self.response_stream_closed:
            raise RuntimeError("I6 violation: body write after stream phase closed")

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

    def _resp_seal(self, explicit: bool = False):
        self._ensure_response_open()
        if self.response_sealed:
            # I3 (use-after-seal): re-sealing via the explicit verb is rejected;
            # Respond's internal seal is idempotent.
            if explicit:
                raise RuntimeError("I3 violation: response already sealed")
            return
        self.response_graph.append(self._desc("DESC_COMMIT", "SEAL", None))
        self.response_sealed = True
        if explicit and self.response_mode is None:
            self.response_mode = "stream"

    def _resp_end(self):
        self._ensure_response_open()
        if self.response_mode is None:
            self.response_mode = "unary"
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
        if method == "SetSlice":
            self.req_slice_offset = max(0, _sx32(R[rs1]))
            self.req_slice_len = max(0, _sx32(R[rs2]))
            R[rd] = 1
            return True
        if method == "BodyLen":
            idx = _sx32(R[rs1])
            h = ctx["body"][idx] if 0 <= idx < len(ctx["body"]) else 0
            R[rd] = len(self._span_raw(vm, h)) if h else 0
            return True
        if method == "BodySlice":
            idx = _sx32(R[rs1])
            h = ctx["body"][idx] if 0 <= idx < len(ctx["body"]) else 0
            data = self._span_raw(vm, h) if h else b""
            off = min(self.req_slice_offset, len(data))
            end = min(off + self.req_slice_len, len(data))
            R[rd] = self._new_span_bytes(vm, data[off:end])
            return True
        return False

    def _resp(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        R = vm.regs
        if method == "Status":
            self._resp_status(vm, R[rs1]); return True
        if method == "Header":
            self._ensure_response_open(); self._ensure_preamble_mutable(); self._ensure_header_phase()
            self.response_graph.append(self._desc("DESC_HEADER", None, {
                "name": self._span_payload(vm, R[rs1]),
                "value": self._span_payload(vm, R[rs2]),
            }))
            return True
        if method == "Write":
            self._ensure_response_open(); self._ensure_stream_open()
            self.response_body_started = True
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
            self._resp_seal(explicit=True); return True
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
            self._ensure_response_open(); self._ensure_stream_open()
            if self.response_mode != "stream":
                raise RuntimeError("I6 violation: EndStream outside stream mode (no open stream phase)")
            self.response_graph.append(self._desc("DESC_CONTROL", "END_STREAM", None))
            self.response_stream_closed = True
            return True
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

    @staticmethod
    def _i8(b: int) -> int:
        return b - 256 if b > 127 else b

    @staticmethod
    def _i32be_at(data: bytes, idx: int) -> int:
        off = idx * 4
        if off + 4 > len(data):
            return 0
        v = int.from_bytes(data[off:off + 4], "big", signed=True)
        return v

    @staticmethod
    def _i32be_pack(vals: List[int]) -> bytes:
        out = bytearray()
        for v in vals:
            out += int(_sx32(v)).to_bytes(4, "big", signed=True)
        return bytes(out)

    def _tensor(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetShape":
            self.tensor_rows = max(0, _sx32(vm.regs[rs1]))
            self.tensor_cols = max(0, _sx32(vm.regs[rs2]))
            vm.regs[rd] = 1
            return True
        if method == "DotI8":
            a = self._span_raw(vm, vm.regs[rs1])
            b = self._span_raw(vm, vm.regs[rs2])
            n = self.tensor_cols or min(len(a), len(b))
            acc = 0
            for i in range(min(n, len(a), len(b))):
                acc += self._i8(a[i]) * self._i8(b[i])
            vm.regs[rd] = acc & MASK32
            return True
        if method == "MatVecI8":
            mat = self._span_raw(vm, vm.regs[rs1])
            vec = self._span_raw(vm, vm.regs[rs2])
            rows = self.tensor_rows
            cols = self.tensor_cols or len(vec)
            vals = []
            for r in range(rows):
                acc = 0
                base = r * cols
                for c in range(cols):
                    if base + c < len(mat) and c < len(vec):
                        acc += self._i8(mat[base + c]) * self._i8(vec[c])
                vals.append(acc)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        if method in ("AddI32", "MulI32", "ScaleI32", "ReluI32", "RmsNormI32", "RoPEI32", "SoftmaxI32", "ArgMaxI32"):
            a = self._span_raw(vm, vm.regs[rs1])
            n = len(a) // 4
            if method == "ArgMaxI32":
                best_i, best_v = 0, None
                for i in range(n):
                    v = self._i32be_at(a, i)
                    if best_v is None or v > best_v:
                        best_i, best_v = i, v
                vm.regs[rd] = best_i
                return True
            vals = []
            if method == "AddI32":
                b = self._span_raw(vm, vm.regs[rs2])
                n = min(n, len(b) // 4)
                vals = [self._i32be_at(a, i) + self._i32be_at(b, i) for i in range(n)]
            elif method == "MulI32":
                b = self._span_raw(vm, vm.regs[rs2])
                n = min(n, len(b) // 4)
                vals = [_sx32((self._i32be_at(a, i) * self._i32be_at(b, i)) >> 8) for i in range(n)]
            elif method == "ScaleI32":
                scale = _sx32(vm.regs[rs2])
                vals = [self._i32be_at(a, i) * scale for i in range(n)]
            elif method == "ReluI32":
                vals = [max(0, self._i32be_at(a, i)) for i in range(n)]
            elif method == "RmsNormI32":
                import math
                b = self._span_raw(vm, vm.regs[rs2])
                ss = sum(self._i32be_at(a, i) * self._i32be_at(a, i) for i in range(n))
                rms = max(1, int(math.isqrt(max(1, ss // max(1, n)))))
                vals = []
                for i in range(n):
                    g = self._i32be_at(b, i) if i * 4 + 4 <= len(b) else 256
                    num = self._i32be_at(a, i) * g
                    vals.append(_sx32((abs(num) // rms) * (-1 if num < 0 else 1)))
            elif method == "RoPEI32":
                b = self._span_raw(vm, vm.regs[rs2])
                vals = []
                pairs = n // 2
                for i in range(pairs):
                    x = self._i32be_at(a, i * 2)
                    y = self._i32be_at(a, i * 2 + 1)
                    cs = self._i32be_at(b, i * 2) if (i * 2) * 4 + 4 <= len(b) else 32768
                    sn = self._i32be_at(b, i * 2 + 1) if (i * 2 + 1) * 4 + 4 <= len(b) else 0
                    vals.append(_sx32((x * cs - y * sn) >> 15))
                    vals.append(_sx32((x * sn + y * cs) >> 15))
            elif method == "SoftmaxI32":
                xs = [self._i32be_at(a, i) for i in range(n)]
                if xs:
                    mx = max(xs)
                    ws = [max(1, 32768 >> min(15, max(0, (mx - x) >> 8))) for x in xs]
                    s = max(1, sum(ws))
                    vals = [(w * 32767) // s for w in ws]
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        return False

    @staticmethod
    def _ternary_weight(packed: bytes, idx: int) -> int:
        if idx // 4 >= len(packed):
            return 0
        code = (packed[idx // 4] >> ((idx & 3) * 2)) & 3
        return 1 if code == 1 else (-1 if code == 2 else 0)

    def _bitlinear(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetShape":
            self.bitlinear_rows = max(0, _sx32(vm.regs[rs1]))
            self.bitlinear_cols = max(0, _sx32(vm.regs[rs2]))
            vm.regs[rd] = 1
            return True
        if method == "MatVecTernary":
            weights = self._span_raw(vm, vm.regs[rs1])
            vec = self._span_raw(vm, vm.regs[rs2])
            rows, cols = self.bitlinear_rows, self.bitlinear_cols or len(vec)
            vals = []
            for r in range(rows):
                acc = 0
                base = r * cols
                for c in range(cols):
                    if c < len(vec):
                        acc += self._ternary_weight(weights, base + c) * self._i8(vec[c])
                vals.append(acc)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        return False

    def _query_helpers(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "BuildLookupFilter":
            pack = self._span_str(vm, vm.regs[rs1])
            parts = self._span_str(vm, vm.regs[rs2]).split("|")
            while len(parts) < 6:
                parts.append("")
            display, filt, op, value, current_id_field, current_id = parts[:6]
            lines = [f"S:{display}", f"F:{pack}"]
            if filt and op:
                lines.append(f"W:{filt}|{op}|{value}")
            if current_id_field and current_id:
                lines.append(f"W:{current_id_field}|!=|{current_id}")
            vm.regs[rd] = self._str_span(vm, "\n".join(lines))
            return True
        if method == "BuildManyToManyMap":
            pack = self._span_str(vm, vm.regs[rs1])
            parts = self._span_str(vm, vm.regs[rs2]).split("|")
            while len(parts) < 3:
                parts.append("")
            source_field, source_id, target_field = parts[:3]
            vm.regs[rd] = self._str_span(vm, f"S:{target_field}\nF:{pack}\nW:{source_field}|==|{source_id}")
            return True
        return False

    @staticmethod
    def _search_key(pack: str, card: int) -> int:
        try:
            p = int(pack) & 0x3FF
        except ValueError:
            p = 0
        return (p << 22) | (card & 0x3FFFFF)

    @staticmethod
    def _search_terms(text: str) -> List[str]:
        terms, cur = [], []
        for ch in text.lower():
            if ch.isalnum():
                cur.append(ch)
            elif cur:
                terms.append("".join(cur)); cur = []
        if cur:
            terms.append("".join(cur))
        return terms

    def _record_text(self, rec: dict) -> str:
        return " ".join(str(v) for _k, v in sorted(rec.items()))

    def _search(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        pack = str(self.cur_pack)
        if method == "Clear":
            self.search_docs.clear(); self.search_results = []
            self.search_plan = {"lexical": 0, "vector": 0, "hybrid": 0, "semantic": 0}
            vm.regs[rd] = 1; return True
        if method == "UpsertText":
            card = vm.regs[rs1] & MASK32
            text = self._span_str(vm, vm.regs[rs2])
            self.search_docs[self._search_key(pack, card)] = {"card": card, "text": text, "vector": self.search_vector_sig}
            vm.regs[rd] = 1; return True
        if method == "Delete":
            card = vm.regs[rs1] & MASK32
            key = self._search_key(pack, card)
            ok = 1 if key in self.search_docs else 0
            self.search_docs.pop(key, None)
            vm.regs[rd] = ok; return True
        if method == "IndexPack":
            p = str(vm.regs[rs1] & MASK32)
            n = 0
            for cid, rec in self.store.all(p):
                self.search_docs[self._search_key(p, cid)] = {"card": cid, "text": self._record_text(rec), "vector": 0}
                n += 1
            vm.regs[rd] = n; return True
        if method == "SetVector":
            self.search_vector_sig = vm.regs[rs1] & MASK32
            vm.regs[rd] = 1; return True
        if method == "SetSemanticWeight":
            self.search_semantic_weight = max(0, _sx32(vm.regs[rs1]))
            vm.regs[rd] = self.search_semantic_weight; return True
        if method in ("QueryText", "QueryHybrid"):
            q = self._span_str(vm, vm.regs[rs1])
            qterms = self._search_terms(q)
            results, lexical, vector, semantic = [], 0, 0, 0
            for key, doc in self.search_docs.items():
                dterms = self._search_terms(doc["text"])
                score = sum(dterms.count(t) for t in qterms)
                if score:
                    lexical += 1
                if method == "QueryHybrid" and self.search_vector_sig and doc.get("vector") == self.search_vector_sig:
                    score += 1; vector += 1
                if self.search_semantic_weight and q.lower() in doc["text"].lower():
                    score += self.search_semantic_weight; semantic += 1
                if score:
                    results.append((doc["card"], score, key))
            results.sort(key=lambda x: (-x[1], x[0]))
            self.search_results = results[:128]
            self.search_plan = {"lexical": lexical, "vector": vector, "hybrid": len(self.search_results), "semantic": semantic}
            vm.regs[rd] = len(self.search_results); return True
        if method == "Result":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self.search_results[idx][0] if 0 <= idx < len(self.search_results) else 0
            return True
        if method == "Score":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self.search_results[idx][1] if 0 <= idx < len(self.search_results) else 0
            return True
        if method == "Plan":
            which = _sx32(vm.regs[rs1])
            vals = [self.search_plan.get("lexical", 0), self.search_plan.get("vector", 0),
                    self.search_plan.get("hybrid", 0), self.search_plan.get("semantic", 0)]
            vm.regs[rd] = vals[which] if 0 <= which < len(vals) else 0
            return True
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
        if method == "Ready":
            vm.regs[rd] = 1
            self.host_status = 0
            return True
        if method == "IsUserPack":
            p = vm.regs[rs1] & MASK32
            vm.regs[rd] = 1 if 2 <= p <= 0x3FF else 0
            return True
        if method == "GetSchemaForPack":
            data = self.schemas.get(vm.regs[rs1] & MASK32, b"")
            vm.regs[rd] = self._new_span_bytes(vm, data)
            return True
        if method == "SetSchemaForPack":
            self.schemas[vm.regs[rs1] & MASK32] = self._span_raw(vm, vm.regs[rs2])
            vm.regs[rd] = 1
            return True
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
        if method == "SetSlice":
            self.slice_offset = max(0, _sx32(vm.regs[rs1]))
            self.slice_len = max(0, _sx32(vm.regs[rs2]))
            vm.regs[rd] = 1
            return True
        if method == "CardLen":
            cid = vm.regs[rs1] & MASK32
            vm.regs[rd] = len(self.blob_cards.get((pack, cid), bytearray()))
            return True
        if method == "ReadSlice":
            cid = vm.regs[rs1] & MASK32
            blob = self.blob_cards.get((pack, cid), bytearray())
            off = min(self.slice_offset, len(blob))
            end = min(off + self.slice_len, len(blob))
            vm.regs[rd] = self._new_span_bytes(vm, bytes(blob[off:end]))
            return True
        if method == "WriteSlice":
            cid = vm.regs[rs1] & MASK32
            data = self._span_raw(vm, vm.regs[rs2])
            key = (pack, cid)
            blob = self.blob_cards.get(key)
            if blob is None:
                blob = bytearray()
                self.blob_cards[key] = blob
            off = self.slice_offset
            if off > len(blob):
                blob.extend(b"\x00" * (off - len(blob)))
            end = off + len(data)
            if end > len(blob):
                blob.extend(b"\x00" * (end - len(blob)))
            blob[off:end] = data
            vm.regs[rd] = 1
            return True
        return False

    def _gpio(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        """Reference GPIO emulator (browser/sim). Pins carry an analog value in
        [0,1024]; dir 0=in/1=out; pull 0=none/1=up/2=down. The real pins are an
        injected OS provider on PIOS (per-pin allow-list + driver); this mirror
        keeps Python and JS byte-identical so the sim/debugger behaves the same.
        """
        if method == "Count":
            vm.regs[rd] = 40                 # reference header size (Pi-style); board config may override
            return True
        pin = vm.regs[rs1] & MASK32
        st = self.gpio.get(pin)
        if st is None:
            st = {"dir": 0, "pull": 0, "value": 0}
            self.gpio[pin] = st
        if method == "SetDir":
            st["dir"] = 1 if (vm.regs[rs2] & MASK32) else 0
            vm.regs[rd] = 1
            return True
        if method == "GetDir":
            vm.regs[rd] = st["dir"]
            return True
        if method == "SetPull":
            p = vm.regs[rs2] & MASK32
            st["pull"] = p if p in (0, 1, 2) else 0
            vm.regs[rd] = 1
            return True
        if method == "GetPull":
            vm.regs[rd] = st["pull"]
            return True
        if method == "Write":
            v = _sx32(vm.regs[rs2])
            st["value"] = 0 if v < 0 else (1024 if v > 1024 else v)
            vm.regs[rd] = 1
            return True
        if method == "Read":
            vm.regs[rd] = st["value"]
            return True
        return False

    # -- Device.*/Stream.* reference DMA-ring emulator ----------------------
    # Streaming hardware modelled as a deterministic ring so capsules are
    # authorable/testable off-device; PIOS injects the real DMA driver. RX frame
    # n byte i = (n+i)&0xFF. ringCfg packs dir(bit0:0=RX/1=TX) | bufSize<<1 |
    # frames<<16. All within the 2-in/1-out host ABI; Python == JS byte-identical.
    @staticmethod
    def _ring_frame(idx: int, buf: int) -> bytes:
        return bytes((idx + i) & 0xFF for i in range(buf))

    def _device(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Open":
            self._dev_seq += 1
            self.devices[self._dev_seq] = {"id": self._span_str(vm, vm.regs[rs1]), "open": True}
            vm.regs[rd] = self._dev_seq
            return True
        h = vm.regs[rs1] & MASK32
        dev = self.devices.get(h)
        if method == "Caps":
            vm.regs[rd] = 0x3 if (dev and dev["open"]) else 0   # stream|duplex bits
            return True
        if method == "Status":
            vm.regs[rd] = 0 if (dev and dev["open"]) else 1     # 0=OK
            return True
        if method == "Close":
            if dev:
                dev["open"] = False
            vm.regs[rd] = 1 if dev else 0
            return True
        return False

    def _stream(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Open":
            dev = self.devices.get(vm.regs[rs1] & MASK32)
            if not dev or not dev["open"]:
                self.host_status = 1                            # NOT_FOUND
                vm.regs[rd] = 0
                return True
            cfg = vm.regs[rs2] & MASK32
            self._stream_seq += 1
            self.streams[self._stream_seq] = {
                "dir": cfg & 1, "buf": (cfg >> 1) & 0x7FFF,
                "frames": (cfg >> 16) & 0xFFFF, "next": 0, "tx": [],
            }
            vm.regs[rd] = self._stream_seq
            return True
        if method == "Next":
            st = self.streams.get(vm.regs[rs1] & MASK32)
            if not st or st["next"] >= st["frames"]:
                self.host_status = 3                            # EOF/EMPTY
                vm.regs[rd] = 0
                return True
            idx = st["next"]; st["next"] += 1
            self._lease_seq += 1
            data = self._ring_frame(idx, st["buf"]) if st["dir"] == 0 else bytes(st["buf"])
            self.leases[self._lease_seq] = {"stream": vm.regs[rs1] & MASK32,
                                            "idx": idx, "data": data, "span": 0, "released": False}
            vm.regs[rd] = self._lease_seq
            return True
        if method == "Span":
            le = self.leases.get(vm.regs[rs1] & MASK32)
            if not le or le["released"]:
                self.host_status = 1
                vm.regs[rd] = 0
                return True
            if not le["span"]:
                le["span"] = self._new_span_bytes(vm, le["data"])
            vm.regs[rd] = le["span"]
            return True
        if method == "SetSlice":
            self.stream_slice_offset = max(0, _sx32(vm.regs[rs1]))
            self.stream_slice_len = max(0, _sx32(vm.regs[rs2]))
            vm.regs[rd] = 1
            return True
        if method == "Slice":
            le = self.leases.get(vm.regs[rs1] & MASK32)
            if not le or le["released"]:
                self.host_status = 1
                vm.regs[rd] = 0
                return True
            data = le["data"]
            off = min(self.stream_slice_offset, len(data))
            end = min(off + self.stream_slice_len, len(data))
            vm.regs[rd] = self._new_span_bytes(vm, data[off:end])
            return True
        if method == "Submit":                                  # TX: hand filled buffer to device
            st = self.streams.get(vm.regs[rs1] & MASK32)
            le = self.leases.get(vm.regs[rs2] & MASK32)
            if st is not None and le is not None and not le["released"]:
                st["tx"].append(self._span_raw(vm, le["span"]) if le["span"] else le["data"])
                le["released"] = True
                vm.regs[rd] = 1
            else:
                vm.regs[rd] = 0
            return True
        if method == "Release":                                 # RX: return buffer to ring
            le = self.leases.get(vm.regs[rs1] & MASK32)
            if le is not None:
                le["released"] = True
                vm.regs[rd] = 1
            else:
                vm.regs[rd] = 0
            return True
        if method == "Close":
            vm.regs[rd] = 1 if self.streams.get(vm.regs[rs1] & MASK32) else 0
            return True
        return False

    # -- Assert.* PSUnit assertion counters ---------------------------------
    # A PicoScript-authored test harness: tests call Assert.Eq/True; the runner
    # (psunit.py / the editor Tests panel) reads Assert.Failed()/Count() after a
    # run. Pure integer logic so the Python VM and the JS VM stay byte-identical.
    def _assert(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Eq":
            ok = 1 if (vm.regs[rs1] & MASK32) == (vm.regs[rs2] & MASK32) else 0
            self.assert_total += 1
            if not ok:
                self.assert_failed += 1
            vm.regs[rd] = ok
            return True
        if method == "True":
            ok = 1 if (vm.regs[rs1] & MASK32) != 0 else 0
            self.assert_total += 1
            if not ok:
                self.assert_failed += 1
            vm.regs[rd] = ok
            return True
        if method == "Count":
            vm.regs[rd] = self.assert_total & MASK32
            return True
        if method == "Failed":
            vm.regs[rd] = self.assert_failed & MASK32
            return True
        if method == "Reset":
            self.assert_total = 0
            self.assert_failed = 0
            vm.regs[rd] = 0
            return True
        return False

    # -- Event.* reactive event queue ---------------------------------------
    # The reactive core: a deterministic in-runtime FIFO of events, each a
    # (type, target, data-span) record. Post enqueues; Next dequeues the oldest
    # (0 = empty), mirroring the Stream.Next lease pattern. External event
    # sources (browser UI, PIOS timers/IRQs) inject via the same Post path, so a
    # program's event loop is identical in the sim and on hardware. Pure integer
    # + arena logic -> Python VM == JS VM byte-identical.
    def _event(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Post":
            self._event_seq += 1
            ev = self._event_seq
            self.events[ev] = {"type": vm.regs[rs1] & MASK32,
                               "target": vm.regs[rs2] & MASK32,
                               "data": None, "span": 0}
            self.event_queue.append(ev)
            vm.regs[rd] = ev
            return True
        if method == "Next":
            vm.regs[rd] = self.event_queue.pop(0) if self.event_queue else 0
            return True
        if method == "Count":
            vm.regs[rd] = len(self.event_queue)
            return True
        ev = self.events.get(vm.regs[rs1] & MASK32)
        if method == "Type":
            vm.regs[rd] = ev["type"] if ev else 0
            return True
        if method == "Target":
            vm.regs[rd] = ev["target"] if ev else 0
            return True
        if method == "Data":
            if not ev or ev["data"] is None:
                vm.regs[rd] = 0
                return True
            if not ev["span"]:
                ev["span"] = self._new_span_bytes(vm, ev["data"])
            vm.regs[rd] = ev["span"]
            return True
        if method == "SetSlice":
            self.event_slice_offset = max(0, _sx32(vm.regs[rs1]))
            self.event_slice_len = max(0, _sx32(vm.regs[rs2]))
            vm.regs[rd] = 1
            return True
        if method == "DataLen":
            vm.regs[rd] = len(ev["data"]) if ev and ev["data"] is not None else 0
            return True
        if method == "DataSlice":
            data = ev["data"] if ev and ev["data"] is not None else b""
            off = min(self.event_slice_offset, len(data))
            end = min(off + self.event_slice_len, len(data))
            vm.regs[rd] = self._new_span_bytes(vm, data[off:end])
            return True
        if method == "SetData":
            if ev is not None:
                ev["data"] = self._span_raw(vm, vm.regs[rs2])
                ev["span"] = 0
                vm.regs[rd] = 1
            else:
                vm.regs[rd] = 0
            return True
        return False

    # -- Ui.* retained scene tree + PicoWire serialize ----------------------
    # A clean, minimal remote-windowing model (RDP/X spirit, tiny): build a
    # window + boxes/text/controls as a retained tree, then Ui.Serialize emits a
    # compact, deterministic binary (PicoWire) a thin client renders; user input
    # comes back as Event.* records (target = control id). Tree + serializer live
    # in the runtime so Python VM == JS VM byte-identical. See docs/PICO_UI.md.
    _UI_KIND = {"Window": 1, "Panel": 2, "Label": 3, "Button": 4, "TextBox": 5, "Checkbox": 6}

    def _ui(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method in self._UI_KIND:
            self._ui_seq += 1
            node = self._ui_seq
            text = b""
            if method == "Window":
                parent = 0
                text = self._span_raw(vm, vm.regs[rs1])     # window title in rs1
            else:
                parent = vm.regs[rs1] & MASK32
                if method != "Panel":
                    text = self._span_raw(vm, vm.regs[rs2])  # caption/text in rs2
            self.ui_nodes[node] = {"kind": self._UI_KIND[method], "id": 0,
                                   "x": 0, "y": 0, "w": 0, "h": 0, "value": 0,
                                   "text": text, "children": []}
            p = self.ui_nodes.get(parent)
            if p is not None:
                p["children"].append(node)
            vm.regs[rd] = node
            return True
        nd = self.ui_nodes.get(vm.regs[rs1] & MASK32)
        if method == "Pos":
            v = vm.regs[rs2] & MASK32
            if nd:
                nd["x"] = (v >> 16) & 0xFFFF; nd["y"] = v & 0xFFFF
            vm.regs[rd] = 1 if nd else 0
            return True
        if method == "Size":
            v = vm.regs[rs2] & MASK32
            if nd:
                nd["w"] = (v >> 16) & 0xFFFF; nd["h"] = v & 0xFFFF
            vm.regs[rd] = 1 if nd else 0
            return True
        if method == "SetText":
            if nd:
                nd["text"] = self._span_raw(vm, vm.regs[rs2])
            vm.regs[rd] = 1 if nd else 0
            return True
        if method == "SetId":
            if nd:
                nd["id"] = vm.regs[rs2] & 0xFFFF
            vm.regs[rd] = 1 if nd else 0
            return True
        if method == "SetValue":
            if nd:
                nd["value"] = vm.regs[rs2] & 0xFFFF
            vm.regs[rd] = 1 if nd else 0
            return True
        if method == "Serialize":
            vm.regs[rd] = self._new_span_bytes(vm, self._ui_wire(vm.regs[rs1] & MASK32))
            return True
        return False

    @staticmethod
    def _u16(out: bytearray, v: int) -> None:
        out.append((v >> 8) & 0xFF); out.append(v & 0xFF)

    @staticmethod
    def _psc1_int(out: bytearray, key: bytes, v: int) -> None:
        out.append(len(key)); out += key
        out.append(1)                                   # T_INT (picoserializer)
        out += (v & 0xFFFFFFFF).to_bytes(4, "big")

    @staticmethod
    def _psc1_str(out: bytearray, key: bytes, vb: bytes) -> None:
        vb = vb[:0xFFFF]
        out.append(len(key)); out += key
        out.append(2)                                   # T_STR (picoserializer)
        out += len(vb).to_bytes(2, "big"); out += vb

    def _ui_wire(self, root: int) -> bytes:
        """PicoWire document: a u16 node count then a pre-order DFS of nodes, each
        encoded as a canonical PicoSerializer (PSC1) record -- so the windowing
        wire reuses the same byte format as the card data plane (picoserializer.py,
        MAGIC 'PSC1', T_INT/T_STR, sorted keys), not a private format. Per-node
        fields: c=kind ch=childCount h id t=text v=value w x y (sorted). Big-endian
        and deterministic -> Python VM == JS VM, and every node is PSC1-decodable."""
        order: List[int] = []

        def walk(nid: int) -> None:
            nd = self.ui_nodes.get(nid)
            if nd is None:
                return
            order.append(nid)
            for c in nd["children"]:
                walk(c)

        if root in self.ui_nodes:
            walk(root)
        out = bytearray()
        self._u16(out, len(order))
        for nid in order:
            nd = self.ui_nodes[nid]
            out += b"PSC1"
            self._u16(out, 9)                           # 9 fields per node record
            self._psc1_int(out, b"c", nd["kind"])
            self._psc1_int(out, b"ch", len(nd["children"]))
            self._psc1_int(out, b"h", nd["h"])
            self._psc1_int(out, b"id", nd["id"])
            self._psc1_str(out, b"t", nd["text"])
            self._psc1_int(out, b"v", nd["value"])
            self._psc1_int(out, b"w", nd["w"])
            self._psc1_int(out, b"x", nd["x"])
            self._psc1_int(out, b"y", nd["y"])
        return bytes(out)


class Halt(Exception):
    pass


class PicoVM:
    """Deterministic interpreter for the 16-opcode PicoScript ISA."""

    def __init__(self, host: Optional[HostApi] = None, max_steps: int = 1_000_000,
                 arena_bytes: int = ARENA_BYTES, caps: Optional[int] = None,
                 seed: Optional[int] = None, no_alloc: Optional[bool] = None):
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
        if caps is not None:                 # restrict granted bindings (INV-17)
            self.host.caps = caps
        if seed is not None:                 # host-injected Random.U32 seed (INV-15)
            self.host.rng_state = seed
        if no_alloc is not None:             # hot-path no-allocation mode (INV-5)
            self.host.no_alloc = no_alloc
        self.max_steps = max_steps
        self.steps = 0
        self.pc = 0
        self.cur_pc = 0
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
        self.cur_pc = 0
        self.halted = False
        self.steps = 0

    def _verify(self):
        """INV-10: reject static out-of-range JUMP/CALL/BRANCH targets before execution
        (register/indexed jumps are dynamic -> runtime-checked in _step)."""
        n = len(self.program)
        for i, word in enumerate(self.program):
            d = isa.decode_instruction(word)
            op, rs2, imm16 = d["opcode"], d["rs2"], d["imm16"]
            if op == isa.OP_JUMP and rs2 == 0:
                tgt = imm16
            elif op == isa.OP_CALL:
                tgt = imm16
            elif op == isa.OP_BRANCH:
                tgt = i + _sx16(imm16)
            else:
                continue
            if tgt < 0 or tgt > n:
                raise PicoFault(PV_FAULT_BAD_JUMP, i, tgt, f"bad static target {tgt} at pc={i}")

    def run(self, words: Optional[List[int]] = None) -> "PicoVM":
        if words is not None:
            self.load(words)
        self._verify()                       # INV-10: verify before execution
        try:
            while not self.halted:
                if self.pc >= len(self.program):
                    break
                if self.steps >= self.max_steps:
                    raise PicoFault(PV_FAULT_STEP_BUDGET, self.pc, 0,
                                    f"step budget exceeded ({self.max_steps})")
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
        self.cur_pc = cur
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
                raise PicoFault(PV_FAULT_BAD_JUMP, cur, tgt, f"bad jump target {tgt} at pc={cur}")
            self.pc = tgt
        elif op == isa.OP_BRANCH:
            if self._cond(rs2, self.regs[rd], self.regs[rs1]):
                tgt = cur + _sx16(imm16)
                if tgt < 0 or tgt > len(self.program):
                    raise PicoFault(PV_FAULT_BAD_JUMP, cur, tgt, f"bad branch target {tgt} at pc={cur}")
                self.pc = tgt
        elif op == isa.OP_CALL:
            if imm16 < 0 or imm16 > len(self.program):
                raise PicoFault(PV_FAULT_BAD_JUMP, cur, imm16, f"bad call target {imm16} at pc={cur}")
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
            raise PicoFault(PV_FAULT_BAD_OPCODE, cur, op, f"bad opcode {op:#x} at pc={cur}")

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
