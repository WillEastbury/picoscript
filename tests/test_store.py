#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_store.py -- PicoBinarySerializer + PicoStore (CRUD + query) tests.

Verifies:
  * serialize/deserialize round-trips
  * Python and JS serializers produce byte-identical card bytes
  * Python and JS query language give identical results
  * CRUD (create / read / update / patch / delete) behaves

Run:  python tests/test_store.py   (drives Node for the JS side)
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoserializer import serialize_card, deserialize_card, to_hex   # noqa: E402
from picostore import PicoStore, compile_query                        # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

RECORDS = [
    {"qty": 42, "sku": "ABC", "status": 1, "price": -150},
    {"qty": 10, "sku": "XYZ", "status": 0, "price": 99},
    {"qty": 99, "sku": "ABD", "status": 1, "price": 0},
    {"name": "widget", "tags": "a,b,c"},
]
QUERIES = [
    "qty > 40 AND status = 1",
    "sku ~ AB",
    "status = 2 OR qty <= 10",
    "price >= 0 AND price < 100",
    "name ~ wid",
    "",
]


def run_js(records, queries):
    inp = json.dumps({"records": records, "queries": queries})
    runner = os.path.join(VM_DIR, "picostore_check.js")
    out = subprocess.run(["node", runner], input=inp, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("node: " + out.stderr)
    return json.loads(out.stdout)


def main():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if cond:
            passed += 1
        else:
            failed += 1

    # serializer round-trip
    for r in RECORDS:
        check(f"round-trip {sorted(r)}", deserialize_card(serialize_card(r)) == r)

    # Python vs JS: byte-identical serialization + identical query results
    py_hexes = [to_hex(serialize_card(r)) for r in RECORDS]
    s = PicoStore()
    for r in RECORDS:
        s.create("p", r)
    py_results = [[[cid, rec] for cid, rec in s.query("p", q)] for q in QUERIES]

    js = run_js(RECORDS, QUERIES)
    check("serializer bytes Python == JS", py_hexes == js["hexes"])
    check("query results Python == JS", py_results == js["results"])
    check("JS round-trip == records", js["roundtrip"] == RECORDS)

    # CRUD
    s2 = PicoStore()
    i1 = s2.create("orders", {"qty": 5, "sku": "A"})
    i2 = s2.create("orders", {"qty": 7, "sku": "B"})
    check("create returns ids", i1 == 1 and i2 == 2)
    check("read", s2.read("orders", i1) == {"qty": 5, "sku": "A"})
    s2.update("orders", i1, {"qty": 50, "sku": "A"})
    check("update", s2.read("orders", i1)["qty"] == 50)
    s2.patch("orders", i2, {"qty": 70})
    check("patch", s2.read("orders", i2) == {"qty": 70, "sku": "B"})
    s2.delete("orders", i1)
    check("delete", [c for c, _ in s2.all("orders")] == [i2])
    check("read deleted -> None", s2.read("orders", i1) is None)

    # query predicate directly
    pred = compile_query("qty >= 40 OR sku ~ B")
    check("predicate eval", pred({"qty": 70, "sku": "B"}) and not pred({"qty": 1, "sku": "Z"}))

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
