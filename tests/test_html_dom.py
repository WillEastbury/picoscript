#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_html_dom.py -- Html.* real DOM tree ops: CreateNode/AddChildNode/
RemoveChildNode/SetAttribute/GetAttribute/ParseTree/Serialize/QuerySelector.

Part of the language-equivalence sweep (see docs/FEATURE_MATRIX.md and
docs/NAMESPACE_STATUS.md's "HTML DOM + HTTP parsing" section): these methods
were previously explicit 0/empty-span stubs (a full mutable tree model +
parser was "a separate, much larger feature" than the silent-fallthrough
bugs fixed elsewhere in that pass). They need NO host state (no host
identity/PKI/network/entropy the way Auth/X509/Net/Environment genuinely
do) -- a mutable node table + a minimal, permissive HTML parser is entirely
implementable in-VM, so (unlike those namespaces) this closes a REAL
capability gap rather than documenting a legitimate host-injected one.

Design (mirrors Descriptor/Lease/Fifo's precedent of adding a real, pure
primitive rather than stubbing further):
  - Node = {tag: span handle, attrs: {key: span handle}, children: [handle]}.
  - A node is a *text* node iff its attrs contain reserved key "#text" (its
    value span is the text content) -- CreateNode+SetAttribute alone can
    build one; ParseTree's internal builder uses the same convention for
    text runs. An empty tag with no "#text" is a transparent fragment/
    wrapper (used for ParseTree's synthetic multi-root wrapper).
  - SetAttribute packs "key=value" into rs2 (single span) since the 2-in/
    1-out host-hook ABI has no 3rd argument register (see
    docs/NAMESPACE_STATUS.md's "3-argument ops" section) -- same convention
    as String.SetReplace's separate-call pattern, just packed instead.
  - ParseTree/Serialize/QuerySelector are bounded to HTML_MAX_DEPTH (32)
    tree-walk depth on all 3 runtimes -- protects against a script-
    constructed cycle (AddChildNode has no cycle check, matching every
    other handle-table namespace's simplicity/determinism-over-
    defensiveness tradeoff); stops descending rather than faulting.
  - The C VM's node/attr/child tables are fixed-size (PV_MAX_HTML_NODES=64,
    PV_HTML_MAX_ATTRS=8, PV_HTML_MAX_CHILDREN=16), consistent with this
    embedded runtime's other handle tables (Map/Descriptor/Lease/Fifo/Log)
    -- a bounded, deterministic difference from Python/JS's unbounded
    dict-backed version, not a behavioral divergence at any realistic scale.

Verified byte-identical on all five execution paths: Python VM, JS VM
(vm/picovm.js), C VM interpreter (vm/picovm.c), native C transpile
(lower_to_c), and native JS transpile (lower_to_js) -- the last two just
forward generically to the same runtime dispatch (see
docs/FEATURE_MATRIX.md's introduction), so there is no separate
implementation to keep in sync, only to verify.

Uses ziglang (a pip-installed C compiler) and Node; marked "slow" by
conftest.py's ziglang detection -- run with `pytest --runslow`.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run_html_test.exe")
BUILD = os.path.join(ROOT, ".test_build_html_dom")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def parse_out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w & 0xFFFFFFFF:08x}" for w in words) + "\n"
    return parse_out_bytes(subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout)


def js_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w & 0xFFFFFFFF:08x}" for w in words) + "\n"
    return parse_out_bytes(subprocess.run(["node", os.path.join(VM_DIR, "picovm_run.js")],
                                          input=inp, capture_output=True, text=True).stdout)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c"); exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
                        f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    jsfile = os.path.join(BUILD, f"{slot}.js"); runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js'); const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2,'0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    words = lower_to_bytecode_safe(compile_c(prog))
    runs = {
        "Python VM": b"".join(PicoVM(max_steps=200000).run(list(words)).output),
        "JS VM": js_interp_out(words),
        "C interp": c_interp_out(words),
        "toC native": c_native_out(compile_c(prog), slot),
        "toJS native": js_native_out(compile_c(prog), slot),
    }
    for label, got in runs.items():
        assert got == expected, f"[{slot}] {label} {got!r} != {expected!r}"


def check_basic_node_building():
    check(
        'int root = Html.CreateNode("div");\n'
        'Html.SetAttribute(root, "id=main");\n'
        'int child = Html.CreateNode("span");\n'
        'Html.SetAttribute(child, "class=hi");\n'
        'Html.AddChildNode(root, child);\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<div id="main"><span class="hi"></span></div>', "basic_build",
    )


def check_remove_child_node():
    check(
        'int root = Html.CreateNode("div");\n'
        'int c1 = Html.CreateNode("a");\n'
        'int c2 = Html.CreateNode("b");\n'
        'Html.AddChildNode(root, c1);\n'
        'Html.AddChildNode(root, c2);\n'
        'Html.RemoveChildNode(root, c1);\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<div><b></b></div>', "remove_child",
    )


def check_parse_serialize_roundtrip():
    check(
        'int root = Html.ParseTree("<div id=\\"a\\"><p class=\\"x\\">Hello <b>World</b>!</p></div>");\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<div id="a"><p class="x">Hello <b>World</b>!</p></div>', "roundtrip",
    )


def check_void_and_self_closing_elements():
    check(
        'int root = Html.ParseTree("<div><br><img src=\\"x.png\\"/></div>");\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<div><br></br><img src="x.png"></img></div>', "void_elems",
    )


def check_get_attribute_not_found_returns_empty():
    check(
        'int root = Html.CreateNode("div");\n'
        'int v = Html.GetAttribute(root, "missing");\n'
        'Io.Write(v);\n'
        'Io.WriteByte(124);\n'
        'Io.WriteByte(Status.Last());\n',
        b'|\x01', "getattr_missing",
    )


def check_query_selector_by_tag_id_class():
    check(
        'int root = Html.ParseTree("<div><p id=\\"target\\">A</p><p class=\\"foo\\">B</p></div>");\n'
        'int byId = Html.QuerySelector(root, "#target");\n'
        'int byClass = Html.QuerySelector(root, ".foo");\n'
        'int byTag = Html.QuerySelector(root, "p");\n'
        'Io.Write(Html.GetAttribute(byId, "id"));\n'
        'Io.WriteByte(124);\n'
        'Io.Write(Html.GetAttribute(byClass, "class"));\n'
        'Io.WriteByte(124);\n'
        'Io.Write(Html.Serialize(byTag));\n',
        b'target|foo|<p id="target">A</p>', "query_selector",
    )


def check_query_selector_no_match_returns_null_handle():
    check(
        'int root = Html.CreateNode("div");\n'
        'int m = Html.QuerySelector(root, "#nope");\n'
        'Io.WriteByte(m);\n',
        bytes([0]), "query_no_match",
    )


def check_text_node_via_public_api():
    # CreateNode("") + SetAttribute(node, "#text=...") builds a text node
    # with the exact same primitives ParseTree's internal builder uses --
    # no separate "CreateTextNode" method needed (see this file's docstring).
    check(
        'int root = Html.CreateNode("div");\n'
        'int txt = Html.CreateNode("");\n'
        'Html.SetAttribute(txt, "#text=hello & <world>");\n'
        'Html.AddChildNode(root, txt);\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<div>hello &amp; &lt;world&gt;</div>', "text_node_public_api",
    )


def check_nested_tree_multiple_children():
    check(
        'int root = Html.CreateNode("ul");\n'
        'int i1 = Html.CreateNode("li"); Html.SetAttribute(i1, "id=one");\n'
        'int i2 = Html.CreateNode("li"); Html.SetAttribute(i2, "id=two");\n'
        'Html.AddChildNode(root, i1);\n'
        'Html.AddChildNode(root, i2);\n'
        'Io.Write(Html.Serialize(root));\n',
        b'<ul><li id="one"></li><li id="two"></li></ul>', "nested_tree",
    )


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picocompress.js", "picobrotli.js"):
        s = os.path.join(VM_DIR, dep)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(BUILD, dep))
    try:
        check_basic_node_building()
        check_remove_child_node()
        check_parse_serialize_roundtrip()
        check_void_and_self_closing_elements()
        check_get_attribute_not_found_returns_empty()
        check_query_selector_by_tag_id_class()
        check_query_selector_no_match_returns_null_handle()
        check_text_node_via_public_api()
        check_nested_tree_multiple_children()
        print("PASS: Html.* real DOM tree ops (CreateNode/AddChildNode/RemoveChildNode/"
              "SetAttribute/GetAttribute/ParseTree/Serialize/QuerySelector) byte-identical "
              "on all five runtimes (Python VM == JS VM == C interp == toC-native == "
              "toJS-native)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)
        if os.path.exists(VM_EXE):
            os.remove(VM_EXE)


def test_main():
    main()


if __name__ == "__main__":
    main()
