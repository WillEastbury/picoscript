#!/usr/bin/env python3
"""Public PicoWAL overlay/page APIs and LISWG storage-boundary tests."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import HostApi, PicoVM


def run(trace: str) -> bytes:
    source = (ROOT / "host/picowal/picowal.pico").read_text(encoding="utf-8")
    source += "\n" + (ROOT / "host/liswg/liswg.pico").read_text(encoding="utf-8")
    words = lower_to_bytecode_safe(compile_c(source + "\n" + trace))
    vm = PicoVM(host=HostApi())
    vm.load(words); vm.run()
    return b"".join(vm.output)


def ints(data: bytes):
    assert len(data) % 4 == 0
    return [int.from_bytes(data[i:i + 4], "big", signed=True)
            for i in range(0, len(data), 4)]


def test_positional_fulltext_modes():
    output = run(r'''
pw_fulltext_upsert(20, 1, 0, "the river bank has water");
pw_fulltext_upsert(20, 2, 0, "the bank approved finance");
pw_fulltext_upsert(20, 3, 0, "bank beside the river");
print(pw_fulltext_find(20, 0, "bank", 0, 0));
print(pw_fulltext_find(20, 0, "river bank", 1, 0));
print(pw_fulltext_find(20, 0, "river bank", 2, 0));
print(pw_fulltext_result(0));
print(pw_fulltext_find(20, 0, "bank river", 3, 3));
''')
    assert ints(output) == [3, 2, 1, 1, 2]


def test_signed_graph_and_liswg_wavefront():
    output = run(r'''
pw_graph_add_edge(30, 7, 10, 20, 8192);
pw_graph_add_edge(30, 7, 10, 30, -4096);
print(pw_graph_out(30, 7, 10));
print(pw_graph_result_node(0));
print(pw_graph_result_weight(0));
print(pw_graph_result_node(1));
print(pw_graph_result_weight(1));
print(liswg_wavefront_hop(30, 7, 10, 8));
print(liswg_wavefront_node(0)); print(liswg_wavefront_amplitude(0));
print(liswg_wavefront_node(1)); print(liswg_wavefront_amplitude(1));
pw_graph_set_weight(30, 7, 10, 30, -2048);
print(pw_graph_weight(30, 7, 10, 30));
print(pw_graph_in(30, 7, 20));
print(pw_graph_result_node(0));
''')
    assert ints(output) == [2, 20, 8192, 30, -4096, 2,
                            20, 8192, 30, -4096, -2048, 1, 10]


def test_row_and_column_page_sealing():
    output = run(r'''
pw_page_begin(1, 0);
pw_page_add(9, "aaaaaaaaaaaaaaaa");
pw_page_add(2, "river");
print(pw_page_seal());
int row = pw_page_data();
print(pw_page_verify(row));
print(Span.Len(row));
pw_page_begin(2, 1);
pw_page_add(9, "aaaaaaaaaaaaaaaa");
pw_page_add(2, "river");
print(pw_page_seal());
int col = pw_page_data();
print(pw_page_verify(col));
print(Span.Len(col));
''')
    values = ints(output)
    assert values[0:2] == [1, 1]
    assert 28 < values[2] <= 16384
    assert values[3:5] == [1, 1]
    assert 28 < values[5] <= 16384
