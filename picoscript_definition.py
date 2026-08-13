"""Deterministic Definition/index registry over provider key/value storage."""

from dataclasses import dataclass
import hashlib
import json


INDEX_KINDS = frozenset({
    "HASH", "ORDERED", "FULLTEXT", "POSITIONAL",
    "GRAPH_NODE", "GRAPH_EDGE", "GRAPH_WEIGHT",
})


@dataclass(frozen=True)
class IndexDefinition:
    pack: str
    kind: str
    field_id: int
    version: int = 1
    generation: int = 0

    @property
    def index_id(self):
        value = json.dumps(
            [self.pack, self.kind, self.field_id, self.version],
            separators=(",", ":"), sort_keys=True,
        ).encode()
        return hashlib.sha256(value).hexdigest()[:16]

    def encoded(self):
        return {
            "id": self.index_id, "pack": self.pack, "kind": self.kind,
            "field_id": self.field_id, "version": self.version,
            "generation": self.generation,
        }


class DefinitionRegistry:
    """Persistent metadata registry; index pages remain provider-owned."""

    def __init__(self, backend=None):
        self.backend = backend if backend is not None else {}
        self.status = 0

    def _key(self, index_id):
        return f"definition:{index_id}"

    def _get(self, key):
        return self.backend.get(key) if hasattr(self.backend, "get") else self.backend.get(key)

    def _put(self, key, value):
        if hasattr(self.backend, "set"):
            self.backend.set(key, value)
        else:
            self.backend[key] = value

    def _delete(self, key):
        if hasattr(self.backend, "remove"):
            self.backend.remove(key)
        else:
            del self.backend[key]

    def _read(self, index_id):
        raw = self._get(self._key(index_id))
        return json.loads(raw) if raw is not None and isinstance(raw, str) else raw

    def add(self, pack, kind, field_id, *, version=1, generation=0):
        if kind not in INDEX_KINDS or int(field_id) < 0 or int(version) < 1:
            self.status = 2
            raise ValueError("invalid index definition")
        definition = IndexDefinition(str(pack), kind, int(field_id), int(version), int(generation))
        existing = self._read(definition.index_id)
        if existing is not None:
            self.status = 3
            return definition
        self._put(self._key(definition.index_id), json.dumps(
            definition.encoded(), sort_keys=True, separators=(",", ":")
        ))
        self.status = 0
        return definition

    def remove(self, index_id):
        key = self._key(str(index_id))
        if key not in self.backend:
            self.status = 1
            return False
        self._delete(key)
        self.status = 0
        return True

    def state(self, index_id):
        value = self._read(str(index_id))
        self.status = 0 if value is not None else 1
        return value

    def rebuild(self, index_id, generation):
        value = self.state(index_id)
        if value is None:
            return None
        value["generation"] = int(generation)
        self._put(self._key(str(index_id)), json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ))
        self.status = 0
        return value

    def resolve_key(self, pack, kind, field_id):
        definition = IndexDefinition(str(pack), kind, int(field_id))
        return definition.index_id if self._read(definition.index_id) is not None else ""

    def resolve_pack(self, index_id):
        value = self.state(index_id)
        return value["pack"] if value is not None else ""
