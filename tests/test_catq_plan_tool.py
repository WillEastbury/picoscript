#!/usr/bin/env python3
"""Dependency-free model-plan compiler for C-PicoScript CAT-Q workflows."""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_tool(tmp_path):
    executable = tmp_path / "catq_plan.exe"
    build = subprocess.run(
        [
            sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
            os.path.join(ROOT, "tools", "catq_plan.c"),
            "-o", str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    return executable


def write_index(model_dir, weight_map):
    (model_dir / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {}, "weight_map": weight_map}),
        encoding="utf-8",
    )


def test_qwen35_plan_emits_executable_picoscript(tmp_path):
    tool = build_tool(tmp_path)
    model_dir = tmp_path / "qwen"
    model_dir.mkdir()
    write_index(
        model_dir,
        {
            "model.language_model.layers.0.linear_attn.in_proj_qkv.weight": "model-00001.safetensors",
            "model.language_model.layers.0.mlp.gate.weight": "model-00001.safetensors",
        },
    )
    manifest = tmp_path / "activations.tsv"
    output = tmp_path / "plan.pc"
    manifest.write_text(
        "model.language_model.layers.0.linear_attn.in_proj_qkv.weight\t"
        "calibration.safetensors\tlayer0.input\tlayer0.qkv.ternary.safetensors\n"
        "model.language_model.layers.0.mlp.gate.weight\t"
        "calibration.safetensors\tlayer0.router\tlayer0.router.ternary.safetensors\n",
        encoding="utf-8",
    )
    run = subprocess.run(
        [str(tool), "qwen3.5", str(model_dir), str(manifest), str(output)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    source = output.read_text(encoding="utf-8")
    assert "in_proj_qkv.weight" in source
    assert "mlp.gate.weight" not in source
    assert "CatQ.Optimize" in source
    compile_result = subprocess.run(
        [
            sys.executable, os.path.join(ROOT, "picoscript_build.py"),
            "emit", str(output), "--as", "bytecode", "--hex",
            "-o", str(tmp_path / "plan.hex"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr + compile_result.stdout


def test_gpt_oss_plan_rejects_mxfp4_expert_blocks(tmp_path):
    tool = build_tool(tmp_path)
    model_dir = tmp_path / "gpt-oss"
    model_dir.mkdir()
    name = "model.layers.0.mlp.experts.gate_up_proj_blocks"
    write_index(model_dir, {name: "model-00000.safetensors"})
    manifest = tmp_path / "activations.tsv"
    output = tmp_path / "plan.pc"
    manifest.write_text(
        f"{name}\tcalibration.safetensors\tlayer0.expert\tout.safetensors\n",
        encoding="utf-8",
    )
    run = subprocess.run(
        [str(tool), "gpt-oss", str(model_dir), str(manifest), str(output)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 6
    assert "MXFP4 tensor requires dequantization" in run.stderr
