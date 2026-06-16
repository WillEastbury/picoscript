#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Language primitives for Picowal PR #78 host features.

PR #78 added disk-only/readiness semantics, bounded relation query helpers and a
host search layer. PicoScript exposes deterministic VM facades for those surfaces
so code can be authored once and later bound to the real Picowal host backend.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")

SRC = r'''
print(Storage.Ready());
print(Storage.IsUserPack(1));
print(Storage.IsUserPack(2));

int pack = "todos";
int lookup = "name|status|!=|completed|id|123";
Io.Write(Query.BuildLookupFilter(pack, lookup));
Io.WriteByte(124);

int rel = "investigator|42|capability";
Io.Write(Query.BuildManyToManyMap("investigator_capability", rel));
Io.WriteByte(124);

Storage.UsePack(2);
Search.Clear();
Search.Configure("retail-products", 7);
print(Search.Compatible("retail-products", 7));
Search.UpsertText(1, "red apple fruit");
Search.UpsertText(2, "blue berry fruit");
Search.SetFacet(1, "category|fruit");
Search.SetFacet(2, "category|fruit");
Search.SetNumber(1, "price|89");
Search.SetNumber(2, "price|129");
int hits = Search.QueryText("red fruit");
print(hits);
print(Search.Result(0));
print(Search.Score(0));
print(Search.Plan(0));
print(Search.Facets("category"));
int fv = Search.FacetValue(0); Io.Write(fv); Io.WriteByte(124);
print(Search.FacetCount(0));
print(Search.Range("price|80|100"));
print(Search.Result(0));
Search.Save("seg");
Search.Clear();
print(Search.QueryText("red"));
Search.Load("seg");
print(Search.QueryText("red"));
Search.JournalDelete(1);
print(Search.QueryText("red"));
'''

EXPECTED_PREFIX = (
    (1).to_bytes(4, "big") +
    (0).to_bytes(4, "big") +
    (1).to_bytes(4, "big") +
    b"S:name\nF:todos\nW:status|!=|completed\nW:id|!=|123|"
    b"S:capability\nF:investigator_capability\nW:investigator|==|42|"
)


def _run_py(words):
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _run_js(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def test_picowal_pr78_facades():
    words = lower_to_bytecode_safe(compile_c(SRC))
    py = _run_py(words)
    js = _run_js(words)
    assert py == js
    assert py.startswith(EXPECTED_PREFIX), py
    tail = py[len(EXPECTED_PREFIX):]
    # After the text query metadata, the stream contains: compatible, hit count,
    # first result/score/plan, facet count, facet value text, facet count, range
    # count/result, save/clear/load query counts and journal-delete query count.
    vals = [int.from_bytes(tail[i:i+4], "big") for i in range(0, len(tail), 4)]
    assert tail[:20] == b"\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00\x02"
    rest = tail[20:]
    assert rest.startswith((1).to_bytes(4, "big") + b"fruit|" + (2).to_bytes(4, "big"))
    ints_tail = []
    p = 4 + len(b"fruit|") + 4
    while p + 4 <= len(rest):
        ints_tail.append(int.from_bytes(rest[p:p+4], "big"))
        p += 4
    assert ints_tail == [1, 1, 0, 1, 0], ints_tail


def main():
    test_picowal_pr78_facades()
    print("PASS Picowal PR78 facades: Ready/IsUserPack, Query helpers, Search text query (Python VM == JS VM)")


if __name__ == "__main__":
    main()
