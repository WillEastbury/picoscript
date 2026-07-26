#!/usr/bin/env python3
"""Coarse CAT-Q appliance primitives and host-provider seams."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_english import compile_english  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_vm import HostApi, PicoVM, SocketNetworkProvider  # noqa: E402


class FakeComputeProvider:
    def __init__(self):
        self.calls = []

    def call(self, namespace, method, a, b, *, vm, host):
        self.calls.append((namespace, method, a, b))
        values = {
            ("Tensor", "Map"): 11,
            ("Tensor", "View"): 12,
            ("Tensor", "Gemm"): 13,
            ("Tensor", "Reduce"): 14,
            ("Tensor", "Elementwise"): 15,
            ("CatQ", "Calibrate"): 21,
            ("CatQ", "Optimize"): 22,
            ("CatQ", "Ternarize"): 23,
            ("BitLinear", "MatVecCatQ"): 24,
            ("Tensor", "Add"): 25,
            ("Tensor", "Mul"): 26,
            ("Tensor", "RmsNorm"): 27,
            ("Tensor", "SwiGLU"): 28,
            ("Async", "Submit"): 31,
            ("Async", "Wait"): 1,
            ("Async", "Result"): 32,
            ("Shard", "Load"): 41,
            ("Shard", "Save"): 1,
        }
        if (namespace, method) == ("CatQ", "Pack"):
            return b"TERN"
        return values.get((namespace, method))


COMPUTE_SOURCE = r'''
int mapped = Tensor.Map("weights", "dtype=bf16");
int view = Tensor.View(mapped, "rows=0:128");
int gemm = Tensor.Gemm(view, mapped);
int reduced = Tensor.Reduce(gemm, "mse");
int elt = Tensor.Elementwise(reduced, "normalize");
int calibration = CatQ.Calibrate(elt, "group=128");
int optimized = CatQ.Optimize(calibration, mapped);
int ternary = CatQ.Ternarize(calibration, optimized);
int packed = CatQ.Pack(calibration, ternary);
int projected = BitLinear.MatVecCatQ(packed, mapped);
int added = Tensor.Add(projected, projected);
int multiplied = Tensor.Mul(projected, projected);
int normalized = Tensor.RmsNorm(projected, projected);
int swiglu = Tensor.SwiGLU(projected, projected);
int job = Async.Submit("save", packed);
int waited = Async.Wait(job, 1000);
int result = Async.Result(job);
int shard = Shard.Load("model.safetensors", "mmap");
int saved = Shard.Save(shard, "model.ternary");
Io.WriteByte(mapped);
Io.WriteByte(view);
Io.WriteByte(gemm);
Io.WriteByte(reduced);
Io.WriteByte(elt);
Io.WriteByte(calibration);
Io.WriteByte(optimized);
Io.WriteByte(ternary);
Io.Write(packed);
Io.WriteByte(projected);
Io.WriteByte(added);
Io.WriteByte(multiplied);
Io.WriteByte(normalized);
Io.WriteByte(swiglu);
Io.WriteByte(job);
Io.WriteByte(waited);
Io.WriteByte(result);
Io.WriteByte(shard);
Io.WriteByte(saved);
'''


def test_python_compute_provider_routes_coarse_operations():
    provider = FakeComputeProvider()
    words = lower_to_bytecode_safe(compile_c(COMPUTE_SOURCE))
    vm = PicoVM(host=HostApi(compute_provider=provider)).run(words)
    output = b"".join(vm.output)
    assert output == bytes([11, 12, 13, 14, 15, 21, 22, 23]) + b"TERN" + bytes([24, 25, 26, 27, 28, 31, 1, 32, 41, 1])
    assert [(ns, method) for ns, method, _, _ in provider.calls] == [
        ("Tensor", "Map"), ("Tensor", "View"), ("Tensor", "Gemm"),
        ("Tensor", "Reduce"), ("Tensor", "Elementwise"),
        ("CatQ", "Calibrate"), ("CatQ", "Optimize"),
        ("CatQ", "Ternarize"), ("CatQ", "Pack"),
        ("BitLinear", "MatVecCatQ"),
        ("Tensor", "Add"), ("Tensor", "Mul"), ("Tensor", "RmsNorm"), ("Tensor", "SwiGLU"),
        ("Async", "Submit"), ("Async", "Wait"), ("Async", "Result"),
        ("Shard", "Load"), ("Shard", "Save"),
    ]


def test_all_lowerers_emit_code_keyed_host_calls():
    il = compile_c(COMPUTE_SOURCE)
    c_source = lower_to_c(il)
    js_source = lower_to_js(il)
    for code in range(0x370, 0x37E):
        assert f"0x{code:X}" in c_source
        assert f"0x{code:X}" in js_source
    assert "0x381" in c_source
    assert "0x381" in js_source
    for code in range(0x382, 0x385):
        assert f"0x{code:X}" in c_source
        assert f"0x{code:X}" in js_source
    assert "0x385" in c_source
    assert "0x385" in js_source


def test_browser_compiler_accepts_catq_and_raw_net_hooks():
    source = (
        'int t=Tensor.Map("w","bf16");'
        'int c=CatQ.Optimize(1,t);'
        'int n=Net.Connect("127.0.0.1",9000);'
        'Net.SendSpan(n,"x");'
    )
    expected = lower_to_bytecode_safe(compile_c(source))
    run = subprocess.run(
        ["node", os.path.join(ROOT, "vm", "picoc_compile.js"), "c"],
        input=source,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert [int(word, 16) for word in run.stdout.split()] == expected


def test_core_frontends_share_the_same_catq_hooks():
    c_src = 'int t=Tensor.Map("w","bf16"); int q=CatQ.Optimize(1,t); return q;'
    basic_src = 'DIM t = Tensor.Map("w", "bf16")\nDIM q = CatQ.Optimize(1, t)\nRETURN q'
    python_src = 't = Tensor.Map("w", "bf16")\nq = CatQ.Optimize(1, t)\nreturn q'
    english_src = 'Set t to Tensor.Map("w", "bf16").\nSet q to CatQ.Optimize(1, t).\nReturn q.'
    variants = [
        lower_to_bytecode_safe(compile_c(c_src)),
        lower_to_bytecode_safe(compile_basic(basic_src)),
        lower_to_bytecode_safe(compile_python(python_src)),
        lower_to_bytecode_safe(compile_english(english_src)),
    ]
    assert all(words == variants[0] for words in variants[1:])


def test_unbound_primitives_have_defined_defaults():
    source = r'''
int t = Tensor.Map("w", "bf16");
int c = CatQ.Calibrate(t, "group=128");
int j = Async.Submit("run", c);
int s = Shard.Load("missing", 0);
int r = Net.RecvSpan(0, 16);
Io.WriteByte(t);
Io.WriteByte(c);
Io.WriteByte(j);
Io.WriteByte(s);
Io.WriteByte(Span.Len(r));
Io.WriteByte(Status.Last());
'''
    vm = PicoVM().run(lower_to_bytecode_safe(compile_c(source)))
    assert b"".join(vm.output) == bytes([0, 0, 0, 0, 0, 1])


def test_socket_provider_runs_picoscript_client():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def echo():
        conn, _ = listener.accept()
        with conn:
            assert conn.recv(4) == b"ping"
            conn.sendall(b"pong")
        listener.close()

    thread = threading.Thread(target=echo)
    thread.start()
    provider = SocketNetworkProvider()
    source = f'''
int conn = Net.Connect("127.0.0.1", {port});
int sent = Net.SendSpan(conn, "ping");
int reply = Net.RecvSpan(conn, 4);
Io.WriteByte(sent);
Io.Write(reply);
Net.Shutdown(conn);
'''
    vm = PicoVM(host=HostApi(network_provider=provider)).run(
        lower_to_bytecode_safe(compile_c(source))
    )
    thread.join(timeout=5)
    provider.close_all()
    assert not thread.is_alive()
    assert b"".join(vm.output) == bytes([4]) + b"pong"


def test_socket_provider_runs_picoscript_server():
    provider = SocketNetworkProvider()
    host = HostApi(network_provider=provider)
    source = r'''
int server = Net.Listen(0, 4);
int conn = Net.Accept(server, 5000);
int request = Net.RecvSpan(conn, 4);
Io.Write(request);
Net.SendSpan(conn, "pong");
Net.Shutdown(conn);
Net.Shutdown(server);
'''
    result = {}

    def run_server():
        result["vm"] = PicoVM(host=host).run(lower_to_bytecode_safe(compile_c(source)))

    thread = threading.Thread(target=run_server)
    thread.start()
    deadline = time.time() + 5
    while not provider._sockets and time.time() < deadline:
        time.sleep(0.01)
    assert provider._sockets
    port = provider.local_port(min(provider._sockets))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(b"ping")
        assert conn.recv(4) == b"pong"
    thread.join(timeout=5)
    provider.close_all()
    assert not thread.is_alive()
    assert b"".join(result["vm"].output) == b"ping"


def test_javascript_provider_seam(tmp_path):
    js_program = lower_to_js(compile_c(COMPUTE_SOURCE), module_name="catq_provider")
    program = tmp_path / "catq_provider.js"
    program.write_text(js_program, encoding="utf-8")
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picocompress.js", "picobrotli.js"):
        source = os.path.join(ROOT, "vm", dep)
        if os.path.exists(source):
            (tmp_path / dep).write_bytes(open(source, "rb").read())
    runner = tmp_path / "run.js"
    runner.write_text(
        """
const p = require('./catq_provider.js');
const values = {
  'Tensor.Map':11,'Tensor.View':12,'Tensor.Gemm':13,'Tensor.Reduce':14,'Tensor.Elementwise':15,
  'CatQ.Calibrate':21,'CatQ.Optimize':22,'CatQ.Ternarize':23,
  'BitLinear.MatVecCatQ':24,
  'Tensor.Add':25,'Tensor.Mul':26,'Tensor.RmsNorm':27,
  'Tensor.SwiGLU':28,
  'Async.Submit':31,'Async.Wait':1,'Async.Result':32,'Shard.Load':41,'Shard.Save':1
};
const provider = { call: function(ns, method) {
  if (ns === 'CatQ' && method === 'Pack') return new Uint8Array([84,69,82,78]);
  return values[ns + '.' + method];
}};
const rt = p.makeRuntime({computeProvider: provider});
p.run(rt);
console.log(Buffer.from(rt.output).toString('hex'));
""",
        encoding="utf-8",
    )
    run = subprocess.run(["node", str(runner)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == (
        bytes([11, 12, 13, 14, 15, 21, 22, 23]) + b"TERN" + bytes([24, 25, 26, 27, 28, 31, 1, 32, 41, 1])
    ).hex()


def test_native_c_provider_seams(tmp_path):
    harness = tmp_path / "provider.c"
    harness.write_text(
        r'''
#include <stdio.h>
#include "picovm.h"
#include "pico_hooks.h"

static int compute(pv_ctx *ctx, int hook, int rd, int rs1, int rs2) {
    (void)rs1; (void)rs2;
    if (hook == PV_HOOK_TENSOR_MAP) { ctx->regs[rd] = 77; return 1; }
    if (hook == PV_HOOK_CATQ_OPTIMIZE) { ctx->regs[rd] = 88; return 1; }
    return 0;
}

static int network(pv_ctx *ctx, int hook, int rd, int rs1, int rs2) {
    (void)rs1; (void)rs2;
    if (hook == PV_HOOK_NET_CONNECT) { ctx->regs[rd] = 99; return 1; }
    return 0;
}

int main(void) {
    pv_ctx ctx;
    pv_init(&ctx);
    pv_compute_hook = compute;
    pv_net_hook = network;
    printf("%lld %lld %lld\n",
        (long long)pv_host2(&ctx, PV_HOOK_TENSOR_MAP, 1, 2),
        (long long)pv_host2(&ctx, PV_HOOK_CATQ_OPTIMIZE, 1, 2),
        (long long)pv_host2(&ctx, PV_HOOK_NET_CONNECT, 1, 2));
    return 0;
}
''',
        encoding="utf-8",
    )
    exe = tmp_path / "provider.exe"
    compile_result = subprocess.run(
        [
            sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
            f"-I{os.path.join(ROOT, 'vm')}",
            str(harness), os.path.join(ROOT, "vm", "picovm.c"),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "77 88 99"
