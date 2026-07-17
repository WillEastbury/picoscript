#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_namespace_equalization.py -- coverage for the namespace-equality
pass: every host namespace must be callable, from every dialect and VM, and
either do something real or return a well-defined default -- never crash and
never leave a destination register silently untouched. See docs/FEATURE_MATRIX.md.

Covers (Python VM, `picoscript_vm.py`):
  - Data.* Python/C-vs-JS asymmetry fix (explicit 0/empty-span default).
  - Real, deterministic primitives added this pass: Descriptor.*, Lease.*,
    Fifo.*, Pack.Use, Thread.YieldCounted, Kernel.WaitIRQ/WaitSWIRQ/FireSWIRQ,
    Kernel.ProfileStart/ProfileEnd/TracePoint (via Log.*).
  - String.Split/String.Join (Map-backed multi-value result).
  - Reserved/host-injected namespaces (Auth, Card, Context, Environment, Net,
    X509) now return a defined 0/empty-span default instead of silently
    falling through.

C VM (vm/picovm.c) and JS VM (vm/picovm.js) parity for the same features is
covered by tests/test_native_toc.py-style ad hoc verification during
development; see docs/FEATURE_MATRIX.md for the full per-runtime status
(Kernel.Profile*/TracePoint and Log.* are Python+JS only, not yet on the C VM
interpreter -- a documented, scoped-out follow-up).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src, max_steps=20000):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM(max_steps=max_steps).run(words)


def out_bytes(vm):
    return b"".join(vm.output)


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# -- Data.* ------------------------------------------------------------------

def test_data_fieldnum_returns_defined_zero():
    vm = run("int a = 99; int r = Data.FieldNum(a); print(r);")
    assert out_ints(vm) == [0]


def test_data_fieldstr_returns_defined_empty_span():
    vm = run('int a = 99; int s = Data.FieldStr(a); Io.Write(s);')
    assert out_bytes(vm) == b""


# -- Descriptor.* (real) ------------------------------------------------------

def test_descriptor_make_and_accessors():
    vm = run("""
int p = Memory.ArenaAlloc(16);
int h = Descriptor.Make(p, 8);
Descriptor.SetFlags(h, 42);
int len = Descriptor.GetLen(h);
int flags = Descriptor.GetFlags(h);
int ptr = Descriptor.GetPtr(h);
print(len); print(flags); print(ptr == p);
""")
    assert out_ints(vm) == [8, 42, 1]


def test_descriptor_copybatch_copies_bytes():
    vm = run("""
int p1 = Memory.ArenaAlloc(8);
int p2 = Memory.ArenaAlloc(8);
Memory.Set(p1, 65);
int h1 = Descriptor.Make(p1, 1);
int h2 = Descriptor.Make(p2, 1);
int n = Descriptor.CopyBatch(h1, h2);
int v = Memory.Get(p2);
print(n); print(v);
""")
    assert out_ints(vm) == [1, 65]


def test_descriptor_invalid_handle_returns_zero():
    vm = run("int r = Descriptor.GetLen(999); print(r);")
    assert out_ints(vm) == [0]


# -- Lease.* (real) -----------------------------------------------------------

def test_lease_acquire_validate_release():
    vm = run("""
int s = "hello";
int h = Lease.Acquire(s, 7);
int v1 = Lease.Validate(h);
int th = Lease.GetTypeHint(h);
Lease.Release(h);
int v2 = Lease.Validate(h);
print(v1); print(th); print(v2);
""")
    assert out_ints(vm) == [1, 7, 0]


def test_lease_cached_validate_matches_validate():
    vm = run("""
int s = "x";
int h = Lease.Acquire(s, 1);
int v1 = Lease.Validate(h);
int v2 = Lease.CachedValidate(h);
print(v1); print(v2);
""")
    assert out_ints(vm) == [1, 1]


def test_lease_getspan_returns_acquired_span():
    vm = run("""
int s = "hello";
int h = Lease.Acquire(s, 0);
int got = Lease.GetSpan(h);
Io.Write(got);
""")
    assert out_bytes(vm) == b"hello"


# -- Fifo.* (real) -------------------------------------------------------------

def test_fifo_send_recv_roundtrip():
    vm = run("""
int ch = Fifo.Open(0);
int s = "abc";
Fifo.Send(ch, s);
int r = Fifo.Recv(ch);
Io.Write(r);
""")
    assert out_bytes(vm) == b"abc"


def test_fifo_poll_reports_depth():
    vm = run("""
int ch = Fifo.Open(0);
Fifo.Send(ch, "a");
Fifo.Send(ch, "b");
int d = Fifo.Poll(ch);
print(d);
""")
    assert out_ints(vm) == [2]


def test_fifo_recv_empty_returns_empty_span():
    vm = run("""
int ch = Fifo.Open(0);
int r = Fifo.Recv(ch);
Io.Write(r);
""")
    assert out_bytes(vm) == b""


def test_fifo_independent_channels():
    vm = run("""
int a = Fifo.Open(0);
int b = Fifo.Open(0);
Fifo.Send(a, "one");
int r = Fifo.Recv(b);
Io.Write(r);
""")
    assert out_bytes(vm) == b""  # channel b never got a message


# -- Pack.Use / Thread.YieldCounted (real) ------------------------------------

def test_pack_use_returns_selected_pack():
    vm = run("int r = Pack.Use(5); print(r);")
    assert out_ints(vm) == [5]


def test_thread_yield_counted_increments():
    vm = run("""
int a = Thread.YieldCounted();
int b = Thread.YieldCounted();
int c = Thread.YieldCounted();
print(a); print(b); print(c);
""")
    assert out_ints(vm) == [1, 2, 3]


# -- Kernel.* (real: Wait/Fire/Profile via Log) -------------------------------

def test_kernel_fireswirq_acks_and_logs():
    vm = run("Kernel.FireSWIRQ(3); int r = 1; print(r);")
    assert out_ints(vm) == [1]
    assert "raise swirq channel=3" in vm.host.log


def test_kernel_profile_start_end_use_log_table():
    vm = run("""
int s = "label";
int p = Kernel.ProfileStart(s);
Kernel.ProfileEnd(p);
int cnt = Log.Count();
print(cnt);
""")
    assert out_ints(vm) == [2]


def test_kernel_waitirq_halts_vm():
    vm = run("Kernel.WaitIRQ(); int r = 1; print(r);")
    assert vm.waiting is True
    assert out_ints(vm) == []  # halted before the print


# -- String.Split / String.Join (real, Map-backed) ----------------------------

def test_string_split_join_roundtrip():
    vm = run("""
int s = "a,b,c";
int delim = ",";
int parts = String.Split(s, delim);
int sep = "-";
int r = String.Join(sep, parts);
Io.Write(r);
""")
    assert out_bytes(vm) == b"a-b-c"


def test_string_split_does_not_disturb_caller_active_map():
    vm = run("""
int m = Map.New();
Map.PutII(1, 42);
int s = "x,y";
int delim = ",";
int parts = String.Split(s, delim);
Map.Use(m);
int v = Map.GetII(1);
print(v);
""")
    assert out_ints(vm) == [42]


def test_string_join_empty_delim_split_is_single_part():
    vm = run("""
int s = "abc";
int delim = "";
int parts = String.Split(s, delim);
int sep = "-";
int r = String.Join(sep, parts);
Io.Write(r);
""")
    assert out_bytes(vm) == b"abc"


# -- Reserved/host-injected namespaces: defined defaults, never a crash -------

def test_reserved_int_methods_return_zero():
    # Note: C-style source uses "Net." as reserved syntax sugar for the
    # native HTTP response-status/type framing (Net.Status/Type/Body/Close/
    # Header) -- a pre-existing, unrelated grammar special-case in
    # picoscript_cfront.py -- so Net.* (the reserved host namespace) is
    # exercised via the JS/Python-style dialects in the JS smoke coverage
    # instead; here we cover the other five reserved namespaces from C-style.
    vm = run("""
int a = Auth.ValidateCredentials(0, 0);
int b = Context.GetPort(0, 0);
int c = Environment.GetCpuCount(0, 0);
int e = Card.Read(0, 0);
int f = X509.IsCertValid(0, 0);
print(a); print(b); print(c); print(e); print(f);
""")
    assert out_ints(vm) == [0, 0, 0, 0, 0]


def test_reserved_span_methods_return_empty_span():
    vm = run("""
int a = Auth.GetToken(0, 0);
int b = Context.GetPath(0, 0);
int c = Environment.GetHostname(0, 0);
int d = X509.FetchCertificate(0, 0);
Io.Write(a); Io.Write(b); Io.Write(c); Io.Write(d);
""")
    assert out_bytes(vm) == b""


# -- Partial namespaces: unbuilt sub-features return defined defaults --------
# (Http.* live-connection ops are host-injected by design and remain stubbed.
# Html.* DOM tree ops used to be stubbed here too, but are now a real, pure
# primitive -- see tests/test_html_dom.py for full CreateNode/AddChildNode/
# RemoveChildNode/SetAttribute/GetAttribute/ParseTree/Serialize/QuerySelector
# coverage across all five execution paths. This test now verifies the real
# behavior of the exact same call sequence instead of stub defaults.)

def test_html_dom_ops_now_do_real_work():
    vm = run("""
int a = 0;
int h = Html.CreateNode(a, a);
int ok = Html.AddChildNode(h, a);
int attr = Html.GetAttribute(h, a);
int html = Html.Serialize(h);
Io.Write(attr); Io.Write(html);
print(h); print(ok);
""")
    # h = CreateNode(tag=span 0 -- the null/empty span) allocates a real,
    # non-zero node handle (1, first node in a fresh VM) -- no longer the old
    # stubbed 0. ok = AddChildNode(h, child=0) is correctly rejected (handle
    # 0 is never a valid node, same null convention as every other handle
    # table) -- still 0, but now because the child handle is genuinely
    # invalid, not because the whole namespace was unbuilt. attr = empty (no
    # attributes exist on a freshly created node) and html = empty (an
    # empty-tag node with no children serializes as a transparent, empty
    # fragment) -- both correctly empty for genuinely different, real
    # reasons than the old "unbuilt" stub.
    assert vm.output == [b"", b"", (1).to_bytes(4, "big"), (0).to_bytes(4, "big")]


def test_http_live_connection_ops_return_defined_defaults():
    vm = run("""
int a = 0;
int hdr = Http.ReadHeader(a, a);
int body = Http.ReadBody(a, a);
int req = Http.Request(a, a);
int status = Http.RespStatus(a, a);
Io.Write(hdr); Io.Write(body);
print(req); print(status);
""")
    assert vm.output == [b"", b"", (0).to_bytes(4, "big"), (0).to_bytes(4, "big")]

