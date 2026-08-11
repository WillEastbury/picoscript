from picoscript_abi import HandleTable, Span, SpanFlags, Status, read_le, u64_from_pair, u64_pair, write_le


def test_span_wire_bounds_and_little_endian():
    raw = bytearray(range(16))
    span = Span(0, 16, SpanFlags.BORROWED)
    assert Span.unpack(span.pack()) == (span, Status.OK)
    assert read_le(raw, span, 1, 4) == (0x04030201, Status.OK)
    assert read_le(raw, span, 14, 4)[1] == Status.INVALID_ARGUMENT
    assert write_le(raw, span, 2, 0xAABBCCDD, 4) == Status.OK
    assert raw[2:6] == b"\xdd\xcc\xbb\xaa"


def test_generation_checked_handles_and_last_status():
    table = HandleTable(1)
    handle, st = table.open(7, "x")
    assert st == Status.OK
    assert table.get(handle, 7) == ("x", Status.OK)
    assert table.close(handle, 7) == Status.OK
    assert table.get(handle, 7)[1] == Status.INVALID_HANDLE
    assert table.last_status == Status.INVALID_HANDLE
    assert table.open(7, "y")[1] == Status.OK


def test_u64_pair_is_stable():
    value = 0xFEDCBA9876543210
    assert u64_from_pair(*u64_pair(value)) == value
