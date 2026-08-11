from picoscript_query import CursorSnapshot, Field, Query, Schema


def test_schema_binding_digest_and_typed_plan():
    schema = Schema(12, 3, 1, (Field("OrderNumber", 1, "UINT32", 8), Field("Name", 2, "SPAN", 12)))
    assert schema.bind("OrderNumber").field_id == 1
    assert len(schema.digest) == 64
    query = Query(12, schema.schema_id).where("GT", schema.bind("OrderNumber"), 12)
    assert query.plan([("ORDERED", 1), ("HASH", 2)])["access"] == "ORDERED"


def test_cursor_snapshot_explicit_invalidation_and_copy():
    snap = CursorSnapshot({"OrderNumber": 14}, 7)
    target = {}
    assert snap.copy_current(target) and target == {"OrderNumber": 14}
    snap.invalidate()
    assert not snap.copy_current({})
