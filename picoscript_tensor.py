"""Shared tensor descriptor, handle, and provider-request ABI helpers."""

from dataclasses import dataclass
from enum import IntEnum
import struct


class TensorStatus(IntEnum):
    OK = 0
    INVALID_HANDLE = 1
    INVALID_ARGUMENT = 2
    EMPTY = 3
    ALLOCATION_FAILED = 7
    UNSUPPORTED_DEVICE = 8
    CANCELLED = 9
    TIMEOUT = 10
    WORKSPACE_EXHAUSTED = 11
    FORMAT_MISMATCH = 12
    SHARD_CORRUPT = 13


class TensorDType(IntEnum):
    INT8 = 1
    UINT8 = 2
    INT32 = 3
    FP16 = 4
    BF16 = 5
    Q16_16 = 6


PTEN_MAGIC = b"PTEN"
PTEN_VERSION = 1
PTEN_HEADER = struct.Struct("<4sBBBBII")
MAX_TENSOR_RANK = 8


@dataclass(frozen=True)
class TensorDescriptor:
    dtype: int
    dimensions: tuple
    strides: tuple
    flags: int = 0
    byte_offset: int = 0
    byte_length: int = 0
    version: int = PTEN_VERSION

    def __post_init__(self):
        rank = len(self.dimensions)
        if self.version != PTEN_VERSION:
            raise ValueError("unsupported tensor descriptor version")
        if rank < 1 or rank > MAX_TENSOR_RANK or len(self.strides) != rank:
            raise ValueError("tensor rank or stride count is invalid")
        if int(self.dtype) not in {item.value for item in TensorDType}:
            raise ValueError("unsupported tensor dtype")
        if any(int(dim) <= 0 for dim in self.dimensions):
            raise ValueError("tensor dimensions must be positive")
        if int(self.byte_offset) < 0 or int(self.byte_length) < 0:
            raise ValueError("tensor byte bounds must be non-negative")

    def encode(self):
        header = PTEN_HEADER.pack(
            PTEN_MAGIC, self.version, int(self.dtype), len(self.dimensions),
            int(self.flags) & 0xFF, int(self.byte_offset), int(self.byte_length),
        )
        dims = b"".join(struct.pack("<I", int(value)) for value in self.dimensions)
        strides = b"".join(struct.pack("<i", int(value)) for value in self.strides)
        return header + dims + strides

    @classmethod
    def decode(cls, payload):
        payload = bytes(payload)
        if len(payload) < PTEN_HEADER.size:
            raise ValueError("tensor descriptor is truncated")
        magic, version, dtype, rank, flags, offset, length = PTEN_HEADER.unpack_from(payload)
        if magic != PTEN_MAGIC:
            raise ValueError("tensor descriptor magic mismatch")
        if rank < 1 or rank > MAX_TENSOR_RANK:
            raise ValueError("tensor descriptor rank is invalid")
        expected = PTEN_HEADER.size + rank * 8
        if len(payload) != expected:
            raise ValueError("tensor descriptor length mismatch")
        pos = PTEN_HEADER.size
        dimensions = struct.unpack_from("<" + "I" * rank, payload, pos)
        pos += rank * 4
        strides = struct.unpack_from("<" + "i" * rank, payload, pos)
        return cls(dtype, dimensions, strides, flags, offset, length, version)


@dataclass
class ProviderRequest:
    workspace_bytes: int = 0
    workspace_limit: int = 0
    deadline_ticks: int = 0
    cancel_token: int = 0
    capability_mask: int = 0
    cancelled: bool = False

    def status(self):
        if self.cancelled:
            return TensorStatus.CANCELLED
        if self.workspace_bytes < 0 or self.workspace_limit < 0:
            return TensorStatus.INVALID_ARGUMENT
        if self.workspace_limit and self.workspace_bytes > self.workspace_limit:
            return TensorStatus.WORKSPACE_EXHAUSTED
        return TensorStatus.OK

    def cancel(self):
        self.cancelled = True


class TensorHandleTable:
    """Generation-protected opaque handle table for provider-owned objects."""

    def __init__(self, capacity=65535):
        self.capacity = int(capacity)
        self._entries = {}
        self._generations = {}
        self._free = []
        self._next_slot = 1

    def allocate(self, value):
        if self._free:
            slot = self._free.pop()
        else:
            slot = self._next_slot
            self._next_slot += 1
        if slot > self.capacity:
            raise MemoryError("tensor handle table exhausted")
        generation = self._generations.get(slot, 1)
        self._entries[slot] = value
        self._generations[slot] = generation
        return (generation << 16) | slot

    def get(self, handle):
        slot = int(handle) & 0xFFFF
        generation = (int(handle) >> 16) & 0xFFFF
        if slot == 0 or self._generations.get(slot) != generation or slot not in self._entries:
            raise KeyError(handle)
        return self._entries[slot]

    def release(self, handle):
        slot = int(handle) & 0xFFFF
        generation = (int(handle) >> 16) & 0xFFFF
        if slot == 0 or self._generations.get(slot) != generation or slot not in self._entries:
            return False
        del self._entries[slot]
        self._generations[slot] = (generation + 1) & 0xFFFF or 1
        self._free.append(slot)
        return True
