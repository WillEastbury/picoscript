import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_tensor import (ProviderRequest, TensorDescriptor, TensorDType,
                               TensorHandleTable, TensorStatus)


def test_descriptor_round_trip_and_validation():
    descriptor = TensorDescriptor(
        TensorDType.INT8, (2, 3), (3, 1), byte_offset=4, byte_length=6
    )
    assert TensorDescriptor.decode(descriptor.encode()) == descriptor
    bad = bytearray(descriptor.encode())
    bad[6] = 0
    try:
        TensorDescriptor.decode(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid rank was accepted")


def test_generation_handles_and_provider_statuses():
    handles = TensorHandleTable()
    handle = handles.allocate({"shape": (2, 3)})
    assert handles.get(handle)["shape"] == (2, 3)
    assert handles.release(handle)
    assert not handles.release(handle)
    request = ProviderRequest(workspace_bytes=12, workspace_limit=8)
    assert request.status() == TensorStatus.WORKSPACE_EXHAUSTED
    request.cancel()
    assert request.status() == TensorStatus.CANCELLED


def test_javascript_descriptor_matches_python():
    descriptor = TensorDescriptor(
        TensorDType.BF16, (4, 2), (2, 1), flags=3, byte_offset=8, byte_length=16
    )
    script = """
const p = require('./picoscript_tensor_abi.js');
const d = p.decode(Buffer.from(process.argv[1], 'hex'));
process.stdout.write(JSON.stringify(d));
"""
    abi = os.path.join(ROOT, "vm", "picoscript_tensor_abi.js")
    if not os.path.exists(abi):
        raise AssertionError("JavaScript tensor ABI helper is missing")
    result = subprocess.run(
        ["node", "-e", script, descriptor.encode().hex()],
        cwd=os.path.dirname(abi), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == {
        "version": 1, "dtype": 5, "rank": 2, "flags": 3,
        "byteOffset": 8, "byteLength": 16,
        "dimensions": [4, 2], "strides": [2, 1],
    }
