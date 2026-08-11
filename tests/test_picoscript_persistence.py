from picoscript_persistence import CheckpointJournal


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
