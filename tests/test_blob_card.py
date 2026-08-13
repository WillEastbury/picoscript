import os
import subprocess

from picostore import PicoStore
from picoscript_schema import blob_card_schema, generate_struct
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
        "    @id(1) byte[16777216] data;",
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
