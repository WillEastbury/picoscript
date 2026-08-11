"""Explicit re-entrant contexts, capabilities, and resource profiles."""

from dataclasses import dataclass, asdict
import json


@dataclass(frozen=True)
class Capabilities:
    storage: bool = False
    compression: bool = False
    graph: bool = False
    tensor: bool = False
    model: bool = False
    persistence: bool = False
    cancellation: bool = False


@dataclass(frozen=True)
class ResourceProfile:
    name: str
    version: int
    arena: int
    handles: int
    cursors: int
    frontier: int
    postings: int
    pages: int
    checkpoints: int
    output: int

    def serialize(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def load(data, expected_name, expected_version):
        value = json.loads(data)
        if value.get("name") != expected_name or value.get("version") != expected_version:
            raise ValueError("incompatible resource profile")
        return ResourceProfile(**value)


class ScratchContext:
    def __init__(self, arena, capabilities=Capabilities(), profile=None):
        self.arena = bytearray(arena)
        self.capabilities = capabilities
        self.profile = profile
        self._marks = []
        self.top = 0

    def mark(self):
        self._marks.append(self.top)
        return self.top

    def rewind(self, mark):
        if not self._marks or mark != self._marks[-1] or mark < 0 or mark > self.top:
            raise ValueError("invalid scratch mark")
        self.top = mark; self._marks.pop()

    def reserve(self, size):
        if size < 0 or self.top + size > len(self.arena):
            raise MemoryError("scratch exhausted")
        start = self.top; self.top += size
        return memoryview(self.arena)[start:self.top]
