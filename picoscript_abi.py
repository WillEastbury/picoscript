"""Small, allocation-conscious reference ABI shared by PicoScript hosts.

This module intentionally contains no reflection or persistence policy.  It is
the executable reference for the wire-level values used by native providers.
"""

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import struct


class Status(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    INVALID_HANDLE = 2
    MALFORMED_RECORD = 3
    CRC_MISMATCH = 4
    SCHEMA_MISMATCH = 5
    CHECKPOINT_MISMATCH = 6
    RESOURCE_EXHAUSTED = 7
    CANCELLED = 8
    PROVIDER_UNAVAILABLE = 9
    UNSUPPORTED = 10
    OVERFLOW = 11


class SpanFlags(IntFlag):
    BORROWED = 1
    OWNED = 2
    READONLY = 4
    INVALID = 8


MAX_SPAN_LENGTH = 0xFFFFFFFF


@dataclass(frozen=True)
class Span:
    """Portable descriptor: offset/pointer token, length, and flags."""

    offset: int = 0
    length: int = 0
    flags: SpanFlags = SpanFlags.BORROWED

    def valid(self) -> bool:
        return not (self.flags & SpanFlags.INVALID) and 0 <= self.offset <= 0xFFFFFFFFFFFFFFFF and 0 <= self.length <= MAX_SPAN_LENGTH

    def subspan(self, start: int, length: int):
        if not self.valid() or start < 0 or length < 0 or start > self.length - length:
            return None, Status.INVALID_ARGUMENT
        return Span(self.offset + start, length, self.flags), Status.OK

    def pack(self) -> bytes:
        return struct.pack("<QII", self.offset, self.length, int(self.flags))

    @staticmethod
    def unpack(data: bytes):
        if len(data) != 16:
            return None, Status.MALFORMED_RECORD
        off, length, flags = struct.unpack("<QII", data)
        span = Span(off, length, SpanFlags(flags))
        return (span, Status.OK) if span.valid() else (None, Status.INVALID_ARGUMENT)


def _check(span, offset, width):
    if not isinstance(span, Span) or not span.valid() or offset < 0 or width < 0 or offset > span.length - width:
        return Status.INVALID_ARGUMENT
    return Status.OK


def read_le(data: bytes, span: Span, offset: int, width: int):
    st = _check(span, offset, width)
    if st != Status.OK or span.offset + span.length > len(data):
        return 0, Status.INVALID_ARGUMENT
    return int.from_bytes(data[span.offset + offset:span.offset + offset + width], "little"), Status.OK


def write_le(data: bytearray, span: Span, offset: int, value: int, width: int):
    st = _check(span, offset, width)
    if st != Status.OK or span.flags & SpanFlags.READONLY or span.offset + span.length > len(data):
        return Status.INVALID_ARGUMENT
    if value < 0 or value >= (1 << (width * 8)):
        return Status.OVERFLOW
    data[span.offset + offset:span.offset + offset + width] = value.to_bytes(width, "little")
    return Status.OK


class HandleTable:
    """Bounded generation-checked handle table for host references."""

    def __init__(self, capacity=256):
        self._slots = [None] * capacity
        self._generation = [1] * capacity
        self.last_status = Status.OK

    def open(self, type_tag, value):
        for i, old in enumerate(self._slots):
            if old is None:
                self._slots[i] = (type_tag, value)
                self.last_status = Status.OK
                return ((self._generation[i] << 16) | (i + 1)), Status.OK
        self.last_status = Status.RESOURCE_EXHAUSTED
        return 0, self.last_status

    def get(self, handle, type_tag):
        idx = (handle & 0xFFFF) - 1
        gen = handle >> 16
        if idx < 0 or idx >= len(self._slots) or self._slots[idx] is None or self._generation[idx] != gen or self._slots[idx][0] != type_tag:
            self.last_status = Status.INVALID_HANDLE
            return None, self.last_status
        self.last_status = Status.OK
        return self._slots[idx][1], Status.OK

    def close(self, handle, type_tag):
        idx = (handle & 0xFFFF) - 1
        value, st = self.get(handle, type_tag)
        if st != Status.OK:
            return st
        self._slots[idx] = None
        self._generation[idx] = (self._generation[idx] + 1) & 0xFFFFFFFF or 1
        self.last_status = Status.OK
        return Status.OK


def u64_pair(value: int):
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise OverflowError(value)
    return value & 0xFFFFFFFF, value >> 32


def u64_from_pair(lo: int, hi: int):
    return (lo & 0xFFFFFFFF) | ((hi & 0xFFFFFFFF) << 32)
