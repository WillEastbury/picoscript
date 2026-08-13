#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picostore.py -- PicoStore: a pack-based card store with CRUD and a small query
language, backed by PicoBinarySerializer (picoserializer.py).

A *pack* is a named collection of cards (records) keyed by an auto-incrementing
id. Cards are serialized to binary and held in a pluggable key/value byte backend
(an in-memory dict here; localStorage in the browser, see vm/picostore.js).

CRUD:
    sid = store.create(pack, {"qty": 42, "sku": "ABC"})
    rec = store.read(pack, sid)
    store.update(pack, sid, {...})
    store.delete(pack, sid)

Query language (string):
    qty > 40 AND sku ~ "AB"
    status = 1 OR qty <= 0
Operators: =  ==  !=  <>  <  >  <=  >=  ~ (string contains).  NOT, AND, and
OR combine comparisons; NOT binds tightest, then AND, then OR. Field values
are int literals, quoted strings, or barewords. A missing field never matches.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional, Tuple

from picoserializer import serialize_card, deserialize_card, to_hex, from_hex
from picoscript_schema import BlobCardView, schema_from_struct

DEFAULT_MAX_CARD_BYTES = 4096


# ── key/value byte backend ───────────────────────────────────────────────────

class DictBackend:
    """Default in-memory backend. Values are strings (hex / csv / ints)."""

    def __init__(self):
        self._d: Dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._d.get(key)

    def set(self, key: str, value: str) -> None:
        self._d[key] = value

    def remove(self, key: str) -> None:
        self._d.pop(key, None)

    def keys(self) -> List[str]:
        return list(self._d.keys())


class JournalBackend:
    """Atomic key/value backend over the shared PicoWAL checkpoint protocol."""

    def __init__(self, journal=None):
        if journal is None:
            from picoscript_persistence import CheckpointJournal
            journal = CheckpointJournal()
        self.journal = journal
        self._data: Dict[str, str] = {}
        self._load()

    def _load(self):
        recovery = self.journal.recover()
        payload = recovery.pages.get(1)
        if payload is None:
            self._data = {}
            return
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("storage backend state is corrupt") from exc
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in value.items()):
            raise ValueError("storage backend state has invalid shape")
        self._data = dict(value)

    def _commit(self):
        recovery = self.journal.recover()
        payload = json.dumps(self._data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not self.journal.compare_and_commit(
            recovery.generation, recovery.generation + 1, {1: payload}
        ):
            raise RuntimeError("storage backend generation conflict")

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = str(value)
        self._commit()

    def remove(self, key: str) -> None:
        self._data.pop(key, None)
        self._commit()

    def keys(self) -> List[str]:
        return list(self._data)

    def sync(self) -> int:
        return 0

    def recover(self) -> int:
        self._load()
        return 0


# ── query language ───────────────────────────────────────────────────────────

_CMP2 = {"==", "!=", "<=", ">=", "<>"}


def _q_tokens(q: str) -> List[Tuple[str, str]]:
    toks: List[Tuple[str, str]] = []
    i, n = 0, len(q)
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1; continue
        if c in "\"'":
            j = i + 1; buf = []
            while j < n and q[j] != c:
                buf.append(q[j]); j += 1
            if j == n:
                raise ValueError("query: unterminated string literal")
            toks.append(("str", "".join(buf))); i = j + 1; continue
        two = q[i:i + 2]
        if two in _CMP2:
            toks.append(("op", two)); i += 2; continue
        if c in "<>=~":
            toks.append(("op", c)); i += 1; continue
        if c in "()":
            toks.append(("paren", c)); i += 1; continue
        j = i
        while j < n and (not q[j].isspace()) and q[j] not in "<>=~()\"'":
            j += 1
        w = q[i:j]; i = j
        up = w.upper()
        if up in ("AND", "OR", "NOT"):
            toks.append(("kw", up))
        else:
            toks.append(("word", w))
    return toks


class _QParser:
    def __init__(self, toks):
        self.toks = toks; self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def nxt(self):
        t = self.toks[self.i]; self.i += 1; return t

    def parse(self):
        node = self.parse_or()
        if self.peek() is not None:
            raise ValueError(f"query: unexpected token {self.peek()}")
        return node

    def parse_or(self):
        left = self.parse_and()
        while self.peek() and self.peek() == ("kw", "OR"):
            self.nxt(); right = self.parse_and()
            left = ("or", left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek() and self.peek() == ("kw", "AND"):
            self.nxt(); right = self.parse_not()
            left = ("and", left, right)
        return left

    def parse_not(self):
        if self.peek() and self.peek() == ("kw", "NOT"):
            self.nxt()
            return ("not", self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        if self.peek() and self.peek()[0] == "paren" and self.peek()[1] == "(":
            self.nxt(); node = self.parse_or()
            if not self.peek() or self.peek() != ("paren", ")"):
                raise ValueError("query: expected ')'")
            self.nxt()
            return node
        field = self.nxt()
        if field[0] != "word":
            raise ValueError(f"query: expected field, got {field}")
        op = self.nxt()
        if op[0] != "op":
            raise ValueError(f"query: expected operator, got {op}")
        val = self.nxt()
        return ("cmp", field[1], op[1], _coerce(val))


def _coerce(tok):
    kind, text = tok
    if kind == "str":
        return text
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        return text


def _eval_cmp(field, op, value, rec):
    if field not in rec:
        return False
    fv = rec[field]
    if op in ("=", "=="):
        return fv == value
    if op in ("!=", "<>"):
        return fv != value
    if op == "~":
        return str(value) in str(fv)
    # ordered comparisons
    try:
        if op == "<":
            return fv < value
        if op == ">":
            return fv > value
        if op == "<=":
            return fv <= value
        if op == ">=":
            return fv >= value
    except TypeError:
        return False
    raise ValueError(f"query: unknown operator {op}")


def _eval(node, rec) -> bool:
    kind = node[0]
    if kind == "and":
        return _eval(node[1], rec) and _eval(node[2], rec)
    if kind == "or":
        return _eval(node[1], rec) or _eval(node[2], rec)
    if kind == "not":
        return not _eval(node[1], rec)
    return _eval_cmp(node[1], node[2], node[3], rec)


def compile_query(q: str) -> Callable[[dict], bool]:
    """Compile a query string to a predicate `record -> bool`. Empty = match all."""
    q = (q or "").strip()
    if not q:
        return lambda rec: True
    ast = _QParser(_q_tokens(q)).parse()
    return lambda rec: _eval(ast, rec)


# ── store ────────────────────────────────────────────────────────────────────

STATUS_OK = 0
STATUS_NOT_FOUND = 1
STATUS_INVALID = 2
STATUS_DUPLICATE = 3
STATUS_CONFLICT = 4
STATUS_CORRUPT = 5


class SchemaMigrationError(ValueError):
    """Raised when a schema changes without an explicit version migration."""


class DuplicatePackError(ValueError):
    """Raised when a pack identifier is registered twice."""


class PicoStore:
    def __init__(self, backend=None):
        self.b = backend if backend is not None else DictBackend()
        self.last_status = STATUS_OK

    @staticmethod
    def _pack_name(pack) -> str:
        value = str(pack)
        if not value or ":" in value:
            raise ValueError("pack identifiers must be non-empty and colon-free")
        return value

    def _pack_meta_key(self, pack) -> str:
        return f"pack:{self._pack_name(pack)}:meta"

    def _schema_key(self, pack) -> str:
        return f"pack:{self._pack_name(pack)}:schema"

    def _ensure_pack(self, pack):
        pack = self._pack_name(pack)
        if self.b.get(self._pack_meta_key(pack)) is None:
            self.b.set(self._pack_meta_key(pack), json.dumps(
                {"id": pack, "name": pack, "max_card_bytes": DEFAULT_MAX_CARD_BYTES},
                sort_keys=True, separators=(",", ":")
            ))
        return pack

    def create_pack(self, pack_id, name=None, max_card_bytes=None) -> str:
        pack = self._pack_name(pack_id)
        if self.b.get(self._pack_meta_key(pack)) is not None:
            self.last_status = STATUS_DUPLICATE
            raise DuplicatePackError(pack)
        limit = DEFAULT_MAX_CARD_BYTES if max_card_bytes is None else int(max_card_bytes)
        if limit <= 0:
            raise ValueError("max_card_bytes must be positive")
        self.b.set(self._pack_meta_key(pack), json.dumps(
            {"id": pack, "name": str(name if name is not None else pack),
             "max_card_bytes": limit},
            sort_keys=True, separators=(",", ":")
        ))
        self.last_status = STATUS_OK
        return pack

    def pack_info(self, pack):
        raw = self.b.get(self._pack_meta_key(pack))
        if raw is None:
            self.last_status = STATUS_NOT_FOUND
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_status = STATUS_CORRUPT
            raise ValueError("pack metadata is corrupt") from exc
        self.last_status = STATUS_OK
        return value

    def packs(self) -> List[str]:
        result = []
        for key in self.b.keys():
            if key.startswith("pack:") and key.endswith(":meta"):
                result.append(key[5:-5])
        return sorted(set(result))

    @staticmethod
    def _normalize_schema(schema):
        if not isinstance(schema, dict):
            raise ValueError("schema must be an object")
        fields = schema.get("fields")
        if fields is None:
            return dict(schema)
        if not isinstance(fields, list):
            raise ValueError("schema fields must be a list")
        normalized = []
        names = set()
        ids = set()
        for index, field in enumerate(fields):
            if not isinstance(field, dict) or not field.get("name"):
                raise ValueError("schema field must have a name")
            name = str(field["name"])
            field_id = int(field.get("id", index + 1))
            if name in names or field_id in ids or field_id < 0:
                raise ValueError("schema fields must have unique non-negative ids and names")
            names.add(name); ids.add(field_id)
            normalized.append({
                "id": field_id,
                "name": name,
                "type": str(field.get("type", "ANY")).upper(),
                "required": bool(field.get("required", False)),
            })
        return {"fields": sorted(normalized, key=lambda item: item["id"])}

    @staticmethod
    def _schema_id(pack, version, schema):
        value = json.dumps(
            [pack, int(version), schema], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        h = 0xCBF29CE484222325
        for byte in value:
            h ^= byte
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"

    def register_schema(self, pack, schema, *, version=1, migrate=False):
        pack = self._ensure_pack(pack)
        version = int(version)
        if version < 1:
            raise ValueError("schema version must be positive")
        normalized = self._normalize_schema(schema)
        raw = self.b.get(self._schema_key(pack))
        current = json.loads(raw) if raw else None
        if current is not None and current["schema"] != normalized:
            if not migrate or version <= int(current["version"]):
                self.last_status = STATUS_CONFLICT
                raise SchemaMigrationError(
                    f"schema migration required for pack {pack}: "
                    f"{current['version']} -> {version}"
                )
        value = {"id": self._schema_id(pack, version, normalized),
                 "version": version, "schema": normalized}
        self.b.set(self._schema_key(pack), json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ))
        self.last_status = STATUS_OK
        return value

    def bind_struct(self, pack, struct_def, *, version=1, migrate=False):
        """Register or validate a parsed fixed-layout struct as the pack schema."""
        schema = schema_from_struct(struct_def, pack_id=0, schema_id=0, version=version)
        fields = [{
            "id": field.field_id,
            "name": field.name,
            "type": field.type,
            "required": True,
        } for field in schema.fields]
        return self.register_schema(
            pack, {"fields": fields}, version=version, migrate=migrate
        )

    def schema(self, pack):
        raw = self.b.get(self._schema_key(pack))
        if raw is None:
            self.last_status = STATUS_NOT_FOUND
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_status = STATUS_CORRUPT
            raise ValueError("schema is corrupt") from exc
        self.last_status = STATUS_OK
        return value

    def schema_compatible(self, pack, schema, version=None) -> bool:
        current = self.schema(pack)
        if current is None:
            return False
        normalized = self._normalize_schema(schema)
        return current["schema"] == normalized and (
            version is None or int(current["version"]) == int(version)
        )

    def _validate_record(self, pack, record):
        schema = self.schema(pack)
        pack_info = self.pack_info(pack)
        if pack_info and "data" in record and isinstance(record["data"], (bytes, bytearray, memoryview)):
            if len(record["data"]) > int(pack_info.get("max_card_bytes", DEFAULT_MAX_CARD_BYTES)):
                raise ValueError("blobCard data exceeds pack max_card_bytes")
        if schema is None or "fields" not in schema["schema"]:
            return
        fields = schema["schema"]["fields"]
        allowed = {field["name"] for field in fields}
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"record contains unknown fields: {sorted(unknown)}")
        for field in fields:
            name = field["name"]
            if field["required"] and name not in record:
                raise ValueError(f"record is missing required field: {name}")
            if name not in record or field["type"] == "ANY":
                continue
            value = record[name]
            kind = field["type"]
            if kind in ("INT", "INT32", "UINT32") and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"field {name} must be an integer")
            if kind in ("STRING", "TEXT", "SPAN") and not isinstance(value, str):
                raise ValueError(f"field {name} must be text")
            if kind.startswith("BYTES") and not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"field {name} must be bytes")

    def sync(self) -> int:
        sync = getattr(self.b, "sync", None)
        self.last_status = int(sync() if sync is not None else STATUS_OK)
        return self.last_status

    def recover(self) -> int:
        recover = getattr(self.b, "recover", None)
        self.last_status = int(recover() if recover is not None else STATUS_OK)
        return self.last_status

    def _ids(self, pack) -> List[int]:
        pack = self._pack_name(pack)
        raw = self.b.get(f"{pack}:ids")
        return [int(x) for x in raw.split(",") if x] if raw else []

    def _set_ids(self, pack, ids):
        pack = self._pack_name(pack)
        self.b.set(f"{pack}:ids", ",".join(str(x) for x in ids))

    def create(self, pack: str, record: dict, card_id: Optional[int] = None) -> int:
        pack = self._ensure_pack(pack)
        self._validate_record(pack, record)
        nxt = int(self.b.get(f"{pack}:next") or "1")
        card_id = nxt if card_id is None else int(card_id)
        if card_id < 1 or card_id in self._ids(pack):
            self.last_status = STATUS_DUPLICATE
            raise ValueError(f"duplicate or invalid card id: {card_id}")
        self.b.set(f"{pack}:card:{card_id}", to_hex(serialize_card(record)))
        self._set_ids(pack, self._ids(pack) + [card_id])
        self.b.set(f"{pack}:next", str(max(nxt, card_id + 1)))
        self.last_status = STATUS_OK
        return card_id

    insert = create

    def create_blob(self, pack: str, payload, card_id: Optional[int] = None) -> int:
        """Insert an implicit blobCard without decoding its payload."""
        pack = self._ensure_pack(pack)
        if card_id is None:
            card_id = int(self.b.get(f"{pack}:next") or "1")
        return self.create(pack, {"id": int(card_id), "data": bytes(payload)}, card_id)

    def read_blob(self, pack: str, card_id: int) -> Optional[BlobCardView]:
        """Return a lazy blobCard view over the serialized card bytes."""
        pack = self._pack_name(pack)
        encoded = self.b.get(f"{pack}:card:{card_id}")
        if not encoded:
            self.last_status = STATUS_NOT_FOUND
            return None
        self.last_status = STATUS_OK
        return BlobCardView(int(card_id), from_hex(encoded))

    def read(self, pack: str, card_id: int) -> Optional[dict]:
        pack = self._pack_name(pack)
        hexs = self.b.get(f"{pack}:card:{card_id}")
        if not hexs:
            self.last_status = STATUS_NOT_FOUND
            return None
        try:
            record = deserialize_card(from_hex(hexs))
        except (TypeError, ValueError, IndexError) as exc:
            self.last_status = STATUS_CORRUPT
            raise ValueError("card payload is corrupt") from exc
        self.last_status = STATUS_OK
        return record

    def update(self, pack: str, card_id: int, record: dict) -> bool:
        pack = self._ensure_pack(pack)
        if card_id not in self._ids(pack):
            self.last_status = STATUS_NOT_FOUND
            return False
        self._validate_record(pack, record)
        self.b.set(f"{pack}:card:{card_id}", to_hex(serialize_card(record)))
        self.last_status = STATUS_OK
        return True

    def patch(self, pack: str, card_id: int, fields: dict) -> bool:
        rec = self.read(pack, card_id)
        if rec is None:
            return False
        rec.update(fields)
        return self.update(pack, card_id, rec)

    def delete(self, pack: str, card_id: int) -> bool:
        pack = self._pack_name(pack)
        ids = self._ids(pack)
        if card_id not in ids:
            self.last_status = STATUS_NOT_FOUND
            return False
        ids.remove(card_id)
        self._set_ids(pack, ids)
        self.b.remove(f"{pack}:card:{card_id}")
        self.last_status = STATUS_OK
        return True

    def all(self, pack: str) -> List[Tuple[int, dict]]:
        out = []
        for cid in self._ids(pack):
            rec = self.read(pack, cid)
            if rec is not None:
                out.append((cid, rec))
        return out

    def query(self, pack: str, q: str) -> List[Tuple[int, dict]]:
        pred = compile_query(q)
        return [(cid, rec) for cid, rec in self.all(pack) if pred(rec)]

    def card_bytes_hex(self, pack: str, card_id: int) -> Optional[str]:
        pack = self._pack_name(pack)
        return self.b.get(f"{pack}:card:{card_id}")
