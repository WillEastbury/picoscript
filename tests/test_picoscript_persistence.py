import os
import tempfile

from picoscript_persistence import (
    ArtifactStore,
    CheckpointJournal,
    DurableCheckpointJournal,
)


def test_last_complete_checkpoint_wins_and_torn_tail_is_quarantined():
    journal = CheckpointJournal()
    assert journal.compare_and_commit(0, 1, {2: b"one"})
    assert journal.compare_and_commit(1, 2, {2: b"two", 3: b"three"})
    journal._data.extend(b"partial")
    recovered = journal.recover()
    assert recovered.generation == 2
    assert recovered.pages == {2: b"two", 3: b"three"}
    assert recovered.quarantined
    assert not journal.compare_and_commit(1, 2, {2: b"stale"})


def test_crc_corruption_quarantines_tail_but_keeps_previous_commit():
    journal = CheckpointJournal()
    assert journal.compare_and_commit(0, 1, {1: b"good"})
    journal.append_page(2, 1, b"bad")
    journal._data[-1] ^= 0xFF
    recovered = journal.recover()
    assert recovered.generation == 1
    assert recovered.pages == {1: b"good"}
    assert recovered.quarantined


def test_artifact_publish_validate_stream_dependency_and_rollback():
    store = ArtifactStore()
    source = store.publish("stats", "statistics", [b"stats-v1"])
    model = store.publish(
        "model", "model_page", [b"page-a", b"page-b"],
        dependencies=(("stats", source.generation),),
        metadata=b"provenance",
    )
    assert store.validate("model").valid
    assert b"".join(bytes(chunk) for chunk in store.stream("model", chunk_size=3)) == b"page-apage-b"
    assert bytes(store.read_page("model", 1)) == b"page-b"
    store.publish("stats", "statistics", [b"stats-v2"])
    stale = store.validate("model")
    assert not stale.valid and stale.stale
    restored = store.rollback("model", model.generation)
    assert restored.generation == 4
    assert b"".join(bytes(chunk) for chunk in store.stream("model")) == b"page-apage-b"


def test_durable_artifact_reopens_and_rejects_oversized_pages():
    fd, raw_path = tempfile.mkstemp(suffix=".pwal")
    os.close(fd)
    path = raw_path
    first = ArtifactStore(DurableCheckpointJournal(path), max_page_bytes=8)
    manifest = first.publish("checkpoint", "checkpoint", [b"01234567"])
    reopened = ArtifactStore(DurableCheckpointJournal(path), max_page_bytes=8)
    assert reopened.validate("checkpoint", manifest.generation).valid
    try:
        reopened.publish("bad", "checkpoint", [b"012345678"])
    except ValueError as exc:
        assert "page" in str(exc)
    else:
        raise AssertionError("oversized artifact page was accepted")
    os.remove(path)
