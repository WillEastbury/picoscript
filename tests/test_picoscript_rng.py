from picoscript_rng import RandomStream
from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import PicoVM
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_streams_are_independent_and_checkpointable():
    a = RandomStream(123, 1)
    b = RandomStream(123, 2)
    first = [a.next_u64() for _ in range(5)]
    assert first != [b.next_u64() for _ in range(5)]
    state = a.checkpoint()
    expected = [a.next_u64() for _ in range(4)]
    restored = RandomStream.restore(state)
    assert [restored.next_u64() for _ in range(4)] == expected


def test_below_is_bounded_and_checkpoint_contains_position():
    r = RandomStream(9, 99)
    values = [r.below(7) for _ in range(100)]
    assert all(0 <= x < 7 for x in values)
    assert r.checkpoint()[2] >= 100


def test_seeded_maths_random_is_bounded_and_reproducible():
    words = lower_to_bytecode_safe(compile_c(
        "print(Maths.Random());"
        "print(Maths.RandomRange(10, 12));"
        "print(Maths.RandomRange(12, 10));"
    ))
    first = PicoVM(seed=123).run(words)
    second = PicoVM(seed=123).run(words)
    assert first.output == second.output
    output = b"".join(first.output)
    assert output[0:4] == (64889).to_bytes(4, "big")
    assert 10 <= int.from_bytes(output[4:8], "big") <= 12
    assert 10 <= int.from_bytes(output[8:12], "big") <= 12


def test_seeded_maths_random_matches_javascript_vm():
    words = lower_to_bytecode_safe(compile_c(
        "Io.WriteByte(Maths.Random());"
        "Io.WriteByte(Maths.RandomRange(10, 12));"
        "Io.WriteByte(Maths.RandomRange(12, 10));"
    ))
    python_vm = PicoVM(seed=123).run(words)
    payload = f"{len(words)}\n" + "\n".join(f"{word:08x}" for word in words) + "\n"
    result = subprocess.run(
        ["node", os.path.join(ROOT, "vm", "picovm_run.js")],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "PICOVM_SEED": "123"},
    )
    assert result.returncode == 0, result.stderr
    js_bytes = b""
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "OUT":
            js_bytes = bytes(int(value, 16) for value in fields[1:])
    assert b"".join(python_vm.output) == js_bytes
