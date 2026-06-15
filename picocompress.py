"""picocompress – pure-Python port of the picocompress C library.

Provides byte-identical compressed output to the C reference implementation.
Requires Python 3.10+, zero external dependencies.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, Protocol

# ---------------------------------------------------------------------------
# Constants (must match picocompress.h)
# ---------------------------------------------------------------------------

LITERAL_MAX = 64
MATCH_MIN = 2
MATCH_CODE_BITS = 5
MATCH_MAX = MATCH_MIN + ((1 << MATCH_CODE_BITS) - 1)  # 33
OFFSET_SHORT_BITS = 9
OFFSET_SHORT_MAX = (1 << OFFSET_SHORT_BITS) - 1  # 511
LONG_MATCH_MIN = 2
LONG_MATCH_MAX = 17
OFFSET_LONG_MAX = 65535
DICT_COUNT = 96
DICT_MAX_LEN = 8
GOOD_MATCH = 8
REPEAT_CACHE_SIZE = 3


# ---------------------------------------------------------------------------
# Profile configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Profile:
    """Encoder configuration profile.

    The decoder is profile-independent; any decoder can decompress data
    produced by any encoder profile.
    """
    block_size: int = 508
    hash_bits: int = 9
    chain_depth: int = 2
    history_size: int = 504
    lazy_steps: int = 1

    @property
    def hash_size(self) -> int:
        return 1 << self.hash_bits

    @property
    def block_max_compressed(self) -> int:
        return self.block_size + (self.block_size // LITERAL_MAX) + 16


DEFAULT_PROFILE = Profile()

PROFILES: dict[str, Profile] = {
    "micro":      Profile(block_size=192, hash_bits=8, chain_depth=1, history_size=64,   lazy_steps=1),
    "minimal":    Profile(block_size=508, hash_bits=8, chain_depth=1, history_size=128,  lazy_steps=1),
    "balanced":   Profile(block_size=508, hash_bits=9, chain_depth=2, history_size=504,  lazy_steps=1),
    "aggressive": Profile(block_size=508, hash_bits=8, chain_depth=4, history_size=504,  lazy_steps=1),
    "q3":         Profile(block_size=508, hash_bits=10, chain_depth=2, history_size=1024, lazy_steps=2),
    "q4":         Profile(block_size=508, hash_bits=11, chain_depth=2, history_size=2048, lazy_steps=2),
}


# ---------------------------------------------------------------------------
# Static dictionary (96 entries – must be identical to the C version)
# ---------------------------------------------------------------------------

STATIC_DICT: list[bytes] = [
    # 0-3: high-value multi-byte patterns
    b'": "',                    # 0  ": " JSON key-value
    b'},\n"',                   # 1  },\n" JSON object sep
    b'</div',                   # 2
    b'tion',                    # 3
    # 4-7: common English suffixes
    b'ment',                    # 4
    b'ness',                    # 5
    b'able',                    # 6
    b'ight',                    # 7
    # 8-15: three/four-byte patterns
    b'":"',                     # 8
    b'</di',                    # 9
    b'="ht',                    # 10
    b'the',                     # 11
    b'ing',                     # 12
    b',",',                     # 13  (C-reference bytes 0x2C,0x22,0x2C; picoscript fix vs upstream picocompress.py)
    b'":{',                     # 14
    b'":[',                     # 15
    # 16-23
    b'ion',                     # 16
    b'ent',                     # 17
    b'ter',                     # 18
    b'and',                     # 19
    b'/>\r\n',                  # 20
    b'"},',                     # 21
    b'"],',                     # 22
    b'have',                    # 23
    # 24-39: four-byte
    b'no":',                    # 24
    b'true',                    # 25
    b'null',                    # 26
    b'name',                    # 27
    b'data',                    # 28
    b'time',                    # 29
    b'type',                    # 30
    b'mode',                    # 31
    b'http',                    # 32
    b'tion',                    # 33
    b'code',                    # 34
    b'size',                    # 35
    b'ment',                    # 36
    b'list',                    # 37
    b'item',                    # 38
    b'text',                    # 39
    # 40-47: five-byte
    b'false',                   # 40
    b'error',                   # 41
    b'value',                   # 42
    b'state',                   # 43
    b'alert',                   # 44
    b'input',                   # 45
    b'ation',                   # 46
    b'order',                   # 47
    # 48-55: six-byte
    b'status',                  # 48
    b'number',                  # 49
    b'active',                  # 50
    b'device',                  # 51
    b'region',                  # 52
    b'string',                  # 53
    b'result',                  # 54
    b'length',                  # 55
    # 56-59: seven-byte
    b'message',                 # 56
    b'content',                 # 57
    b'request',                 # 58
    b'default',                 # 59
    # 60-63: eight-byte
    b'number":',                # 60
    b'operator',                # 61
    b'https://',                # 62
    b'response',                # 63
    # 64-67: sentence starters
    b'. The ',                  # 64
    b'. It ',                   # 65
    b'. This ',                 # 66
    b'. A ',                    # 67
    # 68-71: capitalized terms
    b'HTTP',                    # 68
    b'JSON',                    # 69
    b'The ',                    # 70
    b'None',                    # 71
    # 72-75: phoneme patterns
    b'ment',                    # 72
    b'ness',                    # 73
    b'able',                    # 74
    b'ight',                    # 75
    # 76-79: phoneme + structural
    b'ation',                   # 76
    b'ould ',                   # 77
    b'": "',                    # 78
    b'", "',                    # 79
    # 80-95: uppercase keyword primitives (0xD0..0xDF tokens)
    b'DIM',                     # 80
    b'FOR',                     # 81
    b'END',                     # 82
    b'REL',                     # 83
    b'EACH',                    # 84
    b'LOAD',                    # 85
    b'SAVE',                    # 86
    b'CARD',                    # 87
    b'JUMP',                    # 88
    b'PRINT',                   # 89
    b'INPUT',                   # 90
    b'GOSUB',                   # 91
    b'STREAM',                  # 92
    b'RETURN',                  # 93
    b'SWITCH',                  # 94
    b'PROGRAM',                 # 95
]

assert len(STATIC_DICT) == DICT_COUNT


# ---------------------------------------------------------------------------
# Hash function (portable – matches C when PC_HAS_HW_CRC32 == 0)
# ---------------------------------------------------------------------------

def _hash3(data: bytes | bytearray | memoryview, pos: int, mask: int) -> int:
    v = data[pos] * 251 + data[pos + 1] * 11 + data[pos + 2] * 3
    return v & mask


# ---------------------------------------------------------------------------
# Match length
# ---------------------------------------------------------------------------

def _match_len(a: bytes | bytearray | memoryview, a_off: int,
               b: bytes | bytearray | memoryview, b_off: int,
               limit: int) -> int:
    m = 0
    while m < limit and a[a_off + m] == b[b_off + m]:
        m += 1
    return m


# ---------------------------------------------------------------------------
# Emit literals helper
# ---------------------------------------------------------------------------

def _emit_literals(src: bytes | bytearray | memoryview, src_off: int,
                   src_len: int, dst: bytearray, op: int) -> int:
    """Append literal tokens to *dst* starting at *op*. Returns new op."""
    pos = 0
    while pos < src_len:
        chunk = min(src_len - pos, LITERAL_MAX)
        dst.append(0)  # placeholder
        dst[op] = chunk - 1  # 0x00..0x3F
        op += 1
        dst.extend(src[src_off + pos: src_off + pos + chunk])
        op += chunk
        pos += chunk
    return op


# ---------------------------------------------------------------------------
# Hash-table insert
# ---------------------------------------------------------------------------

def _head_insert(head: list[list[int]], h: int, pos: int, depth: int) -> None:
    for d in range(depth - 1, 0, -1):
        head[d][h] = head[d - 1][h]
    head[0][h] = pos


# ---------------------------------------------------------------------------
# Find-best (repeat cache → dictionary → LZ hash chain)
# ---------------------------------------------------------------------------

def _find_best(
    vbuf: bytearray | memoryview, vbuf_len: int, vpos: int,
    head: list[list[int]], rep_offsets: list[int],
    good_match: int, skip_dict: bool,
    profile: Profile,
) -> tuple[int, int, int, int, int]:
    """Return (best_savings, best_len, best_off, best_dict, best_is_repeat)."""
    best_savings = 0
    best_len = 0
    best_off = 0
    best_dict = 0xFFFF
    best_is_repeat = 0
    remaining = vbuf_len - vpos

    # 1. Repeat-offset cache
    if remaining >= MATCH_MIN:
        max_rep = min(remaining, MATCH_MAX)
        for d in range(REPEAT_CACHE_SIZE):
            off = rep_offsets[d]
            if off == 0 or off > vpos:
                continue
            if vbuf[vpos] != vbuf[vpos - off]:
                continue
            if remaining >= 2 and vbuf[vpos + 1] != vbuf[vpos - off + 1]:
                continue
            mlen = _match_len(vbuf, vpos - off, vbuf, vpos, max_rep)
            if mlen < MATCH_MIN:
                continue
            is_rep = 1 if (d == 0 and mlen <= 17) else 0
            token_cost = 1 if is_rep else (2 if off <= OFFSET_SHORT_MAX else 3)
            s = mlen - token_cost
            if s > best_savings:
                best_savings = s
                best_len = mlen
                best_off = off
                best_dict = 0xFFFF
                best_is_repeat = is_rep
                if mlen >= good_match:
                    return best_savings, best_len, best_off, best_dict, best_is_repeat

    # 2. Dictionary
    if not skip_dict:
        first_byte = vbuf[vpos]
        for d_idx in range(DICT_COUNT):
            entry = STATIC_DICT[d_idx]
            dlen = len(entry)
            if dlen > remaining:
                continue
            if dlen - 1 <= best_savings:
                continue
            if entry[0] != first_byte:
                continue
            if vbuf[vpos: vpos + dlen] != entry:
                continue
            s = dlen - 1
            best_savings = s
            best_dict = d_idx
            best_len = dlen
            best_off = 0
            best_is_repeat = 0
            if dlen >= good_match:
                return best_savings, best_len, best_off, best_dict, best_is_repeat

    # 3. LZ hash-chain
    if remaining >= 3:
        h = _hash3(vbuf, vpos, profile.hash_size - 1)
        max_len_short = min(remaining, MATCH_MAX)
        max_len_long = min(remaining, LONG_MATCH_MAX)
        first_byte = vbuf[vpos]

        for d in range(profile.chain_depth):
            prev = head[d][h]
            if prev < 0:
                continue
            if prev >= vpos:
                continue
            off = vpos - prev
            if off == 0 or off > OFFSET_LONG_MAX:
                continue
            if vbuf[prev] != first_byte:
                continue
            max_len = max_len_short if off <= OFFSET_SHORT_MAX else max_len_long
            mlen = _match_len(vbuf, prev, vbuf, vpos, max_len)
            if mlen < MATCH_MIN:
                continue
            token_cost = 2 if off <= OFFSET_SHORT_MAX else 3
            s = mlen - token_cost

            if (s > best_savings
                    or (s == best_savings and mlen > best_len)
                    or (s == best_savings and mlen == best_len and off < best_off)
                    or (s == best_savings - 1 and mlen >= best_len + 2)):
                best_savings = mlen - token_cost
                best_len = mlen
                best_off = off
                best_dict = 0xFFFF
                best_is_repeat = 0
                if mlen >= good_match:
                    return best_savings, best_len, best_off, best_dict, best_is_repeat

    return best_savings, best_len, best_off, best_dict, best_is_repeat


# ---------------------------------------------------------------------------
# Block compression
# ---------------------------------------------------------------------------

def _compress_block(vbuf: bytearray, hist_len: int, block_len: int,
                    profile: Profile) -> bytes | None:
    """Compress *block_len* bytes starting at *hist_len* inside *vbuf*.

    Returns compressed bytes, or *None* on overflow.
    """
    depth = profile.chain_depth
    hash_size = profile.hash_size
    hash_mask = hash_size - 1

    head: list[list[int]] = [[-1] * hash_size for _ in range(depth)]
    rep_offsets = [0] * REPEAT_CACHE_SIZE
    vbuf_len = hist_len + block_len
    out = bytearray()
    op = 0

    # Seed hash table from history
    if hist_len >= 3:
        for p in range(hist_len - 2):
            _head_insert(head, _hash3(vbuf, p, hash_mask), p, depth)
        # Re-inject positions near block boundary into slot 0
        tail_start = hist_len - 64 if hist_len > 64 else 0
        for p in range(tail_start, hist_len - 2):
            h = _hash3(vbuf, p, hash_mask)
            if head[0][h] != p:
                save = head[depth - 1][h]
                _head_insert(head, h, p, depth)
                head[depth - 1][h] = save

    anchor = hist_len
    vpos = hist_len

    # Self-disabling dictionary check
    dict_skip = False
    if block_len >= 1:
        b0 = vbuf[hist_len]
        if b0 in (ord('{'), ord('['), ord('<'), 0xEF):
            dict_skip = False
        else:
            check_len = min(block_len, 4)
            for ci in range(check_len):
                c = vbuf[hist_len + ci]
                if c < 0x20 or c > 0x7E:
                    dict_skip = True
                    break

    while vpos < vbuf_len:
        if vbuf_len - vpos < MATCH_MIN:
            break

        best_savings, best_len, best_off, best_dict, best_is_repeat = _find_best(
            vbuf, vbuf_len, vpos, head, rep_offsets, GOOD_MATCH, dict_skip, profile)

        # Insert current position into hash table
        if vbuf_len - vpos >= 3:
            _head_insert(head, _hash3(vbuf, vpos, hash_mask), vpos, depth)

        # Literal run extension
        if best_savings <= 1 and best_dict == 0xFFFF and anchor < vpos:
            best_savings = 0

        # Lazy matching
        if best_savings > 0 and best_len < GOOD_MATCH:
            retry = False
            for step in range(1, profile.lazy_steps + 1):
                npos = vpos + step
                if npos >= vbuf_len or vbuf_len - npos < MATCH_MIN:
                    break
                n_sav, n_len, n_off, n_dict, n_rep = _find_best(
                    vbuf, vbuf_len, npos, head, rep_offsets, GOOD_MATCH,
                    dict_skip, profile)
                if n_sav > best_savings:
                    # Insert positions we're skipping
                    for s in range(step):
                        sp = vpos + s
                        if vbuf_len - sp >= 3:
                            _head_insert(head, _hash3(vbuf, sp, hash_mask), sp, depth)
                    vpos = npos
                    retry = True
                    break
            if retry:
                continue  # restart loop at new vpos

        # Emit
        if best_savings > 0:
            lit_len = vpos - anchor
            if lit_len > 0:
                op = _emit_literals(vbuf, anchor, lit_len, out, op)

            if best_dict != 0xFFFF:
                if best_dict < 64:
                    out.append(0x40 | (best_dict & 0x3F))
                elif best_dict < 80:
                    out.append(0xE0 | ((best_dict - 64) & 0x0F))
                else:
                    out.append(0xD0 | ((best_dict - 80) & 0x0F))
                op += 1
            elif best_is_repeat:
                out.append(0xC0 | ((best_len - MATCH_MIN) & 0x0F))
                op += 1
            elif best_off <= OFFSET_SHORT_MAX and best_len <= MATCH_MAX:
                out.append(0x80 | (((best_len - MATCH_MIN) & 0x1F) << 1) | ((best_off >> 8) & 0x01))
                out.append(best_off & 0xFF)
                op += 2
            else:
                elen = min(best_len, LONG_MATCH_MAX)
                out.append(0xF0 | ((elen - LONG_MATCH_MIN) & 0x0F))
                out.append((best_off >> 8) & 0xFF)
                out.append(best_off & 0xFF)
                best_len = elen
                op += 3

            # Update repeat-offset cache
            if not best_is_repeat and best_off != 0 and best_dict == 0xFFFF:
                rep_offsets[2] = rep_offsets[1]
                rep_offsets[1] = rep_offsets[0]
                rep_offsets[0] = best_off

            for k in range(1, best_len):
                if vpos + k + 2 >= vbuf_len:
                    break
                _head_insert(head, _hash3(vbuf, vpos + k, hash_mask), vpos + k, depth)

            vpos += best_len
            anchor = vpos
        else:
            vpos += 1

    if anchor < vbuf_len:
        op = _emit_literals(vbuf, anchor, vbuf_len - anchor, out, op)

    return bytes(out)


# ---------------------------------------------------------------------------
# History update
# ---------------------------------------------------------------------------

def _update_history(hist: bytearray, data: bytes | bytearray | memoryview,
                    history_size: int) -> bytearray:
    """Return updated history buffer after appending *data*."""
    combined = hist + bytearray(data)
    if len(combined) > history_size:
        return bytearray(combined[len(combined) - history_size:])
    return combined


# ---------------------------------------------------------------------------
# Block decompression
# ---------------------------------------------------------------------------

def _copy_match(out: bytearray, op: int, hist: bytearray, off: int,
                match_len: int) -> int:
    hist_len = len(hist)
    if off <= op:
        src = op - off
        for j in range(match_len):
            out.append(out[src + j])
        op += match_len
    else:
        hist_back = off - op
        hist_start = hist_len - hist_back
        for j in range(match_len):
            src = hist_start + j
            if src < hist_len:
                out.append(hist[src])
            else:
                out.append(out[src - hist_len])
        op += match_len
    return op


def _decompress_block(hist: bytearray, compressed: bytes | bytearray,
                      raw_len: int) -> bytes:
    """Decompress a single block. Raises ValueError on corrupt data."""
    hist_len = len(hist)
    inp = compressed
    in_len = len(inp)
    ip = 0
    op = 0
    last_offset = 0
    out = bytearray()

    while ip < in_len:
        token = inp[ip]; ip += 1

        # 0x00..0x3F: short literal
        if token < 0x40:
            lit_len = (token & 0x3F) + 1
            if ip + lit_len > in_len or op + lit_len > raw_len:
                raise ValueError("corrupt: literal overflow")
            out.extend(inp[ip: ip + lit_len])
            ip += lit_len
            op += lit_len
            continue

        # 0x40..0x7F: dictionary ref (0..63)
        if token < 0x80:
            idx = token & 0x3F
            if idx >= DICT_COUNT:
                raise ValueError("corrupt: dict index out of range")
            entry = STATIC_DICT[idx]
            if op + len(entry) > raw_len:
                raise ValueError("corrupt: dict overflow")
            out.extend(entry)
            op += len(entry)
            continue

        # 0x80..0xBF: LZ match (short offset)
        if token < 0xC0:
            if ip >= in_len:
                raise ValueError("corrupt: LZ truncated")
            match_len = ((token >> 1) & 0x1F) + MATCH_MIN
            off = ((token & 0x01) << 8) | inp[ip]; ip += 1
            if off == 0:
                raise ValueError("corrupt: zero offset")
            if off > op + hist_len:
                raise ValueError("corrupt: offset exceeds available data")
            if op + match_len > raw_len:
                raise ValueError("corrupt: match overflow")
            op = _copy_match(out, op, hist, off, match_len)
            last_offset = off
            continue

        # 0xC0..0xCF: repeat-offset match
        if token < 0xD0:
            match_len = (token & 0x0F) + MATCH_MIN
            if last_offset == 0:
                raise ValueError("corrupt: repeat with no prior offset")
            if last_offset > op + hist_len:
                raise ValueError("corrupt: repeat offset exceeds data")
            if op + match_len > raw_len:
                raise ValueError("corrupt: repeat overflow")
            op = _copy_match(out, op, hist, last_offset, match_len)
            continue

        # 0xD0..0xDF: dictionary ref (entries 80..95)
        if token < 0xE0:
            idx = 80 + (token & 0x0F)
            if idx >= DICT_COUNT:
                raise ValueError("corrupt: dict index out of range")
            entry = STATIC_DICT[idx]
            if op + len(entry) > raw_len:
                raise ValueError("corrupt: dict overflow")
            out.extend(entry)
            op += len(entry)
            continue

        # 0xE0..0xEF: dictionary ref (entries 64..79)
        if token < 0xF0:
            idx = 64 + (token & 0x0F)
            if idx >= DICT_COUNT:
                raise ValueError("corrupt: dict index out of range")
            entry = STATIC_DICT[idx]
            if op + len(entry) > raw_len:
                raise ValueError("corrupt: dict overflow")
            out.extend(entry)
            op += len(entry)
            continue

        # 0xF0..0xFF: long-offset LZ
        match_len = (token & 0x0F) + LONG_MATCH_MIN
        if ip + 2 > in_len:
            raise ValueError("corrupt: long LZ truncated")
        off = (inp[ip] << 8) | inp[ip + 1]; ip += 2
        if off == 0:
            raise ValueError("corrupt: zero long offset")
        if off > op + hist_len:
            raise ValueError("corrupt: long offset exceeds data")
        if op + match_len > raw_len:
            raise ValueError("corrupt: long match overflow")
        op = _copy_match(out, op, hist, off, match_len)
        last_offset = off

    if op != raw_len:
        raise ValueError(f"corrupt: output size mismatch ({op} != {raw_len})")
    return bytes(out)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class PicocompressError(Exception):
    """Base exception for picocompress errors."""


class CorruptDataError(PicocompressError):
    """Raised when decompression encounters corrupt data."""


# ---------------------------------------------------------------------------
# Buffer API
# ---------------------------------------------------------------------------

def compress(data: bytes | bytearray, *, profile: Profile | None = None) -> bytes:
    """Compress *data* and return the compressed byte string."""
    if profile is None:
        profile = DEFAULT_PROFILE
    enc = Encoder(profile=profile)
    out = bytearray()
    enc.sink(data, out.extend)
    enc.finish(out.extend)
    return bytes(out)


def decompress(data: bytes | bytearray) -> bytes:
    """Decompress *data* and return the original byte string."""
    dec = Decoder()
    out = bytearray()
    dec.sink(data, out.extend)
    dec.finish()
    return bytes(out)


def compress_bound(input_len: int) -> int:
    """Return the worst-case compressed size for *input_len* bytes."""
    if input_len == 0:
        return 0
    blocks = (input_len + DEFAULT_PROFILE.block_size - 1) // DEFAULT_PROFILE.block_size
    return input_len + blocks * 4


# ---------------------------------------------------------------------------
# Streaming encoder
# ---------------------------------------------------------------------------

WriteFn = Callable[[bytes | bytearray], None]


class Encoder:
    """Streaming picocompress encoder."""

    def __init__(self, *, profile: Profile | None = None) -> None:
        self.profile = profile or DEFAULT_PROFILE
        self._block = bytearray()
        self._history = bytearray()

    def sink(self, data: bytes | bytearray | memoryview, write_fn: WriteFn) -> None:
        """Feed *data* into the encoder; completed blocks are emitted via *write_fn*."""
        pos = 0
        while pos < len(data):
            room = self.profile.block_size - len(self._block)
            take = min(len(data) - pos, room)
            self._block.extend(data[pos: pos + take])
            pos += take
            if len(self._block) == self.profile.block_size:
                self._flush(write_fn)

    def finish(self, write_fn: WriteFn) -> None:
        """Flush any remaining data."""
        if self._block:
            self._flush(write_fn)

    def _flush(self, write_fn: WriteFn) -> None:
        raw = bytes(self._block)
        raw_len = len(raw)

        # Build virtual buffer [history | block]
        vbuf = bytearray(self._history) + bytearray(raw)
        hist_len = len(self._history)

        compressed = _compress_block(vbuf, hist_len, raw_len, self.profile)

        # Update history
        self._history = _update_history(self._history, raw, self.profile.history_size)

        # Build header
        if compressed is None or len(compressed) >= raw_len:
            # Store raw
            header = struct.pack("<HH", raw_len, 0)
            write_fn(header)
            write_fn(raw)
        else:
            comp_len = len(compressed)
            header = struct.pack("<HH", raw_len, comp_len)
            write_fn(header)
            write_fn(compressed)

        self._block.clear()


# ---------------------------------------------------------------------------
# Streaming decoder
# ---------------------------------------------------------------------------

class Decoder:
    """Streaming picocompress decoder."""

    def __init__(self) -> None:
        self._header_buf = bytearray()
        self._raw_len = 0
        self._comp_len = 0
        self._payload_buf = bytearray()
        self._history = bytearray()

    def sink(self, data: bytes | bytearray | memoryview, write_fn: WriteFn) -> None:
        """Feed compressed *data* into the decoder; decompressed blocks are
        emitted via *write_fn*."""
        pos = 0
        while pos < len(data):
            # Accumulate header
            if len(self._header_buf) < 4:
                need = 4 - len(self._header_buf)
                take = min(len(data) - pos, need)
                self._header_buf.extend(data[pos: pos + take])
                pos += take
                if len(self._header_buf) < 4:
                    continue
                self._raw_len = self._header_buf[0] | (self._header_buf[1] << 8)
                self._comp_len = self._header_buf[2] | (self._header_buf[3] << 8)
                self._payload_buf.clear()
                if self._raw_len == 0 and self._comp_len == 0:
                    self._header_buf.clear()
                    continue
                if self._raw_len == 0:
                    raise CorruptDataError("raw_len is zero")

            # Accumulate payload
            target = self._raw_len if self._comp_len == 0 else self._comp_len
            need = target - len(self._payload_buf)
            take = min(len(data) - pos, need)
            self._payload_buf.extend(data[pos: pos + take])
            pos += take

            if len(self._payload_buf) == target:
                self._emit_block(write_fn)
                self._header_buf.clear()
                self._raw_len = 0
                self._comp_len = 0
                self._payload_buf.clear()

    def finish(self) -> None:
        """Verify the decoder has no partial block pending."""
        if self._header_buf or self._raw_len or self._comp_len or self._payload_buf:
            raise CorruptDataError("unexpected end of compressed data")

    def _emit_block(self, write_fn: WriteFn) -> None:
        if self._comp_len == 0:
            # Stored raw
            raw = bytes(self._payload_buf)
            write_fn(raw)
            self._history = _update_history(self._history, raw,
                                            DEFAULT_PROFILE.history_size)
        else:
            try:
                raw = _decompress_block(self._history,
                                        bytes(self._payload_buf),
                                        self._raw_len)
            except ValueError as exc:
                raise CorruptDataError(str(exc)) from exc
            write_fn(raw)
            self._history = _update_history(self._history, raw,
                                            DEFAULT_PROFILE.history_size)
