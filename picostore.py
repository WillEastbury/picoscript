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
Operators: =  ==  !=  <>  <  >  <=  >=  ~ (string contains).  AND / OR combine
comparisons; AND binds tighter than OR. Field values are int literals, quoted
strings, or barewords. A missing field never matches.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from picoserializer import serialize_card, deserialize_card, to_hex, from_hex


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
        if up in ("AND", "OR"):
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
        return node

    def parse_or(self):
        left = self.parse_and()
        while self.peek() and self.peek() == ("kw", "OR"):
            self.nxt(); right = self.parse_and()
            left = ("or", left, right)
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while self.peek() and self.peek() == ("kw", "AND"):
            self.nxt(); right = self.parse_cmp()
            left = ("and", left, right)
        return left

    def parse_cmp(self):
        if self.peek() and self.peek()[0] == "paren" and self.peek()[1] == "(":
            self.nxt(); node = self.parse_or()
            if self.peek() and self.peek() == ("paren", ")"):
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
    return _eval_cmp(node[1], node[2], node[3], rec)


def compile_query(q: str) -> Callable[[dict], bool]:
    """Compile a query string to a predicate `record -> bool`. Empty = match all."""
    q = (q or "").strip()
    if not q:
        return lambda rec: True
    ast = _QParser(_q_tokens(q)).parse()
    return lambda rec: _eval(ast, rec)


# ── store ────────────────────────────────────────────────────────────────────

class PicoStore:
    def __init__(self, backend=None):
        self.b = backend if backend is not None else DictBackend()

    def _ids(self, pack) -> List[int]:
        raw = self.b.get(f"{pack}:ids")
        return [int(x) for x in raw.split(",") if x] if raw else []

    def _set_ids(self, pack, ids):
        self.b.set(f"{pack}:ids", ",".join(str(x) for x in ids))

    def create(self, pack: str, record: dict) -> int:
        nxt = int(self.b.get(f"{pack}:next") or "1")
        self.b.set(f"{pack}:card:{nxt}", to_hex(serialize_card(record)))
        self._set_ids(pack, self._ids(pack) + [nxt])
        self.b.set(f"{pack}:next", str(nxt + 1))
        return nxt

    def read(self, pack: str, card_id: int) -> Optional[dict]:
        hexs = self.b.get(f"{pack}:card:{card_id}")
        return deserialize_card(from_hex(hexs)) if hexs else None

    def update(self, pack: str, card_id: int, record: dict) -> bool:
        if card_id not in self._ids(pack):
            return False
        self.b.set(f"{pack}:card:{card_id}", to_hex(serialize_card(record)))
        return True

    def patch(self, pack: str, card_id: int, fields: dict) -> bool:
        rec = self.read(pack, card_id)
        if rec is None:
            return False
        rec.update(fields)
        return self.update(pack, card_id, rec)

    def delete(self, pack: str, card_id: int) -> bool:
        ids = self._ids(pack)
        if card_id not in ids:
            return False
        ids.remove(card_id)
        self._set_ids(pack, ids)
        self.b.remove(f"{pack}:card:{card_id}")
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
        return self.b.get(f"{pack}:card:{card_id}")
