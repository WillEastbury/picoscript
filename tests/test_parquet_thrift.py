import pytest

from picoscript_parquet import (CompactReader, CompactType, FileMetaData, ThriftError,
                                decode_file_metadata, describe_file_metadata)


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


def test_file_metadata_footer_decodes_without_pyarrow():
    thrift = bytes([
        0x51, 0x02,             # version = 1
        0x62, 0x04,             # num_rows = 2
        0x83, 0x04,             # field 6, binary length 4
        0x70, 0x69, 0x63, 0x6F,
        0x00,
    ])
    envelope = b"PAR1" + thrift + len(thrift).to_bytes(4, "little") + b"PAR1"
    metadata = decode_file_metadata(envelope)
    assert metadata.version == 1
    assert metadata.num_rows == 2
    assert metadata.created_by == "pico"


def test_schema_and_row_group_descriptors_are_bounded():
    metadata = FileMetaData(
        1, ({1: 6, 3: 1, 4: b"l", 9: 1},),
        0, ({2: 12, 3: 4, 6: 10, 1: ({})},), "pico", {},
    )
    descriptors = describe_file_metadata(metadata)
    assert descriptors[0][0].name == "l"
