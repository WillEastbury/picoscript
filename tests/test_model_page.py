import pytest

from picoscript_model_page import ModelPage, ModelPageError, ModelRoot


def test_model_page_is_deterministic_compressed_and_round_trips():
    first = ModelPage.seal([(b"z", b"same"), (b"a", b"same")], generation=3)
    second = ModelPage.seal([(b"a", b"same"), (b"z", b"same")], generation=3)
    assert first.encoded == second.encoded
    assert ModelPage.open(first.encoded).entries() == [(b"a", b"same"), (b"z", b"same")]


def test_model_page_corruption_and_overflow_are_rejected():
    page = ModelPage.seal([(b"x", b"value")])
    corrupted = bytearray(page.encoded); corrupted[-1] ^= 0xFF
    with pytest.raises(ModelPageError):
        ModelPage.open(corrupted)
    with pytest.raises(ModelPageError):
        ModelPage.seal([(b"x", b"x" * 20000)])


def test_model_root_cold_reload_validates_page_checksums():
    page = ModelPage.seal([(b"k", b"v")])
    root = ModelRoot(2, (page.encoded,))
    reopened = ModelRoot.decode(root.encode(), (page.encoded,))
    assert reopened.generation == 2
