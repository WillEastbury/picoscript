"""Provider-independent atomic checkpoint journal reference implementation.

Records are self-validating and commits are published only after all records
and the commit marker have been flushed.  Providers can map these operations
to PicoWAL pages or file/WALFS blocks without changing recovery semantics.
"""

from dataclasses import dataclass
import struct
import zlib

MAGIC = b"PSCP"
VERSION = 1
HEADER = struct.Struct("<4sBBHQQI")  # magic, version, kind, reserved, generation, key, length
COMMIT = 1
PAGE = 2


@dataclass(frozen=True)
class Recovery:
    generation: int
    pages: dict
    valid_end: int
    quarantined: bool


class CheckpointJournal:
    def __init__(self, initial=b""):
        self._data = bytearray(initial)

    @property
    def bytes(self):
        return bytes(self._data)

    def append_page(self, generation, key, payload):
        payload = bytes(payload)
        h = HEADER.pack(MAGIC, VERSION, PAGE, 0, generation, key, len(payload))
        self._data.extend(h + payload + struct.pack("<I", zlib.crc32(h + payload) & 0xFFFFFFFF))

    def commit(self, generation):
        h = HEADER.pack(MAGIC, VERSION, COMMIT, 0, generation, 0, 0)
        self._data.extend(h + struct.pack("<I", zlib.crc32(h) & 0xFFFFFFFF))

    def recover(self):
        pos = 0
        valid_end = 0
        pending = {}
        committed = {}
        committed_generation = 0
        quarantined = False
        while pos + HEADER.size + 4 <= len(self._data):
            start = pos
            h = bytes(self._data[pos:pos + HEADER.size])
            magic, version, kind, reserved, generation, key, length = HEADER.unpack(h)
            if magic != MAGIC or version != VERSION or reserved != 0 or length > len(self._data) - pos - HEADER.size - 4:
                quarantined = True
                break
            pos += HEADER.size
            payload = bytes(self._data[pos:pos + length])
            pos += length
            stored = struct.unpack_from("<I", self._data, pos)[0]
            pos += 4
            if stored != zlib.crc32(h + payload) & 0xFFFFFFFF:
                quarantined = True
                break
            if kind == PAGE:
                pending.setdefault(generation, {})[key] = payload
            elif kind == COMMIT and generation in pending and generation >= committed_generation:
                committed = dict(pending[generation])
                committed_generation = generation
                pending = {g: p for g, p in pending.items() if g > generation}
            else:
                quarantined = True
                break
            valid_end = pos
            if start == pos:  # defensive against malformed provider adapters
                quarantined = True
                break
        if valid_end < len(self._data):
            quarantined = True
            del self._data[valid_end:]
        return Recovery(committed_generation, committed, valid_end, quarantined)

    def compare_and_commit(self, expected_generation, generation, pages):
        current = self.recover()
        if current.generation != expected_generation or generation <= expected_generation:
            return False
        for key, payload in sorted(pages.items()):
            self.append_page(generation, key, payload)
        self.commit(generation)
        return True
