#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice-first access for request, stream and event payload data.

These are the non-card equivalents of Storage.ReadSlice: HTTP/TCP/UDP payloads
and stream leases must be readable as windows, while the existing whole-blob APIs
remain available (`Req.BodySpan`, `Stream.Span`, `Event.Data`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


REQ_SLICE = r'''
Req.SetSlice(6, 5);
print(Req.BodyLen(0));
int part = Req.BodySlice(0);
Io.Write(part);
'''

STREAM_SLICE = r'''
int dev = Device.Open("udp0", 0);
int s = Stream.Open(dev, 65588);
int lease = Stream.Next(s);
Stream.SetSlice(10, 5);
int part = Stream.Slice(lease);
Io.Write(part);
Stream.Release(lease);
Stream.Close(s);
Device.Close(dev);
'''

EVENT_SLICE = r'''
int ev = Event.Post(9, 1);
int payload = "event-payload-data";
Event.SetData(ev, payload);
int got = Event.Next();
Event.SetSlice(6, 7);
print(Event.DataLen(got));
int part = Event.DataSlice(got);
Io.Write(part);
'''


def _words(src: str):
    return lower_to_bytecode_safe(compile_c(src))


def _py(words, request_body=None):
    h = HostApi()
    vm = PicoVM(host=h)
    if request_body is not None:
        h.install_request_context(vm, body=[request_body])
    vm.load(words)
    vm.run()
    return b"".join(vm.output)


def _js(words, request_body=None):
    script = r'''
const PicoVM = require("./vm/picovm.js");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const vm = new PicoVM();
vm.load(input.words);
if (input.body !== null) vm.setRequestContext({ body: [input.body] });
vm.run();
process.stdout.write(JSON.stringify({ out: Array.from(vm.output) }));
'''
    r = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        input=json.dumps({"words": words, "body": request_body}),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return bytes(json.loads(r.stdout)["out"])


def _check(src, expected, request_body=None):
    words = _words(src)
    py = _py(words, request_body=request_body)
    js = _js(words, request_body=request_body)
    assert py == expected, py
    assert js == expected, js


def test_request_body_slice():
    body = "hello world payload"
    _check(REQ_SLICE, len(body).to_bytes(4, "big") + b"world", request_body=body)


def test_stream_lease_slice():
    _check(STREAM_SLICE, bytes([10, 11, 12, 13, 14]))


def test_event_data_slice_in_handler_loop():
    _check(EVENT_SLICE, (18).to_bytes(4, "big") + b"payload")


def main():
    test_request_body_slice()
    test_stream_lease_slice()
    test_event_data_slice_in_handler_loop()
    print("PASS Req/Stream/Event slices: body, lease and event payload windows (Python VM == JS VM)")


if __name__ == "__main__":
    main()
