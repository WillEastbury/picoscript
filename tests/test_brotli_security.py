"""CVE-2026-111497 regression: unsupported block types fail closed in every port."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import picobrotli  # noqa: E402

VALID = bytes.fromhex("e21700006498d8586816d1ad01")
PLAIN = b"abc" * 64


def _invalid_inputs(field):
    # RFC 7932: WBITS=16, ISLAST=1, ISLASTEMPTY=0, MNIBBLES=4,
    # then MLEN (16 bits), followed by NBLTYPESL/I/D at bits 21-23.
    malformed = bytearray(VALID)
    malformed[2] |= 1 << (5 + field)
    for length in (0, 1, 2, 3, 8, 9, len(VALID), len(VALID) + 1,
                   len(VALID) + 8, len(VALID) + 9,
                   len(VALID) + 64, len(VALID) + 4096):
        yield (bytes(malformed) + b"\x5a" * 4096)[:length]


@pytest.mark.parametrize("field", range(3), ids=["literal", "command", "distance"])
def test_python_rejects_block_types_with_growing_input(field):
    assert picobrotli.decode(VALID) == PLAIN
    for malformed in _invalid_inputs(field):
        error = "unsupported meta-block" if len(malformed) >= 3 else "bad WBITS|truncated"
        with pytest.raises(ValueError, match=error):
            picobrotli.decode(malformed)
    assert picobrotli.decode(VALID) == PLAIN


def test_javascript_rejects_block_types_with_growing_input():
    script = """
const assert = require("node:assert/strict");
const codec = require(process.argv[1]);
const cases = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const valid = Buffer.from(cases.valid, "hex");
const plain = Buffer.from("abc".repeat(64));
assert.deepEqual(Buffer.from(codec.decode(valid)), plain);
for (const hex of cases.invalid) {
    const input = Buffer.from(hex, "hex");
    assert.throws(() => codec.decode(input), input.length >= 3
                  ? /unsupported meta-block/ : /bad WBITS|truncated/);
}
assert.deepEqual(Buffer.from(codec.decode(valid)), plain);
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "vm" / "picobrotli.js")],
        input=json.dumps({
            "valid": VALID.hex(),
            "invalid": [data.hex() for field in range(3)
                        for data in _invalid_inputs(field)],
        }),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_rejects_block_types_with_growing_input(tmp_path):
    compiler = shutil.which(os.environ.get("CC", "clang"))
    assert compiler, "A native C compiler is required for the Brotli security regression"
    executable = tmp_path / ("brotli_security.exe" if os.name == "nt" else "brotli_security")
    build = subprocess.run(
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1",
         "-I", str(ROOT / "vm"),
         str(ROOT / "tests" / "test_brotli_security.c"),
         str(ROOT / "vm" / "picobrotli.c"), "-o", str(executable)],
        capture_output=True, text=True, timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
