import pytest
from picoscript_codec import CodecError, compress, compress_size, decompress


def test_codec_roundtrip_size_and_bounded_output():
    data = b"record page " * 100
    encoded = compress(data)
    assert compress_size(data) == len(encoded)
    assert decompress(encoded, len(data)) == data
    with pytest.raises(CodecError):
        decompress(encoded, len(data) - 1)


def test_codec_rejects_corrupt_input():
    with pytest.raises(CodecError):
        decompress(b"not a deflate stream")
