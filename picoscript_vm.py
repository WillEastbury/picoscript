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
    NAMED_CONSTANTS,
    to_locale,
)

# Reverse host-hook table: hook code -> (namespace, method)
_HOOK_BY_CODE: Dict[int, tuple] = {code: key for key, code in HOST_HOOK_CODES.items()}
_CT_BY_VALUE: Dict[int, str] = {v: k for k, v in CONTENT_TYPES.items()}

_TZ_BY_ID: Dict[int, str] = {
    0: "UTC",
    1: "Europe/London",
    2: "Europe/Paris",
    3: "America/New_York",
    4: "America/Chicago",
    5: "America/Denver",
    6: "America/Los_Angeles",
    7: "Asia/Tokyo",
    8: "Asia/Singapore",
    9: "Asia/Hong_Kong",
    10: "Australia/Sydney",
    11: "Asia/Dubai",
}
_CURRENCY_CODE_BY_NUM: Dict[int, str] = {}
_CURRENCY_MINOR_BY_CODE: Dict[str, int] = {}
for _k, _v in NAMED_CONSTANTS.items():
    if _k.startswith("CURRENCY_") and not _k.startswith("CURRENCY_MINOR_"):
        _code = _k.split("CURRENCY_", 1)[1]
        if len(_code) == 3:  # pragma: no branch - constant table only contains canonical 3-letter codes
            _CURRENCY_CODE_BY_NUM.setdefault(int(_v), _code)
    if _k.startswith("CURRENCY_MINOR_"):
        _code = _k.split("CURRENCY_MINOR_", 1)[1]
        if len(_code) == 3:  # pragma: no branch - constant table only contains canonical 3-letter codes
            _CURRENCY_MINOR_BY_CODE[_code] = int(_v)

MASK32 = 0xFFFFFFFF
ARENA_BYTES = 520 * 1024                  # PicoVM data arena = RP2350 (Pico 2) 520 KB SRAM
HTML_MAX_DEPTH = 32                       # Html.* DOM tree walk bound (matches template TPL_MAXDEPTH
                                           # convention) -- protects Serialize/QuerySelector against a
                                           # script-constructed cycle (AddChildNode has no cycle check,
                                           # same simplicity/determinism-over-defensiveness tradeoff as
                                           # every other handle-table namespace); stops descending rather
                                           # than faulting, identically on all 3 runtimes.

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


def _parse_int_tolerant(raw: str) -> "tuple[bool, int]":
    """Parse `raw` as an integer, tolerating a trailing decimal fraction by
    truncating it towards zero (e.g. "1000.0" -> 1000, "-3.75" -> -3, "5." -> 5).

    PicoScript's Number type is 32-bit-integer only (see PicoHost._mathslib:
    transcendentals are fixed-point Q16.16, not float), so any exact fractional
    value is necessarily lossy on Parse -- this mirrors the already-established
    "Floor/Ceiling/Round: integer values: identity" convention rather than
    failing closed on an input that is numerically valid, just not integer-
    formatted. This case is common in practice: callers that serialize a
    currency/decimal value via a host language's default float-to-string (e.g.
    Python's str(1000.0) == "1000.0") previously got a silent PARSE_ERROR and a
    value of 0 for every such string, even whole numbers.

    Returns (True, value) on success, (False, 0) if `raw` isn't a recognizable
    integer or decimal literal at all (unchanged behavior for garbage input).
    """
    try:
        return True, int(raw)
    except ValueError:
        pass
    if "." in raw:
        int_part, _, frac_part = raw.partition(".")
        digits = int_part[1:] if int_part[:1] in ("+", "-") else int_part
        if digits and digits.isdigit() and (frac_part == "" or frac_part.isdigit()):
            return True, int(int_part)
    return False, 0


def _default_locale_tag() -> str:
    import locale as _locale
    tag = _locale.getlocale()[0] or _locale.getdefaultlocale()[0]  # type: ignore[attr-defined]
    return tag or "en-US"


def _default_timezone_name() -> str:
    import datetime
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    if key:
        return str(key)
    if tzinfo is not None:
        name = tzinfo.tzname(datetime.datetime.now())
        if name:  # pragma: no branch — tzname() always returns a string on real platforms
            return str(name)
    return "UTC"  # pragma: no cover


def _format_utc_offset(delta) -> str:
    total = int(delta.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total, 60)
    return f"{sign}{hh:02d}:{mm:02d}"


def _format_scaled_int(value: int, scale: int) -> str:
    scale = max(0, min(int(scale), 9))
    if scale == 0:
        return str(int(value))
    sign = "-" if value < 0 else ""
    n = abs(int(value))
    den = 10 ** scale
    whole, frac = divmod(n, den)
    return f"{sign}{whole}.{frac:0{scale}d}"


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


# ---- Decimal.*: string <-> Q16.16 conversion (see HostApi._decimallib) ------
Q16_FRAC_DIGITS = 5   # 2**16 distinct fractions -> up to ~4.8 decimal digits


def _parse_q16(raw: str) -> Optional[int]:
    """Parse a decimal literal ("1000.25", "-3.5", "1000", "1000.") into a
    Q16.16 fixed-point value (round-half-up on the fractional part beyond 16
    bits of precision). Returns None if `raw` isn't a recognizable
    integer/decimal literal (same acceptance rules as _parse_int_tolerant,
    generalized to keep the fraction instead of truncating it away)."""
    raw = raw.strip()
    if not raw:
        return None
    neg = raw[:1] == "-"
    if raw[:1] in ("+", "-"):
        raw = raw[1:]
    int_part, _, frac_part = raw.partition(".") if "." in raw else (raw, "", "")
    int_part = int_part or "0"
    if not int_part.isdigit() or not (frac_part == "" or frac_part.isdigit()):
        return None
    ipart = int(int_part)
    if frac_part:
        fnum = int(frac_part)
        fden = 10 ** len(frac_part)
        fscaled = (fnum * Q16_ONE + fden // 2) // fden   # round-half-up
    else:
        fscaled = 0
    v = (ipart << 16) + fscaled
    return -v if neg else v


def _q16_format_fixed(v: int, digits: int) -> str:
    """Render v (Q16.16, already sign-extended) to exactly `digits` decimal
    places, round-half-up (no trailing-zero trimming -- see _q16_to_str)."""
    neg = v < 0
    v = -v if neg else v
    ip, frac = v >> 16, v & 0xFFFF
    scale = 10 ** digits
    fdigits = (frac * scale + Q16_ONE // 2) // Q16_ONE
    if fdigits >= scale:      # rounding carried into the integer part
        ip += 1
        fdigits -= scale
    s = str(ip)
    if digits:
        s += "." + str(fdigits).zfill(digits)
    return ("-" + s) if (neg and (ip or fdigits)) else s


def _q16_to_str(v: int) -> str:
    """Render a Q16.16 value back to the SHORTEST decimal string that parses
    back to the exact same value (same "shortest round-trip" approach as
    Python's repr(float) / JS's Number.prototype.toString() for IEEE754 --
    tries 0, 1, 2, ... decimal digits and returns the first that round-trips).

    This matters in practice: a naive fixed-precision render at full Q16.16
    precision shows binary-fraction noise on very common values, e.g.
    "19.99" -> nearest Q16.16 -> a naive render as "19.99001" (0.01 isn't
    exactly representable in binary, the same reason 0.1+0.2 != 0.3 in
    IEEE754 float) even though "19.99" itself round-trips perfectly at 2
    digits. The shortest-round-trip search finds that "19.99" directly.
    Guarantees _parse_q16(_q16_to_str(x)) == x for every representable value."""
    v = _sx32(v)
    for digits in range(0, Q16_FRAC_DIGITS + 1):
        s = _q16_format_fixed(v, digits)
        if _parse_q16(s) == v:
            return s
    return _q16_format_fixed(v, Q16_FRAC_DIGITS)  # pragma: no cover - always found by full precision


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
            if acc > 0x7FFFFFFF:  # pragma: no cover - guarded by Q16_EXP_MAX_Z
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
        for j in range(15, -1, -1):  # pragma: no branch
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
                if length > match_len:  # pragma: no branch
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
                raise ValueError("bad compressed data")  # pragma: no cover — valid Huffman trees are bounded

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
            if length > 15:  # pragma: no cover — valid Huffman trees are bounded
                raise ValueError("bad compressed data")

    lengths = []
    while len(lengths) < hlit + hdist:  # pragma: no branch
        s = csym()
        if s < 16:
            lengths.append(s)
        elif s == 16:
            lengths.extend([lengths[-1]] * (take(2) + 3))
        elif s == 17:
            lengths.extend([0] * (take(3) + 3))  # pragma: no cover — short zero run: zlib prefers code 18
        else:
            lengths.extend([0] * (take(7) + 11))  # pragma: no branch — code 18 = long zero run
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
CAP_PROCESS = 1 << 16           # Process.*/Env.* (process lifecycle, environment vars)
CAP_TIMER   = 1 << 17           # Timer.*/Scheduler.* (timers, deterministic time)
CAP_PRINCIPAL = 1 << 18         # Principal.*/Capability.*/Sandbox.* (identity, authz)
CAP_ERROR   = 0                 # Error.* is a pure language primitive -- no cap needed
CAP_CAPSULE_EXEC = 1 << 19     # Capsule.* execution (inter-card module switching)
CAP_ALL     = 0xFFFFF           # default grant: every binding (host restricts to gate)

_CAP_BY_NS = {
    "Kernel": CAP_KERNEL, "Queue": CAP_QUEUE, "Random": CAP_RANDOM,
    "Req": CAP_NET, "Resp": CAP_NET, "Net": CAP_NET,
    "Storage": CAP_STORAGE, "DateTime": CAP_TIME, "Context": CAP_CONTEXT,
    "Data": CAP_STORAGE,
    "Auth": CAP_AUTH, "X509": CAP_AUTH, "Environment": CAP_ENV, "Locale": CAP_ENV,
    "Gpio": CAP_GPIO,
    "Pack": CAP_CAPSULE, "Card": CAP_CAPSULE, "Fifo": CAP_CAPSULE,
    "Device": CAP_DEVICE, "Stream": CAP_DMA,
    "Event": CAP_EVENT, "Ui": CAP_UI,
    "Search": CAP_STORAGE,
    "Process": CAP_PROCESS, "Env": CAP_PROCESS,
    "Timer": CAP_TIMER, "Scheduler": CAP_TIMER,
    "Principal": CAP_PRINCIPAL, "Capability": CAP_PRINCIPAL, "Sandbox": CAP_PRINCIPAL,
    "Capsule": CAP_CAPSULE_EXEC,
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


# ---- shared deserializers (mirror vm/picovm.js + vm/picovm.c) ----------------
def _hexv(c):
    if 48 <= c <= 57:
        return c - 48
    if 97 <= c <= 102:
        return c - 87
    if 65 <= c <= 70:
        return c - 55
    return 0


def _push_utf8(out, cp):
    if cp < 0x80:
        out.append(cp)
    elif cp < 0x800:
        out.append(0xC0 | (cp >> 6))
        out.append(0x80 | (cp & 0x3F))
    else:
        out.append(0xE0 | (cp >> 12))
        out.append(0x80 | ((cp >> 6) & 0x3F))
        out.append(0x80 | (cp & 0x3F))


def _json_parse_object(b, put_si, put_ss, put_ns):
    """Parse a flat JSON object; scalars decoded, nested captured as raw substring."""
    n = len(b)
    i = 0

    def skip():
        nonlocal i
        while i < n and b[i] in (32, 9, 10, 13):
            i += 1

    def pstr():
        nonlocal i
        i += 1
        out = bytearray()
        while i < n:
            c = b[i]
            i += 1
            if c == 34:
                break
            if c == 92:
                e = b[i]
                i += 1
                if e == 34:
                    out.append(34)
                elif e == 92:
                    out.append(92)
                elif e == 47:
                    out.append(47)
                elif e == 110:
                    out.append(10)
                elif e == 116:
                    out.append(9)
                elif e == 114:
                    out.append(13)
                elif e == 98:
                    out.append(8)
                elif e == 102:
                    out.append(12)
                elif e == 117:
                    cp = 0
                    for _ in range(4):
                        cp = (cp << 4) | _hexv(b[i])
                        i += 1
                    _push_utf8(out, cp)
                else:
                    out.append(e)
            else:
                out.append(c)
        return bytes(out)

    def praw():
        nonlocal i
        start = i
        depth = 0
        instr = False
        while i < n:
            c = b[i]
            if instr:
                if c == 92:
                    i += 2
                    continue
                if c == 34:
                    instr = False
                i += 1
                continue
            if c == 34:
                instr = True
                i += 1
                continue
            if c in (123, 91):
                depth += 1
            elif c in (125, 93):
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        return bytes(b[start:i])

    skip()
    if i >= n or b[i] != 123:
        return
    i += 1
    while i < n:
        skip()
        if i < n and b[i] == 125:
            i += 1
            break
        if i >= n or b[i] != 34:
            break
        key = pstr()
        skip()
        if i >= n or b[i] != 58:
            break
        i += 1
        skip()
        if i >= n:
            break
        c = b[i]
        if c == 34:
            put_ss(key, pstr())
        elif c in (123, 91):
            put_ss(key, praw())
        elif c == 116:
            i += 4
            put_si(key, 1)
        elif c == 102:
            i += 5
            put_si(key, 0)
        elif c == 110:
            i += 4
            put_ns(key)
        else:
            neg = (b[i] == 45)
            if b[i] in (45, 43):
                i += 1
            val = 0
            while i < n and 48 <= b[i] <= 57:
                val = val * 10 + (b[i] - 48)
                i += 1
            if i < n and b[i] in (46, 101, 69):
                i += 1
                while i < n and (48 <= b[i] <= 57 or b[i] in (46, 101, 69, 45, 43)):
                    i += 1
            put_si(key, (-val if neg else val) & MASK32)
        skip()
        if i < n and b[i] == 44:
            i += 1
            continue
        if i < n and b[i] == 125:
            i += 1
            break
        break


def _psc1_parse(b, put_si, put_ss):
    if len(b) < 6:
        return
    magic = (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]
    if (magic & MASK32) != 0x50534331:
        return
    count = (b[4] << 8) | b[5]
    pos = 6
    for _ in range(count):
        nlen = b[pos]
        pos += 1
        key = bytes(b[pos:pos + nlen])
        pos += nlen
        t = b[pos]
        pos += 1
        if t == 1:
            x = (b[pos] << 24) | (b[pos + 1] << 16) | (b[pos + 2] << 8) | b[pos + 3]
            pos += 4
            put_si(key, x & MASK32)
        elif t == 2:
            vlen = (b[pos] << 8) | b[pos + 1]
            pos += 2
            put_ss(key, bytes(b[pos:pos + vlen]))
            pos += vlen
        else:
            break


def _psc1_serialize(entries):
    es = [e for e in entries if e[0] == "s"]
    es.sort(key=lambda e: e[2])
    out = bytearray([0x50, 0x53, 0x43, 0x31, (len(es) >> 8) & 255, len(es) & 255])
    for e in es:
        kb = e[2]
        out.append(len(kb) & 255)
        out.extend(kb)
        if e[3] == "s":
            v = e[5] or b""
            out.append(2)
            out.append((len(v) >> 8) & 255)
            out.append(len(v) & 255)
            out.extend(v)
        else:
            out.append(1)
            x = (e[4] if e[3] == "i" else 0) & MASK32
            out.append((x >> 24) & 255)
            out.append((x >> 16) & 255)
            out.append((x >> 8) & 255)
            out.append(x & 255)
    return out


# ---- BSO1 (BareMetal.Binary): schema-driven, little-endian, HMAC-SHA256 signed.
# Wire-compatible with BareMetalJsTools/src/BareMetal.Binary.js. See docs/MAP.md.
_BSO1_SZ = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 4, 7: 4, 8: 8, 9: 8, 10: 4, 11: 8, 12: 16, 13: 2, 14: 0, 15: 16, 16: 9, 17: 4, 18: 8, 19: 10, 20: 8, 21: 16, 22: 4}
_BSO1_INT = {1: "u", 2: "u", 3: "s", 4: "s", 5: "u", 6: "s", 7: "u", 13: "u", 17: "s", 22: "s"}


def _le_int(b, pos, size, signed):
    v = 0
    for i in range(size):
        v += (b[pos + i] if 0 <= pos + i < len(b) else 0) << (8 * i)
    if signed:
        lim = 1 << (size * 8 - 1)
        if v >= lim:
            v -= lim << 1
    return v


def _bso1_schema(map_obj):
    members = []
    version = 1
    if map_obj:
        for e in map_obj.values():
            nm = bytes(e[2])
            if nm and nm[0] == 58:  # ':' pseudo-field
                if nm == b":version" and e[3] == "i":
                    version = e[4]
                continue
            code = e[4] if e[3] == "i" else 0
            members.append((nm, code & 0xFF, (code & 0x100) != 0))
    return members, version


def _bso1_read(b, members, put_si, put_ss, put_ns):
    pos = 45
    if pos >= len(b):
        return
    present = b[pos]
    pos += 1
    if present == 0:
        return
    for (name, wt, nullable) in members:
        if nullable:
            f = b[pos] if pos < len(b) else 0
            pos += 1
            if f == 0:
                put_ns(name)
                continue
        if wt == 14:  # String
            ln = _le_int(b, pos, 4, True)
            pos += 4
            if ln < 0:
                put_ns(name)
            else:
                put_ss(name, bytes(b[pos:pos + ln]))
                pos += ln
        elif wt in _BSO1_INT:
            sz = _BSO1_SZ[wt]
            put_si(name, _le_int(b, pos, sz, _BSO1_INT[wt] == "s"))
            pos += sz
        else:
            sz = _BSO1_SZ[wt]
            put_ss(name, bytes(b[pos:pos + sz]))
            pos += sz


def _bso1_write(data_map, members, version, hmac_key):
    out = bytearray()

    def w32(v):
        out.append(v & 255)
        out.append((v >> 8) & 255)
        out.append((v >> 16) & 255)
        out.append((v >> 24) & 255)

    w32(0x314F5342)
    w32(3)
    w32(version & MASK32)
    out.append(0)
    out.extend(b"\x00" * 32)
    out.append(1)
    for (name, wt, nullable) in members:
        e = data_map.get(("s", bytes(name))) if data_map else None
        is_null = (e is None) or (e[3] == "n")
        if nullable:
            if is_null:
                out.append(0)
                continue
            out.append(1)
        if wt == 14:
            if is_null:
                w32((-1) & MASK32)
            else:
                sb = e[5] if (e and e[3] == "s") else b""
                w32(len(sb))
                out.extend(sb)
        elif wt in _BSO1_INT:
            sz = _BSO1_SZ[wt]
            val = e[4] if (e and e[3] == "i") else 0
            for k in range(sz):
                out.append((val >> (8 * k)) & 255)
        else:
            sz = _BSO1_SZ[wt]
            rb = e[5] if (e and e[3] == "s") else b""
            for k in range(sz):
                out.append(rb[k] if k < len(rb) else 0)
    if hmac_key:
        import hashlib
        import hmac as _hmac
        parts = bytes(out[0:13]) + bytes(out[45:])
        sig = _hmac.new(bytes(hmac_key), parts, hashlib.sha256).digest()
        out[13:45] = sig
    return bytes(out)


def _bso1_verify(b, key):
    if not key or len(b) < 45:
        return 0
    import hashlib
    import hmac as _hmac
    parts = bytes(b[0:13]) + bytes(b[45:])
    sig = _hmac.new(bytes(key), parts, hashlib.sha256).digest()
    for i in range(32):
        if (b[13 + i] if 13 + i < len(b) else 0) != sig[i]:
            return 0
    return 1


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
        self.cap_ceiling = CAP_ALL   # immutable maximum this host may grant via Capability.Request
        self.no_alloc = False        # INV-5: when True, arena allocation in a hook raises PicoFault
        self.host_status = 0         # INV-18: typed status of the last fallible hook (0=OK)
        self.const_floor = 0x8000    # INV-9: lowest literal const-pool address; [floor,0x8000) is RO
        self.const_used = set()      # addresses initialized by compiler-only Memory.SetConst
        self.log: List[str] = []
        self.handlers: Dict[tuple, Callable] = {}
        # Card store (PicoStore) + program-level Storage.* context.
        self._store = None
        self.cur_pack = 0
        self.cur_card = 0
        self.query_results: List[int] = []
        self.search_docs: Dict[int, dict] = {}
        self.search_results: List[tuple] = []
        self.search_facets: Dict[tuple, str] = {}
        self.search_numbers: Dict[tuple, int] = {}
        self.search_facet_results: List[tuple] = []
        self.search_range_results: List[int] = []
        self.search_saved = None
        self.search_meta = {"name": "", "schema": 0, "generation": 0, "flags": 0}
        self.search_plan: Dict[str, int] = {"lexical": 0, "vector": 0, "hybrid": 0, "semantic": 0}
        self.search_vector_sig = 0
        self.search_semantic_weight = 0
        self.tensor_rows = 0
        self.tensor_cols = 0
        self.bitlinear_rows = 0
        self.bitlinear_cols = 0
        self.tokenizer_tokens: List[int] = []
        self.tokenizer_vocab: List[tuple] = []  # [(piece_bytes, id), ...] longest first
        self.tokenizer_rev: Dict[int, bytes] = {}
        self.model_config: Dict[int, int] = {}
        self.model_tensors: Dict[int, dict] = {}
        self.model_block = {"start": 0, "count": 0}
        self.kv_shape = {"layers": 0, "positions": 0, "dim": 0}
        self.kv_head = 0
        self.kv_k: Dict[tuple, bytes] = {}
        self.kv_v: Dict[tuple, bytes] = {}
        self.sampling_temp = 256
        self.attn_shape = {"heads": 1, "dim": 0}
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
        # Log.* deterministic, script-visible tracing/audit log (see
        # docs/LOGGING.md): an append-only table of {level, span}, keyed by a
        # monotonic sequence id returned by Log.Write. Deliberately NOT
        # timestamped (wall-clock time is host-injected/non-deterministic by
        # this VM's own established convention -- see docs/NAMESPACE_STATUS.md
        # -- so entries are ordered by their sequence id, not a clock).
        self.logs: Dict[int, dict] = {}       # logId -> {level, span}
        self._log_seq = 0
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
        # Locale.* formatting context (language + timezone). Date/time values are
        # interpreted as UTC epoch seconds at rest and rendered with an explicit
        # timezone offset at display time.
        self.locale_tag = _default_locale_tag()
        self.locale_tz = _default_timezone_name()
        # Automatic per-request arena scope: snapshot of (arena_top, span_count)
        # taken at the first handler invocation; each subsequent request rewinds
        # to it so a reused server VM never leaks (set_arena_base() can move it
        # forward after one-time setup such as Template.Compile).
        self._handler_mark: Optional[tuple] = None
        # -- OS-worker: Process/Env lifecycle --
        self._process_seq = 0          # next fake pid
        self._process_self = 1         # current process pid
        self._process_parent = 0       # parent pid (0 = root)
        self._process_table: Dict[int, dict] = {}   # pid -> {status, exit_code, pack, entry}
        self._process_args: bytes = b""              # launch arguments
        self._env_vars: Dict[str, str] = {}          # process environment key-value
        # -- OS-worker: Timer/Scheduler --
        self._timer_seq = 0
        self._timers: Dict[int, dict] = {}  # handle -> {ms, repeat, remaining, active}
        self._elapsed_ms = 0                # simulated monotonic clock
        # -- OS-worker: Principal/Capability/Sandbox --
        self._principal_name: str = "anonymous"
        self._principal_roles: List[str] = []
        self._principal_claims: Dict[str, str] = {}
        self._sandbox_denied: int = 0    # bitmask of denied capabilities
        # -- OS-worker: Error handling --
        # A real stack (not a single slot): SetHandler pushes so try/except
        # blocks can nest correctly (the inner try's handler is active only
        # for its own body; on normal completion or once its except/finally
        # has run, PopHandler restores the enclosing try's handler, if any).
        self._error_handler_stack: List[int] = []
        # Parallel to _error_handler_stack: vm.call_stack's depth at the
        # moment each handler was pushed. A Raise/caught fault must truncate
        # vm.call_stack back to this depth before jumping to the handler --
        # otherwise a Raise from inside a called subroutine leaves a stale
        # return address on the call stack (pointing just past the CALL,
        # into the middle of the try body that should have been skipped),
        # which a LATER, unrelated RETURN pops and silently resumes there,
        # re-executing code that should never run again. Found via native-C
        # transpile's cross-function-raise test needing a ground truth to
        # compare against -- see docs/EXCEPTION_ENGINE.md's "discovered,
        # pre-existing bytecode-VM bug" section.
        self._error_handler_call_depth: List[int] = []
        self._error_code: int = 0        # last fault code (0=none)
        self._error_detail: int = 0      # last fault detail
        self._error_resume_pc: int = 0   # pc to resume from after fault
        # -- OS-worker: Capsule execution --
        self._capsule_modules: Dict[int, dict] = {}  # handle -> {pack, card, bytecode}
        self._capsule_seq = 0
        self._capsule_schedules: List[dict] = []     # [{pack, card, eventType}]
        # -- Descriptor.*: pure buffer descriptor (ptr/len/flags), no host state.
        self.descriptors: Dict[int, dict] = {}
        self._descriptor_seq = 0
        # -- Lease.*: generic capability/ownership token over a span (distinct
        # from Stream's own internal per-frame "leases" dict above, which is a
        # different, unrelated concept that predates this namespace).
        self.lease_tokens: Dict[int, dict] = {}
        self._lease_token_seq = 0
        # -- Kernel.*: ProfileStart/ProfileEnd/TracePoint reuse the Log.* table
        # (see self.logs above) so they are deterministic and script-visible
        # via the same mechanism, not a separate wall-clock profiler.
        # -- Thread.YieldCounted: deterministic cooperative-yield counter.
        self._thread_yield_count = 0
        # -- Pack.Use: a lightweight "active pack" selector, independent of
        # Storage's own cur_pack (a different namespace/concept).
        self._active_pack = 0
        # -- Fifo.*: independent named byte-channel FIFOs (distinct from the
        # single 8-channel Queue.* int FIFO above).
        self.fifo_channels: Dict[int, dict] = {}
        self._fifo_seq = 0
        # -- Html.* DOM tree: pure in-VM node table, no host state, so it is a
        # real, fully deterministic primitive (see docs/NAMESPACE_STATUS.md's
        # "HTML DOM + HTTP parsing" section and _htmllib below for the full
        # design). Node = {"tag": span handle, "attrs": {bytes: span handle},
        # "children": [handle, ...]}. A node is a *text* node iff its attrs
        # dict has the reserved key b"#text" (its value span is the text
        # content) -- this needs no separate flag, and lets scripts build
        # text nodes with the same CreateNode+SetAttribute primitives used
        # for elements. Handle 0 = null, matching every other handle table.
        self.html_nodes: Dict[int, dict] = {}
        self._html_node_seq = 0
        # -- Fast O(1) namespace dispatch (microcode/superinstruction-style
        # dispatch table) for every namespace that resolves to exactly ONE
        # handler regardless of `method` -- see call()'s use of
        # self._ns_dispatch below. This replaces what was previously a ~40-
        # entry sequential if/elif string-comparison chain in call() with a
        # single dict lookup for the common case. Deliberately EXCLUDES any
        # namespace with compound/split routing that this dict-first design
        # could change the behavior of if included naively:
        #   - "Json": also handled earlier by _parse_hook for method=="Parse"
        #     (shared with "Binary"), and only falls through to _textio for
        #     other methods -- putting "Json" in this dict would skip the
        #     _parse_hook attempt entirely.
        #   - "Req": has a SECOND, method-specific handler (_req_param) below
        #     for Param/ParamCount that _req() itself doesn't cover.
        #   - Random.U32, Queue.*, Bits, Dot8, Memory.*, Span.*, Arena, Data,
        #     Io.*, Pack.Use, Thread.YieldCounted: small inline single-method
        #     handlers, not worth the indirection, left in the chain as-is.
        # For these excluded cases, a dict miss just falls through to the
        # unchanged, original chain below -- zero behavior change either way.
        self._ns_dispatch: Dict[str, Callable] = {
            "Map": self._map_hook,
            "Tensor": self._tensor,
            "BitLinear": self._bitlinear,
            "Quant": self._quant,
            "Attention": self._attention,
            "Tokenizer": self._tokenizer,
            "Model": self._model,
            "Kv": self._kv,
            "Sampling": self._sampling,
            "Resp": self._resp,
            "Query": self._query_helpers,
            "Search": self._search,
            "Storage": self._storage,
            "Gpio": self._gpio,
            "Device": self._device,
            "Stream": self._stream,
            "Assert": self._assert,
            "Event": self._event,
            "Ui": self._ui,
            "String": self._stringlib,
            "Number": self._numberlib,
            "Decimal": self._decimallib,
            "Template": self._templatelib,
            "Maths": self._mathslib,
            "Compress": self._compresslib,
            "Crypto": self._cryptolib,
            "Html": self._htmllib,
            "Http": self._httplib,
            "TextRender": self._textrender,
            "Error": self._error_hook,
            "Capsule": self._capsule_exec,
            "Base64": self._base64,
            "Encoding": self._encoding,
            "DateTime": self._datetime,
            "Locale": self._locale,
            "Log": self._log_hook,
            "Descriptor": self._descriptor,
            "Lease": self._lease_ns,
            "Fifo": self._fifo,
            "Kernel": self._kernel,
            "Utf8Writer": lambda vm, method, rd, rs1, rs2: self._textio(vm, "Utf8Writer", method, rd, rs1, rs2),
            "Utf8Reader": lambda vm, method, rd, rs1, rs2: self._textio(vm, "Utf8Reader", method, rd, rs1, rs2),
            "Xml": lambda vm, method, rd, rs1, rs2: self._textio(vm, "Xml", method, rd, rs1, rs2),
            "Process": lambda vm, method, rd, rs1, rs2: self._process_env(vm, "Process", method, rd, rs1, rs2),
            "Env": lambda vm, method, rd, rs1, rs2: self._process_env(vm, "Env", method, rd, rs1, rs2),
            "Timer": lambda vm, method, rd, rs1, rs2: self._timer_scheduler(vm, "Timer", method, rd, rs1, rs2),
            "Scheduler": lambda vm, method, rd, rs1, rs2: self._timer_scheduler(vm, "Scheduler", method, rd, rs1, rs2),
            "Principal": lambda vm, method, rd, rs1, rs2: self._principal_cap(vm, "Principal", method, rd, rs1, rs2),
            "Capability": lambda vm, method, rd, rs1, rs2: self._principal_cap(vm, "Capability", method, rd, rs1, rs2),
            "Sandbox": lambda vm, method, rd, rs1, rs2: self._principal_cap(vm, "Sandbox", method, rd, rs1, rs2),
        }

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
        # Fast O(1) dispatch for every namespace resolving to exactly one
        # handler regardless of method -- see self._ns_dispatch (built once
        # in __init__) for the exact inclusion/exclusion rules.
        ns_handler = self._ns_dispatch.get(ns)
        if ns_handler is not None:
            if ns_handler(vm, method, rd, rs1, rs2):
                return
        # Parsers: Json.Parse / Binary.* (PSC1 card + BSO1 entity) -> Map.
        if (ns == "Json" and method == "Parse") or ns == "Binary":
            if self._parse_hook(vm, ns, method, rd, rs1, rs2):
                return
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
        if ns == "Queue" and method in ("DequeueBatch", "EnqueueBatch"):
            # docs/CONFORMANCE_LEVELS.md's "L3: Profiling & Amortization
            # (Optional)" tier -- an aspirational v2 batch-container API
            # ("no correctness impact if omitted") with no existing
            # container type it can return without inventing new v2
            # semantics that would preempt a future, deliberate design.
            # Explicit defined default (never a silent fallthrough leaving
            # rd untouched), same convention as every other deferred
            # namespace in this codebase.
            vm.regs[rd] = 0
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
            a = vm.regs[rs1] % vm.arena_bytes
            if self.const_floor <= a < 0x8000:        # INV-9: literal const region is read-only
                raise PicoFault(PV_FAULT_CONST_WRITE, getattr(vm, "cur_pc", 0), a,
                                "write to read-only literal const region")
            vm.mem[a] = vm.regs[rs2] & 0xFF
            return
        if ns == "Memory" and method == "SetConst":   # INV-9: compiler-only literal write
            a = vm.regs[rs1] % vm.arena_bytes
            b = vm.regs[rs2] & 0xFF
            if a in self.const_used and vm.mem[a] != b:
                raise PicoFault(PV_FAULT_CONST_WRITE, getattr(vm, "cur_pc", 0), a,
                                "conflicting write to read-only literal const region")
            vm.mem[a] = b
            self.const_used.add(a)
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
        # Program-level card store: Storage.* over a PicoStore (text via byte-spans).
        # Data.* host-bound read: no active server/data context in the reference
        # VM, so return a defined empty/0 default (mirrors vm/picovm.js exactly)
        # and let the authoritative host enforce data-dependent rules.
        if ns == "Data":
            vm.regs[rd] = self._new_span_bytes(vm, b"") if method == "FieldStr" else 0
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
        # OS-worker: Process/Env, Timer/Scheduler, Principal/Capability/Sandbox,
        # Error handling, Capsule execution.
        if ns == "Req" and method in ("Param", "ParamCount"):
            if self._req_param(vm, method, rd, rs1, rs2):  # pragma: no branch
                return
        if ns == "Pack" and method == "Use":
            self._active_pack = vm.regs[rs1] & MASK32
            vm.regs[rd] = self._active_pack
            return
        if ns == "Thread" and method == "YieldCounted":
            self._thread_yield_count += 1
            vm.regs[rd] = self._thread_yield_count & MASK32
            return
        if ns in self._RESERVED_NS:
            self._reserved_stub(vm, ns, method, rd, rs1, rs2)
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

    def _map_hook(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        # Active-handle dictionary; keys int/string, values int/string/null;
        # insertion-order enumeration. FNV-1a: offset 0x811c9dc5, prime 0x01000193,
        # 32-bit (identical across all VM implementations). See docs/MAP.md.
        R = vm.regs
        if not hasattr(self, "maps"):
            self.maps = [None]
            self.active_map = 0

        def fnv1a(bs: bytes) -> int:
            h = 0x811c9dc5
            for b in bs:
                h ^= (b & 0xFF)
                h = (h * 0x01000193) & MASK32
            return h & MASK32

        def cur():
            return self.maps[self.active_map] if 0 <= self.active_map < len(self.maps) else None

        def ikey(k):
            return ("i", k & MASK32)

        def skey(b):
            return ("s", bytes(b))

        m = cur()
        if method == "New":
            self.maps.append({})
            self.active_map = len(self.maps) - 1
            R[rd] = self.active_map & MASK32
            return True
        if method == "Use":
            h = R[rs1] & MASK32
            self.active_map = h if (0 < h < len(self.maps) and self.maps[h] is not None) else 0
            return True
        if method == "Free":
            h = R[rs1] & MASK32
            if 0 < h < len(self.maps):
                self.maps[h] = None
                if self.active_map == h:
                    self.active_map = 0
            return True
        if method == "Clear":
            if m is not None:
                m.clear()
            return True
        if method == "Count":
            R[rd] = (len(m) if m is not None else 0) & MASK32
            return True
        if method == "Hash":
            R[rd] = fnv1a(self._span_raw(vm, R[rs1]))
            return True
        # int-keyed
        if method == "PutII":
            if m is not None:
                k = R[rs1] & MASK32
                m[ikey(k)] = ("i", k, None, "i", R[rs2] & MASK32, None)
            return True
        if method == "GetII":
            e = m.get(ikey(R[rs1])) if m is not None else None
            R[rd] = (e[4] if (e and e[3] == "i") else 0) & MASK32
            self.host_status = 0 if e else 1
            return True
        if method == "HasI":
            R[rd] = 1 if (m is not None and ikey(R[rs1]) in m) else 0
            return True
        if method == "DelI":
            if m is not None:
                m.pop(ikey(R[rs1]), None)
            return True
        if method == "PutIS":
            if m is not None:
                k = R[rs1] & MASK32
                m[ikey(k)] = ("i", k, None, "s", 0, self._span_raw(vm, R[rs2]))
            return True
        if method == "GetIS":
            e = m.get(ikey(R[rs1])) if m is not None else None
            if e and e[3] == "s":
                R[rd] = self._new_span_bytes(vm, e[5]); self.host_status = 0
            else:
                R[rd] = self._new_span_bytes(vm, b""); self.host_status = 1
            return True
        if method == "PutNullI":
            if m is not None:
                k = R[rs1] & MASK32
                m[ikey(k)] = ("i", k, None, "n", 0, None)
            return True
        if method == "IsNullI":
            e = m.get(ikey(R[rs1])) if m is not None else None
            R[rd] = 1 if (e and e[3] == "n") else 0
            return True
        # string-keyed
        if method == "PutSI":
            if m is not None:
                kb = self._span_raw(vm, R[rs1])
                m[skey(kb)] = ("s", 0, kb, "i", R[rs2] & MASK32, None)
            return True
        if method == "GetSI":
            e = m.get(skey(self._span_raw(vm, R[rs1]))) if m is not None else None
            R[rd] = (e[4] if (e and e[3] == "i") else 0) & MASK32
            self.host_status = 0 if e else 1
            return True
        if method == "HasS":
            R[rd] = 1 if (m is not None and skey(self._span_raw(vm, R[rs1])) in m) else 0
            return True
        if method == "DelS":
            if m is not None:
                m.pop(skey(self._span_raw(vm, R[rs1])), None)
            return True
        if method == "PutSS":
            if m is not None:
                kb = self._span_raw(vm, R[rs1])
                m[skey(kb)] = ("s", 0, kb, "s", 0, self._span_raw(vm, R[rs2]))
            return True
        if method == "GetSS":
            e = m.get(skey(self._span_raw(vm, R[rs1]))) if m is not None else None
            if e and e[3] == "s":
                R[rd] = self._new_span_bytes(vm, e[5]); self.host_status = 0
            else:
                R[rd] = self._new_span_bytes(vm, b""); self.host_status = 1
            return True
        if method == "PutNullS":
            if m is not None:
                kb = self._span_raw(vm, R[rs1])
                m[skey(kb)] = ("s", 0, kb, "n", 0, None)
            return True
        if method == "IsNullS":
            e = m.get(skey(self._span_raw(vm, R[rs1]))) if m is not None else None
            R[rd] = 1 if (e and e[3] == "n") else 0
            return True
        # enumeration (insertion order)
        if method in ("KeyAt", "KeySpanAt", "ValAt", "ValSpanAt", "ValIsSpan"):
            vals = list(m.values()) if m is not None else []
            i = R[rs1] & MASK32
            e = vals[i] if 0 <= i < len(vals) else None
            if method == "KeyAt":
                R[rd] = (e[1] if (e and e[0] == "i") else 0) & MASK32
            elif method == "KeySpanAt":
                R[rd] = self._new_span_bytes(vm, e[2] if (e and e[0] == "s") else b"")
            elif method == "ValAt":
                R[rd] = (e[4] if (e and e[3] == "i") else 0) & MASK32
            elif method == "ValSpanAt":
                R[rd] = self._new_span_bytes(vm, e[5] if (e and e[3] == "s") else b"")
            else:  # ValIsSpan
                R[rd] = 1 if (e and e[3] == "s") else 0
            return True
        return False

    def _parse_hook(self, vm: "PicoVM", ns, method, rd, rs1, rs2) -> bool:
        # string/bytes -> structured Map. Byte scanners mirror vm/picovm.js +
        # vm/picovm.c so a parsed Map is bit-identical on every VM. See docs/MAP.md.
        R = vm.regs
        if not hasattr(self, "maps"):
            self.maps = [None]
            self.active_map = 0

        def new_map():
            self.maps.append({})
            self.active_map = len(self.maps) - 1
            return self.active_map

        if ns == "Binary" and method == "SetKey":
            kb = self._span_raw(vm, R[rs1])
            self.bso1_key = bytes(kb) if kb else None
            return True
        if ns == "Binary" and method == "ParseEntity":
            blob = self._span_raw(vm, R[rs1])
            sm = self.maps[R[rs2]] if 0 <= R[rs2] < len(self.maps) else None
            members, _ver = _bso1_schema(sm)
            h = new_map()
            m = self.maps[h]

            def bput_si(kb, v):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "i", v & MASK32, None)

            def bput_ss(kb, vb):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "s", 0, bytes(vb))

            def bput_ns(kb):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "n", 0, None)

            _bso1_read(blob, members, bput_si, bput_ss, bput_ns)
            R[rd] = h
            return True
        if ns == "Binary" and method == "SerializeEntity":
            dm = self.maps[R[rs1]] if 0 <= R[rs1] < len(self.maps) else None
            sm = self.maps[R[rs2]] if 0 <= R[rs2] < len(self.maps) else None
            members, ver = _bso1_schema(sm)
            R[rd] = self._new_span_bytes(vm, _bso1_write(dm, members, ver, getattr(self, "bso1_key", None)))
            return True
        if ns == "Binary" and method == "Verify":
            R[rd] = _bso1_verify(self._span_raw(vm, R[rs1]), getattr(self, "bso1_key", None))
            return True
        if ns == "Json" or (ns == "Binary" and method == "ParseCard"):
            b = self._span_raw(vm, R[rs1])
            h = new_map()
            m = self.maps[h]

            def put_si(kb, v):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "i", v & MASK32, None)

            def put_ss(kb, vb):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "s", 0, bytes(vb))

            def put_ns(kb):
                m[("s", bytes(kb))] = ("s", 0, bytes(kb), "n", 0, None)

            if ns == "Json":
                _json_parse_object(b, put_si, put_ss, put_ns)
            else:
                _psc1_parse(b, put_si, put_ss)
            R[rd] = h
            return True
        if ns == "Binary" and method == "SerializeCard":
            mm = self.maps[self.active_map] if 0 <= self.active_map < len(self.maps) else None
            entries = list(mm.values()) if mm else []
            R[rd] = self._new_span_bytes(vm, bytes(_psc1_serialize(entries)))
            return True
        return False

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
        if method == "Eq":
            R[rd] = 1 if a == self._span_raw(vm, R[rs2]) else 0; return True
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
        if method == "Split":
            # Real, deterministic multi-value result: the 2-in/1-out host-hook
            # ABI has no array type, so parts are stored in a fresh Map (int
            # key 0..N-1 -> string part), reusing Map.*'s already-parity-tested
            # storage rather than inventing a new container. Allocated
            # directly (bypassing Map.New's side effect on self.active_map),
            # so Split/Join never disturb a map the caller already has open.
            if not hasattr(self, "maps"):
                self.maps = [None]
                self.active_map = 0
            delim = self._span_raw(vm, R[rs2])
            parts = a.split(delim) if delim else [a]
            self.maps.append({("i", i & MASK32): ("i", i & MASK32, None, "s", 0, part)
                               for i, part in enumerate(parts)})
            R[rd] = len(self.maps) - 1
            return True
        if method == "Join":
            # rs1 = separator span (already decoded into `a` above), rs2 = the
            # Map handle returned by a prior Split (or any int-keyed 0..N-1
            # string map) to join back into a single span.
            if not hasattr(self, "maps"):
                self.maps = [None]
                self.active_map = 0
            mh = R[rs2] & MASK32
            m = self.maps[mh] if (0 < mh < len(self.maps) and self.maps[mh] is not None) else None
            if m is None:
                R[rd] = self._new_span_bytes(vm, b""); return True
            n = sum(1 for k in m if k[0] == "i")
            parts = []
            for i in range(n):
                e = m.get(("i", i & MASK32))
                parts.append(e[5] if (e and e[3] == "s") else b"")
            R[rd] = self._new_span_bytes(vm, a.join(parts))
            return True
        return False

    def _numberlib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        R = vm.regs
        if method == "Parse":
            raw = self._span_raw(vm, R[rs1]).decode("ascii", "replace").strip()
            ok, v = _parse_int_tolerant(raw)  # empty/non-numeric -> (False, 0), status 2
            self.host_status = 0 if ok else 2  # INV-18: PARSE_ERROR
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

    def _decimallib(self, vm: "PicoVM", method, rd, rs1, rs2) -> bool:
        """Decimal.*: Q16.16 fixed-point fractional numeric library (a real
        fractional value in a plain 32-bit register, byte-identical across
        Python/JS VMs -- the same encoding Maths.Sin/Cos/Exp/Log already use).
        Unlike Number.Parse (32-bit-integer only, truncates any fraction),
        this preserves the fractional part -- intended for callers that need
        exact currency/decimal arithmetic rather than integer truncation."""
        R = vm.regs
        if method == "Parse":
            raw = self._span_raw(vm, R[rs1]).decode("ascii", "replace")
            v = _parse_q16(raw)
            self.host_status = 0 if v is not None else 2  # INV-18: PARSE_ERROR
            R[rd] = (v if v is not None else 0) & MASK32; return True
        if method == "ToString":
            R[rd] = self._new_span_bytes(vm, _q16_to_str(_sx32(R[rs1])).encode()); return True
        if method == "ToInt":
            R[rd] = _q16_idiv(_sx32(R[rs1]), Q16_ONE) & MASK32; return True  # truncate towards zero
        a, b = _sx32(R[rs1]), _sx32(R[rs2])
        if method == "Add":
            R[rd] = (a + b) & MASK32; return True
        if method == "Sub":
            R[rd] = (a - b) & MASK32; return True
        if method == "Mul":
            R[rd] = _q16_fixmul(a, b) & MASK32; return True
        if method == "Div":
            R[rd] = (_q16_fixdiv(a, b) if b != 0 else 0) & MASK32; return True
        if method == "Compare":
            R[rd] = (0 if a == b else (1 if a > b else -1)) & MASK32; return True
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
            except Exception:  # pragma: no branch — picobrotli raises on bad data
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
        # -- DOM tree ops: a real, pure, deterministic node table (no host
        # state needed -- see docs/NAMESPACE_STATUS.md's "HTML DOM + HTTP
        # parsing" section and self.html_nodes above for the full design).
        # Node = {"tag": span handle, "attrs": {bytes: span handle},
        # "children": [handle, ...]}. A node is a *text* node iff its attrs
        # dict has reserved key b"#text" (its value span is the text
        # content) -- CreateNode+SetAttribute alone can build one (no
        # separate "CreateTextNode" needed), and ParseTree's internal
        # builder uses the exact same convention for text runs it parses.
        # An empty tag with no "#text" attr is a transparent fragment/
        # wrapper (used for ParseTree's synthetic multi-root wrapper, and
        # available to scripts building their own fragments the same way).
        if method == "CreateNode":
            self._html_node_seq += 1
            h = self._html_node_seq
            self.html_nodes[h] = {"tag": vm.regs[rs1], "attrs": {}, "children": []}
            vm.regs[rd] = h; return True
        if method == "AddChildNode":
            p = self.html_nodes.get(vm.regs[rs1])
            c = vm.regs[rs2]
            ok = p is not None and c in self.html_nodes
            if ok:
                p["children"].append(c)
            vm.regs[rd] = 1 if ok else 0; return True
        if method == "RemoveChildNode":
            p = self.html_nodes.get(vm.regs[rs1])
            c = vm.regs[rs2]
            ok = False
            if p is not None and c in p["children"]:
                p["children"].remove(c)
                ok = True
            vm.regs[rd] = 1 if ok else 0; return True
        if method == "SetAttribute":
            # rs2 packs "key=value" into a single span (the 2-in/1-out ABI
            # has no 3rd argument register -- same convention documented in
            # docs/NAMESPACE_STATUS.md's "3-argument ops" section). No '='
            # present -> the whole span is the key, value is empty.
            n = self.html_nodes.get(vm.regs[rs1])
            if n is not None:
                kv = self._span_raw(vm, vm.regs[rs2])
                if b"=" in kv:
                    k, v = kv.split(b"=", 1)
                else:
                    k, v = kv, b""
                n["attrs"][k] = self._new_span_bytes(vm, v)
            vm.regs[rd] = 1 if n is not None else 0; return True
        if method == "GetAttribute":
            n = self.html_nodes.get(vm.regs[rs1])
            k = self._span_raw(vm, vm.regs[rs2])
            v = n["attrs"].get(k) if n is not None else None
            if v is not None:
                vm.regs[rd] = v; self.host_status = 0
            else:
                vm.regs[rd] = self._new_span_bytes(vm, b""); self.host_status = 1  # INV-18: NOT_FOUND
            return True
        if method == "ParseTree":
            vm.regs[rd] = self._html_parse(vm, src); return True
        if method == "Serialize":
            out = self._html_serialize(vm, vm.regs[rs1], 0)
            vm.regs[rd] = self._new_span_bytes(vm, out); return True
        if method == "QuerySelector":
            sel = self._span_raw(vm, vm.regs[rs2])
            vm.regs[rd] = self._html_query(vm, vm.regs[rs1], sel, 0); return True
        return False

    def _html_new_node(self, tag_span: int) -> int:
        self._html_node_seq += 1
        h = self._html_node_seq
        self.html_nodes[h] = {"tag": tag_span, "attrs": {}, "children": []}
        return h

    def _html_parse(self, vm: "PicoVM", src: bytes) -> int:
        """Minimal, permissive HTML parser (not full HTML5 conformance --
        see docs/NAMESPACE_STATUS.md): tokenizes `<tag k="v" k2='v2'>`,
        `</tag>` (closes the innermost open element regardless of name
        match -- permissive), self-closing `<tag/>`, and a fixed void-
        element list. Everything else is a text run. Always returns a
        synthetic fragment-root handle (empty tag, no "#text" attr) whose
        children are the top-level parsed nodes, so multi-root/bare-text
        input always has a single handle to return."""
        VOID = {b"br", b"img", b"hr", b"input", b"meta", b"link", b"area",
                b"base", b"col", b"embed", b"source", b"track", b"wbr"}
        empty_tag = self._new_span_bytes(vm, b"")
        root = self._html_new_node(empty_tag)
        stack = [root]
        i, n = 0, len(src)

        def cur_parent():
            return self.html_nodes[stack[-1]]

        while i < n:
            lt = src.find(b"<", i)
            if lt < 0:
                if i < n and len(stack) < HTML_MAX_DEPTH:
                    txt = self._html_new_node(empty_tag)
                    self.html_nodes[txt]["attrs"][b"#text"] = self._new_span_bytes(vm, src[i:])
                    cur_parent()["children"].append(txt)
                break
            if lt > i:
                if len(stack) < HTML_MAX_DEPTH:
                    txt = self._html_new_node(empty_tag)
                    self.html_nodes[txt]["attrs"][b"#text"] = self._new_span_bytes(vm, src[i:lt])
                    cur_parent()["children"].append(txt)
            gt = src.find(b">", lt + 1)
            if gt < 0:
                break                                    # unterminated tag: stop (permissive)
            tag_src = src[lt + 1:gt]
            i = gt + 1
            if tag_src.startswith(b"/"):                  # closing tag
                if len(stack) > 1:
                    stack.pop()
                continue
            self_close = tag_src.endswith(b"/")
            if self_close:
                tag_src = tag_src[:-1]
            parts = tag_src.split(None, 1)
            if not parts:
                continue
            name = parts[0]
            elem = self._html_new_node(self._new_span_bytes(vm, name))
            if len(stack) < HTML_MAX_DEPTH:
                cur_parent()["children"].append(elem)
            if len(parts) > 1:
                self._html_parse_attrs(vm, elem, parts[1])
            if not self_close and name.lower() not in VOID and len(stack) < HTML_MAX_DEPTH:
                stack.append(elem)
        return root

    def _html_parse_attrs(self, vm: "PicoVM", node_handle: int, text: bytes) -> None:
        node = self.html_nodes[node_handle]
        i, n = 0, len(text)
        while i < n:
            while i < n and text[i:i + 1].isspace():
                i += 1
            start = i
            while i < n and not text[i:i + 1].isspace() and text[i] != 0x3D:  # not '='
                i += 1
            key = text[start:i]
            if not key:
                break
            while i < n and text[i:i + 1].isspace():
                i += 1
            val = b""
            if i < n and text[i] == 0x3D:                 # '='
                i += 1
                while i < n and text[i:i + 1].isspace():
                    i += 1
                if i < n and text[i] in (0x22, 0x27):      # quote/apostrophe
                    q = text[i]; i += 1; vs = i
                    end = text.find(bytes([q]), i)
                    if end < 0:
                        end = n
                    val = text[vs:end]
                    i = end + 1
                else:
                    vs = i
                    while i < n and not text[i:i + 1].isspace():
                        i += 1
                    val = text[vs:i]
            node["attrs"][key] = self._new_span_bytes(vm, val)

    def _html_serialize(self, vm: "PicoVM", handle: int, depth: int) -> bytes:
        n = self.html_nodes.get(handle)
        if n is None or depth >= HTML_MAX_DEPTH:
            return b""
        if b"#text" in n["attrs"]:
            txt = self._span_raw(vm, n["attrs"][b"#text"])
            return (txt.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")
                       .replace(b'"', b"&quot;").replace(b"'", b"&#39;"))
        tag = self._span_raw(vm, n["tag"])
        kids = b"".join(self._html_serialize(vm, c, depth + 1) for c in n["children"])
        if not tag:
            return kids                                    # transparent fragment wrapper
        attrs = b"".join(
            b' ' + k + b'="' + (self._span_raw(vm, v).replace(b"&", b"&amp;").replace(b'"', b"&quot;")) + b'"'
            for k, v in n["attrs"].items()
        )
        return b"<" + tag + attrs + b">" + kids + b"</" + tag + b">"

    def _html_query(self, vm: "PicoVM", handle: int, sel: bytes, depth: int) -> int:
        n = self.html_nodes.get(handle)
        if n is None or depth >= HTML_MAX_DEPTH:
            return 0
        if self._html_matches(vm, n, sel):
            return handle
        for c in n["children"]:
            m = self._html_query(vm, c, sel, depth + 1)
            if m:
                return m
        return 0

    def _html_matches(self, vm: "PicoVM", n: dict, sel: bytes) -> bool:
        if not sel:
            return False
        if sel[0:1] == b"#":
            v = self._html_attr_raw(vm, n, b"id")
            return v == sel[1:]
        if sel[0:1] == b".":
            v = self._html_attr_raw(vm, n, b"class")
            return sel[1:] in v.split()
        return self._html_tag_raw(vm, n) == sel

    def _html_attr_raw(self, vm: "PicoVM", n: dict, key: bytes) -> bytes:
        h = n["attrs"].get(key)
        return self._span_raw(vm, h) if h is not None else b""

    def _html_tag_raw(self, vm: "PicoVM", n: dict) -> bytes:
        return self._span_raw(vm, n["tag"])


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
            while pos[0] < n:  # pragma: no branch
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
                return  # pragma: no cover — depth bounded by 32 in template, 64 here is defensive
            skipws()
            if pos[0] >= n:
                return  # pragma: no branch
            c = s[pos[0]]
            if c == 0x7b:
                pos[0] += 1; skipws()
                if pos[0] < n and s[pos[0]] == 0x7d:
                    pos[0] += 1; return
                while pos[0] < n:  # pragma: no branch
                    skipws()
                    if pos[0] >= n or s[pos[0]] != 0x22:
                        break  # pragma: no cover — loop exit when no more keys
                    key = parse_string(); skipws()
                    if pos[0] < n and s[pos[0]] == 0x3a:  # pragma: no branch
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
                while pos[0] < n:  # pragma: no branch
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
        # ReadHeader/ReadBody/GenerateHeaders/GenerateResponse/Request/
        # RespStatus/RespHeaders/RespBody all read/write a live host
        # connection -- host-injected by design (see docs/NAMESPACE_STATUS.md).
        # Explicit empty-span default (never leave `rd` untouched), same
        # convention as the reserved namespaces.
        if method in ("ReadHeader", "ReadBody", "GenerateHeaders", "GenerateResponse",
                      "RespHeaders", "RespBody"):
            vm.regs[rd] = self._new_span_bytes(vm, b""); return True
        if method in ("Request", "RespStatus"):
            vm.regs[rd] = 0; return True
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
                while p < n and depth > 0:  # pragma: no branch
                    o = plan[p]; p += 1
                    if o == 0x01:
                        p += 2 + ((plan[p] << 8) | plan[p + 1])
                    elif o == 0x02:
                        p += 1 + plan[p]
                    elif o in (0x03, 0x04, 0x06):
                        p += 1 + plan[p]; depth += 1
                    elif o == 0x05:  # pragma: no branch
                        depth -= 1
                return p

            out = bytearray()
            prefix = b""
            stack = []                 # frames: [kind, saved_prefix, body_start, count, full, idx]
            i, n = 0, len(plan)
            while i < n:  # pragma: no branch
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
                        raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), cnt,  # pragma: no cover
                                        "template each-count exceeded")
                    if cnt == 0:
                        i = skip_block(i)
                    else:
                        if len(stack) >= 32:
                            raise PicoFault(PV_FAULT_TEMPLATE, getattr(vm, "cur_pc", 0), 0,  # pragma: no cover
                                            "template depth exceeded")
                        stack.append(["each", prefix, i, cnt, full, 0])
                        prefix = full + b".0"
                elif op == 0x05:                         # end of section / each
                    if stack:  # pragma: no branch
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
            return False  # pragma: no cover — all Xml methods handled above
        return False  # pragma: no cover — caller always routes to a valid subsystem

    def _model_lookup(self, model: str, key: str) -> str:
        prefix = key + "="
        for line in model.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        return ""

    def _textrender(self, vm, method, rd, rs1, rs2) -> bool:
        R = vm.regs
        if method == "Hole":
            model = self._span_str(vm, R[rs1])
            key = self._span_str(vm, R[rs2])
            hw = self.writers.get(1)
            if hw:
                self._w_text(vm, hw, self._xml_esc(self._model_lookup(model, key)))
                R[rd] = 1
            else:
                R[rd] = 0
            return True
        w = self.writers.get(R[rs1])
        if not w:
            R[rd] = 0
            return True
        if method == "Raw":
            self._w_span(vm, w, R[rs2]); R[rd] = 1; return True
        if method == "Text":
            self._w_text(vm, w, self._xml_esc(self._span_str(vm, R[rs2]))); R[rd] = 1; return True
        if method == "Open":
            self._w_byte(vm, w, 0x3C); self._w_span(vm, w, R[rs2]); R[rd] = 1; return True
        if method == "Attr":
            spec = self._span_str(vm, R[rs2])
            name, value = (spec.split("=", 1) + [""])[:2]
            self._w_byte(vm, w, 0x20); self._w_text(vm, w, name); self._w_text(vm, w, '="')
            self._w_text(vm, w, self._xml_esc(value)); self._w_byte(vm, w, 0x22); R[rd] = 1; return True
        if method == "OpenEnd":
            self._w_byte(vm, w, 0x3E); R[rd] = 1; return True
        if method == "Close":
            self._w_text(vm, w, "</"); self._w_span(vm, w, R[rs2]); self._w_byte(vm, w, 0x3E); R[rd] = 1; return True
        if method == "Empty":
            self._w_text(vm, w, "/>"); R[rd] = 1; return True
        if method == "Br":
            self._w_text(vm, w, "<br/>"); R[rd] = 1; return True
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
        if method == "HasAccel":
            # Reference VM is scalar-only but supports every Tensor hook semantically.
            vm.regs[rd] = 1 if self._span_str(vm, vm.regs[rs1]).lower() in ("scalar", "vm") else 0
            return True
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
            elif method == "SoftmaxI32":  # pragma: no branch
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

    def _tensor_blob_and_layout(self, tid: int):
        t = self.model_tensors.get(tid, {})
        pack = str(t.get("pack", 0))
        card = t.get("card", 0)
        offset = t.get("offset", 0)
        rows = t.get("rows", 0)
        cols = t.get("cols", 0)
        fmt = t.get("format", 0)
        elem = 1 if fmt in (1, 2, 3, 15) else 4
        return self.blob_cards.get((pack, card), bytearray()), int(offset), int(rows), int(cols), int(fmt), int(elem)

    @staticmethod
    def _decode_row_spec(spec: int, default_start: int, default_count: int, max_rows: int):
        spec &= MASK32
        if spec:
            start = (spec >> 16) & 0xFFFF
            count = spec & 0xFFFF
        else:
            start = default_start
            count = default_count
        if count <= 0:
            count = max(0, max_rows - start)
        if start < 0:
            start = 0
        if start > max_rows:
            start = max_rows
        return start, min(count, max(0, max_rows - start))

    @staticmethod
    def _base3_weight(packed: bytes, row: int, col: int, cols: int) -> int:
        row_bytes = (cols + 4) // 5
        stride = (row_bytes + 3) & ~3
        idx = row * stride + (col // 5)
        if idx >= len(packed):
            return 0
        code = packed[idx]
        trits = [0] * 5
        for i in range(4, -1, -1):
            t = code % 3
            code //= 3
            trits[i] = 0 if t == 0 else (1 if t == 1 else -1)
        return trits[col % 5]

    @staticmethod
    def _bitmap_weight(packed: bytes, row: int, col: int, cols: int) -> int:
        mask_bytes = (cols + 7) // 8
        base = row * mask_bytes * 2
        byte = col // 8
        bit = 1 << (col & 7)
        zero = base + byte < len(packed) and (packed[base + byte] & bit)
        minus = base + mask_bytes + byte < len(packed) and (packed[base + mask_bytes + byte] & bit)
        return 0 if zero else (-1 if minus else 1)

    def _model_block_matvec(self, vm: "PicoVM", rd, tid: int, vec_handle: int, fmt_kind: str) -> bool:
        blob, offset, rows, cols, _fmt, elem = self._tensor_blob_and_layout(tid)
        if cols <= 0:
            vec = self._span_raw(vm, vec_handle)
            cols = len(vec)
        else:
            vec = self._span_raw(vm, vec_handle)
        start, count = self._decode_row_spec(0, self.model_block["start"], self.model_block["count"], rows)
        vals = []
        for r in range(start, start + count):
            acc = 0
            if fmt_kind == "i8":
                base = offset + r * cols * elem
                for c in range(cols):
                    if c < len(vec) and base + c < len(blob):
                        acc += self._i8(blob[base + c]) * self._i8(vec[c])
            else:
                packed = bytes(blob[offset:])
                for c in range(cols):
                    if c >= len(vec):
                        break
                    if fmt_kind == "ternary":
                        w = self._ternary_weight(packed, r * cols + c)
                    elif fmt_kind == "bitmap":
                        w = self._bitmap_weight(packed, r, c, cols)
                    else:
                        w = self._base3_weight(packed, r, c, cols)
                    acc += w * self._i8(vec[c])
            vals.append(acc)
        vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
        return True

    def _bitlinear(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "HasFormat":
            fmt = vm.regs[rs1] & MASK32
            vm.regs[rd] = 1 if fmt in (1, 2, 3) else 0
            return True
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
        if method == "MatVecBitmap":
            weights = self._span_raw(vm, vm.regs[rs1])
            vec = self._span_raw(vm, vm.regs[rs2])
            rows, cols = self.bitlinear_rows, self.bitlinear_cols or len(vec)
            mask_bytes = (cols + 7) // 8
            vals = []
            for r in range(rows):
                acc = 0
                row = r * mask_bytes * 2
                zero = weights[row:row + mask_bytes]
                minus = weights[row + mask_bytes:row + mask_bytes * 2]
                for c in range(cols):
                    if c >= len(vec):
                        break
                    bit = 1 << (c & 7)
                    z = c // 8 < len(zero) and (zero[c // 8] & bit)
                    m = c // 8 < len(minus) and (minus[c // 8] & bit)
                    w = 0 if z else (-1 if m else 1)
                    acc += w * self._i8(vec[c])
                vals.append(acc)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        if method == "MatVecBase3":
            weights = self._span_raw(vm, vm.regs[rs1])
            vec = self._span_raw(vm, vm.regs[rs2])
            rows, cols = self.bitlinear_rows, self.bitlinear_cols or len(vec)
            row_bytes = (cols + 4) // 5
            stride = (row_bytes + 3) & ~3
            vals = []
            for r in range(rows):
                acc = 0
                row = r * stride
                col = 0
                for b in range(row_bytes):
                    code = weights[row + b] if row + b < len(weights) else 0
                    trits = [0] * 5
                    for i in range(4, -1, -1):
                        t = code % 3; code //= 3
                        trits[i] = 0 if t == 0 else (1 if t == 1 else -1)
                    for t in trits:
                        if col >= cols or col >= len(vec):
                            break
                        acc += t * self._i8(vec[col]); col += 1
                vals.append(acc)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        if method == "MatVecTernaryBlock":
            return self._model_block_matvec(vm, rd, _sx32(vm.regs[rs1]), vm.regs[rs2], "ternary")
        if method == "MatVecBitmapBlock":
            return self._model_block_matvec(vm, rd, _sx32(vm.regs[rs1]), vm.regs[rs2], "bitmap")
        if method == "MatVecBase3Block":
            return self._model_block_matvec(vm, rd, _sx32(vm.regs[rs1]), vm.regs[rs2], "base3")
        return False

    def _quant(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        data = self._span_raw(vm, vm.regs[rs1]) if method != "GroupScale" else b""
        if method == "AbsMax":
            n = len(data) // 4
            vm.regs[rd] = max((abs(self._i32be_at(data, i)) for i in range(n)), default=0) & MASK32
            return True
        if method == "QuantI8":
            scale = max(1, _sx32(vm.regs[rs2]))
            vals = []
            for i in range(len(data) // 4):
                q = int(self._i32be_at(data, i) / scale)
                vals.append(0 if q < -128 else (255 if q > 127 else (q & 0xFF)))
            vm.regs[rd] = self._new_span_bytes(vm, bytes(vals))
            return True
        if method == "DequantI8":
            scale = _sx32(vm.regs[rs2])
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack([self._i8(b) * scale for b in data]))
            return True
        if method == "ApplyScale":
            scale = _sx32(vm.regs[rs2])
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack([self._i32be_at(data, i) * scale for i in range(len(data) // 4)]))
            return True
        if method == "GroupScale":
            # Pack two 16-bit settings into rs1: high=elements, low=group size.
            spec = vm.regs[rs1] & MASK32
            n = (spec >> 16) & 0xFFFF
            group = max(1, spec & 0xFFFF)
            vals = []
            for start in range(0, n, group):
                end = min(n, start + group)
                vals.append(end - start)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        return False

    def _kv_key(self, reg: int, head: Optional[int] = None) -> tuple:
        layer = (reg >> 16) & 0xFFFF
        pos = reg & 0xFFFF
        return (layer, pos, self.kv_head if head is None else head)

    def _tokenizer(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetVocab":
            text = self._span_str(vm, vm.regs[rs1])
            vocab = []
            rev = {}
            for line in text.replace(";", "\n").splitlines():
                if not line or "=" not in line:
                    continue
                piece, sid = line.rsplit("=", 1)
                try:
                    tid = int(sid, 0)
                except ValueError:
                    continue
                b = piece.encode("utf-8")
                vocab.append((b, tid)); rev[tid] = b
            self.tokenizer_vocab = sorted(vocab, key=lambda x: (-len(x[0]), x[1]))
            self.tokenizer_rev = rev
            vm.regs[rd] = len(self.tokenizer_vocab)
            return True
        if method == "EncodeBytes":
            data = self._span_raw(vm, vm.regs[rs1])
            self.tokenizer_tokens = [b + 3 for b in data]  # SentencePiece byte fallback ids 3..258
            vm.regs[rd] = len(self.tokenizer_tokens)
            return True
        if method == "EncodeTrie":
            data = self._span_raw(vm, vm.regs[rs1])
            out = []
            i = 0
            while i < len(data):
                matched = None
                for piece, tid in self.tokenizer_vocab:
                    if piece and data.startswith(piece, i):
                        matched = (piece, tid); break
                if matched:
                    out.append(matched[1]); i += len(matched[0])
                else:
                    out.append(data[i] + 3); i += 1
            self.tokenizer_tokens = out
            vm.regs[rd] = len(out)
            return True
        if method == "DecodeBytes":
            out = bytes((t - 3) & 0xFF for t in self.tokenizer_tokens if 3 <= t <= 258)
            vm.regs[rd] = self._new_span_bytes(vm, out)
            return True
        if method == "DecodeTrie":
            out = bytearray()
            for t in self.tokenizer_tokens:
                if t in self.tokenizer_rev:
                    out += self.tokenizer_rev[t]
                elif 3 <= t <= 258:  # pragma: no branch
                    out.append((t - 3) & 0xFF)
            vm.regs[rd] = self._new_span_bytes(vm, bytes(out))
            return True
        if method == "Count":
            vm.regs[rd] = len(self.tokenizer_tokens)
            return True
        if method == "Token":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self.tokenizer_tokens[idx] if 0 <= idx < len(self.tokenizer_tokens) else 0
            return True
        return False

    def _model(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetConfig":
            self.model_config[_sx32(vm.regs[rs1])] = _sx32(vm.regs[rs2])
            vm.regs[rd] = 1; return True
        if method == "GetConfig":
            vm.regs[rd] = self.model_config.get(_sx32(vm.regs[rs1]), 0)
            return True
        if method == "TensorView":
            spec = self._span_str(vm, vm.regs[rs2]).split("|")
            spec_len = len(spec)
            while len(spec) < 6:
                spec.append("0")
            tid = _sx32(vm.regs[rs1])
            if spec_len >= 6:
                pack, card, off, rows, cols, fmt = spec[:6]
            else:
                pack, card, off, rows, cols, fmt = "0", "0", spec[0], spec[1], spec[2], spec[3]
            self.model_tensors[tid] = {
                "pack": int(pack or 0), "card": int(card or 0),
                "offset": int(off or 0), "rows": int(rows or 0),
                "cols": int(cols or 0), "format": int(fmt or 0),
            }
            vm.regs[rd] = tid; return True
        t = self.model_tensors.get(_sx32(vm.regs[rs1]), {})
        if method == "TensorOffset":
            vm.regs[rd] = t.get("offset", 0); return True
        if method == "TensorRows":
            vm.regs[rd] = t.get("rows", 0); return True
        if method == "TensorCols":
            vm.regs[rd] = t.get("cols", 0); return True
        if method == "TensorFormat":
            vm.regs[rd] = t.get("format", 0); return True
        if method == "SetBlock":
            self.model_block = {"start": max(0, _sx32(vm.regs[rs1])), "count": max(0, _sx32(vm.regs[rs2]))}
            vm.regs[rd] = 1
            return True
        if method == "ReadTensor" or method == "ReadTensorRow" or method == "ReadTensorBlock":
            tid = _sx32(vm.regs[rs1])
            blob, offset, rows, cols, _fmt, elem = self._tensor_blob_and_layout(tid)
            row_bytes = cols * elem
            if method == "ReadTensorRow":
                row = max(0, _sx32(vm.regs[rs2]))
                start = offset + row * row_bytes
                n = row_bytes
            elif method == "ReadTensorBlock":
                row, count = self._decode_row_spec(_sx32(vm.regs[rs2]), self.model_block["start"], self.model_block["count"], rows)
                start = offset + row * row_bytes
                n = count * row_bytes
            else:
                start = offset
                n = rows * row_bytes
            vm.regs[rd] = self._new_span_bytes(vm, bytes(blob[start:start + n]))
            return True
        if method == "MatVecI8Block":
            return self._model_block_matvec(vm, rd, _sx32(vm.regs[rs1]), vm.regs[rs2], "i8")
        return False

    def _kv(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetShape":
            self.kv_shape = {"layers": _sx32(vm.regs[rs1]), "positions": _sx32(vm.regs[rs2]), "dim": _sx32(vm.regs[rs2])}
            vm.regs[rd] = 1; return True
        if method == "SetHead":
            self.kv_head = max(0, _sx32(vm.regs[rs1]))
            vm.regs[rd] = self.kv_head
            return True
        key = self._kv_key(_sx32(vm.regs[rs1]), 0)
        hkey = self._kv_key(_sx32(vm.regs[rs1]))
        if method == "WriteK":
            self.kv_k[key] = self._span_raw(vm, vm.regs[rs2]); vm.regs[rd] = 1; return True
        if method == "WriteV":
            self.kv_v[key] = self._span_raw(vm, vm.regs[rs2]); vm.regs[rd] = 1; return True
        if method == "WriteKH":
            self.kv_k[hkey] = self._span_raw(vm, vm.regs[rs2]); vm.regs[rd] = 1; return True
        if method == "WriteVH":
            self.kv_v[hkey] = self._span_raw(vm, vm.regs[rs2]); vm.regs[rd] = 1; return True
        if method == "ReadK":
            vm.regs[rd] = self._new_span_bytes(vm, self.kv_k.get(key, b"")); return True
        if method == "ReadV":
            vm.regs[rd] = self._new_span_bytes(vm, self.kv_v.get(key, b"")); return True
        if method == "ReadKH":
            vm.regs[rd] = self._new_span_bytes(vm, self.kv_k.get(hkey, b"")); return True
        if method == "ReadVH":
            vm.regs[rd] = self._new_span_bytes(vm, self.kv_v.get(hkey, b"")); return True
        if method == "Len":
            vm.regs[rd] = len(self.kv_k) + len(self.kv_v); return True
        if method == "Clear":
            self.kv_k.clear(); self.kv_v.clear(); vm.regs[rd] = 1; return True
        return False

    def _sampling(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Temperature":
            self.sampling_temp = max(1, _sx32(vm.regs[rs1]))
            vm.regs[rd] = self.sampling_temp; return True
        if method == "ArgMax":
            return self._tensor(vm, "ArgMaxI32", rd, rs1, rs2)
        if method == "ArgMaxRows":
            # rs1=matrix i8 rows, rs2=activation i8. Uses Tensor shape.
            old_rd = rd
            tmp = self._tensor(vm, "MatVecI8", rd, rs1, rs2)
            if not tmp:  # pragma: no cover — MatVecI8 always returns True when shapes are set
                return False
            h = vm.regs[old_rd]
            return self._tensor(vm, "ArgMaxI32", rd, 0, 0) if False else self._argmax_span(vm, rd, h)
        if method == "TopK":
            data = self._span_raw(vm, vm.regs[rs1])
            k = max(1, _sx32(vm.regs[rs2]))
            vals = [(self._i32be_at(data, i), i) for i in range(len(data) // 4)]
            vals.sort(key=lambda x: (-x[0], x[1]))
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack([idx for _v, idx in vals[:k]]))
            return True
        return False

    def _argmax_span(self, vm: "PicoVM", rd, handle: int) -> bool:
        data = self._span_raw(vm, handle)
        best_i, best_v = 0, None
        for i in range(len(data) // 4):
            v = self._i32be_at(data, i)
            if best_v is None or v > best_v:
                best_i, best_v = i, v
        vm.regs[rd] = best_i
        return True

    def _attention(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetShape":
            self.attn_shape = {"heads": max(1, _sx32(vm.regs[rs1])), "dim": max(0, _sx32(vm.regs[rs2]))}
            vm.regs[rd] = 1
            return True
        if method == "Scores":
            q = self._span_raw(vm, vm.regs[rs1])
            k = self._span_raw(vm, vm.regs[rs2])
            dim = self.attn_shape.get("dim", 0) or min(len(q), len(k))
            nkeys = len(k) // max(1, dim)
            vals = []
            for r in range(nkeys):
                acc = 0
                base = r * dim
                for c in range(dim):
                    if c < len(q) and base + c < len(k):
                        acc += self._i8(q[c]) * self._i8(k[base + c])
                vals.append(acc)
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(vals))
            return True
        if method == "Mix":
            weights = self._span_raw(vm, vm.regs[rs1])
            values = self._span_raw(vm, vm.regs[rs2])
            dim = self.attn_shape.get("dim", 0) or 1
            n = min(len(weights) // 4, len(values) // max(1, dim))
            out = []
            for c in range(dim):
                acc = 0
                for r in range(n):
                    w = self._i32be_at(weights, r)
                    acc += w * self._i8(values[r * dim + c])
                out.append(_sx32(acc >> 15))
            vm.regs[rd] = self._new_span_bytes(vm, self._i32be_pack(out))
            return True
        if method == "Attend":
            if not self._attention(vm, "Scores", rd, rs1, rs2):  # pragma: no cover - Scores always handles
                return False
            score_span = vm.regs[rd]
            if not self._tensor(vm, "SoftmaxI32", rd, score_span, 0):  # pragma: no cover - SoftmaxI32 always handles
                return False
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
            self.search_facets.clear(); self.search_numbers.clear()
            self.search_facet_results = []; self.search_range_results = []
            self.search_plan = {"lexical": 0, "vector": 0, "hybrid": 0, "semantic": 0}
            vm.regs[rd] = 1; return True
        if method == "Configure":
            self.search_meta = {
                "name": self._span_str(vm, vm.regs[rs1]),
                "schema": vm.regs[rs2] & MASK32,
                "generation": vm.regs[rs2] & MASK32,
                "flags": 0,
            }
            vm.regs[rd] = 1; return True
        if method == "Compatible":
            vm.regs[rd] = 1 if self.search_meta.get("name") == self._span_str(vm, vm.regs[rs1]) and self.search_meta.get("schema") == (vm.regs[rs2] & MASK32) else 0
            return True
        if method == "Rebuild":
            self.search_results = []; self.search_facet_results = []; self.search_range_results = []
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
            self.search_facets = {k: v for k, v in self.search_facets.items() if k[0] != key}
            self.search_numbers = {k: v for k, v in self.search_numbers.items() if k[0] != key}
            vm.regs[rd] = ok; return True
        if method == "SetFacet":
            card = vm.regs[rs1] & MASK32
            field_val = self._span_str(vm, vm.regs[rs2]).split("|", 1)
            field, value = (field_val + [""])[:2]
            self.search_facets[(self._search_key(pack, card), field)] = value
            vm.regs[rd] = 1; return True
        if method == "SetNumber":
            card = vm.regs[rs1] & MASK32
            field_val = self._span_str(vm, vm.regs[rs2]).split("|", 1)
            field = field_val[0]
            try:
                value = int(field_val[1])
            except (IndexError, ValueError):
                value = 0
            self.search_numbers[(self._search_key(pack, card), field)] = value
            vm.regs[rd] = 1; return True
        if method == "ClearFields":
            key = self._search_key(pack, vm.regs[rs1] & MASK32)
            self.search_facets = {k: v for k, v in self.search_facets.items() if k[0] != key}
            self.search_numbers = {k: v for k, v in self.search_numbers.items() if k[0] != key}
            vm.regs[rd] = 1; return True
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
        if method == "Facets":
            field = self._span_str(vm, vm.regs[rs1])
            counts: Dict[str, int] = {}
            for (_key, f), value in self.search_facets.items():
                if f == field:  # pragma: no branch
                    counts[value] = counts.get(value, 0) + 1
            self.search_facet_results = sorted(counts.items())
            vm.regs[rd] = len(self.search_facet_results); return True
        if method == "FacetValue":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self._str_span(vm, self.search_facet_results[idx][0]) if 0 <= idx < len(self.search_facet_results) else 0
            return True
        if method == "FacetCount":
            idx = _sx32(vm.regs[rs1])
            vm.regs[rd] = self.search_facet_results[idx][1] if 0 <= idx < len(self.search_facet_results) else 0
            return True
        if method == "Range":
            field_min_max = self._span_str(vm, vm.regs[rs1]).split("|")
            field = field_min_max[0] if field_min_max else ""
            try:
                lo = int(field_min_max[1]); hi = int(field_min_max[2])
            except (IndexError, ValueError):
                lo = -0x80000000; hi = 0x7FFFFFFF
            hits = []
            for (key, f), value in self.search_numbers.items():
                if f == field and lo <= value <= hi:
                    hits.append(key & 0x3FFFFF)
            self.search_range_results = sorted(hits)
            self.search_results = [(card, 1, card) for card in self.search_range_results]
            vm.regs[rd] = len(self.search_range_results); return True
        if method == "Save":
            self.search_saved = (
                dict(self.search_docs), dict(self.search_facets), dict(self.search_numbers),
                dict(self.search_meta)
            )
            vm.regs[rd] = 1; return True
        if method == "Load":
            if self.search_saved:
                docs, facets, numbers, meta = self.search_saved
                self.search_docs = dict(docs); self.search_facets = dict(facets)
                self.search_numbers = dict(numbers); self.search_meta = dict(meta)
                vm.regs[rd] = 1
            else:
                vm.regs[rd] = 0
            return True
        if method == "JournalUpsert":
            # Deterministic sim: journal mutation is equivalent to immediate mutation.
            return self._search(vm, "UpsertText", rd, rs1, rs2)
        if method == "JournalDelete":
            return self._search(vm, "Delete", rd, rs1, rs2)
        if method == "JournalFacet":
            return self._search(vm, "SetFacet", rd, rs1, rs2)
        if method == "JournalNumber":
            return self._search(vm, "SetNumber", rd, rs1, rs2)
        if method == "JournalReplay":
            vm.regs[rd] = 1; return True
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
            if not ev["span"]:  # pragma: no branch — span is cached after first call
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

    # -- Log.* deterministic, script-visible tracing/audit log (see
    # docs/LOGGING.md) -- an append-only table any script can write
    # structured entries to (level + message span) and any script/host tool
    # can read back, in order, by sequence id. Distinct from `self.log`
    # (this class's own Python-side debug convenience list, used only for
    # internal fallback/diagnostic messages -- never exposed to scripts) --
    # Log.* is the real, working, first-class subsystem previously missing
    # entirely (see docs/DIALECT_PARITY.md's logging/tracing/auditing
    # investigation). Pure integer + arena span logic -> Python VM == JS VM
    # byte-identical, same as Event.*.
    def _log_hook(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Write":
            self._log_seq += 1
            lid = self._log_seq
            self.logs[lid] = {"level": vm.regs[rs1] & MASK32, "span": vm.regs[rs2] & MASK32}
            vm.regs[rd] = lid
            return True
        if method == "Count":
            vm.regs[rd] = len(self.logs)
            return True
        e = self.logs.get(vm.regs[rs1] & MASK32)
        if method == "Level":
            vm.regs[rd] = e["level"] if e else 0
            return True
        if method == "Message":
            vm.regs[rd] = e["span"] if e else 0
            return True
        if method == "Clear":
            self.logs.clear()
            vm.regs[rd] = 1
            return True
        return False

    # -- Descriptor.*: a pure buffer descriptor (ptr/len/flags handle table),
    # deliberately kept separate from Span.* (Span is the arena-string-library
    # view type; Descriptor adds a host/driver-facing `flags` word alongside
    # ptr/len, e.g. for DMA/IO-buffer metadata) -- no host state, so it is a
    # real, fully deterministic primitive on every runtime, unlike the
    # host-injected namespaces below. 2-in/1-out ABI: `SetFlags` is a
    # separate call from `Make` (same 2-call pattern as `String.SetReplace`).
    def _descriptor(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Make":
            self._descriptor_seq += 1
            h = self._descriptor_seq
            self.descriptors[h] = {"ptr": vm.regs[rs1] & MASK32, "len": max(0, _sx32(vm.regs[rs2])), "flags": 0}
            vm.regs[rd] = h
            return True
        d = self.descriptors.get(vm.regs[rs1] & MASK32)
        if method == "SetFlags":
            if d is not None:
                d["flags"] = vm.regs[rs2] & MASK32
            vm.regs[rd] = 1 if d is not None else 0
            return True
        if method == "GetPtr":
            vm.regs[rd] = d["ptr"] if d else 0
            return True
        if method == "GetLen":
            vm.regs[rd] = d["len"] if d else 0
            return True
        if method == "GetFlags":
            vm.regs[rd] = d["flags"] if d else 0
            return True
        if method == "CopyBatch":
            # rs1 = source descriptor handle, rs2 = destination descriptor handle;
            # copies min(src.len, dst.len) bytes src.ptr -> dst.ptr in the shared
            # arena (memcpy semantics, same convention as Span.Materialize) and
            # returns the byte count actually copied.
            src = d
            dst = self.descriptors.get(vm.regs[rs2] & MASK32)
            if not src or not dst:
                vm.regs[rd] = 0
                return True
            n = min(src["len"], dst["len"])
            vm.mem[dst["ptr"]:dst["ptr"] + n] = vm.mem[src["ptr"]:src["ptr"] + n]
            vm.regs[rd] = n
            return True
        return False

    # -- Lease.*: a generic capability/ownership token over a span + type hint.
    # Pure in-VM bookkeeping (acquire/release/validate), no host state -- a
    # real, deterministic primitive distinct from Stream.*'s own internal,
    # unrelated per-frame "leases" dict (self.leases/`_lease_seq` above),
    # which predates this namespace and is not exposed to scripts directly.
    def _lease_ns(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Acquire":
            self._lease_token_seq += 1
            h = self._lease_token_seq
            self.lease_tokens[h] = {"span": vm.regs[rs1] & MASK32, "type": vm.regs[rs2] & MASK32, "valid": True}
            vm.regs[rd] = h
            return True
        t = self.lease_tokens.get(vm.regs[rs1] & MASK32)
        if method == "Release":
            if t is not None:
                t["valid"] = False
            vm.regs[rd] = 1 if t is not None else 0
            return True
        if method in ("Validate", "CachedValidate"):
            # CachedValidate is a host-optimization hint (a real host may memoize
            # the check); the reference VM has no cache to distinguish, so both
            # give the same correct, deterministic answer.
            vm.regs[rd] = 1 if (t is not None and t["valid"]) else 0
            self.host_status = 0 if (t is not None and t["valid"]) else 1
            return True
        if method == "GetSpan":
            vm.regs[rd] = t["span"] if (t and t["valid"]) else self._new_span_bytes(vm, b"")
            return True
        if method == "GetTypeHint":
            vm.regs[rd] = t["type"] if (t and t["valid"]) else 0
            return True
        return False

    # -- Fifo.*: independent named byte-channel FIFOs (Open returns a fresh
    # channel handle so a program can have many concurrent FIFOs). Distinct
    # from Queue.* (a single fixed 8-channel int FIFO indexed by rs1 & 7) --
    # Fifo carries byte spans, not raw ints, and channels are dynamically
    # allocated. Pure in-VM deque, no host state -- deterministic everywhere.
    def _fifo(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Open":
            self._fifo_seq += 1
            h = self._fifo_seq
            self.fifo_channels[h] = {"q": []}
            vm.regs[rd] = h
            return True
        ch = self.fifo_channels.get(vm.regs[rs1] & MASK32)
        if method == "Send":
            if ch is not None:
                ch["q"].append(self._span_raw(vm, vm.regs[rs2]))
            vm.regs[rd] = 1 if ch is not None else 0
            return True
        if method == "Recv":
            if ch is not None and ch["q"]:
                vm.regs[rd] = self._new_span_bytes(vm, ch["q"].pop(0))
                self.host_status = 0
            else:
                vm.regs[rd] = self._new_span_bytes(vm, b"")
                self.host_status = 1  # NOT_FOUND -- channel empty/unknown
            return True
        if method == "Poll":
            vm.regs[rd] = len(ch["q"]) if ch is not None else 0
            return True
        return False

    # -- Kernel.*: WaitIRQ/WaitSWIRQ reuse the VM's own cooperative-yield halt
    # (identical to the raw OP_WAIT opcode); FireSWIRQ reuses the same
    # software-IRQ log line as the raw OP_RAISE opcode; ProfileStart/
    # ProfileEnd/TracePoint reuse the Log.* table (see docs/LOGGING.md) so
    # tracing is deterministic and script-visible, not a wall-clock profiler.
    def _kernel(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method in ("WaitIRQ", "WaitSWIRQ"):
            vm.waiting = True
            raise Halt()
        if method == "FireSWIRQ":
            self.log.append(f"raise swirq channel={vm.regs[rs1] & MASK32}")
            vm.regs[rd] = 1
            return True
        if method in ("ProfileStart", "ProfileEnd", "TracePoint"):
            self._log_seq += 1
            lid = self._log_seq
            level = {"ProfileStart": 100, "ProfileEnd": 101, "TracePoint": 102}[method]
            self.logs[lid] = {"level": level, "span": vm.regs[rs1] & MASK32}
            vm.regs[rd] = lid
            return True
        return False

    # -- Reserved namespaces: genuinely external/host-injected state this
    # deterministic VM has no way to source itself (identity provider,
    # physical card reader, live request/connection, OS facts, network
    # socket, PKI trust store). Every method still returns a defined,
    # documented default (0, or an empty span for text-shaped results)
    # instead of silently falling through to the generic "unknown hook"
    # log-and-continue path -- so every namespace/method is callable from
    # every dialect and VM, even where the real capability must be supplied
    # by the host/PIOS kernel. See docs/FEATURE_MATRIX.md.
    _RESERVED_NS = {"Auth", "Card", "Context", "Environment", "Net", "X509"}
    _RESERVED_SPAN_METHODS = {
        ("Auth", "GetUserCredentials"), ("Auth", "GetUserPermissions"), ("Auth", "RequestToken"),
        ("Auth", "GetToken"), ("Auth", "RefreshToken"),
        ("Context", "GetVerb"), ("Context", "GetPath"), ("Context", "GetHost"),
        ("Context", "GetRemoteAddr"), ("Context", "GetUser"), ("Context", "GetPermissions"),
        ("Context", "GetHeaders"), ("Context", "GetQueryString"), ("Context", "GetBody"),
        ("Context", "GetRequestId"), ("Context", "GetClientCert"), ("Context", "GetTraceId"),
        ("Environment", "GetOsVersion"), ("Environment", "GetHostname"), ("Environment", "GetTimeZone"),
        ("Net", "Read"),
        ("X509", "FetchCertificate"), ("X509", "GenerateCSR"), ("X509", "GenerateKeyPair"),
        ("X509", "GetCertInfo"),
    }

    def _reserved_stub(self, vm: "PicoVM", ns: str, method: str, rd, rs1, rs2):
        if (ns, method) in self._RESERVED_SPAN_METHODS:
            vm.regs[rd] = self._new_span_bytes(vm, b"")
        else:
            vm.regs[rd] = 0
        self.host_status = 1  # INV-18: NOT_FOUND -- no host binding installed

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

    # -- Process.*/Env.* OS-worker process lifecycle -------------------------
    def _process_env(self, vm: "PicoVM", ns: str, method: str, rd, rs1, rs2) -> bool:
        if ns == "Process":
            if method == "Self":
                vm.regs[rd] = self._process_self
                return True
            if method == "Parent":
                vm.regs[rd] = self._process_parent
                return True
            if method == "Spawn":
                self._process_seq += 1
                pid = self._process_seq + 100  # fake pids start at 101
                pack_id = vm.regs[rs1] & MASK32
                entry = vm.regs[rs2] & MASK32
                self._process_table[pid] = {"status": 0, "exit_code": 0,
                                            "pack": pack_id, "entry": entry}
                self.log.append(f"Process.Spawn pack={pack_id} entry={entry} -> pid={pid}")
                vm.regs[rd] = pid
                return True
            if method == "Exit":
                code = _sx32(vm.regs[rs1])
                self._process_table[self._process_self] = {"status": 1, "exit_code": code,
                                                           "pack": 0, "entry": 0}
                self.log.append(f"Process.Exit code={code}")
                raise Halt()
            if method == "Kill":
                pid = vm.regs[rs1] & MASK32
                p = self._process_table.get(pid)
                if p and p["status"] == 0:
                    p["status"] = 2; p["exit_code"] = -1
                    vm.regs[rd] = 1
                else:
                    vm.regs[rd] = 0
                return True
            if method == "Status":
                pid = vm.regs[rs1] & MASK32
                p = self._process_table.get(pid)
                vm.regs[rd] = p["status"] if p else 1  # unknown pid -> exited
                return True
            if method == "Wait":
                pid = vm.regs[rs1] & MASK32
                p = self._process_table.get(pid)
                vm.regs[rd] = (p["exit_code"] & MASK32) if p else 0
                return True
            if method == "Args":
                vm.regs[rd] = self._new_span_bytes(vm, self._process_args)
                return True
        if ns == "Env":
            if method == "Get":
                key = self._span_str(vm, vm.regs[rs1])
                val = self._env_vars.get(key)
                if val is not None:
                    vm.regs[rd] = self._new_span_bytes(vm, val.encode("utf-8"))
                else:
                    vm.regs[rd] = 0
                    self.host_status = 1  # NOT_FOUND
                return True
            if method == "Set":
                key = self._span_str(vm, vm.regs[rs1])
                val = self._span_str(vm, vm.regs[rs2])
                self._env_vars[key] = val
                vm.regs[rd] = 1
                return True
            if method == "Count":
                vm.regs[rd] = len(self._env_vars)
                return True
            if method == "Key":
                idx = vm.regs[rs1] & MASK32
                keys = sorted(self._env_vars.keys())
                if idx < len(keys):
                    vm.regs[rd] = self._new_span_bytes(vm, keys[idx].encode("utf-8"))
                else:
                    vm.regs[rd] = 0
                    self.host_status = 1
                return True
        return False

    # -- Timer.*/Scheduler.* timers and deterministic scheduler ---------------
    def _timer_scheduler(self, vm: "PicoVM", ns: str, method: str, rd, rs1, rs2) -> bool:
        if ns == "Timer":
            if method == "After":
                self._timer_seq += 1
                h = self._timer_seq
                ms = vm.regs[rs1] & MASK32
                self._timers[h] = {"ms": ms, "repeat": False,
                                   "remaining": ms, "active": True}
                vm.regs[rd] = h
                return True
            if method == "Every":
                self._timer_seq += 1
                h = self._timer_seq
                ms = vm.regs[rs1] & MASK32
                self._timers[h] = {"ms": ms, "repeat": True,
                                   "remaining": ms, "active": True}
                vm.regs[rd] = h
                return True
            if method == "Cancel":
                h = vm.regs[rs1] & MASK32
                t = self._timers.get(h)
                if t:
                    t["active"] = False
                    vm.regs[rd] = 1
                else:
                    vm.regs[rd] = 0
                return True
            if method == "Elapsed":
                vm.regs[rd] = self._elapsed_ms & MASK32
                return True
        if ns == "Scheduler":
            if method == "Tick":
                delta = vm.regs[rs1] & MASK32
                self._elapsed_ms += delta
                fired = 0
                for h, t in list(self._timers.items()):
                    if not t["active"]:
                        continue
                    t["remaining"] -= delta
                    while t["remaining"] <= 0 and t["active"]:
                        fired += 1
                        # Inject EVENT_TIMER (type=100) with target=timer_handle
                        self._event_seq += 1
                        ev = self._event_seq
                        self.events[ev] = {"type": 100, "target": h,
                                           "data": None, "span": 0}
                        self.event_queue.append(ev)
                        if t["repeat"]:
                            t["remaining"] += t["ms"]
                        else:
                            t["active"] = False
                            break
                vm.regs[rd] = fired
                return True
        return False

    # -- Principal.*/Capability.*/Sandbox.* identity & authz harness ----------
    def _principal_cap(self, vm: "PicoVM", ns: str, method: str, rd, rs1, rs2) -> bool:
        if ns == "Principal":
            if method == "Current":
                vm.regs[rd] = self._new_span_bytes(vm, self._principal_name.encode("utf-8"))
                return True
            if method == "HasRole":
                role = self._span_str(vm, vm.regs[rs1])
                vm.regs[rd] = 1 if role in self._principal_roles else 0
                return True
            if method == "Claims":
                pairs = ";".join(f"{k}={v}" for k, v in sorted(self._principal_claims.items()))
                vm.regs[rd] = self._new_span_bytes(vm, pairs.encode("utf-8"))
                return True
        if ns == "Capability":
            if method == "Has":
                cap_bit = vm.regs[rs1] & MASK32
                denied = self._sandbox_denied
                has = (self.caps & cap_bit) and not (denied & cap_bit)
                vm.regs[rd] = 1 if has else 0
                return True
            if method == "Request":
                cap_bit = vm.regs[rs1] & MASK32
                if self._sandbox_denied & cap_bit or (cap_bit & ~self.cap_ceiling):
                    vm.regs[rd] = 0  # denied by sandbox
                else:
                    self.caps |= cap_bit
                    vm.regs[rd] = 1
                return True
            if method == "Drop":
                cap_bit = vm.regs[rs1] & MASK32
                self.caps &= ~cap_bit
                vm.regs[rd] = 1
                return True
        if ns == "Sandbox":
            if method == "Deny":
                cap_bit = vm.regs[rs1] & MASK32
                self._sandbox_denied |= cap_bit
                self.caps &= ~cap_bit
                vm.regs[rd] = 1
                return True
        return False

    # -- Error.* global error handler + fault inspection ---------------------
    def _active_handler_pc(self) -> int:
        """Top of the handler stack, honoring the "0 = no handler" convention
        SetHandler(0) has always documented (a pushed 0 is a deliberate
        no-op registration, not a real jump target) -- returns 0 if the
        stack is empty OR its top entry is 0."""
        return self._error_handler_stack[-1] if self._error_handler_stack else 0

    def _error_hook(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "SetHandler":
            # Pushes -- see the handler-stack note on _error_handler_stack's
            # declaration. lower_try() pairs every SetHandler with a matching
            # PopHandler once the try block is done (success, or having run
            # except/finally), so nested try/except restores the enclosing
            # handler correctly instead of leaking an inner one. Pushing 0 is
            # a legitimate "register a no-op handler" call (matches the
            # pre-existing "0 = unset" convention) -- HasHandler/Raise below
            # look at the top entry's truthiness, not just stack depth.
            # Also records vm.call_stack's CURRENT depth (see
            # _error_handler_call_depth's declaration) so a Raise/caught
            # fault can truncate back to it before jumping.
            self._error_handler_stack.append(vm.regs[rs1] & MASK32)
            self._error_handler_call_depth.append(len(vm.call_stack))
            vm.regs[rd] = 1
            return True
        if method == "PopHandler":
            if self._error_handler_stack:
                self._error_handler_stack.pop()
                self._error_handler_call_depth.pop()
                vm.regs[rd] = 1
            else:
                vm.regs[rd] = 0
            return True
        if method == "HasHandler":
            vm.regs[rd] = 1 if self._active_handler_pc() else 0
            return True
        if method == "Code":
            vm.regs[rd] = self._error_code & MASK32
            return True
        if method == "Detail":
            vm.regs[rd] = self._error_detail & MASK32
            return True
        if method == "Resume":
            self._error_code = 0
            self._error_detail = 0
            if self._error_resume_pc:
                vm.pc = self._error_resume_pc
                self._error_resume_pc = 0
            vm.regs[rd] = 1
            return True
        if method == "Clear":
            self._error_code = 0
            self._error_detail = 0
            vm.regs[rd] = 1
            return True
        if method == "Raise":
            # Script-level "throw a value": if a handler is registered (we're
            # lexically inside a try), jump straight there -- same effect a
            # genuine VM fault has via PicoVM.run()'s PicoFault handling, just
            # triggered in-band instead of via a caught Python exception.
            # Error.Code() in the except body reads back exactly the raised
            # value (this shares one channel with real VM fault codes --
            # e.g. a script Raise(2) and a genuine bad-opcode fault are both
            # readable as Code()==2; this is a documented, accepted tradeoff,
            # not a bug: most languages share one errno/exception-code space
            # between system and user-level errors).
            # If there is NO handler, this must not be silently swallowed --
            # propagate as a real, uncaught PicoFault so it crashes the
            # program (or is caught by an *outer* frame's handler) exactly
            # like an unhandled exception would.
            code = vm.regs[rs1] & MASK32
            handler_pc = self._active_handler_pc()
            if handler_pc:
                self._error_code = code
                self._error_detail = 0
                self._error_resume_pc = vm.pc   # _step() already advanced pc past this call
                # Truncate the call stack back to what it was when this
                # handler was registered -- a Raise from inside a called
                # subroutine must discard that subroutine's (and any deeper
                # nested calls') now-abandoned return addresses, or a LATER
                # RETURN would pop one of them and resume in the middle of
                # the try body that this jump is meant to skip entirely.
                del vm.call_stack[self._error_handler_call_depth[-1]:]
                vm.pc = handler_pc
                vm.regs[rd] = 1
            else:
                raise PicoFault(code, vm.cur_pc, 0,
                                 f"unhandled Raise(code={code}) at pc={vm.cur_pc}")
            return True
        return False

    # -- Capsule.* inter-card module switching --------------------------------
    def _capsule_exec(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        if method == "Call":
            pack = vm.regs[rs1] & MASK32
            card = vm.regs[rs2] & MASK32
            self.log.append(f"Capsule.Call pack={pack} card={card}")
            vm.regs[rd] = 0  # simulated: no actual bytecode to run
            return True
        if method == "Schedule":
            pack = vm.regs[rs1] & MASK32
            card = vm.regs[rs2] & MASK32
            self._capsule_schedules.append({"pack": pack, "card": card})
            self.log.append(f"Capsule.Schedule pack={pack} card={card}")
            vm.regs[rd] = 1
            return True
        if method == "Jump":
            pack = vm.regs[rs1] & MASK32
            card = vm.regs[rs2] & MASK32
            self.log.append(f"Capsule.Jump pack={pack} card={card}")
            raise Halt()  # transfer execution ends current program
        if method == "LoadModule":
            pack = vm.regs[rs1] & MASK32
            card = vm.regs[rs2] & MASK32
            self._capsule_seq += 1
            h = self._capsule_seq
            self._capsule_modules[h] = {"pack": pack, "card": card, "bytecode": None}
            self.log.append(f"Capsule.LoadModule pack={pack} card={card} -> handle={h}")
            vm.regs[rd] = h
            return True
        if method == "RunModule":
            h = vm.regs[rs1] & MASK32
            m = self._capsule_modules.get(h)
            if m:
                self.log.append(f"Capsule.RunModule handle={h} pack={m['pack']} card={m['card']}")
                vm.regs[rd] = 0  # simulated: no bytecode
            else:
                vm.regs[rd] = 0
            return True
        return False

    # -- Base64 encode/decode ------------------------------------------------
    def _base64(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        import base64
        if method == "Encode":
            data = self._span_raw(vm, vm.regs[rs1])
            enc = base64.b64encode(bytes(data))
            vm.regs[rd] = self._new_span_bytes(vm, enc)
            return True
        if method == "UrlEncode":
            data = self._span_raw(vm, vm.regs[rs1])
            enc = base64.urlsafe_b64encode(bytes(data)).rstrip(b"=")
            vm.regs[rd] = self._new_span_bytes(vm, enc)
            return True
        if method == "Decode":
            data = self._span_raw(vm, vm.regs[rs1])
            try:
                dec = base64.b64decode(bytes(data))
            except Exception:
                dec = b""
                self.host_status = 2  # PARSE_ERROR
            vm.regs[rd] = self._new_span_bytes(vm, dec)
            return True
        if method == "UrlDecode":
            data = self._span_raw(vm, vm.regs[rs1])
            s = bytes(data).decode("ascii", "replace")
            s = s.replace("-", "+").replace("_", "/")
            pad = (4 - len(s) % 4) % 4
            s += "=" * pad
            try:
                dec = base64.b64decode(s)
            except Exception:
                dec = b""
                self.host_status = 2
            vm.regs[rd] = self._new_span_bytes(vm, dec)
            return True
        return False

    def _encoding(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        data = self._span_raw(vm, vm.regs[rs1])
        try:
            if method == "AsciiEncode":
                text = bytes(data).decode("utf-8", "replace")
                out = text.encode("ascii", "replace")
            elif method == "AsciiDecode":
                out = bytes((b if b < 128 else ord("?")) for b in data)
            elif method == "Utf8Encode":
                text = bytes(data).decode("utf-8", "replace")
                out = text.encode("utf-8")
            elif method == "Utf8Decode":
                out = bytes(data).decode("utf-8", "replace").encode("utf-8")
            elif method == "Utf16LEEncode":
                out = bytes(data).decode("utf-8", "replace").encode("utf-16le")
            elif method == "Utf16LEDecode":
                out = bytes(data).decode("utf-16le", "replace").encode("utf-8")
            elif method == "Utf16BEEncode":
                out = bytes(data).decode("utf-8", "replace").encode("utf-16be")
            elif method == "Utf16BEDecode":
                out = bytes(data).decode("utf-16be", "replace").encode("utf-8")
            elif method == "Utf7Encode":
                out = bytes(data).decode("utf-8", "replace").encode("utf-7")
            elif method == "Utf7Decode":
                out = bytes(data).decode("utf-7", "replace").encode("utf-8")
            elif method == "HexEncode":
                out = bytes(data).hex().encode("ascii")
            elif method == "HexDecode":
                raw = bytes(data).decode("ascii", "ignore").strip()
                if len(raw) & 1:
                    raw = "0" + raw
                out = bytes.fromhex(raw)
            else:
                return False
            self.host_status = 0
        except (UnicodeError, ValueError):
            out = b""
            self.host_status = 2
        vm.regs[rd] = self._new_span_bytes(vm, out)
        return True

    # -- DateTime core (UTC epoch-seconds storage) -----------------------------
    def _datetime(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        import datetime
        if method == "UtcNow" or method == "Now":
            vm.regs[rd] = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) & MASK32
            return True
        if method == "UnixTimestamp":
            vm.regs[rd] = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) & MASK32
            return True
        if method == "Parse":
            raw = self._span_str(vm, vm.regs[rs1]).strip()
            if not raw:
                self.host_status = 2
                vm.regs[rd] = 0
                return True
            parsed = None
            if raw.lstrip("+-").isdigit():
                parsed = int(raw)
            else:
                txt = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
                try:
                    dt = datetime.datetime.fromisoformat(txt)
                except ValueError:
                    dt = None
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    parsed = int(dt.timestamp())
            if parsed is None:
                self.host_status = 2
                vm.regs[rd] = 0
            else:
                self.host_status = 0
                vm.regs[rd] = int(parsed) & MASK32
            return True
        if method == "Format":
            sec = _sx32(vm.regs[rs1])
            try:
                dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
            except (OSError, OverflowError, ValueError):
                vm.regs[rd] = self._new_span_bytes(vm, b"")
                self.host_status = 2
                return True
            text = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            vm.regs[rd] = self._new_span_bytes(vm, text.encode("utf-8"))
            self.host_status = 0
            return True
        if method == "AddSeconds":
            vm.regs[rd] = (_sx32(vm.regs[rs1]) + _sx32(vm.regs[rs2])) & MASK32
            return True
        if method == "AddMinutes":
            vm.regs[rd] = (_sx32(vm.regs[rs1]) + (_sx32(vm.regs[rs2]) * 60)) & MASK32
            return True
        if method == "AddHours":
            vm.regs[rd] = (_sx32(vm.regs[rs1]) + (_sx32(vm.regs[rs2]) * 3600)) & MASK32
            return True
        if method == "AddDays":
            vm.regs[rd] = (_sx32(vm.regs[rs1]) + (_sx32(vm.regs[rs2]) * 86400)) & MASK32
            return True
        if method == "GetDayOfWeek":
            sec = _sx32(vm.regs[rs1])
            try:
                dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
            except (OSError, OverflowError, ValueError):
                vm.regs[rd] = 0
                self.host_status = 2
                return True
            vm.regs[rd] = dt.isoweekday() & MASK32
            self.host_status = 0
            return True
        if method == "GetDayOfYear":
            sec = _sx32(vm.regs[rs1])
            try:
                dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
            except (OSError, OverflowError, ValueError):
                vm.regs[rd] = 0
                self.host_status = 2
                return True
            vm.regs[rd] = dt.timetuple().tm_yday & MASK32
            self.host_status = 0
            return True
        if method in ("DiffDays", "Year", "Month", "Day"):
            return self._datetime_ext(vm, method, rd, rs1, rs2)
        return False

    # -- Locale formatting (language + timezone conversion) --------------------
    def _locale(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        import datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        def _arg_to_text(reg_value: int) -> str:
            if 0 < reg_value < len(vm.spans) and vm.spans[reg_value]:
                return self._span_str(vm, reg_value).strip()
            return ""

        def _arg_to_timezone(reg_value: int) -> Optional[str]:
            if _sx32(reg_value) == 0:
                return None
            text = _arg_to_text(reg_value)
            if text and ("/" in text or text.upper() == "UTC" or text.upper().startswith("GMT") or text[0].isalpha()):
                return text
            return _TZ_BY_ID.get(_sx32(reg_value))

        def _currency_code(reg_value: int) -> str:
            text = _arg_to_text(reg_value).upper()
            if len(text) == 3 and text.isalpha():
                return text
            return _CURRENCY_CODE_BY_NUM.get(_sx32(reg_value), "USD")

        if method == "SetLocale":
            locale_spec = _arg_to_text(vm.regs[rs1])
            locale_tag = self.locale_tag
            tz_name = self.locale_tz
            if locale_spec:  # pragma: no branch
                if "@" in locale_spec:
                    locale_part, tz_part = locale_spec.split("@", 1)
                    locale_tag = locale_part.strip() or locale_tag
                    if tz_part.strip():  # pragma: no branch
                        tz_name = tz_part.strip()
                else:
                    locale_tag = locale_spec
            tz_arg = _arg_to_timezone(vm.regs[rs2])
            if tz_arg:
                tz_name = tz_arg
            try:
                ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                self.host_status = 2
                vm.regs[rd] = 0
                return True
            self.locale_tag = locale_tag or self.locale_tag
            self.locale_tz = tz_name
            self.host_status = 0
            vm.regs[rd] = 1
            return True

        if method == "GetCurrentLocale":
            current = f"{self.locale_tag}@{self.locale_tz}"
            vm.regs[rd] = self._new_span_bytes(vm, current.encode("utf-8"))
            return True

        if method == "FormatNumber":
            value = _sx32(vm.regs[rs1])
            scale = _sx32(vm.regs[rs2])
            text = _format_scaled_int(value, scale)
            vm.regs[rd] = self._new_span_bytes(vm, text.encode("utf-8"))
            self.host_status = 0
            return True

        if method == "FormatCurrency":
            amount_minor = _sx32(vm.regs[rs1])
            code = _currency_code(vm.regs[rs2])
            scale = _CURRENCY_MINOR_BY_CODE.get(code, 2)
            text = f"{code} {_format_scaled_int(amount_minor, scale)}"
            vm.regs[rd] = self._new_span_bytes(vm, text.encode("utf-8"))
            self.host_status = 0
            return True

        if method == "FormatDate" or method == "FormatTime":
            sec = _sx32(vm.regs[rs1])
            tz_name = _arg_to_timezone(vm.regs[rs2]) or self.locale_tz
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                self.host_status = 2
                vm.regs[rd] = self._new_span_bytes(vm, b"")
                return True
            dt_utc = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
            local = dt_utc.astimezone(tz)
            offset = _format_utc_offset(local.utcoffset() or datetime.timedelta(0))
            if method == "FormatDate":
                text = f"{local.strftime('%Y-%m-%d')} {offset}"
            else:
                text = f"{local.strftime('%H:%M:%S')} {offset}"
            vm.regs[rd] = self._new_span_bytes(vm, text.encode("utf-8"))
            self.host_status = 0
            return True

        if method == "Translate":
            key = _arg_to_text(vm.regs[rs1])
            locale_override = _arg_to_text(vm.regs[rs2]) or self.locale_tag
            translated = to_locale(key, locale=locale_override, include_description=False) if key else None
            if translated is None:
                translated = key
            vm.regs[rd] = self._new_span_bytes(vm, translated.encode("utf-8"))
            self.host_status = 0 if translated else 2
            return True

        return False

    # -- DateTime extended (DiffDays, Year, Month, Day) ----------------------
    def _datetime_ext(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        import datetime
        if method == "DiffDays":
            a = _sx32(vm.regs[rs1])  # epoch seconds
            b = _sx32(vm.regs[rs2])  # epoch seconds
            diff = a - b
            q = abs(diff) // 86400
            vm.regs[rd] = (-q if diff < 0 else q) & MASK32
            return True
        sec = _sx32(vm.regs[rs1])
        try:
            dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
        except (ValueError, OSError, OverflowError):
            vm.regs[rd] = 0
            return True
        if method == "Year":
            vm.regs[rd] = dt.year
            return True
        if method == "Month":
            vm.regs[rd] = dt.month
            return True
        if method == "Day":
            vm.regs[rd] = dt.day
            return True
        return False

    # -- Req.Param / Req.ParamCount ------------------------------------------
    def _req_param(self, vm: "PicoVM", method: str, rd, rs1, rs2) -> bool:
        ctx = self.request_context
        path = ""
        if ctx and "path" in ctx:  # pragma: no branch
            raw = ctx["path"]
            # request_context stores path as a span handle (see Req.Path), so
            # decode it; tolerate a bare string for direct/testing callers.
            path = self._span_str(vm, raw) if isinstance(raw, int) else str(raw)  # pragma: no branch
        segments = [s for s in path.split("/") if s]
        if method == "ParamCount":
            vm.regs[rd] = len(segments)
            return True
        if method == "Param":
            idx = vm.regs[rs1] & MASK32
            if idx < len(segments):
                vm.regs[rd] = self._new_span_bytes(vm, segments[idx].encode("utf-8"))
            else:
                vm.regs[rd] = 0
                self.host_status = 1  # NOT_FOUND
            return True
        return False


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
            self.host.cap_ceiling = caps
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
        self._verified = False               # INV-10 verify is cached per program

    def reset_for_request(self) -> "PicoVM":
        """Restore per-request execution state for warm reuse in a server loop.

        Reuses the loaded program, the 'mem' arena buffer, and the cached
        verification result (skips the O(program) re-verify). Only the volatile
        execution state is cleared, so a kept-warm VM can serve request after
        request without reconstruction. Pair with HostApi.install_request_context.
        """
        regs = self.regs
        for i in range(len(regs)):
            regs[i] = 0
        # NOTE: self.cards (the Storage.* card table) is deliberately NOT cleared
        # -- it is the persistent store and must survive across warm requests.
        self.call_stack.clear()
        self.output.clear()
        self.http_status = None
        self.http_type = None
        self.arena_top = 0x8000
        self.spans = [None]
        self.pc = 0
        self.cur_pc = 0
        self.halted = False
        self.waiting = False
        self.retval = 0
        self.steps = 0
        h = self.host
        h._handler_mark = None               # let install re-capture the arena base
        h.host_status = 0
        h.const_floor = getattr(h, "const_floor", self.arena_bytes)
        return self

    def _verify(self):
        """INV-10: reject static out-of-range JUMP/CALL/BRANCH targets before execution
        (register/indexed jumps are dynamic -> runtime-checked in _step)."""
        n = len(self.program)
        for i, word in enumerate(self.program):
            op, _rd, _rs1, rs2, imm16 = isa.decode_instruction_fast(word)
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
        if not getattr(self, "_verified", False):
            self._verify()                   # INV-10: verify once, then cache
            self._verified = True
        try:
            while not self.halted:  # pragma: no branch - exercised via instruction dispatch within the loop
                if self.pc >= len(self.program):
                    break
                if self.steps >= self.max_steps:
                    raise PicoFault(PV_FAULT_STEP_BUDGET, self.pc, 0,
                                    f"step budget exceeded ({self.max_steps})")
                self.steps += 1
                try:
                    self._step()
                except PicoFault as pf:
                    handler_pc = self.host._active_handler_pc()
                    if handler_pc:
                        self.host._error_code = pf.code
                        self.host._error_detail = pf.detail
                        self.host._error_resume_pc = pf.pc + 1
                        # Same call-stack truncation Error.Raise applies (see
                        # _error_hook) -- a genuine fault inside a called
                        # subroutine must discard that subroutine's abandoned
                        # return address too, for the identical reason.
                        del self.call_stack[self.host._error_handler_call_depth[-1]:]
                        self.pc = handler_pc
                    else:
                        raise
        except Halt:
            self.halted = True
        return self

    def output_text(self) -> str:
        """Decode the output buffer (PIPE ints + Io.Write bytes) as UTF-8 text."""
        return b"".join(self.output).decode("utf-8", "replace")

    # -- core ------------------------------------------------------------
    def _step(self):
        word = self.program[self.pc]
        op, rd, rs1, rs2, imm16 = isa.decode_instruction_fast(word)
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
                    raise PicoFault(PV_FAULT_BAD_JUMP, cur, tgt, f"bad branch target {tgt} at pc={cur}")  # pragma: no cover
                self.pc = tgt
        elif op == isa.OP_CALL:
            if imm16 < 0 or imm16 > len(self.program):
                raise PicoFault(PV_FAULT_BAD_JUMP, cur, imm16, f"bad call target {imm16} at pc={cur}")  # pragma: no cover
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
        else:  # pragma: no cover - isa.decode_instruction only yields defined opcodes
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
