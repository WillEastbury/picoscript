"""Immutable deterministic model-page format for PicoWAL overlays."""

from dataclasses import dataclass
import struct
import zlib

MAGIC = b"PMPG"
VERSION = 1
PAGE_SIZE = 16 * 1024
HEADER = struct.Struct("<4sBBHIIIIIII")


class ModelPageError(ValueError):
    pass


def _encode_entries(entries):
    ordered = sorted(((bytes(key), bytes(value)) for key, value in entries), key=lambda item: item[0])
    out = bytearray()
    for key, value in ordered:
        if len(key) > 0xFFFF:
            raise ModelPageError("model-page key is too long")
        out.extend(struct.pack("<HI", len(key), len(value)))
        out.extend(key); out.extend(value)
    return bytes(out), len(ordered)


@dataclass(frozen=True)
class ModelPage:
    encoded: bytes
    generation: int
    compressed: bool
    entry_count: int
    logical_length: int

    @classmethod
    def seal(cls, entries, *, generation=1):
        decoded, count = _encode_entries(entries)
        if len(decoded) > PAGE_SIZE:
            raise ModelPageError("decoded model page exceeds 16 KiB")
        compressed_data = zlib.compress(decoded, level=9)
        compressed = len(compressed_data) < len(decoded)
        payload = compressed_data if compressed else decoded
        if len(payload) > PAGE_SIZE:
            raise ModelPageError("encoded model page exceeds 16 KiB")
        header = HEADER.pack(
            MAGIC, VERSION, 1 if compressed else 0, 0,
            len(decoded), len(payload), count,
            zlib.crc32(payload) & 0xFFFFFFFF,
            zlib.crc32(decoded) & 0xFFFFFFFF,
            int(generation), 0,
        )
        return cls(header + payload, int(generation), compressed, count, len(decoded))

    @classmethod
    def open(cls, encoded):
        raw = bytes(encoded)
        if len(raw) < HEADER.size:
            raise ModelPageError("model page is truncated")
        magic, version, flags, reserved, logical, encoded_len, count, packed_crc, decoded_crc, generation, _ = HEADER.unpack_from(raw)
        if magic != MAGIC or version != VERSION or reserved != 0 or flags & ~1:
            raise ModelPageError("model page format mismatch")
        if encoded_len > PAGE_SIZE or logical > PAGE_SIZE or HEADER.size + encoded_len != len(raw):
            raise ModelPageError("model page bounds mismatch")
        payload = raw[HEADER.size:]
        if zlib.crc32(payload) & 0xFFFFFFFF != packed_crc:
            raise ModelPageError("model page compressed checksum mismatch")
        decoded = zlib.decompress(payload) if flags & 1 else payload
        if len(decoded) != logical or zlib.crc32(decoded) & 0xFFFFFFFF != decoded_crc:
            raise ModelPageError("model page decoded checksum mismatch")
        _, actual_count = _decode_entries(decoded)
        if actual_count != count:
            raise ModelPageError("model page entry count mismatch")
        return cls(raw, generation, bool(flags & 1), count, logical)

    def entries(self):
        payload = self.encoded[HEADER.size:]
        decoded = zlib.decompress(payload) if self.compressed else payload
        return _decode_entries(decoded)[0]


def _decode_entries(data):
    pos = 0
    entries = []
    while pos < len(data):
        if pos + 6 > len(data):
            raise ModelPageError("model page entry header truncated")
        key_len, value_len = struct.unpack_from("<HI", data, pos)
        pos += 6
        if pos + key_len + value_len > len(data):
            raise ModelPageError("model page entry truncated")
        key = bytes(data[pos:pos + key_len]); pos += key_len
        value = bytes(data[pos:pos + value_len]); pos += value_len
        entries.append((key, value))
    return entries, len(entries)


@dataclass(frozen=True)
class ModelRoot:
    generation: int
    pages: tuple

    def encode(self):
        return struct.pack("<4sBI", MAGIC, VERSION, self.generation) + b"".join(
            struct.pack("<I", zlib.crc32(page) & 0xFFFFFFFF) for page in self.pages
        )

    @classmethod
    def decode(cls, data, page_bytes):
        raw = bytes(data)
        if len(raw) < 9 or raw[:4] != MAGIC or raw[4] != VERSION:
            raise ModelPageError("model root format mismatch")
        generation = struct.unpack_from("<I", raw, 5)[0]
        checksums = [struct.unpack_from("<I", raw, pos)[0] for pos in range(9, len(raw), 4)]
        if len(checksums) != len(page_bytes):
            raise ModelPageError("model root page count mismatch")
        if any(zlib.crc32(page) & 0xFFFFFFFF != checksum for page, checksum in zip(page_bytes, checksums)):
            raise ModelPageError("model root page checksum mismatch")
        return cls(generation, tuple(page_bytes))
