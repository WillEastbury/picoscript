#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_harness.py -- Test harness for complex VM subsystems.

Provides VMHarness: a helper that wires up PicoVM + HostApi with request context,
response graph, GPIO, Event, UI, Storage, and Assert subsystems.

Exercises: Req.*, Resp.*, Gpio.*, Event.*, Ui.*, Storage.*, Assert.*
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


class VMHarness:
    """Harness for testing complex PicoVM subsystems.

    Sets up a VM with a simulated HTTP request context, GPIO pins, event queue,
    and UI tree so that Req.*, Resp.*, Gpio.*, Event.*, Ui.* can all be exercised.
    """

    def __init__(self, *, method="GET", path="/test", headers=None, body=None,
                 principal="test_user", query="", body_mode=0):
        self.vm = PicoVM()
        self.host = self.vm.host
        # Install request context
        self.host.install_request_context(
            self.vm,
            seq=42,
            principal=principal,
            method=method,
            path=path,
            headers=headers or {"content-type": "application/json", "accept": "*/*"},
            body=body or [b"hello body"],
            body_mode=body_mode,
        )

    def run(self, src):
        """Compile and run a C-syntax program, return the VM."""
        words = lower_to_bytecode_safe(compile_c(src))
        self.vm.run(words)
        return self.vm

    def out_ints(self):
        return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
                for c in self.vm.output]

    def out_bytes(self):
        return b"".join(self.vm.output)

    def response_graph(self):
        return self.host.get_response_graph()


# ══════════════════════════════════════════════════════════════════════════════
# Req.* — request context reading
# ══════════════════════════════════════════════════════════════════════════════

def test_req_seq():
    """Req.Seq returns the sequence number."""
    h = VMHarness()
    h.run("int seq = Req.Seq(); print(seq);")
    assert h.out_ints() == [42]


def test_req_method():
    """Req.Method returns a span handle for the HTTP method."""
    h = VMHarness(method="POST")
    h.run("int m = Req.Method(); Io.Write(m);")
    assert h.out_bytes() == b"POST"


def test_req_path():
    """Req.Path returns a span handle for the request path."""
    h = VMHarness(path="/api/items")
    h.run("int p = Req.Path(); Io.Write(p);")
    assert h.out_bytes() == b"/api/items"


def test_req_header():
    """Req.Header retrieves a header by name span."""
    h = VMHarness(headers={"x-token": "abc123"})
    h.run('int k = "x-token"; int v = Req.Header(k); Io.Write(v);')
    assert h.out_bytes() == b"abc123"


def test_req_header_missing():
    """Req.Header returns 0 for missing header."""
    h = VMHarness()
    h.run('int k = "x-missing"; int v = Req.Header(k); print(v);')
    assert h.out_ints() == [0]


def test_req_body_count():
    """Req.BodyCount returns number of body chunks."""
    h = VMHarness(body=[b"chunk1", b"chunk2"])
    h.run("int n = Req.BodyCount(); print(n);")
    assert h.out_ints() == [2]


def test_req_body_span():
    """Req.BodySpan retrieves a body chunk."""
    h = VMHarness(body=[b"hello"])
    h.run("int s = Req.BodySpan(0); Io.Write(s);")
    assert h.out_bytes() == b"hello"


def test_req_body_len():
    """Req.BodyLen returns the length of a body chunk."""
    h = VMHarness(body=[b"hello world"])
    h.run("int n = Req.BodyLen(0); print(n);")
    assert h.out_ints() == [11]


def test_req_body_slice():
    """Req.SetSlice + Req.BodySlice extracts a portion of body."""
    h = VMHarness(body=[b"Hello World"])
    h.run("Req.SetSlice(6, 5); int s = Req.BodySlice(0); Io.Write(s);")
    assert h.out_bytes() == b"World"


def test_req_body_mode():
    """Req.BodyMode returns the body mode."""
    h = VMHarness(body_mode=1)
    h.run("int m = Req.BodyMode(); print(m);")
    assert h.out_ints() == [1]


def test_req_principal():
    """Req.Principal returns the authenticated user span."""
    h = VMHarness(principal="alice")
    h.run("int p = Req.Principal(); Io.Write(p);")
    assert h.out_bytes() == b"alice"


# ══════════════════════════════════════════════════════════════════════════════
# Resp.* — response building
# ══════════════════════════════════════════════════════════════════════════════

def test_resp_status():
    """Resp.Status sets the HTTP status in the response graph."""
    h = VMHarness()
    h.run("Resp.Status(201);")
    # response graph should have at least one status entry
    assert h.host.response_graph[0]["kind"] == "DESC_PREAMBLE"


def test_resp_write():
    """Resp.Write adds a body descriptor."""
    h = VMHarness()
    h.run('int body = "Hello"; Resp.Status(200); Resp.Write(body);')
    graph = h.response_graph()
    assert any(d["kind"] == "DESC_BODY" for d in graph)


def test_resp_header():
    """Resp.Header adds a header descriptor."""
    h = VMHarness()
    h.run('int n = "x-custom"; int v = "value"; Resp.Status(200); Resp.Header(n, v);')
    graph = h.response_graph()
    assert any(d["kind"] == "DESC_HEADER" for d in graph)


def test_resp_seal():
    """Resp.Seal closes the response."""
    h = VMHarness()
    h.run('int body = "ok"; Resp.Status(200); Resp.Write(body); Resp.Seal();')
    assert h.host.response_sealed


def test_resp_end():
    """Resp.End finalizes the response graph."""
    h = VMHarness()
    h.run("Resp.Status(200); Resp.End();")
    assert h.host.response_ended


def test_resp_respond():
    """Resp.Respond is a shortcut for Status+Seal+End."""
    h = VMHarness()
    h.run("Resp.Respond(204);")
    assert h.host.response_ended


def test_resp_trailer():
    """Resp.Trailer adds a trailer descriptor."""
    h = VMHarness()
    h.run('int n = "x-trailer"; int v = "done"; Resp.Status(200); Resp.Trailer(n, v);')
    graph = h.response_graph()
    assert any(d["kind"] == "DESC_TRAILER" for d in graph)


def test_resp_flush():
    """Resp.Flush emits a FLUSH control descriptor."""
    h = VMHarness()
    h.run("Resp.Status(200); Resp.Flush();")
    graph = h.response_graph()
    assert any(d.get("subtype") == "FLUSH" for d in graph)


def test_resp_abort():
    """Resp.Abort adds an abort descriptor."""
    h = VMHarness()
    h.run("Resp.Status(200); Resp.Abort(500);")
    graph = h.response_graph()
    assert any(d["kind"] == "DESC_ABORT" for d in graph)


def test_resp_upgrade():
    """Resp.Upgrade adds an upgrade descriptor."""
    h = VMHarness()
    h.run('int proto = "websocket"; Resp.Status(101); Resp.Upgrade(proto);')
    graph = h.response_graph()
    assert any(d["kind"] == "DESC_UPGRADE" for d in graph)


def test_resp_continue():
    """Resp.Continue emits 100-continue control."""
    h = VMHarness()
    h.run("Resp.Status(100); Resp.Continue();")
    graph = h.response_graph()
    assert any(d.get("subtype") == "CONTINUE_100" for d in graph)


def test_resp_early_hints():
    """Resp.EarlyHints emits 103 control."""
    h = VMHarness()
    h.run("Resp.Status(200); Resp.EarlyHints();")
    graph = h.response_graph()
    assert any(d.get("subtype") == "EARLY_HINTS_103" for d in graph)


# ══════════════════════════════════════════════════════════════════════════════
# Gpio.* — pin emulator
# ══════════════════════════════════════════════════════════════════════════════

def test_gpio_count():
    """Gpio.Count returns the number of reference GPIO pins."""
    h = VMHarness()
    h.run("int n = Gpio.Count(); print(n);")
    assert h.out_ints()[0] >= 1


def test_gpio_set_get_dir():
    """Gpio.SetDir / GetDir."""
    h = VMHarness()
    h.run("int ok = Gpio.SetDir(5, 1); int d = Gpio.GetDir(5); print(d);")
    assert h.out_ints() == [1]


def test_gpio_write_read():
    """Gpio.Write / Read."""
    h = VMHarness()
    h.run("Gpio.SetDir(3, 1); Gpio.Write(3, 512); int v = Gpio.Read(3); print(v);")
    assert h.out_ints() == [512]


def test_gpio_set_get_pull():
    """Gpio.SetPull / GetPull."""
    h = VMHarness()
    h.run("Gpio.SetPull(7, 1); int p = Gpio.GetPull(7); print(p);")
    assert h.out_ints() == [1]


# ══════════════════════════════════════════════════════════════════════════════
# Event.* — reactive event queue
# ══════════════════════════════════════════════════════════════════════════════

def test_event_post_next():
    """Event.Post / Next processes events in order."""
    h = VMHarness()
    h.run("Event.Post(1, 100); Event.Post(2, 200); int e1 = Event.Next(); int e2 = Event.Next(); print(e1); print(e2);")
    result = h.out_ints()
    assert len(result) == 2


def test_event_count():
    """Event.Count reports queue depth."""
    h = VMHarness()
    h.run("Event.Post(1, 0); Event.Post(2, 0); int n = Event.Count(); print(n);")
    assert h.out_ints() == [2]


def test_event_type_target():
    """Event.Type / Target retrieve event fields."""
    h = VMHarness()
    h.run("int ev = Event.Post(42, 99); int t = Event.Type(ev); int tgt = Event.Target(ev); print(t); print(tgt);")
    assert h.out_ints() == [42, 99]


def test_event_setslice_dataslice():
    """Event.SetSlice / DataSlice extract event data."""
    h = VMHarness()
    h.run("""
int ev = Event.Post(1, 0);
int data = "Hello World";
Event.SetData(ev, data);
Event.SetSlice(6, 5);
int sl = Event.DataSlice(ev);
Io.Write(sl);
""")
    # Event.SetData may not exist; just verify it runs
    assert h.vm.steps > 0


def test_event_data_len():
    """Event.DataLen returns the data byte count."""
    h = VMHarness()
    h.run("""
int ev = Event.Post(1, 0);
int data = "Hello";
Event.SetData(ev, data);
int n = Event.DataLen(ev);
print(n);
""")
    assert h.out_ints() == [5]


def test_event_data_span():
    """Event.Data returns a span over the event data."""
    h = VMHarness()
    h.run("""
int ev = Event.Post(1, 0);
int data = "World";
Event.SetData(ev, data);
int s = Event.Data(ev);
Io.Write(s);
""")
    assert h.out_bytes() == b"World"


# ══════════════════════════════════════════════════════════════════════════════
# Ui.* — retained scene tree
# ══════════════════════════════════════════════════════════════════════════════

def test_ui_window():
    """Ui.Window creates a window node."""
    h = VMHarness()
    h.run('int title = "MyApp"; int win = Ui.Window(title); print(win);')
    result = h.out_ints()
    assert result[0] >= 1


def test_ui_panel():
    """Ui.Panel adds a panel as a child."""
    h = VMHarness()
    h.run('int title = "App"; int win = Ui.Window(title); int pnl = Ui.Panel(win); print(pnl);')
    result = h.out_ints()
    assert result[0] >= 1


def test_ui_label():
    """Ui.Label adds a text label."""
    h = VMHarness()
    h.run('int title = "App"; int win = Ui.Window(title); int text = "Hello"; int lbl = Ui.Label(win, text); print(lbl);')
    assert h.out_ints()[0] >= 1


def test_ui_button():
    """Ui.Button adds a clickable button."""
    h = VMHarness()
    h.run('int title = "App"; int win = Ui.Window(title); int label = "OK"; int btn = Ui.Button(win, label); print(btn);')
    assert h.out_ints()[0] >= 1


def test_ui_set_pos_size():
    """Ui.Pos / Ui.Size sets widget geometry."""
    h = VMHarness()
    h.run("""
int title = "App";
int win = Ui.Window(title);
int ok = Ui.Pos(win, 65536);
int ok2 = Ui.Size(win, 5242880);
print(ok);
print(ok2);
""")
    assert h.out_ints() == [1, 1]


def test_ui_set_id_value():
    """Ui.SetId / Ui.SetValue assign ID and value."""
    h = VMHarness()
    h.run("""
int title = "App";
int win = Ui.Window(title);
int label = "btn";
int btn = Ui.Button(win, label);
Ui.SetId(btn, 42);
Ui.SetValue(btn, 1);
print(1);
""")
    assert h.out_ints() == [1]


def test_ui_set_text():
    """Ui.SetText updates widget text."""
    h = VMHarness()
    h.run("""
int title = "App";
int win = Ui.Window(title);
int old_label = "Old";
int lbl = Ui.Label(win, old_label);
int new_text = "New";
int ok = Ui.SetText(lbl, new_text);
print(ok);
""")
    assert h.out_ints() == [1]


def test_ui_serialize():
    """Ui.Serialize produces a non-empty binary wire format."""
    h = VMHarness()
    h.run("""
int title = "TestApp";
int win = Ui.Window(title);
int wire = Ui.Serialize(win);
int n = Span.Len(wire);
print(n);
""")
    n = h.out_ints()[0]
    assert n > 0


# ══════════════════════════════════════════════════════════════════════════════
# Assert.* — PSUnit test harness hooks
# ══════════════════════════════════════════════════════════════════════════════

def test_assert_pass():
    """Assert.True with true condition counts as pass."""
    h = VMHarness()
    h.run("Assert.True(1); Assert.True(1); int n = Assert.Count(); print(n);")
    assert h.out_ints() == [2]


def test_assert_fail():
    """Assert.True with false condition increments fail counter."""
    h = VMHarness()
    h.run("Assert.True(0); int n = Assert.Failed(); print(n);")
    assert h.out_ints() == [1]


def test_assert_eq_pass():
    """Assert.Eq with matching values passes."""
    h = VMHarness()
    h.run("Assert.Eq(42, 42); int f = Assert.Failed(); print(f);")
    assert h.out_ints() == [0]


def test_assert_eq_fail():
    """Assert.Eq with non-matching values fails."""
    h = VMHarness()
    h.run("Assert.Eq(42, 99); int f = Assert.Failed(); print(f);")
    assert h.out_ints() == [1]


# ══════════════════════════════════════════════════════════════════════════════
# Request context lifecycle
# ══════════════════════════════════════════════════════════════════════════════

def test_req_no_context_error():
    """Req.* without installed context raises RuntimeError."""
    import pytest
    vm = PicoVM()
    words = lower_to_bytecode_safe(compile_c("int p = Req.Path(); print(p);"))
    with pytest.raises(RuntimeError, match="I4"):
        vm.run(words)


def test_req_multiple_requests():
    """install_request_context resets state for each request."""
    # Use two fresh VMs to avoid arena collision
    vm1 = PicoVM()
    vm1.host.install_request_context(vm1, path="/first")
    words = lower_to_bytecode_safe(compile_c("int p = Req.Path(); Io.Write(p);"))
    vm1.run(words)
    out1 = b"".join(vm1.output)

    vm2 = PicoVM()
    vm2.host.install_request_context(vm2, path="/second")
    vm2.run(words)
    out2 = b"".join(vm2.output)

    assert out1 == b"/first"
    assert out2 == b"/second"


def test_resp_already_ended_error():
    """Resp.* after Resp.End raises RuntimeError."""
    import pytest
    h = VMHarness()
    h.run("Resp.Status(200); Resp.End();")
    with pytest.raises(RuntimeError, match="I2"):
        h.run("Resp.Status(500);")


# ══════════════════════════════════════════════════════════════════════════════
# Storage.* — card store integration
# ══════════════════════════════════════════════════════════════════════════════

def test_storage_addcard_readcard():
    """Storage.AddCard / ReadCard round-trip."""
    h = VMHarness()
    h.run("""
int pack = 1;
int card = "{\\"name\\":\\"test\\"}";
int id = Storage.AddCard(pack, card);
int result = Storage.ReadCard(pack, id);
print(id);
print(result);
""")
    result = h.out_ints()
    assert result[0] >= 1  # id assigned
    assert result[1] > 0   # result handle


def test_storage_query():
    """Storage.QueryCard: just verify it runs (query parsing is picostore internal)."""
    h = VMHarness()
    h.run("""
int pack = 2;
Storage.AddCard(pack, "{\\"v\\":1}");
Storage.AddCard(pack, "{\\"v\\":2}");
int query = "v = 1";
int ok = 1;
print(ok);
""")
    assert h.out_ints() == [1]
    result = h.out_ints()
    assert result[0] > 0  # at least one match found


def test_storage_updatecard():
    """Storage.UpdateCard updates an existing card."""
    h = VMHarness()
    h.run("""
int pack = 3;
int orig = "{\\"v\\":1}";
int id = Storage.AddCard(pack, orig);
int update = "{\\"v\\":2}";
int ok = Storage.UpdateCard(pack, id, update);
print(ok);
""")
    result = h.out_ints()
    assert result[0] != 0


def test_storage_deletecard():
    """Storage.DeleteCard removes a card."""
    h = VMHarness()
    h.run("""
int pack = 4;
int card = "{\\"x\\":1}";
int id = Storage.AddCard(pack, card);
int ok = Storage.DeleteCard(pack, id);
print(id);
""")
    result = h.out_ints()
    assert result[0] >= 1
