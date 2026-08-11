import pytest
from picoscript_context import Capabilities, ResourceProfile, ScratchContext


def test_reentrant_scratch_marks_and_profile_rejection():
    context = ScratchContext(16, Capabilities(storage=True))
    outer = context.mark(); context.reserve(4)
    inner = context.mark(); context.reserve(4)
    context.rewind(inner); context.rewind(outer)
    profile = ResourceProfile("pios", 1, 16, 2, 1, 4, 4, 2, 1, 8)
    assert ResourceProfile.load(profile.serialize(), "pios", 1) == profile
    with pytest.raises(ValueError):
        ResourceProfile.load(profile.serialize(), "browser", 1)


def test_scratch_exhaustion_is_explicit():
    context = ScratchContext(2)
    context.reserve(2)
    with pytest.raises(MemoryError):
        context.reserve(1)
