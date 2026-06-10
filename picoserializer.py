#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoserializer.py -- PicoBinarySerializer: compact, self-describing binary
encoding for PicoScript "cards" (records).

A card is a record of named fields, each an int32 or a UTF-8 string. The encoding
is self-describing (field names + types are stored) so a card round-trips without
an external schema, and is deterministic (fields are emitted sorted by UTF-8 name
bytes) so the same record always serializes to the same bytes -- byte-for-byte
identical to the JavaScript port (vm/picoserializer.js).

Layout (big-endian):
    magic   u32  = 0x50534331  ("PSC1")
    count   u16  number of fields
    per field (sorted by name):
        nameLen u8 ; name bytes (UTF-8)
        type    u8 ; 1 = INT32, 2 = STR
        INT32:  value i32
        STR:    valLen u16 ; value bytes (UTF-8)
"""

from __future__ import annotations

MAGIC = 0x50534331            # "PSC1"
T_INT = 1
T_STR = 2


def serialize_card(record: dict) -> bytes:
    """Encode {name: int|str} -> bytes (deterministic, self-describing)."""
    out = bytearray()
    out += MAGIC.to_bytes(4, "big")
    keys = sorted(record.keys(), key=lambda k: k.encode("utf-8"))
    out += len(keys).to_bytes(2, "big")
    for k in keys:
        kb = k.encode("utf-8")
        if len(kb) > 255:
            raise ValueError(f"field name too long: {k!r}")
        out.append(len(kb))
        out += kb
        v = record[k]
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, int):
            out.append(T_INT)
            out += (v & 0xFFFFFFFF).to_bytes(4, "big")
        elif isinstance(v, str):
            vb = v.encode("utf-8")
            if len(vb) > 0xFFFF:
                raise ValueError("string field too long")
            out.append(T_STR)
            out += len(vb).to_bytes(2, "big")
            out += vb
        else:
            raise ValueError(f"unsupported field type for {k!r}: {type(v).__name__}")
    return bytes(out)


def deserialize_card(buf) -> dict:
    """Decode bytes -> {name: int|str}."""
    buf = bytes(buf)
    if len(buf) < 6 or int.from_bytes(buf[0:4], "big") != MAGIC:
        raise ValueError("bad card magic")
    count = int.from_bytes(buf[4:6], "big")
    pos = 6
    rec: dict = {}
    for _ in range(count):
        nlen = buf[pos]; pos += 1
        name = buf[pos:pos + nlen].decode("utf-8"); pos += nlen
        t = buf[pos]; pos += 1
        if t == T_INT:
            raw = int.from_bytes(buf[pos:pos + 4], "big"); pos += 4
            rec[name] = raw - 0x100000000 if raw & 0x80000000 else raw
        elif t == T_STR:
            vlen = int.from_bytes(buf[pos:pos + 2], "big"); pos += 2
            rec[name] = buf[pos:pos + vlen].decode("utf-8"); pos += vlen
        else:
            raise ValueError(f"unknown field type {t}")
    return rec


def to_hex(b: bytes) -> str:
    return bytes(b).hex()


def from_hex(s: str) -> bytes:
    return bytes.fromhex(s)
