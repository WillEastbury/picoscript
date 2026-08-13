"""Systems-ISA semantic parity across the non-C source dialects."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_basic import compile_basic  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_systems import SystemsPicoVM, is_super_word  # noqa: E402
from picoscript_vm import HostApi  # noqa: E402


DIALECTS = (
    (
        "basic",
        compile_basic,
        """\
DIM one = U64.FromR(1)
DIM wide = U64.Shl(one, 40)
DIM answer = U64.Add(U64.FromR(40), U64.FromR(2))
PRINT U64.Gt(wide, U64.FromR(1))
PRINT U64.ToR(answer)
""",
        "PRINT U64.ToR(Block.Size())\n",
    ),
    (
        "python",
        compile_python,
        """\
one = U64.FromR(1)
wide = U64.Shl(one, 40)
answer = U64.Add(U64.FromR(40), U64.FromR(2))
print(U64.Gt(wide, U64.FromR(1)))
print(U64.ToR(answer))
""",
        "print(U64.ToR(Block.Size()))\n",
    ),
    (
        "english",
        compile_english,
        """\
Set one to U64.FromR(1).
Set wide to U64.Shl(one, 40).
Set answer to U64.Add(U64.FromR(40), U64.FromR(2)).
Print U64.Gt(wide, U64.FromR(1)).
Print U64.ToR(answer).
""",
        "Print U64.ToR(Block.Size()).\n",
    ),
)


def _run(compiler, source, host=None):
    words = lower_to_bytecode_safe(compiler(source))
    vm = SystemsPicoVM(host=host).run(words)
    output = [int.from_bytes(item, "big") for item in vm.output]
    return words, output


@pytest.mark.parametrize("_name,compiler,source,_block_source", DIALECTS)
def test_explicit_u64_intrinsics_have_the_same_semantics(
        _name, compiler, source, _block_source):
    words, output = _run(compiler, source)
    assert output == [1, 42]
    assert any(is_super_word(word) for word in words)


@pytest.mark.parametrize("_name,compiler,_source,block_source", DIALECTS)
def test_block_size_is_one_u64_value_in_every_dialect(
        _name, compiler, _source, block_source):
    host = HostApi()
    host.block_data = bytearray(96 * 1024)
    words, output = _run(compiler, block_source, host)
    assert output == [96 * 1024]
    assert any(is_super_word(word) for word in words)


TYPED_SOURCES = (
    (compile_basic, "DIM offset AS U64 = Block.Size()\nDIM following AS U64 = offset + 4096\nPRINT following\n"),
    (compile_python, "offset: u64 = Block.Size()\nnext: u64 = offset + 4096\nprint(next)\n"),
    (compile_english, "Set offset as unsigned 64-bit to Block.Size().\n"
                      "Set next as u64 to offset plus 4096.\nPrint next.\n"),
)


@pytest.mark.parametrize("compiler,source", TYPED_SOURCES)
def test_native_typed_declarations_promote_arithmetic(compiler, source):
    host = HostApi(); host.block_data = bytearray(96 * 1024)
    words, output = _run(compiler, source, host)
    assert output == [100 * 1024]
    assert any(is_super_word(word) for word in words)


def test_first_class_wal_secondary_fts_and_weighted_graph_intrinsics():
    source = """\
DIM wal AS WAL = Wal.Open()
DIM cards AS WAL = Wal.Pack(wal, 5)
PRINT Wal.Put(cards, 42, "red battery pack")
DIM loaded = Wal.Get(cards, 42)
PRINT Span.Len(loaded)

DIM field AS INDEX = Index.Open(cards, 1)
PRINT Index.Upsert(field, 42, 9001)
PRINT Index.Exact(field, 9001)
PRINT Index.Result(field, 0)
PRINT Index.Reverse(field, 9001)

DIM text AS INDEX = FTS.Open(cards, 0)
PRINT FTS.Upsert(text, 42, "red battery pack")
PRINT FTS.Search(text, "red battery", 65536)
PRINT FTS.Result(text, 0)

DIM edges AS WAL = Wal.Pack(wal, 7)
DIM graph AS GRAPH = Graph.Open(edges, 3)
PRINT Graph.Add(graph, 1, 2, 5)
PRINT Graph.Add(graph, 2, 3, 7)
PRINT Graph.Add(graph, 1, 3, 20)
PRINT Graph.Path(graph, 1, 3)
PRINT Graph.ResultNode(graph, 2)
PRINT Graph.ResultWeight(graph, 2)
"""
    words, output = _run(compile_basic, source)
    assert output == [0, 16, 1, 1, 42, 1, 1, 1, 42,
                      1, 1, 1, 3, 3, 12]
    assert any(is_super_word(word) for word in words)
