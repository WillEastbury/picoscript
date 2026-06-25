#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_runtime_coverage.py -- coverage for picoscript_runtime.py.

Targets: ArenaAllocator, LeaseManager, ArenaPool, SimpleQueue, ProfileManager.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_runtime import (  # noqa: E402
    Span,
    Descriptor,
    TypeHint,
    Lease,
    ArenaAllocator,
    LeaseManager,
    ArenaPool,
    SimpleQueue,
    QueueDescriptor,
    ProfileManager,
    PicoStoreHostStorageApi,
)


# ── Span ─────────────────────────────────────────────────────────────────────

def test_span_slice():
    """Span.slice extracts a sub-span."""
    s = Span(ptr=100, length=50)
    sub = s.slice(10, 20)
    assert sub.ptr == 110
    assert sub.length == 20


def test_span_slice_clamp():
    """Span.slice clamps out-of-bounds."""
    s = Span(ptr=0, length=10)
    sub = s.slice(5, 100)  # length exceeds available
    assert sub.length == 5


def test_span_slice_negative_offset():
    """Span.slice clamps negative offset to 0."""
    s = Span(ptr=0, length=10)
    sub = s.slice(-5)
    assert sub.ptr == 0
    assert sub.length == 10


# ── Descriptor ───────────────────────────────────────────────────────────────

def test_descriptor_to_span():
    """Descriptor.to_span creates a Span."""
    d = Descriptor(ptr=200, length=64, flags=1)
    s = d.to_span()
    assert s.ptr == 200
    assert s.length == 64


# ── ArenaAllocator ───────────────────────────────────────────────────────────

def test_arena_alloc_basic():
    """ArenaAllocator allocates sequential regions."""
    a = ArenaAllocator(base_ptr=0x1000, size=1024)
    ptr1 = a.alloc(100)
    ptr2 = a.alloc(200)
    assert ptr1 >= 0x1000
    assert ptr2 > ptr1


def test_arena_alloc_exhausted():
    """ArenaAllocator raises MemoryError when full."""
    import pytest
    a = ArenaAllocator(base_ptr=0, size=100)
    a.alloc(100)
    with pytest.raises(MemoryError):
        a.alloc(1)


def test_arena_reset():
    """ArenaAllocator.reset frees all allocations."""
    a = ArenaAllocator(base_ptr=0, size=256)
    a.alloc(200)
    a.reset()
    ptr = a.alloc(256)
    assert ptr is not None


def test_arena_stats():
    """ArenaAllocator.stats reports usage."""
    a = ArenaAllocator(base_ptr=0, size=1024)
    a.alloc(300)
    stats = a.stats()
    assert stats["used"] == 300 or stats["used"] >= 300
    assert stats["size"] == 1024


def test_arena_zero_alloc():
    """ArenaAllocator.alloc(0) returns current head."""
    a = ArenaAllocator(base_ptr=0x2000, size=512)
    ptr = a.alloc(0)
    assert ptr == 0x2000


# ── LeaseManager ─────────────────────────────────────────────────────────────

def test_lease_acquire_release():
    """LeaseManager basic acquire/release cycle."""
    lm = LeaseManager()
    span = Span(ptr=100, length=64)
    lease = lm.acquire(TypeHint.BYTES, span)
    assert lease.active is True
    assert lm.validate(lease.lease_id) is True
    lm.release(lease.lease_id)
    assert lm.validate(lease.lease_id) is False


def test_lease_get_span():
    """LeaseManager.get_span returns span info."""
    lm = LeaseManager()
    span = Span(ptr=200, length=128)
    lease = lm.acquire(TypeHint.UTF8_TEXT, span)
    got = lm.get_span(lease.lease_id)
    assert got is not None
    assert got.ptr == 200
    assert got.length == 128


def test_lease_get_type_hint():
    """LeaseManager.get_type_hint returns the type."""
    lm = LeaseManager()
    lease = lm.acquire(TypeHint.CARD, Span(0, 32))
    assert lm.get_type_hint(lease.lease_id) == TypeHint.CARD


def test_lease_invalid_id():
    """LeaseManager.validate returns False for unknown IDs."""
    lm = LeaseManager()
    assert lm.validate(9999) is False


def test_lease_reuse_id():
    """Released lease IDs are reused."""
    lm = LeaseManager()
    l1 = lm.acquire(TypeHint.BYTES, Span(0, 10))
    id1 = l1.lease_id
    lm.release(id1)
    l2 = lm.acquire(TypeHint.BYTES, Span(0, 20))
    assert l2.lease_id == id1  # reused


# ── ArenaPool ────────────────────────────────────────────────────────────────

def test_arena_pool_rent_return():
    """ArenaPool rent/return cycle."""
    arena = ArenaAllocator(base_ptr=0, size=4096)
    lm = LeaseManager()
    pool = ArenaPool(arena, lm, chunk_size=64)
    lease = pool.rent()
    assert lease is not None
    assert lease.active is True
    pool.return_lease(lease.lease_id)
    assert lm.validate(lease.lease_id) is False


def test_arena_pool_exhaustion():
    """ArenaPool raises MemoryError when arena is full."""
    import pytest
    arena = ArenaAllocator(base_ptr=0, size=128)
    lm = LeaseManager()
    pool = ArenaPool(arena, lm, chunk_size=64)
    pool.rent()  # 64 bytes
    pool.rent()  # 128 bytes (full)
    with pytest.raises(MemoryError):
        pool.rent()  # should fail


# ── SimpleQueue ──────────────────────────────────────────────────────────────

def test_queue_basic():
    """SimpleQueue enqueue/dequeue FIFO order."""
    q = SimpleQueue()
    q.enqueue(QueueDescriptor(ptr=1, length=10))
    q.enqueue(QueueDescriptor(ptr=2, length=20))
    q.enqueue(QueueDescriptor(ptr=3, length=30))
    assert q.dequeue().ptr == 1
    assert q.dequeue().ptr == 2
    assert q.dequeue().ptr == 3


def test_queue_empty_dequeue():
    """SimpleQueue.dequeue on empty returns None."""
    q = SimpleQueue()
    assert q.dequeue() is None


def test_queue_depth():
    """SimpleQueue.depth reports count."""
    q = SimpleQueue()
    q.enqueue(QueueDescriptor(ptr=0, length=1))
    q.enqueue(QueueDescriptor(ptr=0, length=1))
    assert q.depth() == 2
    q.dequeue()
    assert q.depth() == 1


def test_queue_batch():
    """SimpleQueue enqueue_batch/dequeue_batch."""
    q = SimpleQueue()
    items = [QueueDescriptor(ptr=i, length=8) for i in range(5)]
    count = q.enqueue_batch(items)
    assert count == 5
    batch = q.dequeue_batch(3)
    assert len(batch) == 3
    assert q.depth() == 2


def test_queue_capacity():
    """SimpleQueue respects capacity limit."""
    q = SimpleQueue(capacity=2)
    assert q.enqueue(QueueDescriptor(ptr=0, length=1)) is True
    assert q.enqueue(QueueDescriptor(ptr=0, length=1)) is True
    assert q.enqueue(QueueDescriptor(ptr=0, length=1)) is False


# ── ProfileManager ───────────────────────────────────────────────────────────

def test_profile_start_end():
    """ProfileManager.start/end creates a completed slot."""
    pm = ProfileManager()
    pm.start(0, current_tick=100)
    elapsed = pm.end(0, current_tick=150)
    assert elapsed == 50


def test_profile_trace_point():
    """ProfileManager.trace_point adds a point marker."""
    pm = ProfileManager()
    pm.trace_point(event_id=42, data=7, current_tick=200)
    trace = pm.get_trace()
    assert (42, 7, 200) in trace


def test_profile_clear():
    """ProfileManager.clear_trace resets."""
    pm = ProfileManager()
    pm.trace_point(1, 0, 0)
    pm.clear_trace()
    assert pm.get_trace() == []


def test_profile_invalid_slot():
    """ProfileManager.end on inactive slot returns 0."""
    pm = ProfileManager()
    assert pm.end(0, current_tick=100) == 0


# ── PicoStoreHostStorageApi ──────────────────────────────────────────────────

def test_storage_api_query_operators():
    """PicoStoreHostStorageApi handles various query operators."""
    api = PicoStoreHostStorageApi()
    api.add_card(1, {"name": "Alice", "age": 30, "city": "NYC"})
    api.add_card(1, {"name": "Bob", "age": 25, "city": "LA"})
    api.add_card(1, {"name": "Carol", "age": 35, "city": "NYC"})

    results = api.query_card(1, "city = NYC")
    assert len(results) == 2

    results = api.query_card(1, "age > 28")
    assert len(results) == 2


def test_storage_api_delete_idempotent():
    """Deleting a non-existent card returns False."""
    api = PicoStoreHostStorageApi()
    assert api.delete_card(1, 999) is False
