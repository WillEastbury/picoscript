import pytest

from picoscript_positional import PositionalIndex, PositionalPage, PositionalError


def test_positional_page_is_deterministic_and_round_trips():
    index = PositionalIndex()
    index.upsert(2, "bank river")
    page = index.upsert(1, "bank finance")
    reopened = PositionalPage.open(page.encoded)
    assert reopened.postings() == page.postings()
    assert index.query("bank") == [1, 2]
    assert index.query("bank finance", mode=1) == [1]
    assert index.query("bank finance", mode=2) == [1]


def test_near_query_and_corruption_are_bounded():
    index = PositionalIndex()
    index.upsert(1, "red battery pack")
    index.upsert(2, "red pack battery")
    assert index.query("red pack", mode=3, near_distance=2) == [1, 2]
    raw = bytearray(index.page.encoded); raw[-1] ^= 1
    with pytest.raises(PositionalError):
        PositionalPage.open(raw)
