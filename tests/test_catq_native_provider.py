#!/usr/bin/env python3
"""End-to-end C-PicoScript CAT-Q provider test with no ML dependencies."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import sys

import pytest

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


def has_cuda_toolchain():
    nvcc = shutil.which("nvcc")
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvcc or not nvidia_smi:
        return False
    probe = subprocess.run(
        [nvidia_smi, "-L"], capture_output=True, text=True,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not has_cuda_toolchain(), reason="CUDA toolchain and GPU required")
def test_cuda_chunking_matches_unchunked_optimizer(tmp_path):
    model = tmp_path / "model.safetensors"
    calibration = tmp_path / "calibration.safetensors"
    unchunked = tmp_path / "unchunked.safetensors"
    chunked = tmp_path / "chunked.safetensors"
    source = tmp_path / "chunking.pc"
    executable = tmp_path / "chunking.exe"
    rows, cols = 64, 32
    values = [math.sin(index * 0.17) * 0.5 for index in range(rows * cols)]
    samples = [math.cos(index * 0.11) * 0.5 for index in range(4 * cols)]

    write_safetensor(model, "weight", rows, cols, values, dtype="BF16")
    write_safetensor(calibration, "calibration", 4, cols, samples)
    source.write_text(
        f'''
int model = Shard.Load("{pico_path(model)}", "mmap");
int calShard = Shard.Load("{pico_path(calibration)}", "mmap");
int weights = Tensor.Map(model, "tensor=weight");
int samples = Tensor.Map(calShard, "tensor=calibration");
int full = CatQ.Calibrate(samples, "group=32;epochs=2;batch=1;device=cuda;cuda_required=1;cuda_chunk_weights=1048576");
int fullOptimized = CatQ.Optimize(full, weights);
int fullTernary = CatQ.Ternarize(full, fullOptimized);
int fullPacked = CatQ.Pack(full, fullTernary);
if (Shard.Save(fullPacked, "{pico_path(unchunked)}") == 0) {{ raise 5001; }}
int split = CatQ.Calibrate(samples, "group=32;epochs=2;batch=1;device=cuda;cuda_required=1;cuda_chunk_weights=512");
int splitOptimized = CatQ.Optimize(split, weights);
int splitTernary = CatQ.Ternarize(split, splitOptimized);
int splitPacked = CatQ.Pack(split, splitTernary);
if (Shard.Save(splitPacked, "{pico_path(chunked)}") == 0) {{ raise 5002; }}
return 1;
''',
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            sys.executable, os.path.join(ROOT, "picoscript_build.py"),
            "native", str(source), "--provider", "catq-cuda",
            "--profile", "host", "-o", str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr + build.stdout
    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr + run.stdout
    assert "ERROR 0 0 0" in run.stdout
    assert unchunked.read_bytes() == chunked.read_bytes()


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

    inference_source = tmp_path / "infer.pc"
    inference_executable = tmp_path / "infer.exe"
    inference_output = tmp_path / "projection.safetensors"
    inference_source.write_text(
        f'''
int packedShard = Shard.Load("{pico_path(output)}", "mmap");
int packed = Tensor.Map(packedShard, "catq_packed=1");
if (packed == 0) {{ raise 2101; }}
int calShard = Shard.Load("{pico_path(calibration)}", "mmap");
int samples = Tensor.Map(calShard, "tensor=calibration");
int input = Tensor.View(samples, "row_start=0;row_count=1");
int projection = BitLinear.MatVecCatQ(packed, input);
if (projection == 0) {{ raise 2102; }}
int saved = Shard.Save(projection, "{pico_path(inference_output)}");
if (saved == 0) {{ raise 2103; }}
return saved;
''',
        encoding="utf-8",
    )
    infer_build = subprocess.run(
        [
            sys.executable, os.path.join(ROOT, "picoscript_build.py"),
            "native", str(inference_source), "--provider", "catq",
            "-o", str(inference_executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert infer_build.returncode == 0, infer_build.stderr + infer_build.stdout
    infer_run = subprocess.run([str(inference_executable)], capture_output=True, text=True)
    assert infer_run.returncode == 0, infer_run.stderr + infer_run.stdout
    projection_header, projection_data = read_safetensor(inference_output)
    assert projection_header["tensor"]["shape"] == [2, 1]
    projection = struct.unpack("<2f", projection_data)
    assert all(math.isfinite(value) for value in projection)


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


def test_native_catq_scaled_ternary_matvec(tmp_path):
    harness = tmp_path / "catq_matvec.c"
    executable = tmp_path / "catq_matvec.exe"
    harness.write_text(
        r'''
#include <math.h>
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
    float calibration[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    float activation[4] = {0.25f,-0.5f,1.0f,0.75f};
    float output[2] = {0,0};
    float expected[2] = {0,0};
    unsigned char memory[8192] = {0};
    pv_catq_packed_info info;
    pv_ctx ctx;
    int weight, samples, input, options, catq, optimized, ternary, packed, result;
    int row, col;

    pv_init(&ctx);
    ctx.mem = memory;
    ctx.mem_size = (long)sizeof(memory);
    pv_catq_install();
    weight = pv_catq_register_f32(weights, 2, 4);
    samples = pv_catq_register_f32(calibration, 4, 4);
    input = pv_catq_register_f32(activation, 1, 4);
    options = make_span(&ctx, "group=4;epochs=10;batch=1;gamma=0.8;s0=30;lr=0.02", 100);
    catq = (int)pv_host2(&ctx, PV_HOOK_CATQ_CALIBRATE, samples, options);
    optimized = (int)pv_host2(&ctx, PV_HOOK_CATQ_OPTIMIZE, catq, weight);
    ternary = (int)pv_host2(&ctx, PV_HOOK_CATQ_TERNARIZE, catq, optimized);
    packed = (int)pv_host2(&ctx, PV_HOOK_CATQ_PACK, catq, ternary);
    result = (int)pv_host2(&ctx, PV_HOOK_BITLINEAR_MATVECCATQ, packed, input);
    if (!pv_catq_get_packed(packed, &info) || pv_catq_copy_f32(result, output, 2) != 2)
        return 2;
    for (row = 0; row < 2; row++) {
        for (col = 0; col < 4; col++) {
            size_t index = (size_t)row * 4 + col;
            unsigned int code = (info.codes[index / 4] >> ((index & 3) * 2)) & 3;
            float w = code == 1 ? 1.0f : (code == 2 ? -1.0f : 0.0f);
            expected[row] += w * info.scales[index / (size_t)info.group_size] * activation[col];
        }
    }
    printf("%.8f %.8f %.8f %.8f\n", output[0], output[1], expected[0], expected[1]);
    if (fabsf(output[0] - expected[0]) > 1e-6f ||
        fabsf(output[1] - expected[1]) > 1e-6f)
        return 3;
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
    assert run.returncode == 0, run.stderr + run.stdout
    values = [float(value) for value in run.stdout.split()]
    assert values[0] == pytest.approx(values[2], abs=1e-6)
    assert values[1] == pytest.approx(values[3], abs=1e-6)


def test_native_f32_tensor_ops_for_qwen_mlp(tmp_path):
    harness = tmp_path / "tensor_ops.c"
    executable = tmp_path / "tensor_ops.exe"
    harness.write_text(
        r'''
#include <stdio.h>
#include "picovm.h"
#include "picovm_catq.h"
#include "pico_hooks.h"

int main(void) {
    float x[2] = {3.0f, 4.0f};
    float y[2] = {1.0f, 2.0f};
    float gamma[2] = {1.0f, 2.0f};
    float add[2], mul[2], norm[2], swiglu[2];
    pv_ctx ctx;
    int hx, hy, hg, ha, hm, hn, hs;
    pv_init(&ctx);
    pv_catq_install();
    hx = pv_catq_register_f32(x, 1, 2);
    hy = pv_catq_register_f32(y, 1, 2);
    hg = pv_catq_register_f32(gamma, 1, 2);
    ha = (int)pv_host2(&ctx, PV_HOOK_TENSOR_ADD, hx, hy);
    hm = (int)pv_host2(&ctx, PV_HOOK_TENSOR_MUL, hx, hy);
    hn = (int)pv_host2(&ctx, PV_HOOK_TENSOR_RMSNORM, hx, hg);
    hs = (int)pv_host2(&ctx, PV_HOOK_TENSOR_SWIGLU, hx, hy);
    if (pv_catq_copy_f32(ha, add, 2) != 2 ||
        pv_catq_copy_f32(hm, mul, 2) != 2 ||
        pv_catq_copy_f32(hn, norm, 2) != 2 ||
        pv_catq_copy_f32(hs, swiglu, 2) != 2)
        return 2;
    if ((int)pv_host2(&ctx, PV_HOOK_TENSOR_RELEASE, ha, 0) != 1 ||
        pv_catq_copy_f32(ha, add, 2) != 0)
        return 3;
    printf("%.7f %.7f %.7f %.7f %.7f %.7f %.7f %.7f\n",
           add[0], add[1], mul[0], mul[1], norm[0], norm[1],
           swiglu[0], swiglu[1]);
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
    values = [float(value) for value in run.stdout.split()]
    assert values[:4] == pytest.approx([4.0, 6.0, 3.0, 8.0])
    inverse_rms = 1.0 / math.sqrt((9.0 + 16.0) / 2.0 + 1e-6)
    assert values[4:] == pytest.approx(
        [
            3.0 * inverse_rms,
            4.0 * inverse_rms * 2.0,
            (3.0 / (1.0 + math.exp(-3.0))) * 1.0,
            (4.0 / (1.0 + math.exp(-4.0))) * 2.0,
        ],
        abs=1e-6,
    )


def test_multicore_catq_matches_single_thread_codes(tmp_path):
    harness = tmp_path / "multicore.c"
    executable = tmp_path / "multicore.exe"
    harness.write_text(
        r'''
#include <math.h>
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

static int convert(pv_ctx *ctx, int samples, int weights, int options) {
    int catq = (int)pv_host2(ctx, PV_HOOK_CATQ_CALIBRATE, samples, options);
    int optimized = (int)pv_host2(ctx, PV_HOOK_CATQ_OPTIMIZE, catq, weights);
    int ternary = (int)pv_host2(ctx, PV_HOOK_CATQ_TERNARIZE, catq, optimized);
    return (int)pv_host2(ctx, PV_HOOK_CATQ_PACK, catq, ternary);
}

int main(void) {
    float weights[8] = {-0.9f,-0.2f,0.1f,0.8f,0.7f,-0.6f,0.3f,-0.1f};
    float calibration[24] = {
        1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1,
        1,-1,0.5f,0.25f, -0.5f,0.75f,-1,1
    };
    unsigned char memory[8192] = {0};
    pv_catq_packed_info one, many;
    pv_ctx ctx;
    int weight, samples, single_options, multi_options, single, multi;
    size_t i;
    pv_init(&ctx);
    ctx.mem = memory;
    ctx.mem_size = (long)sizeof(memory);
    pv_catq_install();
    weight = pv_catq_register_f32(weights, 2, 4);
    samples = pv_catq_register_f32(calibration, 6, 4);
    single_options = make_span(
        &ctx, "group=4;epochs=10;batch=2;gamma=0.8;s0=30;lr=0.02;threads=1", 100);
    multi_options = make_span(
        &ctx, "group=4;epochs=10;batch=2;gamma=0.8;s0=30;lr=0.02;threads=4", 300);
    single = convert(&ctx, samples, weight, single_options);
    multi = convert(&ctx, samples, weight, multi_options);
    if (!pv_catq_get_packed(single, &one) || !pv_catq_get_packed(multi, &many) ||
        one.codes_len != many.codes_len || one.scale_count != many.scale_count)
        return 2;
    if (memcmp(one.codes, many.codes, one.codes_len) != 0) return 3;
    for (i = 0; i < one.scale_count; i++)
        if (fabsf(one.scales[i] - many.scales[i]) > 1e-6f) return 4;
    puts("ok");
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
    assert run.returncode == 0, run.stderr + run.stdout
    assert run.stdout.strip() == "ok"
