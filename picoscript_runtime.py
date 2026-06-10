#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reference host runtime structures for PicoScript host hooks.

This module is intentionally lightweight and backend-agnostic. It provides
fillable primitives for arena allocation, span handling, descriptors, and
lease-based typed access that host runtimes can adapt to kernel/IPC/FIFO
implementations.
"""

from dataclasses import dataclass
from enum import IntEnum


@dataclass
class Span:
    ptr: int
    length: int

    def slice(self, offset: int, out_length: int | None = None) -> "Span":
        if offset < 0:
            offset = 0
        if offset > self.length:
            offset = self.length
        if out_length is None:
            out_length = self.length - offset
        if out_length < 0:
            out_length = 0
        if out_length > (self.length - offset):
            out_length = self.length - offset
        return Span(self.ptr + offset, out_length)


@dataclass
class Descriptor:
    ptr: int
    length: int
    flags: int = 0

    def to_span(self) -> Span:
        return Span(self.ptr, self.length)


class TypeHint(IntEnum):
    BYTES = 1
    UTF8_TEXT = 2
    CARD = 3
    SCHEMA = 4
    QUERY = 5
    QUEUE_DESC = 6
    PACK_CTX = 7
    LEASE = 8


@dataclass
class Lease:
    lease_id: int
    type_hint: TypeHint
    span: Span
    generation: int = 0
    flags: int = 0
    active: bool = True


class ArenaAllocator:
    """Simple bump allocator model for process-local arena ownership."""

    def __init__(self, base_ptr: int, size: int):
        self.base_ptr = base_ptr
        self.size = max(0, size)
        self.head = 0

    def alloc(self, size: int, align: int = 8) -> int:
        if size <= 0:
            return self.base_ptr + self.head
        if align <= 0:
            align = 1
        pos = (self.head + (align - 1)) & ~(align - 1)
        end = pos + size
        if end > self.size:
            raise MemoryError("arena exhausted")
        self.head = end
        return self.base_ptr + pos

    def reset(self) -> None:
        self.head = 0

    def stats(self) -> dict:
        used = self.head
        free = max(0, self.size - used)
        return {"base_ptr": self.base_ptr, "size": self.size, "used": used, "free": free}


class LeaseManager:
    """Lease table for type-hinted span/pointer access mediation."""

    def __init__(self):
        self._next_id = 1
        self._leases: dict[int, Lease] = {}
        self._free_ids: list[int] = []

    def acquire(self, type_hint: TypeHint, span: Span, flags: int = 0) -> Lease:
        if self._free_ids:
            lease_id = self._free_ids.pop()
        else:
            lease_id = self._next_id
            self._next_id += 1
        lease = Lease(lease_id=lease_id, type_hint=type_hint, span=span, generation=0, flags=flags, active=True)
        self._leases[lease_id] = lease
        return lease

    def release(self, lease_id: int) -> bool:
        lease = self._leases.get(lease_id)
        if lease is None or not lease.active:
            return False
        lease.active = False
        lease.generation += 1
        self._free_ids.append(lease_id)
        return True

    def validate(self, lease_id: int, expected_type: TypeHint | None = None) -> bool:
        lease = self._leases.get(lease_id)
        if lease is None or not lease.active:
            return False
        if expected_type is not None and lease.type_hint != expected_type:
            return False
        return True

    def get_span(self, lease_id: int) -> Span | None:
        lease = self._leases.get(lease_id)
        if lease is None or not lease.active:
            return None
        return lease.span

    def get_type_hint(self, lease_id: int) -> TypeHint | None:
        lease = self._leases.get(lease_id)
        if lease is None or not lease.active:
            return None
        return lease.type_hint


class ArenaPool:
    """Pool rental helper over an arena, returning lease-backed spans."""

    def __init__(self, arena: ArenaAllocator, lease_manager: LeaseManager, chunk_size: int):
        self.arena = arena
        self.lease_manager = lease_manager
        self.chunk_size = max(1, chunk_size)
        self._free_ptrs: list[int] = []

    def rent(self, type_hint: TypeHint = TypeHint.BYTES) -> Lease:
        if self._free_ptrs:
            ptr = self._free_ptrs.pop()
        else:
            ptr = self.arena.alloc(self.chunk_size, align=8)
        return self.lease_manager.acquire(type_hint, Span(ptr, self.chunk_size))

    def return_lease(self, lease_id: int) -> bool:
        span = self.lease_manager.get_span(lease_id)
        ok = self.lease_manager.release(lease_id)
        if ok and span is not None:
            self._free_ptrs.append(span.ptr)
        return ok


class HostStorageApi:
    """Backend-swappable storage API shape used by Storage.* hook primitives."""

    def get_schema_for_pack(self, pack_ctx: int):
        raise NotImplementedError

    def set_schema_for_pack(self, pack_ctx: int, schema_desc: Descriptor):
        raise NotImplementedError

    def add_card(self, pack_ctx: int, card_desc: Descriptor):
        raise NotImplementedError

    def update_card(self, pack_ctx: int, card_id: int, card_desc: Descriptor):
        raise NotImplementedError

    def delete_card(self, pack_ctx: int, card_id: int):
        raise NotImplementedError

    def patch_card(self, pack_ctx: int, card_id: int, patch_desc: Descriptor):
        raise NotImplementedError

    def read_card(self, pack_ctx: int, card_id: int):
        raise NotImplementedError

    def query_card(self, pack_ctx: int, query_desc: Descriptor):
        raise NotImplementedError


class PicoStoreHostStorageApi(HostStorageApi):
    """Concrete reference backend wiring the Storage.* contract to PicoStore.

    Cards are serialized with the PicoBinarySerializer and held in a swappable
    PicoStore backend (in-memory by default). ``pack_ctx`` maps to a pack name;
    records and queries are passed at the value level (this structural runtime
    has no memory image to decode raw Descriptor bytes from -- that path lives in
    picoscript_vm.PicoVM). Returns the same shapes as the JS/Python store.
    """

    def __init__(self, store=None):
        from picostore import PicoStore  # local import: optional dependency
        self.store = store if store is not None else PicoStore()
        self._schemas: dict[int, object] = {}

    @staticmethod
    def _pack(pack_ctx: int) -> str:
        return str(pack_ctx)

    def get_schema_for_pack(self, pack_ctx: int):
        return self._schemas.get(pack_ctx)

    def set_schema_for_pack(self, pack_ctx: int, schema_desc):
        self._schemas[pack_ctx] = schema_desc
        return True

    def add_card(self, pack_ctx: int, card_desc):
        return self.store.create(self._pack(pack_ctx), card_desc)

    def update_card(self, pack_ctx: int, card_id: int, card_desc):
        return self.store.update(self._pack(pack_ctx), card_id, card_desc)

    def delete_card(self, pack_ctx: int, card_id: int):
        return self.store.delete(self._pack(pack_ctx), card_id)

    def patch_card(self, pack_ctx: int, card_id: int, patch_desc):
        return self.store.patch(self._pack(pack_ctx), card_id, patch_desc)

    def read_card(self, pack_ctx: int, card_id: int):
        return self.store.read(self._pack(pack_ctx), card_id)

    def query_card(self, pack_ctx: int, query_desc):
        return self.store.query(self._pack(pack_ctx), query_desc)


class ProfilingSlot:
    """Profiling bracket: start timestamp, label, state."""

    def __init__(self, slot_id: int):
        self.slot_id = slot_id
        self.start_tick = 0
        self.end_tick = 0
        self.elapsed = 0
        self.active = False


class ProfileManager:
    """Simple profiling manager for Kernel.ProfileStart/End hooks."""

    def __init__(self, slot_count: int = 16):
        self.slots = [ProfilingSlot(i) for i in range(slot_count)]
        self.trace_buffer: list[tuple[int, int, int]] = []  # (event_id, data, tick)

    def start(self, slot: int, current_tick: int = 0) -> None:
        if 0 <= slot < len(self.slots):
            self.slots[slot].start_tick = current_tick
            self.slots[slot].active = True

    def end(self, slot: int, current_tick: int = 0) -> int:
        if 0 <= slot < len(self.slots) and self.slots[slot].active:
            self.slots[slot].end_tick = current_tick
            self.slots[slot].active = False
            self.slots[slot].elapsed = current_tick - self.slots[slot].start_tick
            return self.slots[slot].elapsed
        return 0

    def trace_point(self, event_id: int, data: int, current_tick: int = 0) -> None:
        self.trace_buffer.append((event_id, data, current_tick))

    def get_trace(self) -> list[tuple[int, int, int]]:
        return self.trace_buffer.copy()

    def clear_trace(self) -> None:
        self.trace_buffer.clear()


class QueueDescriptor:
    """Queue item wrapper (for DequeueBatch, EnqueueBatch operations)."""

    def __init__(self, ptr: int, length: int, flags: int = 0, type_id: int = 0):
        self.ptr = ptr
        self.length = length
        self.flags = flags
        self.type_id = type_id

    def to_descriptor(self) -> Descriptor:
        return Descriptor(self.ptr, self.length, self.flags)


class SimpleQueue:
    """Simple FIFO queue for testing batch operations."""

    def __init__(self, capacity: int = 256):
        self.items: list[QueueDescriptor] = []
        self.capacity = capacity

    def enqueue(self, item: QueueDescriptor) -> bool:
        if len(self.items) >= self.capacity:
            return False
        self.items.append(item)
        return True

    def dequeue(self) -> QueueDescriptor | None:
        if not self.items:
            return None
        return self.items.pop(0)

    def enqueue_batch(self, items: list[QueueDescriptor]) -> int:
        """Enqueue multiple items atomically. Returns count enqueued."""
        added = 0
        for item in items:
            if self.enqueue(item):
                added += 1
            else:
                break
        return added

    def dequeue_batch(self, count: int) -> list[QueueDescriptor]:
        """Dequeue up to count items. Returns list (may be shorter than count)."""
        result = []
        for _ in range(min(count, len(self.items))):
            item = self.dequeue()
            if item is not None:
                result.append(item)
        return result

    def depth(self) -> int:
        return len(self.items)
