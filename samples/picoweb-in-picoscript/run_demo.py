#!/usr/bin/env python3
"""run_demo.py -- exercise httpd.ppy on the bytecode VM.

Compiles the PicoScript HTTP server, installs a series of simulated request
contexts (the same path the native pool worker uses), runs the program, and
prints the framed response. Also times the compiled handler to gauge per-request
cost on the interpreter.

    python run_demo.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from picoscript_python import compile_python          # noqa: E402
from picoscript_il import lower_to_bytecode_safe       # noqa: E402
from picoscript_vm import PicoVM, HostApi              # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "httpd.ppy")


def compile_server():
    words = lower_to_bytecode_safe(compile_python(open(SRC, encoding="utf-8").read()))
    return words


def serve(words, method, path, headers=None, body=b""):
    host = HostApi()
    host.caps = 0xFFFFFFFF
    vm = PicoVM(host=host)
    vm.load(words)
    host.install_request_context(
        vm, seq=1, principal="", method=method, path=path,
        headers=headers or {}, body=[body] if body else [], body_mode=0,
    )
    vm.run()
    return _read_response(host)


def make_warm_vm(words):
    """Construct one VM/host and keep it warm; serve_warm reuses it per request."""
    host = HostApi()
    host.caps = 0xFFFFFFFF
    vm = PicoVM(host=host)
    vm.load(words)
    return vm, host


def serve_warm(vm, host, method, path, headers=None, body=b""):
    vm.reset_for_request()
    host.install_request_context(
        vm, seq=1, principal="", method=method, path=path,
        headers=headers or {}, body=[body] if body else [], body_mode=0,
    )
    vm.run()
    return _read_response(host)


def _read_response(host):
    g = host.get_response_graph()
    status, ctype, parts = 200, "?", []
    for d in g:
        if d["kind"] == "DESC_PREAMBLE" and d["subtype"] == "STATUS":
            status = d["payload"]["code"]
        elif d["kind"] == "DESC_HEADER":
            p = d["payload"]; nm = p.get("name"); v = p.get("value")
            nm = nm.get("text") if isinstance(nm, dict) else nm
            v = v.get("text") if isinstance(v, dict) else v
            if str(nm).lower() == "content-type":
                ctype = v
        elif d["kind"] == "DESC_BODY":
            pl = d["payload"]
            parts.append(pl.get("text", "") if isinstance(pl, dict) else str(pl))
    return status, ctype, "".join(parts)


def main():
    words = compile_server()
    print(f"compiled httpd.ppy -> {len(words)} bytecode words\n")

    requests = [
        ("GET", "/", {}, b""),
        ("GET", "/api/ping", {}, b""),
        ("GET", "/api/headers", {"user-agent": "probe/1.0"}, b""),
        ("GET", "/api/echo/hello", {}, b""),
        ("POST", "/api/create", {}, b'{"name":"x"}'),
        ("PUT", "/api/create", {}, b""),
        ("GET", "/nope", {}, b""),
    ]
    for m, p, h, b in requests:
        st, ct, body = serve(words, m, p, h, b)
        print(f"{m:5} {p:18} -> {st} {ct}")
        print(f"        {body}")

    # Verify the warm path produces identical responses to the cold path.
    vm, host = make_warm_vm(words)
    ok = all(
        serve_warm(vm, host, m, p, h, b) == serve(words, m, p, h, b)
        for m, p, h, b in requests
    )
    print(f"\nwarm-path parity with cold path: {'OK' if ok else 'MISMATCH'}")

    # crude per-request timing on the VM: cold (new VM each req) vs warm (reused)
    iters = 5000
    t0 = time.perf_counter()
    for _ in range(iters):
        serve(words, "GET", "/api/ping", {}, b"")
    cold = (time.perf_counter() - t0) / iters * 1e6

    vm, host = make_warm_vm(words)
    t0 = time.perf_counter()
    for _ in range(iters):
        serve_warm(vm, host, "GET", "/api/ping", {}, b"")
    warm = (time.perf_counter() - t0) / iters * 1e6

    print(f"\nVM /api/ping  COLD (new VM/req): {cold:8.1f} us/req  {int(1e6/cold):>7} req/s")
    print(f"VM /api/ping  WARM (reused VM):  {warm:8.1f} us/req  {int(1e6/warm):>7} req/s")
    print(f"speedup: {cold/warm:.1f}x  (single-thread interpreter; the native")
    print("lower_to_c pool removes the interpreter entirely -- see ../README.md)")


if __name__ == "__main__":
    main()
