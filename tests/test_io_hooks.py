#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Req.*/Resp.* simulated PIOS I/O ABI host-hook tests."""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_python import compile_python  # noqa: E402
from picoscript_vm import HostApi, PicoVM  # noqa: E402


CTX = {
    "seq": 201,
    "principal": "user:alice",
    "method": "POST",
    "path": "/submit",
    "headers": {"accept": "text/plain", "x-request": "abc"},
    "body": ["payload-0", "payload-1"],
    "body_mode": 0,
}


def run_py(words, ctx=CTX):
    host = HostApi()
    vm = PicoVM(host=host)
    vm.load(words)
    host.install_request_context(vm, **ctx)
    vm.run()
    return host.get_response_graph()


def simplify_payload(payload):
    if isinstance(payload, dict):
        if "text" in payload:
            return payload["text"]
        return {k: simplify_payload(v) for k, v in payload.items() if k not in ("span", "ptr", "len")}
    return payload


def simplify(graph):
    return [(d["kind"], d["subtype"], simplify_payload(d["payload"])) for d in graph]


def run_js(words, ctx=CTX):
    script = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const PicoVM = require("./vm/picovm.js");
const vm = new PicoVM();
vm.load(input.words);
vm.setRequestContext(input.ctx);
vm.run();
console.log(JSON.stringify(vm.getResponseGraph()));
"""
    payload = json.dumps({"words": words, "ctx": ctx})
    r = subprocess.run(["node", "-e", script], cwd=ROOT, input=payload,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def js_compile(src, lang):
    r = subprocess.run(["node", os.path.join(ROOT, "vm", "picoc_compile.js"), lang],
                       input=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [int(w, 16) for w in r.stdout.split()]


def test_c_request_response_graph():
    src = r'''
int status = Req.Seq();
int method = Req.Method();
int path = Req.Path();
int accept = Req.Header("accept");
int body0 = Req.BodySpan(0);
Resp.Status(status);
Resp.Header("content-type", accept);
Resp.Header("x-powered-by", "pico");
Resp.Write(method);
Resp.Write(" ");
Resp.Write(path);
Resp.Write(" ");
Resp.Write(body0);
Resp.Trailer("done", "yes");
Resp.End();
'''
    words = lower_to_bytecode_safe(compile_c(src))
    graph = simplify(run_py(words))
    assert graph == [
        ("DESC_PREAMBLE", "STATUS", {"code": 201}),
        ("DESC_HEADER", None, {"name": "content-type", "value": "text/plain"}),
        ("DESC_HEADER", None, {"name": "x-powered-by", "value": "pico"}),
        ("DESC_BODY", None, "POST"),
        ("DESC_BODY", None, " "),
        ("DESC_BODY", None, "/submit"),
        ("DESC_BODY", None, " "),
        ("DESC_BODY", None, "payload-0"),
        ("DESC_TRAILER", None, {"name": "done", "value": "yes"}),
        ("DESC_COMMIT", "END", None),
    ]


def test_python_dialect_respond_and_body_metadata():
    src = r'''
status = 200 + Req.BodyMode() + Req.BodyCount()
Resp.Write(Req.Principal())
Resp.Write(Req.BodySpan(1))
Resp.Respond(status)
'''
    words = lower_to_bytecode_safe(compile_python(src))
    graph = simplify(run_py(words))
    assert graph == [
        ("DESC_BODY", None, "user:alice"),
        ("DESC_BODY", None, "payload-1"),
        ("DESC_PREAMBLE", "STATUS", {"code": 202}),
        ("DESC_COMMIT", "SEAL", None),
        ("DESC_COMMIT", "END", None),
    ]


def test_control_descriptors():
    src = r'''
Resp.Continue();
Resp.EarlyHints();
Resp.Status(200);
Resp.Seal();
Resp.Write("chunk");
Resp.Flush();
Resp.EndStream();
Resp.Upgrade("websocket");
Resp.Abort(499);
'''
    words = lower_to_bytecode_safe(compile_c(src))
    graph = simplify(run_py(words))
    assert graph == [
        ("DESC_CONTROL", "CONTINUE_100", None),
        ("DESC_CONTROL", "EARLY_HINTS_103", None),
        ("DESC_PREAMBLE", "STATUS", {"code": 200}),
        ("DESC_COMMIT", "SEAL", None),
        ("DESC_BODY", None, "chunk"),
        ("DESC_CONTROL", "FLUSH", None),
        ("DESC_CONTROL", "END_STREAM", None),
        ("DESC_UPGRADE", None, "websocket"),
        ("DESC_ABORT", None, {"code": 499}),
    ]


def test_i3_header_after_seal_rejected():
    src = r'''
Resp.Status(200);
Resp.Seal();
Resp.Header("late", "no");
'''
    words = lower_to_bytecode_safe(compile_c(src))
    try:
        run_py(words)
    except RuntimeError as exc:
        assert "I3 violation" in str(exc)
    else:
        raise AssertionError("late header after Resp.Seal() was not rejected")


def test_python_vm_js_vm_response_graph_parity_and_compiler_bytes():
    src = r'''
int status = Req.Seq();
Resp.Status(status);
Resp.Header("content-type", Req.Header("accept"));
Resp.Write(Req.Method());
Resp.Write(Req.BodySpan(0));
Resp.Seal();
Resp.Flush();
Resp.End();
'''
    words = lower_to_bytecode_safe(compile_c(src))
    assert words == js_compile(src, "c")
    assert simplify(run_py(words)) == simplify(run_js(words))


def main():
    tests = [
        test_c_request_response_graph,
        test_python_dialect_respond_and_body_metadata,
        test_control_descriptors,
        test_i3_header_after_seal_rejected,
        test_python_vm_js_vm_response_graph_parity_and_compiler_bytes,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"{len(tests) - failed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
