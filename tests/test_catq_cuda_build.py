#!/usr/bin/env python3
"""Optional CUDA provider build/run check."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cuda_available():
    if not shutil.which("nvcc") or not shutil.which("nvidia-smi"):
        return False
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


@pytest.mark.skipif(not cuda_available(), reason="CUDA compiler/GPU unavailable")
def test_catq_cuda_provider_builds_and_runs(tmp_path):
    source = tmp_path / "cuda.pc"
    executable = tmp_path / "cuda.exe"
    source.write_text("return 1;\n", encoding="utf-8")
    build = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "picoscript_build.py"),
            "native",
            str(source),
            "--provider",
            "catq-cuda",
            "--cuda-arch",
            "sm_86",
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
