"""Reflection-free schema/query contracts used by PicoScript host providers."""

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class Field:
    name: str
    field_id: int
    type: str
    offset: int


@dataclass(frozen=True)
class Schema:
    pack_id: int
    schema_id: int
    version: int
    fields: tuple

    @property
    def digest(self):
        body = [(f.name, f.field_id, f.type, f.offset) for f in self.fields]
        return hashlib.sha256(json.dumps([self.pack_id, self.schema_id, self.version, body], separators=(",", ":")).encode()).hexdigest()

    def bind(self, name):
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(name)


class Query:
    def __init__(self, pack_id, schema_id=None, node=None):
        self.pack_id, self.schema_id, self.node = pack_id, schema_id, node or ("PACK", pack_id)

    def where(self, op, field, value):
        return Query(self.pack_id, self.schema_id, (op, ("FIELD", field.field_id, field.type), ("CONST", value)))

    def and_(self, other):
        return Query(self.pack_id, self.schema_id, ("AND", self.node, other.node))

    def plan(self, indexes=()):
        fields = {self.node[1][1]} if self.node[0] in ("EQ", "GT", "GTE", "LT", "LTE") else set()
        chosen = "SCAN"
        for kind, field_id in indexes:
            if field_id in fields and ((kind == "HASH" and self.node[0] == "EQ") or (kind == "ORDERED" and self.node[0] != "EQ")):
                chosen = kind
                break
        return {"pack": self.pack_id, "schema": self.schema_id, "access": chosen, "predicate": self.node}


class CursorSnapshot:
    def __init__(self, record=None, card_id=0):
        self.record, self.card_id, self.valid = record, card_id, record is not None

    def invalidate(self):
        self.valid = False

    def copy_current(self, target):
        if not self.valid:
            return False
        target.clear(); target.update(self.record)
        return True
