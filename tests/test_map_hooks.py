"""tests/test_map_hooks.py -- conformance for the Map.* primitive on the Python
reference VM (picoscript_vm.py), driven through the BASIC frontend. Mirrors the
JS suite (tests/test_map_hooks.js) so the two reference VMs agree.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from picoscript_basic import compile_basic          # noqa: E402
from picoscript_il import lower_to_bytecode_safe     # noqa: E402
from picoscript_vm import PicoVM                      # noqa: E402


def run_basic(src):
    words = lower_to_bytecode_safe(compile_basic(src))
    vm = PicoVM().run(words)
    out = []
    for c in vm.output:
        v = int.from_bytes(c, "big")
        out.append(v - 0x100000000 if v & 0x80000000 else v)
    return out


def test_int_keys_values_count():
    assert run_basic(
        "LET h = Map.New()\n"
        "Map.PutII(1, 100)\n"
        "Map.PutII(2, 200)\n"
        "Map.PutII(1, 111)\n"
        "PRINT Map.GetII(1)\n"
        "PRINT Map.GetII(2)\n"
        "PRINT Map.Count()\n"
    ) == [111, 200, 2]


def test_has_del():
    assert run_basic(
        "LET h = Map.New()\n"
        "Map.PutII(5, 50)\n"
        "PRINT Map.HasI(5)\n"
        "PRINT Map.HasI(9)\n"
        "Map.DelI(5)\n"
        "PRINT Map.HasI(5)\n"
        "PRINT Map.Count()\n"
    ) == [1, 0, 0, 0]


def test_string_keys():
    assert run_basic(
        "LET h = Map.New()\n"
        'Map.PutSI("qty", 42)\n'
        'Map.PutSI("age", 7)\n'
        'PRINT Map.GetSI("qty")\n'
        'PRINT Map.GetSI("age")\n'
        'PRINT Map.HasS("qty")\n'
        'PRINT Map.HasS("nope")\n'
    ) == [42, 7, 1, 0]


def test_enumeration_order():
    assert run_basic(
        "LET h = Map.New()\n"
        'Map.PutSI("a", 10)\n'
        'Map.PutSI("b", 20)\n'
        'Map.PutSI("c", 30)\n'
        "PRINT Map.ValAt(0)\n"
        "PRINT Map.ValAt(1)\n"
        "PRINT Map.ValAt(2)\n"
        "PRINT Map.Count()\n"
    ) == [10, 20, 30, 3]


def test_null_vs_absent():
    assert run_basic(
        "LET h = Map.New()\n"
        "Map.PutNullI(3)\n"
        "PRINT Map.HasI(3)\n"
        "PRINT Map.IsNullI(3)\n"
        "PRINT Map.IsNullI(4)\n"
    ) == [1, 1, 0]


def test_two_maps_use():
    assert run_basic(
        "LET a = Map.New()\n"
        "Map.PutII(1, 10)\n"
        "LET b = Map.New()\n"
        "Map.PutII(1, 99)\n"
        "Map.Use(a)\n"
        "PRINT Map.GetII(1)\n"
        "Map.Use(b)\n"
        "PRINT Map.GetII(1)\n"
    ) == [10, 99]


def test_hash_determinism():
    assert run_basic(
        'LET x = Map.Hash("Content-Type")\n'
        'LET y = Map.Hash("Content-Type")\n'
        "PRINT x - y\n"
    ) == [0]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS " + name)
            except AssertionError as e:
                fails += 1
                print("FAIL " + name + " -> " + str(e))
    print("\n" + ("ALL PASSED" if fails == 0 else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
