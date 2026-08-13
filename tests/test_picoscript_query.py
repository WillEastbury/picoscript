from picoscript_query import CursorSnapshot, Field, Query, Schema


def test_schema_binding_digest_and_typed_plan():
    schema = Schema(12, 3, 1, (Field("Name", 2, "SPAN", 12), Field("OrderNumber", 1, "UINT32", 8)))
    assert schema.bind("OrderNumber").field_id == 1
    assert schema.bind_id(2).name == "Name"
    assert len(schema.digest) == 64
    query = Query(12, schema.schema_id).where("GT", schema.bind("OrderNumber"), 12)
    assert query.plan([("ORDERED", 1), ("HASH", 2)])["access"] == "ORDERED"
    assert query.not_().plan([("ORDERED", 1)])["access"] == "SCAN"
    assert query.or_(query).plan([("ORDERED", 1)])["access"] == "SCAN"


def test_cursor_snapshot_explicit_invalidation_and_copy():
    snap = CursorSnapshot({"OrderNumber": 14}, 7)
    target = {}
    assert snap.copy_current(target) and target == {"OrderNumber": 14}
    snap.invalidate()
    assert not snap.copy_current({})


def test_schema_rejects_ambiguous_bindings():
    field = Field("id", 1, "UINT32", 0)
    try:
        Schema(1, 1, 1, (field, field))
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate schema fields must be rejected")


def test_query_ir_is_canonical_and_plan_preserves_residuals():
    schema = Schema(7, 2, 1, (
        Field("qty", 1, "INT32", 0),
        Field("status", 2, "INT32", 4),
    ))
    query = (
        Query(7, schema.schema_id)
        .where("EQ", schema.bind("qty"), 4)
        .and_(Query(7, schema.schema_id).where("GT", schema.bind("status"), 0))
        .select(schema.bind("qty"))
        .order(schema.bind("qty"), descending=True)
        .limit(10)
    )
    plan = query.plan([("ORDERED", 2), ("HASH", 1)])
    assert plan["access"] == "HASH"
    assert plan["index"] == ["HASH", 1]
    assert plan["residual"] == ("GT", ("FIELD", 2, "INT32"), ("CONST", 0))
    assert query.serialize() == query.serialize()
