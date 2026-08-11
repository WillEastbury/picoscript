import pytest
from picoscript_index_context import ContextTable


def test_pack_scoped_lru_persists_dirty_victim_and_reloads():
    persisted = []
    table = ContextTable(2, lambda context: persisted.append((context.pack_id, context.state)))
    table.acquire(1, lambda pack: {"pack": pack})
    table.acquire(2, lambda pack: {"pack": pack})
    table.mark_dirty(1)
    table.acquire(3, lambda pack: {"pack": pack})
    assert persisted == [(1, {"pack": 1})]
    assert set(table.entries) == {2, 3}
    assert table.acquire(1, lambda pack: {"pack": pack}).state == {"pack": 1}


def test_pinned_contexts_bound_residency():
    table = ContextTable(1, lambda context: None)
    table.acquire(1, lambda pack: pack).pinned = True
    with pytest.raises(MemoryError):
        table.acquire(2, lambda pack: pack)
