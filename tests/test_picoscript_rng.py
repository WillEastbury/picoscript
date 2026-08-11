from picoscript_rng import RandomStream


def test_streams_are_independent_and_checkpointable():
    a = RandomStream(123, 1)
    b = RandomStream(123, 2)
    first = [a.next_u64() for _ in range(5)]
    assert first != [b.next_u64() for _ in range(5)]
    state = a.checkpoint()
    expected = [a.next_u64() for _ in range(4)]
    restored = RandomStream.restore(state)
    assert [restored.next_u64() for _ in range(4)] == expected


def test_below_is_bounded_and_checkpoint_contains_position():
    r = RandomStream(9, 99)
    values = [r.below(7) for _ in range(100)]
    assert all(0 <= x < 7 for x in values)
    assert r.checkpoint()[2] >= 100
