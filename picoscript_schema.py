"""Built-in and generated fixed-layout schema views."""

from dataclasses import dataclass

from picoserializer import deserialize_card
from picoscript_query import Field, Schema


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
