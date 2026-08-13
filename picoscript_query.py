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

    def __post_init__(self):
        ids = [field.field_id for field in self.fields]
        names = [field.name for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("schema: duplicate field id")
        if len(names) != len(set(names)):
            raise ValueError("schema: duplicate field name")
        if any(field.field_id < 0 or field.offset < 0 for field in self.fields):
            raise ValueError("schema: field id and offset must be non-negative")

    @property
    def digest(self):
        body = [(f.name, f.field_id, f.type, f.offset)
                for f in sorted(self.fields, key=lambda item: (item.field_id, item.name))]
        return hashlib.sha256(json.dumps([self.pack_id, self.schema_id, self.version, body], separators=(",", ":")).encode()).hexdigest()

    def bind(self, name):
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(name)

    def bind_id(self, field_id):
        for field in self.fields:
            if field.field_id == field_id:
                return field
        raise KeyError(field_id)


class Query:
    def __init__(self, pack_id, schema_id=None, node=None, projection=(),
                 ordering=(), row_limit=None):
        self.pack_id, self.schema_id = pack_id, schema_id
        self.node = node or ("PACK", pack_id)
        self.projection = tuple(projection)
        self.ordering = tuple(ordering)
        self.row_limit = row_limit

    def where(self, op, field, value):
        return Query(
            self.pack_id, self.schema_id,
            (str(op).upper(), ("FIELD", field.field_id, field.type), ("CONST", value)),
            self.projection, self.ordering, self.row_limit,
        )

    def and_(self, other):
        return Query(self.pack_id, self.schema_id, ("AND", self.node, other.node),
                     self.projection, self.ordering, self.row_limit)

    def or_(self, other):
        return Query(self.pack_id, self.schema_id, ("OR", self.node, other.node),
                     self.projection, self.ordering, self.row_limit)

    def not_(self):
        return Query(self.pack_id, self.schema_id, ("NOT", self.node),
                     self.projection, self.ordering, self.row_limit)

    def select(self, *fields):
        return Query(self.pack_id, self.schema_id, self.node, tuple(
            field.field_id if isinstance(field, Field) else int(field) for field in fields
        ), self.ordering, self.row_limit)

    def order(self, field, descending=False):
        field_id = field.field_id if isinstance(field, Field) else int(field)
        return Query(self.pack_id, self.schema_id, self.node, self.projection,
                     self.ordering + ((field_id, bool(descending)),), self.row_limit)

    def limit(self, count):
        count = int(count)
        if count < 0:
            raise ValueError("query limit must be non-negative")
        return Query(self.pack_id, self.schema_id, self.node, self.projection,
                     self.ordering, count)

    @staticmethod
    def _canonical(node):
        if not isinstance(node, tuple):
            return node
        op = node[0]
        if op in ("AND", "OR"):
            children = []
            for child in node[1:]:
                child = Query._canonical(child)
                if isinstance(child, tuple) and child[0] == op:
                    children.extend(child[1:])
                else:
                    children.append(child)
            children.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
            return (op, *children)
        if op == "NOT":
            child = Query._canonical(node[1])
            if isinstance(child, tuple) and child[0] == "NOT":
                return child[1]
            return ("NOT", child)
        return tuple(Query._canonical(item) for item in node)

    def ir(self):
        return {
            "version": 1,
            "pack": self.pack_id,
            "schema": self.schema_id,
            "predicate": self._canonical(self.node),
            "projection": list(self.projection),
            "order": [[field, descending] for field, descending in self.ordering],
            "limit": self.row_limit,
        }

    def serialize(self):
        return json.dumps(self.ir(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def plan(self, indexes=()):
        def leaves(node):
            if node[0] == "AND":
                merged = leaves(node[1])
                merged.update(leaves(node[2]))
                return merged
            if node[0] in ("OR", "NOT"):
                return {}
            if len(node) > 1 and isinstance(node[1], tuple) and node[1][0] == "FIELD":
                return {node[1][1]: node[0]}
            return {}

        predicate = self._canonical(self.node)
        fields = leaves(predicate)
        chosen = "SCAN"
        chosen_index = None
        rank = {"HASH": 0, "ORDERED": 1, "FULLTEXT": 2, "GRAPH": 3, "SCAN": 4}
        for kind, field_id in sorted(indexes, key=lambda item: (rank.get(item[0], 99), item[1])):
            op = fields.get(field_id)
            if op and ((kind == "HASH" and op == "EQ") or
                       (kind == "ORDERED" and op in ("GT", "GTE", "LT", "LTE"))):
                chosen = kind
                chosen_index = [kind, field_id]
                break
        residual = predicate
        if chosen_index is not None:
            residual = self._remove_index_predicate(predicate, chosen_index[0], chosen_index[1])
        return {
            "version": 1,
            "pack": self.pack_id,
            "schema": self.schema_id,
            "access": chosen,
            "index": chosen_index,
            "predicate": predicate,
            "residual": residual,
            "projection": list(self.projection),
            "order": [[field, descending] for field, descending in self.ordering],
            "limit": self.row_limit,
            "bytes": self.serialize(),
        }

    @staticmethod
    def _remove_index_predicate(node, kind, field_id):
        if not isinstance(node, tuple):
            return node
        if len(node) >= 2 and node[0] in ("EQ", "GT", "GTE", "LT", "LTE"):
            field = node[1]
            if isinstance(field, tuple) and field[0] == "FIELD" and field[1] == field_id:
                if kind == "HASH" and node[0] == "EQ":
                    return ("TRUE",)
                if kind == "ORDERED" and node[0] in ("GT", "GTE", "LT", "LTE"):
                    return ("TRUE",)
        if node[0] == "AND":
            children = [Query._remove_index_predicate(child, kind, field_id) for child in node[1:]]
            children = [child for child in children if child != ("TRUE",)]
            return ("TRUE",) if not children else children[0] if len(children) == 1 else ("AND", *children)
        return node


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
