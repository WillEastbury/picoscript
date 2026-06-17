#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OS-worker primitives: Process/Env, Timer/Scheduler, Principal/Capability/Sandbox,
Error handling, and Capsule execution. Python VM == JS VM parity."""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM, HostApi  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")


def _run_py(src):
    words = lower_to_bytecode_safe(compile_c(src))
    vm = PicoVM(host=HostApi())
    vm.load(words)
    vm.run()
    return b"".join(vm.output), vm


def _run_js(src):
    words = lower_to_bytecode_safe(compile_c(src))
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    r = subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                       input=inp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def _parity(src, expected):
    py_out, _ = _run_py(src)
    js_out = _run_js(src)
    assert py_out == js_out, f"Python != JS:\nPY={py_out!r}\nJS={js_out!r}"
    assert py_out == expected, f"output mismatch:\ngot={py_out!r}\nexpected={expected!r}"


# ═══════════════════════════════════════════════════════════════════════
# 1. Process + Env
# ═══════════════════════════════════════════════════════════════════════

PROCESS_SRC = r'''
int pid = Process.Self();
Io.WriteByte(pid);
int ppid = Process.Parent();
Io.WriteByte(ppid);
int child = Process.Spawn(1024, 42);
Io.WriteByte(child);
int status = Process.Status(child);
Io.WriteByte(status);
int killOk = Process.Kill(child);
Io.WriteByte(killOk);
int status2 = Process.Status(child);
Io.WriteByte(status2);
int exitCode = Process.Wait(child);
int exitLow = Bits.And(exitCode, 255);
Io.WriteByte(exitLow);
'''

def test_process_lifecycle():
    expected = bytes([
        1,     # self pid = 1
        0,     # parent pid = 0
        101,   # spawned child pid = 101
        0,     # child status = 0 (running)
        1,     # kill ok = 1
        2,     # child status = 2 (faulted/killed)
        0xFF,  # exit code = -1 & 0xFF = 255
    ])
    _parity(PROCESS_SRC, expected)


ENV_SRC = r'''
int c0 = Env.Count();
Io.WriteByte(c0);
int k = "PATH";
int v = "/usr/bin";
Env.Set(k, v);
int c1 = Env.Count();
Io.WriteByte(c1);
int got = Env.Get(k);
Io.Write(got);
int k0 = Env.Key(0);
Io.Write(k0);
'''

def test_env_vars():
    expected = bytes([0])  # count = 0
    expected += bytes([1])  # count = 1
    expected += b"/usr/bin"
    expected += b"PATH"
    _parity(ENV_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 2. Timer + Scheduler
# ═══════════════════════════════════════════════════════════════════════

TIMER_SRC = r'''
int t1 = Timer.After(100);
int t2 = Timer.Every(50);
int e0 = Timer.Elapsed();
Io.WriteByte(e0);
int fired = Scheduler.Tick(60);
Io.WriteByte(fired);
int e1 = Timer.Elapsed();
Io.WriteByte(e1);
int evCount = Event.Count();
Io.WriteByte(evCount);
int fired2 = Scheduler.Tick(50);
Io.WriteByte(fired2);
int evCount2 = Event.Count();
Io.WriteByte(evCount2);
int cancelOk = Timer.Cancel(t2);
Io.WriteByte(cancelOk);
int fired3 = Scheduler.Tick(100);
Io.WriteByte(fired3);
'''

def test_timer_scheduler():
    expected = bytes([
        0,     # elapsed = 0
        1,     # fired = 1 (t2 fires at 50ms, tick=60)
        60,    # elapsed = 60
        1,     # event count = 1
        2,     # fired = 2 (t1 fires at 100ms tick=110; t2 fires again at 100ms)
        3,     # event count = 3
        1,     # cancel ok
        0,     # no more fires (t2 cancelled, t1 was one-shot)
    ])
    _parity(TIMER_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 3. Principal + Capability + Sandbox
# ═══════════════════════════════════════════════════════════════════════

PRINCIPAL_SRC = r'''
int name = Principal.Current();
Io.Write(name);
int hasAdmin = Principal.HasRole("admin");
Io.WriteByte(hasAdmin);
int claims = Principal.Claims();
Io.Write(claims);
int hasCap = Capability.Has(8);
Io.WriteByte(hasCap);
Sandbox.Deny(8);
int hasCap2 = Capability.Has(8);
Io.WriteByte(hasCap2);
int reqOk = Capability.Request(8);
Io.WriteByte(reqOk);
'''

def test_principal_capability_sandbox():
    expected = b"anonymous"   # default principal
    expected += bytes([0])    # hasRole("admin") = 0
    expected += b""           # no claims
    expected += bytes([1])    # has cap 8 (STORAGE) = 1
    expected += bytes([0])    # after deny, has = 0
    expected += bytes([0])    # request denied by sandbox
    _parity(PRINCIPAL_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 4. Error handling
# ═══════════════════════════════════════════════════════════════════════

ERROR_SRC = r'''
int h0 = Error.HasHandler();
Io.WriteByte(h0);
int code0 = Error.Code();
Io.WriteByte(code0);
Error.SetHandler(0);
int h1 = Error.HasHandler();
Io.WriteByte(h1);
Error.Clear();
int code1 = Error.Code();
Io.WriteByte(code1);
'''

def test_error_handling_basics():
    expected = bytes([
        0,    # no handler
        0,    # no error code
        0,    # handler set to 0 = effectively no handler
        0,    # after clear, code = 0
    ])
    _parity(ERROR_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 5. Capsule execution
# ═══════════════════════════════════════════════════════════════════════

CAPSULE_SRC = r'''
int result = Capsule.Call(1024, 1);
Io.WriteByte(result);
int ok = Capsule.Schedule(1024, 2);
Io.WriteByte(ok);
int mod = Capsule.LoadModule(1024, 3);
Io.WriteByte(mod);
int runResult = Capsule.RunModule(mod);
Io.WriteByte(runResult);
'''

def test_capsule_execution():
    expected = bytes([
        0,    # Call result = 0 (simulated)
        1,    # Schedule ok
        1,    # LoadModule handle = 1
        0,    # RunModule result = 0 (simulated)
    ])
    _parity(CAPSULE_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 6. Process.Args
# ═══════════════════════════════════════════════════════════════════════

ARGS_SRC = r'''
int args = Process.Args();
int len = Span.Len(args);
Io.WriteByte(len);
'''

def test_process_args():
    expected = bytes([0])  # empty args by default
    _parity(ARGS_SRC, expected)


# ═══════════════════════════════════════════════════════════════════════
# 7. Timer event integration
# ═══════════════════════════════════════════════════════════════════════

TIMER_EVENT_SRC = r'''
int th = Timer.After(10);
Scheduler.Tick(20);
int evId = Event.Next();
int evType = Event.Type(evId);
int evTarget = Event.Target(evId);
Io.WriteByte(evType);
Io.WriteByte(evTarget);
'''

def test_timer_event_integration():
    expected = bytes([
        100,  # EVENT_TIMER type
        1,    # target = timer handle 1
    ])
    _parity(TIMER_EVENT_SRC, expected)


def main():
    test_process_lifecycle()
    print("PASS Process.* lifecycle")
    test_env_vars()
    print("PASS Env.* vars")
    test_timer_scheduler()
    print("PASS Timer.*/Scheduler.* deterministic timers")
    test_principal_capability_sandbox()
    print("PASS Principal.*/Capability.*/Sandbox.*")
    test_error_handling_basics()
    print("PASS Error.* handling basics")
    test_capsule_execution()
    print("PASS Capsule.* execution")
    test_process_args()
    print("PASS Process.Args")
    test_timer_event_integration()
    print("PASS Timer -> Event integration")
    print("\nAll OS-worker primitive tests passed (Python VM == JS VM)")


if __name__ == "__main__":
    main()
