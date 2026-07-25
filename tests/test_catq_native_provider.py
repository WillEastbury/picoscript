#!/usr/bin/env python3
"""End-to-end C-PicoScript CAT-Q provider test with no ML dependencies."""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_safetensor(path, name, rows, cols, values, dtype="F32"):
    if dtype == "BF16":
        words = []
        for value in values:
            bits = struct.unpack("<I", struct.pack("<f", value))[0]
            words.append(bits >> 16)
        data = struct.pack("<" + "H" * len(words), *words)
    else:
        data = struct.pack("<" + "f" * len(values), *values)
    header = json.dumps(
        {
            name: {
                "dtype": dtype,
                "shape": [rows, cols],
                "data_offsets": [0, len(data)],
            }
        },
        separators=(",", ":"),
    ).encode()
    header += b" " * ((-len(header)) % 8)
    with open(path, "wb") as stream:
        stream.write(struct.pack("<Q", len(header)))
        stream.write(header)
        stream.write(data)


def read_safetensor(path):
    with open(path, "rb") as stream:
        header_len = struct.unpack("<Q", stream.read(8))[0]
        header = json.loads(stream.read(header_len).decode().strip())
        data = stream.read()
    return header, data


def write_raw_safetensors(path, tensors):
    header = {}
    payload = bytearray()
    for name, dtype, shape, data in tensors:
        start = len(payload)
        payload.extend(data)
        header[name] = {
            "dtype": dtype,
            "shape": shape,
            "data_offsets": [start, len(payload)],
        }
    encoded = json.dumps(header, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 8)
    with open(path, "wb") as stream:
        stream.write(struct.pack("<Q", len(encoded)))
        stream.write(encoded)
        stream.write(payload)


def pico_path(path):
    return str(path).replace("\\", "/")


def test_native_catq_pipeline(tmp_path):
    model = tmp_path / "model.safetensors"
    calibration = tmp_path / "calibration.safetensors"
    output = tmp_path / "model.ternary.safetensors"
    source = tmp_path / "quantize.pc"
    executable = tmp_path / "quantize.exe"

    write_safetensor(
        model,
        "weight",
        2,
        4,
        [-0.9, -0.2, 0.1, 0.8, 0.7, -0.6, 0.3, -0.1],
        dtype="BF16",
    )
    write_safetensor(
        calibration,
        "calibration",
        6,
        4,
        [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
            1.0, -1.0, 0.5, 0.25,
            -0.5, 0.75, -1.0, 1.0,
        ],
    )

    source.write_text(
        f'''
int model = Shard.Load("{pico_path(model)}", "mmap");
if (model == 0) {{ raise 2001; }}
int calShard = Shard.Load("{pico_path(calibration)}", "mmap");
if (calShard == 0) {{ raise 2002; }}
int weights = Tensor.Map(model, "tensor=weight");
if (weights == 0) {{ raise 2003; }}
int samples = Tensor.Map(calShard, "tensor=calibration");
if (samples == 0) {{ raise 2004; }}
int context = CatQ.Calibrate(samples, "group=4;epochs=20;batch=2;gamma=0.8;s0=30;lr=0.02");
int optimized = CatQ.Optimize(context, weights);
int ternary = CatQ.Ternarize(context, optimized);
int packed = CatQ.Pack(context, ternary);
int saved = Shard.Save(packed, "{pico_path(output)}");
if (saved == 0) {{ raise 2005; }}
return saved;
''',
        encoding="utf-8",
    )

    build = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "picoscript_build.py"),
            "native",
            str(source),
            "--provider",
            "catq",
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr + build.stdout

    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr + run.stdout
    assert output.is_file()

    header, data = read_safetensor(output)
    assert header["__metadata__"]["format"] == "picoscript-catq-ternary-v1"
    assert header["__metadata__"]["shape"] == "2,4"
    assert header["__metadata__"]["group_size"] == "4"
    assert header["codes"]["shape"] == [2]
    assert header["scales"]["shape"] == [2]

    code_end = header["codes"]["data_offsets"][1]
    packed_codes = data[:code_end]
    scales = struct.unpack("<2f", data[code_end:code_end + 8])
    assert len(packed_codes) == 2
    assert all(math.isfinite(scale) and scale > 0 for scale in scales)
    assert all(((byte >> shift) & 3) in (0, 1, 2)
               for byte in packed_codes for shift in (0, 2, 4, 6))


def test_native_optimizer_reconstructs_calibration_outputs(tmp_path):
    harness = tmp_path / "loss.c"
    executable = tmp_path / "loss.exe"
    harness.write_text(
        r'''
#include <stdio.h>
#include <string.h>
#include "picovm.h"
#include "picovm_catq.h"
#include "pico_hooks.h"

static int make_span(pv_ctx *ctx, const char *text, int offset) {
    int handle = ctx->span_count++;
    int length = (int)strlen(text);
    memcpy(ctx->mem + offset, text, (size_t)length);
    ctx->span_ptr[handle] = (uint32_t)offset;
    ctx->span_len[handle] = length;
    return handle;
}

int main(void) {
    float weights[8] = {-0.9f,-0.2f,0.1f,0.8f,0.7f,-0.6f,0.3f,-0.1f};
    float calibration[24] = {
        1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1,
        1,-1,0.5f,0.25f, -0.5f,0.75f,-1,1
    };
    unsigned char memory[8192] = {0};
    pv_ctx ctx;
    int weight, samples, options, catq, optimized;
    pv_init(&ctx);
    ctx.mem = memory;
    ctx.mem_size = (long)sizeof(memory);
    pv_catq_install();
    weight = pv_catq_register_f32(weights, 2, 4);
    samples = pv_catq_register_f32(calibration, 6, 4);
    options = make_span(&ctx, "group=4;epochs=20;batch=2;gamma=0.8;s0=30;lr=0.02", 100);
    catq = (int)pv_host2(&ctx, PV_HOOK_CATQ_CALIBRATE, samples, options);
    optimized = (int)pv_host2(&ctx, PV_HOOK_CATQ_OPTIMIZE, catq, weight);
    printf("%.9f\n", pv_catq_final_loss(optimized));
    pv_catq_cleanup();
    return 0;
}
''',
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
            f"-I{os.path.join(ROOT, 'vm')}",
            str(harness),
            os.path.join(ROOT, "vm", "picovm.c"),
            os.path.join(ROOT, "vm", "picovm_catq.c"),
            "-lm", "-o", str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    loss = float(run.stdout.strip())
    assert math.isfinite(loss)
    assert loss < 0.01


def test_native_mxfp4_dequantization(tmp_path):
    source_shard = tmp_path / "mxfp4.safetensors"
    output_shard = tmp_path / "dequantized.safetensors"
    source = tmp_path / "dequantize.pc"
    executable = tmp_path / "dequantize.exe"
    blocks_name = "model.layers.0.mlp.experts.gate_up_proj_blocks"
    scales_name = "model.layers.0.mlp.experts.gate_up_proj_scales"
    packed = bytes([0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0xDC, 0xFE] * 2)
    write_raw_safetensors(
        source_shard,
        [
            (blocks_name, "U8", [1, 1, 16], packed),
            (scales_name, "U8", [1, 1], bytes([127])),
        ],
    )
    source.write_text(
        f'''
int shard = Shard.Load("{pico_path(source_shard)}", "mmap");
int tensor = Tensor.Map(shard, "mxfp4_blocks={blocks_name};mxfp4_scales={scales_name}");
if (tensor == 0) {{ raise 4001; }}
int saved = Shard.Save(tensor, "{pico_path(output_shard)}");
if (saved == 0) {{ raise 4002; }}
return saved;
''',
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            sys.executable, os.path.join(ROOT, "picoscript_build.py"),
            "native", str(source), "--provider", "catq", "-o", str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr + build.stdout
    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr + run.stdout
    header, data = read_safetensor(output_shard)
    assert header["tensor"]["shape"] == [1, 32]
    values = struct.unpack("<32f", data)
    expected = (
        0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
        -0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0,
    ) * 2
    assert values == expected
