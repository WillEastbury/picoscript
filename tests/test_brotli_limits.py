"""Decoded-output budgets apply before expansion, including VM host calls."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import picobrotli  # noqa: E402
from picoscript_vm import PicoFault, PicoVM  # noqa: E402

VALID = bytes.fromhex("e21700006498d8586816d1ad01")
PLAIN = b"abc" * 64


def _bits(*fields):
    value = width = 0
    for field, count in fields:
        value |= field << width
        width += count
    return value.to_bytes((width + 7) // 8, "little")


MULTIBLOCK = (_bits((0, 1), (0, 1), (0, 2), (2, 16), (1, 1)) + b"abc"
              + _bits((0, 1), (0, 2), (2, 16), (1, 1)) + b"abc" + b"\x03")
OVER_DEFAULT = (_bits((0, 1), (0, 1), (0, 2), (0, 16), (1, 1)) + b"a"
                + _bits((1, 1), (0, 1), (2, 2), (0xffffff, 24)))


@pytest.mark.parametrize("decode", [picobrotli.decode, picobrotli.decompress])
@pytest.mark.parametrize("encoded,plain", [(VALID, PLAIN), (MULTIBLOCK, b"abcabc")])
def test_cumulative_limit_accepts_exact_size_and_rejects_one_byte_less(decode, encoded, plain):
    assert decode(encoded, max_output_size=len(plain)) == plain
    with pytest.raises(ValueError, match="decoded output exceeds limit"):
        decode(encoded, max_output_size=len(plain) - 1)
    assert decode(b"\x06", max_output_size=0) == b""


def test_default_limit_checks_declared_output_before_reading_payload():
    with pytest.raises(ValueError, match="decoded output exceeds limit"):
        picobrotli.decode(OVER_DEFAULT)
    # An explicit larger budget reaches format validation, not the default cap.
    with pytest.raises(ValueError, match="unsupported meta-block"):
        picobrotli.decode(OVER_DEFAULT, max_output_size=16 * 1024 * 1024 + 1)


def test_match_cannot_temporarily_expand_beyond_declared_output(monkeypatch):
    malformed = bytearray(VALID)
    header = int.from_bytes(malformed[:3], "little")
    header = (header & ~(0xffff << 5)) | (3 << 5)  # MLEN=4 instead of 192.
    malformed[:3] = header.to_bytes(3, "little")

    class GuardedOutput(bytearray):
        def append(self, value):
            assert len(self) < 4, "decoder expanded a match before checking its bounds"
            super().append(value)

    monkeypatch.setattr(picobrotli, "bytearray", GuardedOutput, raising=False)
    with pytest.raises(ValueError, match="copy overflow"):
        picobrotli.decode(malformed, max_output_size=4)


@pytest.mark.parametrize("remaining", [0, len(PLAIN) - 1, len(PLAIN)])
def test_vm_uses_remaining_arena_without_resizing_memory(remaining):
    vm = PicoVM(arena_bytes=0x8000 + len(VALID) + remaining)
    vm.regs[1] = vm.host._new_span_bytes(vm, VALID)
    before = bytes(vm.mem)
    top = vm.arena_top
    vm.host._compresslib(vm, "BrotliDecompress", 0, 1, 0)
    result = vm.host._span_raw(vm, vm.regs[0])
    assert len(vm.mem) == len(before)
    if remaining == len(PLAIN):
        assert vm.host.host_status == 0
        assert result == PLAIN
        assert vm.arena_top == top + len(PLAIN)
    else:
        assert vm.host.host_status == 2
        assert result == b""
        assert vm.arena_top == top
        assert bytes(vm.mem) == before


def test_vm_no_alloc_fails_before_decoding(monkeypatch):
    vm = PicoVM()
    vm.regs[1] = vm.host._new_span_bytes(vm, VALID)
    vm.host.no_alloc = True

    def unexpected_decode(*args, **kwargs):
        pytest.fail("decoder ran in no-alloc mode")

    monkeypatch.setattr(picobrotli, "decode", unexpected_decode)
    with pytest.raises(PicoFault) as fault:
        vm.host._compresslib(vm, "BrotliDecompress", 0, 1, 0)
    assert fault.value.code == 9


def test_span_allocation_rechecks_capacity_without_partial_writes():
    vm = PicoVM(arena_bytes=0x8000 + 2)
    before = bytes(vm.mem)
    with pytest.raises(PicoFault) as fault:
        vm.host._new_span_bytes(vm, b"abc")
    assert fault.value.code == 9
    assert bytes(vm.mem) == before
    assert vm.arena_top == 0x8000
    assert vm.spans == [None]


def test_javascript_decoder_and_vm_budgets():
    script = """
const assert = require("node:assert/strict");
const path = require("node:path");
const root = process.argv[1];
const codec = require(path.join(root, "vm", "picobrotli.js"));
const VM = require(path.join(root, "vm", "picovm.js"));
const data = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const valid = Buffer.from(data.valid, "hex"), plain = Buffer.from("abc".repeat(64));
for (const decode of [codec.decode, codec.decompress]) {
    for (const [hex, expected] of [[data.valid, plain], [data.multi, Buffer.from("abcabc")]]) {
        const input = Buffer.from(hex, "hex");
        assert.deepEqual(Buffer.from(decode(input, expected.length)), expected);
        assert.throws(() => decode(input, expected.length - 1), /output exceeds limit/);
    }
    assert.equal(decode(Buffer.from([6]), 0).length, 0);
    assert.throws(() => decode(Buffer.from(data.overDefault, "hex")), /output exceeds limit/);
    assert.throws(() => decode(Buffer.from(data.overDefault, "hex"), 16777217),
                  /unsupported meta-block/);
}
for (const remaining of [0, plain.length - 1, plain.length]) {
    const vm = new VM();
    vm.mem = new Uint8Array(0x8000 + valid.length + remaining);
    vm.regs[1] = vm._newSpanBytes(valid);
    const before = Buffer.from(vm.mem), top = vm.arenaTop;
    vm._compresslib("BrotliDecompress", 0, 1, 0);
    assert.equal(vm.mem.length, before.length);
    if (remaining === plain.length) {
        assert.equal(vm.host_status, 0);
        assert.deepEqual(Buffer.from(vm._spanBytes(vm.regs[0])), plain);
    } else {
        assert.equal(vm.host_status, 2);
        assert.equal(vm._spanBytes(vm.regs[0]).length, 0);
        assert.equal(vm.arenaTop, top);
        assert.deepEqual(Buffer.from(vm.mem), before);
    }
}
const vm = new VM();
vm.regs[1] = vm._newSpanBytes(valid);
vm.noAlloc = true;
const original = codec.decode;
codec.decode = () => assert.fail("decoder ran in no-alloc mode");
assert.throws(() => vm._compresslib("BrotliDecompress", 0, 1, 0), e => e.fault === 9);
codec.decode = original;
vm.noAlloc = false;
vm.arenaTop = vm.mem.length - 2;
const before = Buffer.from(vm.mem), spans = vm.spans.length;
assert.throws(() => vm._newSpanBytes([1, 2, 3]), e => e.fault === 9);
assert.deepEqual(Buffer.from(vm.mem), before);
assert.equal(vm.spans.length, spans);
"""
    run = subprocess.run(
        ["node", "-e", script, str(ROOT)],
        input=json.dumps({"valid": VALID.hex(), "multi": MULTIBLOCK.hex(),
                          "overDefault": OVER_DEFAULT.hex()}),
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("page", ["index.html", "playground.html"])
def test_generated_pages_enforce_same_brotli_budgets(page):
    script = r"""
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({Uint8Array, Int32Array});
for (const name of ["picobrotli", "picovm"]) {
    const pattern = new RegExp("// " + name + "\\.js --[\\s\\S]*?^\\}\\);", "m");
    const source = html.match(pattern);
    assert(source, "missing embedded " + name);
    vm.runInContext(source[0], context);
}
const valid = Buffer.from("e21700006498d8586816d1ad01", "hex");
assert.equal(context.PicoBrotli.decode(valid, 192).length, 192);
assert.throws(() => context.PicoBrotli.decode(valid, 191), /output exceeds limit/);
const host = new context.PicoVM();
host.mem = new Uint8Array(0x8000 + valid.length + 191);
host.regs[1] = host._newSpanBytes(valid);
const before = Buffer.from(host.mem), top = host.arenaTop;
host._compresslib("BrotliDecompress", 0, 1, 0);
assert.equal(host.host_status, 2);
assert.equal(host._spanBytes(host.regs[0]).length, 0);
assert.equal(host.arenaTop, top);
assert.deepEqual(Buffer.from(host.mem), before);
"""
    run = subprocess.run(
        ["node", "-e", script, str(ROOT / "docs" / page)],
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
