import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import PicoVM


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {value});" for i, value in enumerate(data))


def run_python(source):
    return b"".join(PicoVM().run(lower_to_bytecode_safe(compile_c(source))).output)


def run_javascript(words):
    payload = f"{len(words)}\n" + "\n".join(f"{word:08x}" for word in words) + "\n"
    result = subprocess.run(
        ["node", os.path.join(ROOT, "vm", "picovm_run.js")],
        input=payload, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "OUT":
            return bytes(int(value, 16) for value in fields[1:])
    return b""


def test_media_reference_parity_and_round_trip():
    source = (
        setbytes(100, [1, 3, 6, 10]) +
        "Media.SetShape(2, 2);"
        "int source = Span.Make(100, 4);"
        "int encoded = Media.GrayDeltaEncode(source);"
        "int decoded = Media.GrayDeltaDecode(encoded);"
        "Io.Write(decoded);"
    )
    words = lower_to_bytecode_safe(compile_c(source))
    expected = bytes([1, 3, 6, 10])
    assert run_python(source) == expected
    assert run_javascript(words) == expected


def test_media_invalid_shape_and_hevc_are_explicit():
    source = "Media.SetShape(0, 2); Io.WriteByte(Status.Last()); Media.HasHevc(); Io.WriteByte(Status.Last());"
    assert run_python(source) == bytes([2, 0])
