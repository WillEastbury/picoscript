import pytest

from picoscript_parquet import (CompactReader, CompactType, FileMetaData, ThriftError,
                                decode_file_metadata, describe_file_metadata, decode_page_header,
                                decode_plain, decode_rle_bitpacked,
                                decode_dictionary_page, decode_dictionary_indices,
                                decode_dictionary_values, decompress_page)


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


def test_page_header_decodes_sizes_and_data_header():
    # type=DATA_PAGE(0), uncompressed=10, compressed=8, data-page header struct.
    data = bytes([0x51, 0x00, 0x51, 0x14, 0x51, 0x10,
                  0xC2, 0x51, 0x02, 0x00, 0x00])
    header = decode_page_header(data)
    assert header.page_type == 0
    assert header.uncompressed_size == 10
    assert header.compressed_size == 8
    assert header.data_header == {1: 1}


def test_plain_and_rle_bitpacked_page_bodies():
    assert decode_plain((1).to_bytes(4, "little") + (-2).to_bytes(4, "little", signed=True),
                        "INT32", 2) == [1, -2]
    # RLE header (run=3 => 6) followed by value 2.
    assert decode_rle_bitpacked(bytes([6, 2]), 2, 3) == [2, 2, 2]


def test_dictionary_indices_and_builtin_codecs():
    dictionary = decode_dictionary_page(
        (1).to_bytes(4, "little") + (2).to_bytes(4, "little"), "INT32", 2
    )
    indices = decode_dictionary_indices(bytes([6, 1]), 2, 3)
    assert decode_dictionary_values(dictionary, indices) == [2, 2, 2]
    assert decompress_page(b"plain", "UNCOMPRESSED") == b"plain"
