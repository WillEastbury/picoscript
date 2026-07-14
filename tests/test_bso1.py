"""tests/test_bso1.py -- Python BSO1 (BareMetal.Binary) parser/serializer conformance.
Asserts byte-identical output to the JS reference VM (which is byte-identical to
BareMetalJsTools/src/BareMetal.Binary.js, incl. HMAC-SHA256 signing).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from picoscript_vm import _bso1_schema, _bso1_read, _bso1_write, _bso1_verify  # noqa: E402

KEY = bytes((i * 7 + 3) & 0xFF for i in range(32))

# schema Map (ordered dict of ("s", nameBytes) -> entry tuple), value = wireType code
def sput_i(m, name, v):
    kb = name.encode()
    m[("s", kb)] = ("s", 0, kb, "i", v, None)

def sput_s(m, name, vb):
    kb = name.encode()
    m[("s", kb)] = ("s", 0, kb, "s", 0, bytes(vb))

REF_BLOB = bytes([66, 83, 79, 49, 3, 0, 0, 0, 1, 0, 0, 0, 0, 16, 45, 194, 29, 145, 30, 196, 13, 152,
                  151, 50, 29, 73, 202, 76, 2, 40, 89, 113, 123, 60, 127, 178, 159, 180, 249, 88, 154,
                  113, 44, 237, 153, 1, 42, 0, 0, 0, 3, 0, 0, 0, 65, 66, 67, 5, 0, 0, 0, 0, 0, 0, 0, 1])


def _schema():
    m = {}
    sput_i(m, ":version", 1)
    sput_i(m, "Qty", 6)    # Int32
    sput_i(m, "Sku", 14)   # String
    sput_i(m, "Big", 8)    # Int64
    sput_i(m, "Flag", 1)   # Bool
    return m


def _data():
    m = {}
    sput_i(m, "Qty", 42)
    sput_s(m, "Sku", b"ABC")
    sput_s(m, "Big", [5, 0, 0, 0, 0, 0, 0, 0])
    sput_i(m, "Flag", 1)
    return m


def test_serialize_byte_identical():
    members, ver = _bso1_schema(_schema())
    blob = _bso1_write(_data(), members, ver, KEY)
    assert blob == REF_BLOB, blob


def test_verify():
    assert _bso1_verify(REF_BLOB, KEY) == 1
    assert _bso1_verify(REF_BLOB, bytes(32)) == 0     # wrong key
    assert _bso1_verify(REF_BLOB, None) == 0          # no key


def test_round_trip_read():
    members, _ver = _bso1_schema(_schema())
    got = {}

    def si(k, v):
        got[bytes(k)] = ("i", v)

    def ss(k, v):
        got[bytes(k)] = ("s", bytes(v))

    def ns(k):
        got[bytes(k)] = ("n", None)

    _bso1_read(REF_BLOB, members, si, ss, ns)
    assert got[b"Qty"] == ("i", 42)
    assert got[b"Sku"] == ("s", b"ABC")
    assert got[b"Big"] == ("s", bytes([5, 0, 0, 0, 0, 0, 0, 0]))
    assert got[b"Flag"] == ("i", 1)


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
