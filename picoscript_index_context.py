"""Bounded pack-scoped resident index contexts.

The context table owns residency only; WAL/card storage remains authoritative.
Eviction is callback-driven so a host can persist dirty state before release.
"""

from dataclasses import dataclass


@dataclass
class Context:
    pack_id: int
    state: object
    dirty: bool = False
    last_used: int = 0
    pinned: bool = False


class ContextTable:
    def __init__(self, capacity, persist):
        self.capacity = max(1, capacity)
        self.persist = persist
        self.entries = {}
        self.clock = 0

    def acquire(self, pack_id, load):
        self.clock += 1
        current = self.entries.get(pack_id)
        if current:
            current.last_used = self.clock
            return current
        if len(self.entries) >= self.capacity:
            candidates = [c for c in self.entries.values() if not c.pinned]
            if not candidates:
                raise MemoryError("all index contexts pinned")
            victim = min(candidates, key=lambda c: (c.last_used, c.pack_id))
            if victim.dirty:
                self.persist(victim)
            del self.entries[victim.pack_id]
        context = Context(pack_id, load(pack_id), last_used=self.clock)
        self.entries[pack_id] = context
        return context

    def mark_dirty(self, pack_id):
        self.entries[pack_id].dirty = True

    def release(self, pack_id):
        context = self.entries.pop(pack_id, None)
        if context and context.dirty:
            self.persist(context)

