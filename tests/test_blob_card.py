import os
import subprocess

from picostore import PicoStore
from picoscript_cfront import Parser, tokenize
from picoscript_schema import blob_card_schema, generate_struct, schema_from_struct, TypedCardView
from picoscript_query import Field, Schema
from picoserializer import deserialize_card, serialize_card, to_hex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_blob_card_schema_and_lazy_view():
    schema = blob_card_schema(7, 1024)
    assert [(field.name, field.field_id, field.offset) for field in schema.fields] == [
        ("id", 0, 0), ("data", 1, 4)
    ]
    assert generate_struct(schema, "blobCard").splitlines() == [
        "struct blobCard {",
        "    @id(0) int id;",
        "    @id(1) byte[1024] data;",
        "}",
    ]
    store = PicoStore()
    store.create_pack("raw", max_card_bytes=32)
    card_id = store.create_blob("raw", b"\x00\x01payload")
    view = store.read_blob("raw", card_id)
    assert view.id == card_id
    assert bytes(view.data) == b"\x00\x01payload"
    assert view.as_record() == {"id": card_id, "data": b"\x00\x01payload"}
    try:
        store.create_blob("raw", b"x" * 33)
    except ValueError:
        pass
    else:
        raise AssertionError("pack max_card_bytes was not enforced")


def test_binary_serializer_bytes_matches_javascript():
    encoded = serialize_card({"id": 4, "data": b"\x00\xffabc"})
    assert deserialize_card(encoded) == {"id": 4, "data": b"\x00\xffabc"}
    script = """
const S = require('./vm/picoserializer.js');
const b = S.serializeCard({id: 4, data: new Uint8Array([0,255,97,98,99])});
process.stdout.write(S.toHex(b));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == to_hex(encoded)


def test_c_struct_annotations_generate_schema_layout():
    parser = Parser(tokenize("""
struct User {
    @id(0) int id;
    @id(1) text[40] name;
    @id(2) int flags;
};
"""))
    program = parser.parse_program()
    schema = schema_from_struct(program[0], pack_id=7, schema_id=9)
    assert [(field.name, field.field_id, field.offset, field.type) for field in schema.fields] == [
        ("id", 0, 0, "INT32"),
        ("name", 1, 4, "TEXT[40]"),
        ("flags", 2, 44, "INT32"),
    ]


def test_javascript_blob_pack_limit_and_view():
    script = """
const S = require('./vm/picostore.js');
const store = new S.PicoStore();
store.createPack('raw', 'raw', 8);
const id = store.createBlob('raw', new Uint8Array([1,2,3]));
const view = store.readBlob('raw', id);
process.stdout.write(JSON.stringify({id: view.id, data: Array.from(view.data),
  limit: store.packInfo('raw').max_card_bytes}));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"id":1,"data":[1,2,3],"limit":8}'


def test_typed_view_decodes_on_demand_and_supports_int64():
    encoded = serialize_card({"id": 7, "balance": 1 << 40})
    schema = Schema(1, 2, 1, (
        Field("id", 0, "INT32", 0),
        Field("balance", 1, "INT64", 4),
    ))
    view = TypedCardView(7, encoded, schema)
    assert view.get("balance") == 1 << 40
    assert view.copy()["id"] == 7
    script = """
const S = require('./vm/picoserializer.js');
const r = S.deserializeCard(S.serializeCard({id: 7, balance: 1099511627776n}));
process.stdout.write(String(r.balance));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(1 << 40)
