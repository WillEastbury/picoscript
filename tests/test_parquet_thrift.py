import pytest

from picoscript_parquet import CompactReader, CompactType, ThriftError


def test_compact_reader_decodes_fields_lists_and_binary():
    # field 1: i32(42), field 2: binary("ok"), field 3: list<i16>([-1, 2])
    data = bytes([
        0x51, 0x54,  # field 1, I32, zigzag(42); field 2, binary
        0x81, 0x02, 0x6F, 0x6B,
        0x91, 0x24, 0x01, 0x04,  # field 3, list<i16>, -1 and 2
        0x00,
    ])
    assert CompactReader(data).read_value(CompactType.STRUCT) == {
        1: 42, 2: b"ok", 3: [-1, 2]
    }


def test_compact_reader_rejects_truncation_and_limits():
    with pytest.raises(ThriftError):
        CompactReader(b"\x55").read_value(CompactType.STRUCT)
    with pytest.raises(ThriftError):
        CompactReader(b"\x81\x80\x80\x80\x80\x01", max_binary=2).read_value(CompactType.STRUCT)
