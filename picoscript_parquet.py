"""Hosted Parquet helpers and dependency-free Compact-Thrift primitives."""

from dataclasses import dataclass
from pathlib import Path
import struct


@dataclass(frozen=True)
class ParquetMetadata:
    size: int
    footer_length: int
    columns: tuple[str, ...] = ()
    rows: int = 0


@dataclass(frozen=True)
class FileMetaData:
    version: int
    schema: tuple
    num_rows: int
    row_groups: tuple
    created_by: str
    raw: dict


class ParquetError(ValueError):
    pass


class ThriftError(ParquetError):
    """Malformed or truncated Compact-Thrift input."""


class CompactType:
    STOP = 0
    BOOLEAN_TRUE = 1
    BOOLEAN_FALSE = 2
    BYTE = 3
    I16 = 4
    I32 = 5
    I64 = 6
    DOUBLE = 7
    BINARY = 8
    LIST = 9
    SET = 10
    MAP = 11
    STRUCT = 12


@dataclass(frozen=True)
class FieldHeader:
    field_id: int
    type: int
    boolean: bool | None = None


class CompactReader:
    """Bounded Compact-Thrift reader for Parquet metadata."""

    def __init__(self, data: bytes, *, max_binary: int = 64 * 1024 * 1024,
                 max_container: int = 1_000_000, max_depth: int = 64):
        self.data = memoryview(bytes(data))
        self.pos = 0
        self.max_binary = max_binary
        self.max_container = max_container
        self.max_depth = max_depth

    def _take(self, size: int) -> memoryview:
        if size < 0 or self.pos + size > len(self.data):
            raise ThriftError("compact thrift: truncated input")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def read_byte(self) -> int:
        return self._take(1)[0]

    def read_varint(self, *, max_bytes: int = 10) -> int:
        value = 0
        for index in range(max_bytes):
            byte = self.read_byte()
            value |= (byte & 0x7F) << (7 * index)
            if not byte & 0x80:
                if index == max_bytes - 1 and byte > 1:
                    raise ThriftError("compact thrift: varint overflow")
                return value
        raise ThriftError("compact thrift: unterminated varint")

    def read_zigzag(self, bits: int) -> int:
        if bits not in (16, 32, 64):
            raise ValueError("zigzag width must be 16, 32, or 64")
        value = self.read_varint(max_bytes=(bits + 6) // 7)
        return (value >> 1) ^ -(value & 1)

    def read_i16(self) -> int:
        return self.read_zigzag(16)

    def read_i32(self) -> int:
        return self.read_zigzag(32)

    def read_i64(self) -> int:
        return self.read_zigzag(64)

    def read_binary(self) -> bytes:
        length = self.read_varint(max_bytes=5)
        if length > self.max_binary:
            raise ThriftError("compact thrift: binary value exceeds limit")
        return bytes(self._take(length))

    def read_field_header(self, last_field_id: int = 0) -> FieldHeader:
        header = self.read_byte()
        typecode = header >> 4
        delta = header & 0x0F
        if typecode == CompactType.STOP:
            return FieldHeader(last_field_id, CompactType.STOP)
        if delta:
            field_id = last_field_id + delta
        else:
            field_id = self.read_i16()
        if field_id <= 0:
            raise ThriftError("compact thrift: invalid field id")
        if typecode == CompactType.BOOLEAN_TRUE:
            return FieldHeader(field_id, typecode, True)
        if typecode == CompactType.BOOLEAN_FALSE:
            return FieldHeader(field_id, typecode, False)
        if typecode > CompactType.STRUCT:
            raise ThriftError(f"compact thrift: unknown type {typecode}")
        return FieldHeader(field_id, typecode)

    def read_list_header(self) -> tuple[int, int]:
        header = self.read_byte()
        size = header >> 4
        typecode = header & 0x0F
        if size == 15:
            size = self.read_varint(max_bytes=5)
        if size > self.max_container:
            raise ThriftError("compact thrift: container exceeds limit")
        if typecode < CompactType.BOOLEAN_TRUE or typecode > CompactType.STRUCT:
            raise ThriftError(f"compact thrift: invalid list type {typecode}")
        return size, typecode

    def read_map_header(self) -> tuple[int, int, int]:
        size = self.read_varint(max_bytes=5)
        if size > self.max_container:
            raise ThriftError("compact thrift: map exceeds limit")
        if size == 0:
            return 0, CompactType.STOP, CompactType.STOP
        types = self.read_byte()
        key_type, value_type = types >> 4, types & 0x0F
        if not (CompactType.BOOLEAN_TRUE <= key_type <= CompactType.STRUCT and
                CompactType.BOOLEAN_TRUE <= value_type <= CompactType.STRUCT):
            raise ThriftError("compact thrift: invalid map type")
        return size, key_type, value_type

    def read_value(self, typecode: int, *, depth: int = 0, boolean: bool | None = None):
        if depth > self.max_depth:
            raise ThriftError("compact thrift: nesting exceeds limit")
        if typecode == CompactType.BOOLEAN_TRUE or typecode == CompactType.BOOLEAN_FALSE:
            return bool(boolean)
        if typecode == CompactType.BYTE:
            value = self.read_byte()
            return value - 256 if value & 0x80 else value
        if typecode == CompactType.I16:
            return self.read_i16()
        if typecode == CompactType.I32:
            return self.read_i32()
        if typecode == CompactType.I64:
            return self.read_i64()
        if typecode == CompactType.DOUBLE:
            return struct.unpack("<d", self._take(8))[0]
        if typecode == CompactType.BINARY:
            return self.read_binary()
        if typecode in (CompactType.LIST, CompactType.SET):
            size, item_type = self.read_list_header()
            return [self.read_value(item_type, depth=depth + 1) for _ in range(size)]
        if typecode == CompactType.MAP:
            size, key_type, value_type = self.read_map_header()
            return {
                self.read_value(key_type, depth=depth + 1):
                self.read_value(value_type, depth=depth + 1)
                for _ in range(size)
            }
        if typecode == CompactType.STRUCT:
            fields = {}
            last_id = 0
            while True:
                header = self.read_field_header(last_id)
                if header.type == CompactType.STOP:
                    return fields
                fields[header.field_id] = self.read_value(
                    header.type, depth=depth + 1, boolean=header.boolean)
                last_id = header.field_id
        raise ThriftError(f"compact thrift: unsupported type {typecode}")


def inspect_bytes(data: bytes) -> ParquetMetadata:
    if len(data) < 12 or data[:4] != b"PAR1" or data[-4:] != b"PAR1":
        raise ParquetError("invalid PAR1 envelope")
    footer = struct.unpack_from("<I", data, len(data) - 8)[0]
    if footer > len(data) - 12:
        raise ParquetError("footer exceeds file envelope")
    columns = ()
    rows = 0
    try:
        import pyarrow.parquet as pq
        import pyarrow as pa
    except ImportError:
        return ParquetMetadata(len(data), footer)
    try:
        table = pq.read_table(pa.BufferReader(data), columns=[])
    except Exception as exc:
        raise ParquetError("unable to inspect Parquet metadata") from exc
    return ParquetMetadata(len(data), footer, tuple(table.schema.names), table.num_rows)


def inspect(path):
    return inspect_bytes(Path(path).read_bytes())


def decode_file_metadata(data: bytes) -> FileMetaData:
    """Decode the Compact-Thrift FileMetaData footer without pyarrow."""
    envelope = bytes(data)
    if len(envelope) < 12 or envelope[:4] != b"PAR1" or envelope[-4:] != b"PAR1":
        raise ParquetError("invalid PAR1 envelope")
    footer_length = struct.unpack_from("<I", envelope, len(envelope) - 8)[0]
    if footer_length > len(envelope) - 12:
        raise ParquetError("footer exceeds file envelope")
    start = len(envelope) - 8 - footer_length
    try:
        raw = CompactReader(envelope[start:start + footer_length]).read_value(CompactType.STRUCT)
        created = raw.get(6, b"")
        if isinstance(created, bytes):
            created = created.decode("utf-8", "replace")
        return FileMetaData(
            int(raw.get(1, 0)), tuple(raw.get(2, ())), int(raw.get(3, 0)),
            tuple(raw.get(4, ())), str(created), raw,
        )
    except (ThriftError, TypeError, ValueError) as exc:
        raise ParquetError("invalid FileMetaData footer") from exc


def read_rows(path, start=0, limit=None):
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    rows = table.to_pylist()
    end = len(rows) if limit is None else min(len(rows), start + limit)
    return rows[start:end]


def read_column(path, name, start=0, limit=None):
    import pyarrow.parquet as pq
    column = pq.read_table(path, columns=[name]).column(name).to_pylist()
    end = len(column) if limit is None else min(len(column), start + limit)
    return column[start:end]
