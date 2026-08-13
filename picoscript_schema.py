"""Built-in and generated fixed-layout schema views."""

from dataclasses import dataclass

from picoserializer import deserialize_card
from picoscript_query import Field, Schema


def generate_struct(schema, name="Record"):
    """Generate fixed-layout C-style source from a bound schema."""
    if not name.isidentifier():
        raise ValueError("struct name must be an identifier")
    lines = [f"struct {name} {{"]
    for field in sorted(schema.fields, key=lambda item: item.field_id):
        kind = str(field.type).upper()
        if kind in ("INT32", "UINT32"):
            type_name = "int"
        elif kind == "INT64":
            type_name = "int64"
        elif kind.startswith("TEXT[") or kind.startswith("BYTES["):
            type_name = kind.lower().replace("bytes", "byte")
        elif kind == "SPAN":
            raise ValueError(f"field {field.name} is variable-width; use a fixed bound")
        else:
            type_name = kind.lower()
        lines.append(f"    @id({field.field_id}) {type_name} {field.name};")
    lines.append("}")
    return "\n".join(lines)


def schema_from_struct(struct_def, pack_id=0, schema_id=0, version=1):
    """Convert a parsed fixed-layout C struct into a deterministic Schema."""
    fields = []
    offset = 0
    for field in sorted(struct_def.fields, key=lambda item: item.field_id):
        kind = field.ctype.name.upper()
        if kind in ("INT", "INT32", "UINT32"):
            type_name, width = "INT32", 4
        elif kind in ("BYTE", "BYTES", "CHAR", "UINT8_T", "INT8_T"):
            type_name, width = "BYTES", 1
        elif kind == "TEXT":
            type_name, width = "TEXT", 1
        else:
            raise ValueError(f"unsupported fixed struct field type: {field.ctype.name}")
        if field.count <= 0:
            raise ValueError("struct field count must be positive")
        fields.append(Field(field.name, field.field_id, f"{type_name}[{field.count}]" if field.count != 1 else type_name, offset))
        offset += width * field.count
    return Schema(int(pack_id), int(schema_id), int(version), tuple(fields))


def blob_card_schema(pack_id=0, max_bytes=16 * 1024 * 1024):
    """Return the implicit schema for a schema-less pack."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    return Schema(
        int(pack_id), 0, 0,
        (Field("id", 0, "INT32", 0), Field("data", 1, f"BYTES[{int(max_bytes)}]", 4)),
    )


@dataclass
class BlobCardView:
    """Lazy typed view over a serialized blobCard record."""

    card_id: int
    encoded: bytes
    _record: dict | None = None

    @property
    def id(self):
        return self.card_id

    @property
    def data(self):
        if self._record is None:
            self._record = deserialize_card(self.encoded)
        value = self._record.get("data", b"")
        if not isinstance(value, bytes):
            raise ValueError("blobCard data field is not bytes")
        return memoryview(value)

    def as_record(self):
        return {"id": self.id, "data": bytes(self.data)}
