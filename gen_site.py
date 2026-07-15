#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_site.py -- build docs/index.html: the consolidated PicoScript GitHub Pages site.

One self-contained page (no server, no CDN) combining:
  * Language guide -- every construct in both styles, side by side
  * Playground -- compile / run / step PicoScript live in the browser (vm/picoc.js)
  * HTTP / TCP simulator -- paste a web request or hex bytes, send it to the running
    program; the request is exposed as a PicoWAL descriptor (cards in localStorage)
    and the program's Net.* + PRINT output is rendered as the HTTP response
  * Reference docs -- README / LANGUAGE_SPEC / COMPILER_ARCHITECTURE / editor contract,
    rendered from the repo markdown

PicoWAL is simulated with localStorage (cards persist across reloads). picoc.js,
picovm.js and pico_hooks.js are inlined so the page works from file:// or Pages.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import markdown

from gen_playground import CONSTRUCTS, build_example, _styles   # reuse verified gallery data
from picoscript_basic import compile_basic
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import PicoVM
from picoscript_lang import encode_card_addr, HOST_HOOK_CODES, NAMED_CONSTANTS

RESPONDER = """' HTTP responder -- reads the request descriptor from PicoWAL and replies.
' The simulator writes these cards before running the program:
'   (0,0,0)=request length  (0,0,1)=method  (0,0,2)=body length  (0,0,3)=checksum
LET LEN = 0
Storage.Load(0, 0, 0, LEN)
LET METHOD = 0
Storage.Load(0, 0, 1, METHOD)
LET BODYLEN = 0
Storage.Load(0, 0, 2, BODYLEN)
LET SUM = 0
Storage.Load(0, 0, 3, SUM)
IF METHOD EQ 2 THEN
    NET.STATUS(201)
ELSEIF LEN EQ 0 THEN
    NET.STATUS(400)
ELSE
    NET.STATUS(200)
ENDIF
NET.TYPE("application/json")
PRINT LEN
PRINT BODYLEN
PRINT SUM
RETURN
"""

DOC_FILES = [
    ("readme", "README", os.path.join(ROOT, "README.md")),
    ("tutorial", "Tutorial", os.path.join(ROOT, "docs", "TUTORIAL.md")),
    ("primitives", "Primitive Guide", os.path.join(ROOT, "docs", "PRIMITIVES.md")),
    ("methods", "Method Reference", os.path.join(ROOT, "docs", "METHOD_REFERENCE.md")),
    ("constants", "Named Constants", os.path.join(ROOT, "docs", "NAMED_CONSTANTS.md")),
    ("spec", "Language Spec", os.path.join(ROOT, "LANGUAGE_SPEC.md")),
    ("arch", "Compiler Architecture", os.path.join(ROOT, "docs", "COMPILER_ARCHITECTURE.md")),
    ("editor", "Editor Contract", os.path.join(ROOT, "docs", "picoscript-language-editor.md")),
    ("agent", "Agent Prompt", os.path.join(ROOT, "docs", "AGENT_PROMPT.md")),
    ("selfhost", "Self-Hosting", os.path.join(ROOT, "docs", "SELF_HOSTING.md")),
    ("iobinding", "PIOS I/O Binding", os.path.join(ROOT, "docs", "PIOS_IO_BINDING.md")),
    ("iointegration", "PIOS Integration", os.path.join(ROOT, "docs", "PIOS_IO_INTEGRATION.md")),
    ("systems", "Systems Language", os.path.join(ROOT, "docs", "SYSTEMS_LANGUAGE.md")),
    ("strings", "Strings & Templates", os.path.join(ROOT, "docs", "STRING_TEMPLATES.md")),
    ("nsstatus", "Namespace Status", os.path.join(ROOT, "docs", "NAMESPACE_STATUS.md")),
    ("internals", "Internals", os.path.join(ROOT, "docs", "INTERNALS.md")),
]


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def verify_responder():
    words = lower_to_bytecode_safe(compile_basic(RESPONDER))
    vm = PicoVM()
    vm.cards[encode_card_addr(0, 0, 0)] = 40
    vm.cards[encode_card_addr(0, 0, 1)] = 2
    vm.cards[encode_card_addr(0, 0, 2)] = 11
    vm.cards[encode_card_addr(0, 0, 3)] = 1234
    vm.run(words)
    out = [s32(int.from_bytes(b, "big")) for b in vm.output]
    assert vm.http_status == 201 and out == [40, 11, 1234], (vm.http_status, out)


def render_docs():
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists"])
    panels = []
    for key, _title, path in DOC_FILES:
        md.reset()
        text = open(path, encoding="utf-8").read()
        html = md.convert(text)
        panels.append((key, html))
    nav = "".join(
        f'<button class="docnav-btn{" active" if i == 0 else ""}" '
        f'onclick="showDoc(\'{key}\')" data-doc="{key}">{title}</button>'
        for i, (key, title, _p) in enumerate(DOC_FILES)
    )
    body = "".join(
        f'<div class="docpanel{" active" if i == 0 else ""}" id="doc-{key}">{html}</div>'
        for i, (key, html) in enumerate(panels)
    )
    return nav, body


def build_namespace_data():
    """Namespace -> method list from HOST_HOOK_CODES, tagged implemented vs planned.

    A hook is 'implemented' only if the VM actually dispatches it (unimplemented ones
    fall through to an 'unknown host' log entry). Codes >0xFF are dispatched via the
    extended base (imm16 = EXT_HOST_HOOK_BASE | (code & 0xFFF)), so they are real
    hooks too -- not excluded."""
    from picoscript_il import ILBuilder, Imm, lower_to_bytecode_safe
    from picoscript_vm import PicoVM

    def is_impl(ns, method, code):
        b = ILBuilder()
        d = b.vreg()
        b.host(ns, method, (Imm(0), Imm(0)), dst=d)
        try:
            vm = PicoVM().run(lower_to_bytecode_safe(b.insts))
        except Exception:
            return True   # reached a handler that executed (and may have raised on dummy args)
        return not any(f"host {ns}.{method}" in m for m in vm.host.log)

    ns_map = {}
    for (namespace, method), code in sorted(HOST_HOOK_CODES.items(), key=lambda x: x[1]):
        ns_map.setdefault(namespace, []).append(
            {"method": method, "code": f"0x{code:02X}", "impl": is_impl(namespace, method, code)})
    return ns_map


def main():
    verify_responder()
    gallery = []
    for c in CONSTRUCTS:
        t, d, srcs = _styles(c)
        gallery.append({"title": t, "desc": d, **build_example(srcs)})
    docnav, docbody = render_docs()
    ns_data = build_namespace_data()

    hooks_js = open(os.path.join(ROOT, "vm", "pico_hooks.js"), encoding="utf-8").read()
    vm_js = open(os.path.join(ROOT, "vm", "picovm.js"), encoding="utf-8").read()
    picoc_js = open(os.path.join(ROOT, "vm", "picoc.js"), encoding="utf-8").read()
    ser_js = open(os.path.join(ROOT, "vm", "picoserializer.js"), encoding="utf-8").read()
    store_js = open(os.path.join(ROOT, "vm", "picostore.js"), encoding="utf-8").read()
    pcz_js = open(os.path.join(ROOT, "vm", "picocompress.js"), encoding="utf-8").read()
    pbz_js = open(os.path.join(ROOT, "vm", "picobrotli.js"), encoding="utf-8").read()
    # Reusable browser modules are the single source of truth in BareMetalJsTools;
    # picoscript vendors them into vm/vendor/ (see tools/vendor_baremetal.py).
    wf_js = open(os.path.join(ROOT, "vm", "vendor", "BareMetal.WorkflowPico.js"), encoding="utf-8").read()
    layout_js = open(os.path.join(ROOT, "vm", "vendor", "BareMetal.Report.js"), encoding="utf-8").read()
    dd_js = open(os.path.join(ROOT, "vm", "vendor", "BareMetal.DragDrop.js"), encoding="utf-8").read()
    flow_js = open(os.path.join(ROOT, "vm", "vendor", "BareMetal.Workflow.js"), encoding="utf-8").read()
    bus_js = open(os.path.join(ROOT, "vm", "vendor", "BareMetal.PubSub.js"), encoding="utf-8").read()

    html = PAGE
    html = html.replace("/*__HOOKS__*/", hooks_js)
    html = html.replace("/*__PCZ__*/", pcz_js)
    html = html.replace("/*__PBZ__*/", pbz_js)
    html = html.replace("/*__VM__*/", vm_js)
    html = html.replace("/*__PICOC__*/", picoc_js)
    html = html.replace("/*__WF__*/", wf_js)
    html = html.replace("/*__LAYOUT__*/", layout_js)
    html = html.replace("/*__DD__*/", dd_js)
    html = html.replace("/*__FLOW__*/", flow_js)
    html = html.replace("/*__BUS__*/", bus_js)
    html = html.replace("/*__SER__*/", ser_js)
    html = html.replace("/*__STORE__*/", store_js)
    html = html.replace("/*__DATA__*/", json.dumps(gallery))
    html = html.replace("/*__NSDATA__*/", json.dumps(ns_data))
    html = html.replace("/*__CONSTANTS__*/", json.dumps(sorted(NAMED_CONSTANTS.keys())))
    html = html.replace("/*__RESPONDER__*/", json.dumps(RESPONDER))
    html = html.replace("<!--__DOCNAV__-->", docnav)
    html = html.replace("<!--__DOCBODY__-->", docbody)

    out_path = os.path.join(ROOT, "docs", "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    # ensure GitHub Pages serves raw assets (no Jekyll)
    open(os.path.join(ROOT, "docs", ".nojekyll"), "w").close()
    print(f"wrote {out_path} ({len(html)//1024} KB, {len(gallery)} constructs, {len(DOC_FILES)} docs)")
    print("wrote docs/.nojekyll")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PicoScript &mdash; IDE, Guide &amp; Reference</title>
<style>
  :root { --accent:#667eea; --bg:#0f1117; --panel:#1a1d27; --panel2:#232734;
          --text:#e6e8ef; --muted:#9aa0ad; --c:#7ee787; --b:#79c0ff; --py:#ffd866; --en:#f0a3ff; --cob:#ff9f7f; --rpt:#66e0cc; --fn:#a0d0ff;
          --warn:#ffd866; --err:#ff7b72; --sidebar-w:220px; --flyout-w:380px; }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; overflow:hidden; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--text); display:flex; flex-direction:column; }
  a { color:var(--b); }
  .topbar { display:flex; align-items:center; gap:10px; padding:6px 14px; background:var(--panel2);
            border-bottom:1px solid #2c313f; flex-shrink:0; z-index:30; }
  .topbar h1 { font-size:15px; color:var(--accent); white-space:nowrap; }
  .tabs { display:flex; gap:2px; }
  .tab { border:none; background:none; color:var(--muted); padding:6px 12px; font-size:12px;
    font-weight:600; cursor:pointer; border-radius:4px; }
  .tab.active { background:var(--accent); color:#fff; }
  .tab:hover:not(.active) { background:#2c313f; }
  .lang-toggle { display:flex; gap:2px; background:#0c0e14; border-radius:6px; padding:2px; margin-left:auto; }
  .lang-toggle button { border:none; background:none; color:var(--muted); padding:4px 10px;
    border-radius:4px; font-size:11px; font-weight:600; cursor:pointer; }
  .lang-toggle button.active { color:#fff; }
  .lang-toggle button[data-lang="c"].active { background:var(--c); color:#0f1117; }
  .lang-toggle button[data-lang="basic"].active { background:var(--b); color:#0f1117; }
  .lang-toggle button[data-lang="python"].active { background:var(--py); color:#0f1117; }
  .lang-toggle button[data-lang="english"].active { background:var(--en); color:#0f1117; }
  .lang-toggle button[data-lang="cobol"].active { background:var(--cob); color:#0f1117; }
  .lang-toggle button[data-lang="report"].active { background:var(--rpt); color:#0f1117; }
  .pill { display:inline-block; padding:2px 7px; border-radius:10px; font-size:10px; font-weight:600;
          background:#2c313f; color:var(--muted); }
  .main { display:flex; flex:1; overflow:hidden; position:relative; }
  .main.tool-pinned .view.active { padding-right:var(--flyout-w); }
  .view { display:none; flex:1; overflow:hidden; flex-direction:column; }
  .view.active { display:flex; }
  .sidebar { width:var(--sidebar-w); background:var(--panel); border-right:1px solid #2c313f;
             overflow-y:auto; flex-shrink:0; padding:8px 0; }
  .sidebar .group-title { font-size:10px; text-transform:uppercase; letter-spacing:.08em;
    color:var(--muted); padding:8px 12px 3px; font-weight:700; }
  .sidebar .tree-item { padding:5px 12px 5px 18px; font-size:11.5px; cursor:pointer;
    color:var(--text); border-left:3px solid transparent; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }
  .sidebar .tree-item:hover { background:var(--panel2); }
  .sidebar .tree-item.active { border-left-color:var(--accent); background:var(--panel2); color:var(--accent); }
  .card-area { flex:1; overflow-y:auto; padding:18px 24px; }
  .card-area .card-title { font-size:17px; font-weight:700; margin-bottom:4px; }
  .card-area .card-desc { color:var(--muted); font-size:12.5px; margin-bottom:12px; max-width:700px; }
  .card-area pre { background:#0c0e14; border:1px solid #2c313f; border-radius:6px;
    padding:12px; font-family:"SF Mono",Consolas,monospace; font-size:12px; line-height:1.55;
    overflow-x:auto; max-width:700px; }
  .card-area pre.cstyle { color:#cde9c8; border-left:3px solid var(--c); }
  .card-area pre.bstyle { color:#cfe4ff; border-left:3px solid var(--b); }
  .card-area pre.pystyle { color:#f5e6a8; border-left:3px solid var(--py); }
  .card-area pre.enstyle { color:#f3d4ff; border-left:3px solid var(--en); }
  .card-area pre.cobstyle { color:#ffd4c2; border-left:3px solid var(--cob); }
  .card-area pre.rptstyle { color:#c2f5e9; border-left:3px solid var(--rpt); }
  .card-area pre.fnstyle { color:#c8e4ff; border-left:3px solid var(--fn); }
  .run-area { display:flex; align-items:center; gap:8px; margin-top:10px; }
  .out { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:var(--warn); }
  button.act { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:6px 12px;
    font-weight:600; cursor:pointer; font-size:12px; }
  button.ghost { background:#2c313f; color:var(--text); border:none; border-radius:6px; padding:6px 12px;
    font-weight:600; cursor:pointer; font-size:12px; }
  button.act:hover,button.ghost:hover { filter:brightness(1.12); }
  select,textarea,input { background:#0c0e14; color:var(--text); border:1px solid #2c313f;
    border-radius:6px; padding:6px 8px; font-family:inherit; font-size:12px; }
  textarea { font-family:"SF Mono",Consolas,monospace; width:100%; resize:vertical; }
  .ide-wrap { display:flex; flex:1; overflow:hidden; }
  .ide-editor { flex:1; display:flex; flex-direction:column; padding:10px 14px; overflow:hidden; }
  .ide-editor textarea { flex:1; min-height:80px; }
  .ide-editor .controls { display:flex; gap:6px; margin:6px 0; flex-wrap:wrap; align-items:center; }
  .file-sidebar { width:230px; background:var(--panel); border-right:1px solid #2c313f;
    flex-shrink:0; display:flex; flex-direction:column; overflow:hidden; }
  .file-sidebar.collapsed { width:36px; }
  .file-sidebar.collapsed .file-body { display:none; }
  .file-head { display:flex; align-items:center; gap:6px; padding:8px 10px; border-bottom:1px solid #2c313f;
    color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; font-weight:700; }
  .file-head button { margin-left:auto; background:none; border:none; color:var(--muted); cursor:pointer; }
  .file-body { padding:8px; overflow:auto; display:flex; flex-direction:column; gap:8px; }
  .file-actions { display:flex; gap:5px; flex-wrap:wrap; }
  .file-actions button { padding:4px 8px; font-size:10.5px; }
  .file-list { display:flex; flex-direction:column; gap:3px; }
  .file-item { border:1px solid transparent; border-radius:6px; padding:6px 7px; cursor:pointer; background:#0c0e14; }
  .file-item:hover { border-color:#2c313f; background:var(--panel2); }
  .file-item.active { border-color:var(--accent); background:#20263a; }
  .file-row { display:flex; align-items:center; gap:5px; min-width:0; }
  .file-name { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; font-size:12px; }
  .file-dirty { color:var(--warn); width:10px; text-align:center; font-weight:700; }
  .file-badge { font-size:9px; font-weight:700; border-radius:8px; padding:1px 5px; color:#0f1117; }
  .file-badge.c { background:var(--c); } .file-badge.basic { background:var(--b); }
  .file-badge.python { background:var(--py); } .file-badge.english { background:var(--en); }
  .file-badge.k-card { background:#66e0cc; } .file-badge.k-source { background:#ffd866; }
  .file-badge.k-schema { background:#b0a0ff; } .file-badge.k-static { background:#9aa0ad; } .file-badge.k-code { background:var(--accent); color:#fff; }
  .file-badge.k-event { background:#ff9f7f; } .file-badge.k-ontology { background:#f0a3ff; }
  .file-ico { font-size:11px; width:14px; text-align:center; flex-shrink:0; }
  .file-folder { display:flex; align-items:center; gap:4px; padding:3px 5px; cursor:pointer; color:var(--muted); font-size:11.5px; font-weight:600; user-select:none; }
  .file-folder:hover { color:#e6e8ef; }
  .file-fold-caret { width:10px; text-align:center; }
  .file-meta { color:var(--muted); font-size:10px; margin-top:3px; }
  .file-empty,.file-status { color:var(--muted); font-size:11px; line-height:1.4; }
  .cerr { font-family:monospace; font-size:11px; min-height:14px; }
  .dbg-bar { display:flex; gap:2px; background:var(--panel2); border-top:1px solid #2c313f;
    padding:0 10px; flex-shrink:0; }
  .dbg-bar button { border:none; background:none; color:var(--muted); padding:6px 10px;
    font-size:11px; font-weight:600; cursor:pointer; border-bottom:2px solid transparent; }
  .dbg-bar button.active { color:var(--accent); border-bottom-color:var(--accent); }
  .dbg-panels { background:#11141c; border-top:1px solid #2c313f; overflow:hidden;
    transition:max-height .2s; flex-shrink:0; }
  .dbg-panels.collapsed { max-height:0 !important; }
  .dbg-panel { display:none; padding:8px 14px; overflow:auto; max-height:200px; }
  .dbg-panel.active { display:block; }
  .dbg-panels.pinned-mode .dbg-panel.pinned { display:block; border-top:1px solid #2c313f; }
  .dbg-pin { opacity:.65; padding:6px 5px !important; }
  .dbg-pin.active { color:var(--warn) !important; border-bottom-color:var(--warn) !important; opacity:1; }
  .dbg-size { margin-left:8px; width:auto; padding:3px 6px; font-size:11px; }
  .listing { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; max-height:170px;
    overflow:auto; font-family:"SF Mono",Consolas,monospace; font-size:11.5px; }
  .listing .row { padding:2px 10px; white-space:pre; color:var(--muted); }
  .listing .row.pc { background:#2d3550; color:#fff; }
  .listing .row.bp { color:#ffd866; cursor:pointer; }
  .listing .row.bp:before { content:'● '; color:#ff7b72; }
  .listing .row:not(.bp):before { content:'  '; }
  .source-active-line { background:rgba(102,126,234,.22); }
  .source-breakpoint-line { background:rgba(255,123,114,.12); }
  .breakpoint-glyph { background:#ff7b72; border-radius:50%; width:9px !important; height:9px !important; margin-left:5px; margin-top:4px; }
  .regs { display:grid; grid-template-columns:repeat(4,1fr); gap:2px 8px; font-family:"SF Mono",monospace; font-size:11.5px; }
  .regs .r { color:var(--muted); } .regs .r b { color:var(--text); }
  .debug-grid { display:grid; grid-template-columns:2fr 1fr 1.3fr; gap:10px; align-items:start; }
  .debug-section h4 { margin:0 0 6px; font-size:11px; color:var(--accent); text-transform:uppercase; letter-spacing:.06em; }
  .state { font-family:"SF Mono",Consolas,monospace; font-size:11px; color:var(--muted); margin-top:4px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  .wal td,.wal th { border:1px solid #2c313f; padding:3px 8px; text-align:left; font-family:"SF Mono",monospace; }
  .wal th { background:var(--panel2); color:var(--muted); }
  .flyout-overlay { display:none; position:absolute; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,.4); z-index:40; }
  .flyout-overlay.open { display:block; }
  .tool-panel { position:absolute; top:0; right:0; width:var(--flyout-w); height:100%;
    background:var(--panel); border-left:1px solid #2c313f; z-index:50;
    overflow:hidden; padding:0; display:none; flex-direction:column; box-shadow:-8px 0 24px rgba(0,0,0,.25); }
  .tool-panel.open { display:flex; }
  .tool-head { display:flex; align-items:center; gap:6px; padding:8px; border-bottom:1px solid #2c313f; background:var(--panel2); flex-shrink:0; }
  .tool-tabs { display:flex; gap:2px; flex:1; min-width:0; overflow:auto; }
  .tool-tabs button { border:none; background:none; color:var(--muted); padding:5px 8px; border-radius:4px;
    font-size:11px; font-weight:700; cursor:pointer; white-space:nowrap; }
  .tool-tabs button.active { background:var(--accent); color:#fff; }
  .tool-actions { display:flex; gap:4px; flex-shrink:0; }
  .tool-actions button { border:1px solid #2c313f; background:#0c0e14; color:var(--muted); border-radius:5px;
    padding:4px 7px; font-size:13px; line-height:1; cursor:pointer; min-width:28px; }
  .tool-actions button.active { color:#fff; border-color:var(--accent); background:#20263a; }
  .tool-body { flex:1; min-height:0; overflow:auto; padding:14px 16px; }
  .tool-tab { display:none; flex-direction:column; min-height:100%; }
  .tool-tab.active { display:flex; }
  .tool-tab h3 { font-size:14px; color:var(--accent); margin-bottom:10px; }
  .flyout-trigger { display:none; gap:4px; position:absolute; right:0; top:50%; transform:translateY(-50%);
    z-index:35; flex-direction:column; }
  .flyout-trigger button { writing-mode:vertical-rl; text-orientation:mixed; border:none;
    background:var(--panel2); color:var(--muted); padding:8px 5px; font-size:10px; font-weight:600;
    cursor:pointer; border-radius:6px 0 0 6px; border:1px solid #2c313f; border-right:none; }
  .flyout-trigger button:hover { background:var(--accent); color:#fff; }
  .respbox { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; padding:10px;
    font-family:"SF Mono",Consolas,monospace; font-size:12px; white-space:pre-wrap; min-height:80px; color:var(--c); }
  .desc-table td { border:1px solid #2c313f; padding:3px 8px; font-family:"SF Mono",monospace; font-size:11px; }
  .ref-wrap { display:flex; flex:1; overflow:hidden; }
  .ref-content { flex:1; overflow-y:auto; padding:18px 24px; }
  .ref-section { display:none; }
  .ref-section.active { display:block; }
  .wf-designer { margin:6px 0; border:1px solid #2c313f; border-radius:6px; padding:6px; background:#0c0e14; }
  .wf-designer .wf-add { display:flex; gap:6px; align-items:center; margin-bottom:6px; flex-wrap:wrap; }
  .wf-modal { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:200; display:none; align-items:center; justify-content:center; }
  .wf-modal.open { display:flex; }
  .wf-modal-box { background:var(--panel); border:1px solid #2c313f; border-radius:10px; width:min(680px,92vw); max-height:82vh; display:flex; flex-direction:column; overflow:hidden; }
  .wf-modal-head { display:flex; align-items:center; gap:8px; padding:10px 14px; border-bottom:1px solid #2c313f; font-weight:700; }
  .wf-modal-head button { margin-left:auto; }
  .wf-modal-body { padding:12px; overflow:auto; }
  .wf-modal-body textarea { width:100%; min-height:340px; box-sizing:border-box; background:#0c0e14; border:1px solid #2c313f; border-radius:6px; color:#e6e8ef; font:12px/1.4 monospace; padding:8px; }
  .wf-modal-foot { display:flex; gap:8px; padding:10px 14px; border-top:1px solid #2c313f; }
  #wfFlow .fc-node.wf-active > .fc-head, #wfFlow .fc-node.wf-active:not(.fc-block) { box-shadow:0 0 0 2px #ffd866, 0 0 14px rgba(255,216,102,.55) !important; border-radius:8px; }
  #wfFlow .fc-node .fc-type { cursor:pointer; }
  #wfFlow .fc-node .fc-type::before { content:'\25CB'; color:#5a6072; margin-right:5px; font-size:9px; vertical-align:middle; }
  #wfFlow .fc-node.wf-bp .fc-type::before { content:'\25CF'; color:#ff5c5c; }
  .wf-row { display:flex; gap:8px; align-items:center; padding:3px 6px; margin:3px 0; border:1px solid #2c313f;
    border-radius:6px; background:#11141c; }
  .wf-badge { min-width:70px; font-weight:700; font-size:11px; color:var(--accent); }
  .wf-sum { flex:1 1 auto; font-family:"SF Mono",Consolas,monospace; font-size:12px; word-break:break-word; }
  .wf-acts button { padding:1px 6px; font-size:12px; }
  .wf-eng-h { font-size:11px; color:var(--muted); margin:8px 0 2px; }
  .wf-eng { font-family:"SF Mono",Consolas,monospace; font-size:12px; background:#0a0c12; border:1px solid #2c313f;
    border-radius:6px; padding:6px; margin:0; white-space:pre-wrap; color:#9fb0c8; }
  .wf-warn { font-size:11px; color:var(--warn); margin-top:4px; }
  .layout-preview { margin:8px 0; padding:8px; border:1px solid #2c313f; border-radius:6px; background:#fff; color:#111; overflow:auto; }
  .layout-preview table.pico-report { border-collapse:collapse; font-size:13px; }
  .layout-preview table.pico-report th, .layout-preview table.pico-report td { border:1px solid #ccc; padding:2px 8px; text-align:left; }
  .layout-preview table.pico-report tfoot td { font-weight:700; background:#f3f3f3; }
  .layout-preview .pico-form-row { display:flex; gap:10px; flex-wrap:wrap; margin:4px 0; }
  .layout-preview .pico-field { display:flex; flex-direction:column; font-size:12px; }
  .layout-preview .pico-field input { border:1px solid #bbb; border-radius:4px; padding:2px 4px; }
  .layout-text { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:#9fb0c8; white-space:pre-wrap; margin:0; }
  .docnav { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:12px; }
  .docnav-btn { background:#2c313f; color:var(--muted); border:none; border-radius:6px; padding:6px 10px; font-weight:600; cursor:pointer; font-size:11.5px; }
  .docnav-btn.active { background:var(--accent); color:#fff; }
  .docpanel { display:none; background:var(--panel); border:1px solid #2c313f; border-radius:8px; padding:6px 22px 22px; }
  .docpanel.active { display:block; }
  .docpanel h1,.docpanel h2,.docpanel h3 { color:var(--accent); }
  .docpanel h1 { font-size:22px; border-bottom:1px solid #2c313f; padding-bottom:8px; }
  .docpanel h2 { font-size:16px; margin-top:20px; }
  .docpanel code { background:#0c0e14; padding:1px 5px; border-radius:4px; font-size:12px; }
  .docpanel pre { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; padding:10px; }
  .docpanel pre code { background:none; padding:0; }
  .docpanel table { margin:12px 0; } .docpanel td,.docpanel th { border:1px solid #2c313f; padding:5px 9px; }
  .docpanel th { background:var(--panel2); }
  .ns-card { background:var(--panel); border:1px solid #2c313f; border-radius:8px; padding:12px 16px; margin-bottom:12px; }
  .ns-card h4 { color:var(--accent); margin-bottom:6px; font-size:14px; }
  .ns-card .method { font-family:"SF Mono",monospace; font-size:12px; color:var(--text); padding:2px 0; }
  .ns-card .method .code { color:var(--muted); font-size:11px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:700;
    background:#3d2b00; color:#ffd866; margin-left:8px; }
  ::-webkit-scrollbar { width:8px; height:8px; } ::-webkit-scrollbar-thumb { background:#3a4150; border-radius:4px; }
  @media (max-width:900px){ .sidebar{width:180px;} .tool-panel{width:320px;} .main.tool-pinned .view.active{padding-right:320px;} .regs{grid-template-columns:repeat(2,1fr);} .debug-grid{grid-template-columns:1fr;} }
</style>
</head>
<body>
<div class="topbar">
  <h1>PicoScript</h1>
  <div class="tabs">
    <button class="tab active" onclick="showView('guide')">Guide &amp; Reference</button>
    <button class="tab" onclick="showView('play')">WebIDE</button>
    <a class="tab" href="showcase.html" style="text-decoration:none">&#128295; Showcase</a>
  </div>
  <div class="lang-toggle" id="langToggle">
    <button data-lang="c" class="active" onclick="setLang('c')">C &#123;&#125;</button>
    <button data-lang="basic" onclick="setLang('basic')">BASIC</button>
    <button data-lang="python" onclick="setLang('python')">Python</button>
    <button data-lang="english" onclick="setLang('english')">English</button>
    <button data-lang="cobol" onclick="setLang('cobol')">COBOL</button>
    <button data-lang="report" onclick="setLang('report')">Report</button>
    <button data-lang="workflow" onclick="setLang('workflow')">Workflow</button>
  </div>
</div>
<div class="main">
  <!-- GUIDE VIEW -->
  <div class="view active" id="view-guide">
    <div style="display:flex;flex:1;overflow:hidden">
      <div class="sidebar" id="guideTree"></div>
      <div class="card-area" id="guideContent">
        <!-- Reference sections are rendered here when clicked from sidebar -->
        <div class="ref-inline" id="refInlineContent" style="display:none">
          <div class="ref-section" id="ref-overview" style="display:none">
            <h2 style="color:var(--accent);margin-bottom:10px">Overview</h2>
            <p style="color:var(--muted);font-size:13px;max-width:750px;line-height:1.7">
              <b>PicoScript</b> compiles from five interchangeable surface syntaxes (C, BASIC, Python, English, COBOL)
              plus two visual designers (Workflow, Report) that lower to English
              to a single 16-opcode 32-bit bytecode. Programs run on embedded VMs, can be AOT-compiled to C or JS,
              and are designed for request-response workloads on bare-metal or browser runtimes.<br><br>
              <b>Why?</b> One semantic model, seven reading styles. A diverse team picks the syntax they know and
              produces byte-identical output. The compiler is ~10KB JS, zero dependencies.<br><br>
              <b>Use case:</b> Sandboxed, deterministic compute capsules that handle HTTP requests, store data in
              PicoWAL cards, and produce responses with memory safety enforced by the kernel.
            </p>
          </div>
          <div class="ref-section" id="ref-syntax" style="display:none"><h2 style="color:var(--accent);margin-bottom:10px">Language Syntax</h2><div id="syntaxContent"></div></div>
          <div class="ref-section" id="ref-namespaces" style="display:none"><h2 style="color:var(--accent);margin-bottom:10px">Namespaces &amp; Methods</h2><p style="color:var(--muted);font-size:12px;margin-bottom:14px">Every built-in namespace and method. Call as <code>Namespace.Method(args)</code>.</p><div id="nsContent"></div></div>
          <div class="ref-section" id="ref-bindings" style="display:none">
            <h2 style="color:var(--accent);margin-bottom:10px">Bindings &amp; I/O</h2>
            <div style="color:var(--muted);font-size:13px;max-width:750px;line-height:1.7">
              <p><b>Network binding:</b> A capsule is bound to a port by the kernel. The kernel handles TCP/TLS, parses HTTP, and delivers a request descriptor via FIFO. Read with <code>Req.*</code>, respond with <code>Resp.*</code>.</p>
              <p style="margin-top:10px"><b>PicoWAL cards:</b> Persistent card packs via PicoBinarySerializer. Use <code>Storage.UsePack/AddCard/SetField/QueryCard</code>.</p>
              <p style="margin-top:10px"><b>FIFO:</b> Inbound (request descriptors) and outbound (response graph). All inter-capsule communication is kernel-mediated.</p>
              <p style="margin-top:10px"><b>Zero-copy:</b> The kernel manages memory via descriptors and leases. <code>Lease.Acquire/GetSpan</code> reads without copying.</p>
              <p style="margin-top:14px"><b>GPIO (devices):</b> Pins carry an analog value in [0,1024]. Hooks <code>Gpio.SetDir/GetDir/SetPull/GetPull/Write/Read/Count</code>.</p>
              <p style="margin-top:10px"><b>Cards / storage DSL:</b> BASIC: <b>STORE</b> writes, <b>LOAD</b> reads. Typed pack schemas via the playground schema designer.</p>
              <p style="margin-top:10px"><b>Server entry:</b> <code>Server.Main { ... }</code> (C) or <code>SERVER ... ENDSERVER</code> (BASIC); <code>ON Net.Connection:</code> for event-driven servers.</p>
              <p style="margin-top:10px"><b>Capsules:</b> a capsule is a pack namespace (packs 1024&ndash;4095). Runtime hooks <code>Pack.Use/Card.Read/Write/Fifo.Open/Send/Recv</code>.</p>
            </div>
          </div>
          <div class="ref-section" id="ref-samples" style="display:none"><h2 style="color:var(--accent);margin-bottom:10px">Application Samples</h2><div id="samplesContent"></div></div>
          <div class="ref-section" id="ref-internals" style="display:none"><h2 style="color:var(--accent);margin-bottom:10px">Deep Technical Internals <span class="badge">implementer</span></h2><p style="color:var(--muted);font-size:12px;margin-bottom:10px">For contributors. Not required for app authors.</p><div class="docpanel active" style="display:block" id="doc-internals-inline"></div></div>
          <div class="ref-section" id="ref-rawdocs" style="display:none"><h2 style="color:var(--accent);margin-bottom:10px">Source Documentation</h2><div class="docnav"><!--__DOCNAV__--></div><!--__DOCBODY__--></div>
        </div>
      </div>
    </div>
    <div class="dbg-bar" id="guideDbgBar">
      <button class="active" onclick="gToggleDbg(this,'gdbg-debug')">Debug</button>
      <button class="dbg-pin" data-pin="gdbg-debug" onclick="togglePinnedPanel('guide','gdbg-debug')" title="Pin Debug">📌</button>
      <button onclick="gToggleDbg(this,'gdbg-output')">Output</button>
      <button class="dbg-pin" data-pin="gdbg-output" onclick="togglePinnedPanel('guide','gdbg-output')" title="Pin Output">📌</button>
      <select class="dbg-size" id="guideDbgSize" onchange="setDbgSize('guide',this.value)"><option value="shallow">Shallow</option><option value="normal" selected>Normal</option><option value="deep">Deep</option><option value="dynamic">Dynamic</option></select>
      <button style="margin-left:auto" onclick="gCollapseDbg()">&#9660;</button>
    </div>
    <div class="dbg-panels" id="guideDbgPanels" style="max-height:180px">
      <div class="dbg-panel active" id="gdbg-debug"><div class="debug-grid">
        <div class="debug-section"><h4>Disassembly</h4><div class="listing" id="glisting"></div></div>
        <div class="debug-section"><h4>Registers</h4><div class="regs" id="gregs"></div></div>
        <div class="debug-section"><h4>Watches</h4><table class="wal"><thead><tr><th>var</th><th>reg</th><th>value</th></tr></thead><tbody id="gwatches"></tbody></table></div>
      </div><div class="state vm-state"></div></div>
      <div class="dbg-panel" id="gdbg-output"><div class="out" id="gout"></div><div class="state vm-state"></div></div>
    </div>
  </div>
  <!-- PLAYGROUND VIEW -->
  <div class="view" id="view-play">
    <div class="ide-wrap">
      <div class="file-sidebar" id="fileSidebar">
        <div class="file-head">Files <button title="Collapse files" onclick="filesToggle()">&#9664;</button></div>
        <div class="file-body">
          <div class="file-actions">
            <button class="ghost" onclick="psFilesNew()">New</button>
            <button class="ghost" onclick="psFilesOpen()">Open</button>
            <button class="act" onclick="psFilesSave()">Save</button>
            <button class="ghost" onclick="psFilesSaveAs()">Save as</button>
            <button class="ghost" onclick="psFilesRename()">Rename</button>
            <button class="ghost" onclick="psFilesDelete()">Delete</button>
            <button class="ghost" onclick="pkgExportOpen()" title="Export / deploy a card package (application)">Package</button>
            <button class="ghost" onclick="schemaQuickNew()" title="Design a new typed pack/card schema">+ Schema</button>
            <button class="ghost" onclick="eventQuickNew()" title="Design a new fixed-schema event (sync program exit or async handler)">+ Event</button>
            <button class="ghost" onclick="ontologyQuickNew()" title="Design entities/relations and scaffold their pack events">+ Ontology</button>
          </div>
          <div class="file-list" id="fileList"></div>
          <div class="file-status" id="fileStatus"></div>
        </div>
      </div>
      <div class="ide-editor">
        <div id="codeControlsRow" style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <select id="example" style="width:auto"></select>
          <input type="hidden" id="lang" value="basic">
        </div>
        <div id="staticInfo" style="display:none;color:var(--muted);font-size:12px;margin-bottom:6px"></div>
        <div id="monaco" style="flex:1;border:1px solid #2c313f;border-radius:6px;overflow:hidden;min-height:120px"></div>
        <textarea id="src" style="flex:1;display:none;min-height:120px" spellcheck="false"></textarea>
        <div class="controls">
          <button class="act" onclick="compileSrc(true)">Compile &amp; Run &#9654;</button>
          <button class="ghost" onclick="compileSrc(false)">Compile &amp; Step</button>
          <button class="ghost" onclick="dbgStep()">Step</button>
          <button class="ghost" onclick="dbgReset()">Reset</button>
        </div>
        <div id="cerr" class="cerr"></div>
        <div id="wfDesigner" class="wf-designer" style="display:none">
          <div class="wf-add">
            <button class="ghost" onclick="wfOpenJson()" title="View / edit the backing workflow JSON">&#123; &#125; JSON</button>
            <span class="muted" style="font-size:11px">drag boxes from the palette onto the canvas; grab &#9095; to move or nest a box; edit fields inline</span>
          </div>
          <div id="wfFlow"></div>
          <div class="wf-eng-h">derived English (compiles &amp; runs; IL / bytecode / output in the debugger below)</div>
          <pre class="wf-eng" id="wfEng"></pre>
          <div class="wf-warn" id="wfWarn"></div>
        </div>
        <div id="reportDesigner" class="wf-designer" style="display:none">
          <div class="wf-add">
            <button class="ghost" onclick="rptOpenJson()" title="View / edit the backing report JSON">&#123; &#125; JSON</button>
            <span class="muted" style="font-size:11px">edit title / columns / rows &mdash; Compile &amp; Run lowers this to English and prints the real report text</span>
          </div>
          <div id="rptForm"></div>
          <div class="wf-eng-h">derived English (compiles &amp; runs; IL / bytecode / output in the debugger below)</div>
          <pre class="wf-eng" id="rptEng"></pre>
        </div>
        <div id="schemaDesigner" class="wf-designer" style="display:none">
          <div class="wf-add">
            <button class="ghost" onclick="schemaOpenJson()" title="View / edit the backing schema JSON">&#123; &#125; JSON</button>
            <span class="muted" style="font-size:11px">design a typed field list for a pack/card schema &mdash; feeds Cards, Query, Report and Form once bound</span>
          </div>
          <div id="schemaFields"></div>
        </div>
        <div id="eventDesigner" class="wf-designer" style="display:none">
          <div class="wf-add">
            <button class="ghost" onclick="eventOpenJson()" title="View / edit the backing event JSON">&#123; &#125; JSON</button>
            <span class="muted" style="font-size:11px">design a fixed-schema event &mdash; sync = a "program exit" raised and drained inline; async = a queued handler. Bind a Raise/On box to this file via its "schema" field.</span>
          </div>
          <div id="eventFields"></div>
        </div>
        <div id="ontologyDesigner" class="wf-designer" style="display:none">
          <div class="wf-add">
            <button class="ghost" onclick="ontologyOpenJson()" title="View / edit the backing ontology JSON">&#123; &#125; JSON</button>
            <span class="muted" style="font-size:11px">design entities (each bound to a pack schema) and relations between them; "+ event" scaffolds standard created/updated/deleted event schemas for an entity</span>
          </div>
          <div id="ontologyBody"></div>
        </div>
        <div class="wf-modal" id="pkgModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head" id="pkgTitle">Card package<button class="ghost" onclick="pkgModalClose()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="pkgText" spellcheck="false"></textarea><div id="pkgErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="pkgDeployFromModal()">Deploy &#9654;</button><button class="ghost" onclick="pkgModalClose()">Close</button></div>
          </div>
        </div>
        <div class="wf-modal" id="wfJsonModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head">Workflow JSON (backing step array)<button class="ghost" onclick="wfCloseJson()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="wfJsonText" spellcheck="false"></textarea><div id="wfJsonErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="wfApplyJson()">Apply</button><button class="ghost" onclick="wfCloseJson()">Cancel</button></div>
          </div>
        </div>
        <div class="wf-modal" id="rptJsonModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head">Report JSON (backing template)<button class="ghost" onclick="rptCloseJson()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="rptJsonText" spellcheck="false"></textarea><div id="rptJsonErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="rptApplyJson()">Apply</button><button class="ghost" onclick="rptCloseJson()">Cancel</button></div>
          </div>
        </div>
        <div class="wf-modal" id="schemaJsonModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head">Schema JSON (backing field list)<button class="ghost" onclick="schemaCloseJson()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="schemaJsonText" spellcheck="false"></textarea><div id="schemaJsonErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="schemaApplyJson()">Apply</button><button class="ghost" onclick="schemaCloseJson()">Cancel</button></div>
          </div>
        </div>
        <div class="wf-modal" id="eventJsonModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head">Event JSON (mode + fixed field schema + returns)<button class="ghost" onclick="eventCloseJson()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="eventJsonText" spellcheck="false"></textarea><div id="eventJsonErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="eventApplyJson()">Apply</button><button class="ghost" onclick="eventCloseJson()">Cancel</button></div>
          </div>
        </div>
        <div class="wf-modal" id="ontologyJsonModal">
          <div class="wf-modal-box">
            <div class="wf-modal-head">Ontology JSON (entities + relations)<button class="ghost" onclick="ontologyCloseJson()">&#10005;</button></div>
            <div class="wf-modal-body"><textarea id="ontologyJsonText" spellcheck="false"></textarea><div id="ontologyJsonErr" class="cerr"></div></div>
            <div class="wf-modal-foot"><button class="act" onclick="ontologyApplyJson()">Apply</button><button class="ghost" onclick="ontologyCloseJson()">Cancel</button></div>
          </div>
        </div>
      </div>
    </div>
    <div class="dbg-bar" id="playDbgBar">
      <button class="active" onclick="pToggleDbg(this,'pdbg-debug')">Debug</button>
      <button class="dbg-pin" data-pin="pdbg-debug" onclick="togglePinnedPanel('play','pdbg-debug')" title="Pin Debug">📌</button>
      <button onclick="pToggleDbg(this,'pdbg-cards')">Cards</button>
      <button class="dbg-pin" data-pin="pdbg-cards" onclick="togglePinnedPanel('play','pdbg-cards')" title="Pin Cards">📌</button>
      <button onclick="pToggleDbg(this,'pdbg-output')">Output</button>
      <button class="dbg-pin" data-pin="pdbg-output" onclick="togglePinnedPanel('play','pdbg-output')" title="Pin Output">📌</button>
      <button onclick="pToggleDbg(this,'pdbg-events')">Events</button>
      <button class="dbg-pin" data-pin="pdbg-events" onclick="togglePinnedPanel('play','pdbg-events')" title="Pin Events">📌</button>
      <select class="dbg-size" id="playDbgSize" onchange="setDbgSize('play',this.value)"><option value="shallow">Shallow</option><option value="normal" selected>Normal</option><option value="deep">Deep</option><option value="dynamic">Dynamic</option></select>
      <button style="margin-left:auto" onclick="pCollapseDbg()">&#9660;</button>
    </div>
    <div class="dbg-panels" id="playDbgPanels" style="max-height:200px">
      <div class="dbg-panel active" id="pdbg-debug"><div class="debug-grid">
        <div class="debug-section"><h4>Disassembly</h4><div class="listing" id="listing"></div></div>
        <div class="debug-section"><h4>Registers</h4><div class="regs" id="regs"></div></div>
        <div class="debug-section"><h4>Watches</h4><table class="wal"><thead><tr><th>var</th><th>reg</th><th>value</th></tr></thead><tbody id="watches"></tbody></table></div>
      </div><div class="state vm-state"></div></div>
      <div class="dbg-panel" id="pdbg-cards"><table class="wal"><tbody id="walbody"></tbody></table><button class="ghost" style="margin-top:6px" onclick="walClear()">Clear</button><div class="state vm-state"></div></div>
      <div class="dbg-panel" id="pdbg-output"><div class="out" id="out"></div><div class="state vm-state"></div></div>
      <div class="dbg-panel" id="pdbg-events"><div style="display:flex;gap:6px;align-items:center;margin-bottom:4px"><span class="muted" style="font-size:11px">Activity bus &mdash; compile / load / save / run / deploy / workflow RAISE all publish here (BareMetal.PubSub; the same RAISE/ON events)</span><button class="ghost" style="margin-left:auto" onclick="busClear()">Clear</button></div><table class="wal"><thead><tr><th style="width:70px">t (ms)</th><th style="width:150px">event</th><th>detail</th></tr></thead><tbody id="eventsbody"></tbody></table></div>
    </div>
  </div>
  <!-- Flyout triggers -->
  <div class="flyout-trigger" id="flyoutTriggers">
    <button onclick="openToolPanel('http')">HTTP</button>
    <button onclick="openToolPanel('tcp')">TCP</button>
    <button onclick="openToolPanel('cards')">Cards</button>
    <button onclick="openToolPanel('query')">Query</button>
    <button onclick="openToolPanel('spans')">Spans</button>
    <button onclick="openToolPanel('report')">Report</button>
  </div>
  <div class="flyout-overlay" id="flyoutOverlay" onclick="closeToolPanel(false)"></div>
  <div class="tool-panel" id="toolPanel">
    <div class="tool-head">
      <div class="tool-tabs">
        <button data-tool="http" onclick="selectToolTab('http')">HTTP</button>
        <button data-tool="tcp" onclick="selectToolTab('tcp')">TCP</button>
        <button data-tool="cards" onclick="selectToolTab('cards')">Cards</button>
        <button data-tool="query" onclick="selectToolTab('query')">Query</button>
        <button data-tool="spans" onclick="selectToolTab('spans')">Spans</button>
        <button data-tool="report" onclick="selectToolTab('report')">Report/Form</button>
      </div>
      <div class="tool-actions">
        <button id="toolPin" onclick="toggleToolPin()" title="Pin tools panel" aria-label="Pin tools panel">📌</button>
        <button onclick="closeToolPanel(true)" title="Close">&times;</button>
      </div>
    </div>
    <div class="tool-body">
      <div class="tool-tab" id="tool-http">
        <h3>HTTP Simulator</h3>
        <select id="reqmode" style="margin-bottom:6px"><option value="text">HTTP/text</option><option value="hex">hex</option></select>
        <textarea id="reqbox" style="height:90px" spellcheck="false"></textarea>
        <div style="display:flex;gap:6px;margin:8px 0"><button class="act" onclick="sendRequest()">Send &#9654;</button><button class="ghost" onclick="loadSample()">Sample</button><button class="ghost" onclick="loadResponder()">Responder</button></div>
        <div class="respbox" id="respout">(send a request)</div>
      </div>
      <div class="tool-tab" id="tool-tcp">
        <h3>TCP / Raw Bytes</h3>
        <textarea id="tcpbox" style="height:60px" placeholder="48 65 6c 6c 6f" spellcheck="false"></textarea>
        <div style="display:flex;gap:6px;margin:8px 0"><button class="act" onclick="sendTcp()">Send &#9654;</button><button class="ghost" onclick="loadTcpSample()">Sample</button></div>
        <div class="respbox" id="tcpout">(send bytes)</div>
        <div style="max-height:150px;overflow:auto"><table class="wal"><tbody id="walbody2"></tbody></table></div>
        <button class="ghost" style="margin-top:6px" onclick="walClear()">Clear</button>
      </div>
      <div class="tool-tab" id="tool-cards">
        <h3>Cards (PicoStore)</h3>
        <input id="packname" value="orders" style="width:100%;margin-bottom:6px" placeholder="pack name" oninput="cardRender();queryRenderChips();">
        <div id="cardSchemaInfo" style="font-size:11px;margin-bottom:6px"></div>
        <div id="cardTypedForm" style="display:none;gap:6px;flex-wrap:wrap;margin-bottom:6px"></div>
        <textarea id="cardjson" style="height:50px" spellcheck="false">{"qty": 42, "sku": "ABC", "status": 1}</textarea>
        <div style="display:flex;gap:6px;margin:6px 0"><button class="act" onclick="cardCreate()">Create</button><button class="ghost" onclick="cardSeed()">Seed</button><button class="ghost" onclick="cardClear()">Clear</button></div>
        <div id="cardmsg" class="cerr"></div>
        <div class="respbox" id="serout" style="min-height:24px;font-size:10px">&hellip;</div>
        <div style="flex:1;overflow:auto;margin-top:6px"><table class="wal"><thead><tr id="cardHead"><th>id</th><th>record</th><th></th></tr></thead><tbody id="cardlist"></tbody></table></div>
      </div>
      <div class="tool-tab" id="tool-query">
        <h3>Query</h3>
        <div id="queryFieldChips" style="margin-bottom:6px"></div>
        <input id="querybox" value="qty > 40 AND status = 1" style="width:100%;margin-bottom:6px">
        <button class="act" onclick="cardQuery()">Run &#9654;</button>
        <div style="flex:1;overflow:auto;margin-top:8px"><table class="wal"><thead><tr id="qHead"><th>id</th><th>record</th></tr></thead><tbody id="qresults"></tbody></table></div>
      </div>
      <div class="tool-tab" id="tool-spans">
        <h3>Spans &amp; Memory</h3>
        <p style="color:var(--muted);font-size:12px;line-height:1.6">
          <b>Memory.Set(addr, byte)</b> write to arena<br><b>Span.Make(addr, len)</b> create span<br>
          <b>Span.Slice(span, off, len)</b> zero-copy view<br><b>Span.Materialize(span)</b> copy to new region<br>
          <b>Span.Len/Get</b> length and indexed read
        </p>
      </div>
      <div class="tool-tab" id="tool-report">
        <h3>Report / Form <span class="badge">2-stage</span></h3>
        <p style="color:var(--muted);font-size:12px;line-height:1.5">Stage&nbsp;1 = the <b>current program</b> (its output ints are the data). Stage&nbsp;2 = the <b>template</b> below. <b>report</b> = read-only table + aggregate footer; <b>form</b> = editable inputs &amp; <b>Save</b> writes back through the data ABI (into PicoWAL memory).</p>
        <div class="controls" style="margin:6px 0">
          <label><input type="radio" name="layoutMode" value="report" checked onchange="renderLayout()"> report</label>
          <label><input type="radio" name="layoutMode" value="form" onchange="renderLayout()"> form</label>
          <button class="act" onclick="renderLayout()">Render &#9654;</button>
        </div>
        <textarea id="layoutTmpl" style="height:110px" spellcheck="false"></textarea>
        <div class="layout-preview" id="layoutPreview"></div>
        <div style="display:flex;gap:6px;margin:6px 0"><button class="ghost" onclick="layoutSave()">Save form &#128190;</button><span class="muted" id="layoutSaveMsg" style="font-size:11px"></span></div>
        <pre class="layout-text" id="layoutText"></pre>
      </div>
    </div>
  </div>
</div>

<script>/*__HOOKS__*/</script>
<script>/*__PCZ__*/</script>
<script>/*__PBZ__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
<script>/*__WF__*/</script>
<script>/*__DD__*/</script>
<script>/*__FLOW__*/</script>
<script>/*__BUS__*/</script>
<script>/*__LAYOUT__*/</script>
<script>/*__SER__*/</script>
<script>/*__STORE__*/</script>
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js"></script>
<script>
var DATA=/*__DATA__*/;
var NSDATA=/*__NSDATA__*/;
var RESPONDER=/*__RESPONDER__*/;
var WAL_PREFIX='picowal:';
var CUR_LANG='c';
var CUR_GUIDE_CARD=0;

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ---- Activity event bus (the Showcase "events" concept, in the main IDE) ------
// One BareMetal.PubSub bus; every lifecycle action (compile / sample load / file
// save / run / deploy / HTTP request / workflow RAISE) publishes to it, and the
// Events debugger tab shows the live activity log. This is the same RAISE/ON idea
// the workflow dialect exposes -- everything is wired with events.
var BUS=(typeof BareMetal!=='undefined'&&BareMetal.PubSub)?BareMetal.PubSub:null;
var BUS_LOG=[], BUS_T0=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
function busNow(){var n=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();return Math.max(0,Math.round(n-BUS_T0));}
function busEmit(topic,detail){ if(BUS){try{BUS.emit(topic,detail||{});}catch(e){}} else { busRecord(topic,detail); } }
function busRecord(topic,detail){
  BUS_LOG.push({t:busNow(),topic:topic,detail:detail});
  if(BUS_LOG.length>300) BUS_LOG.shift();
  busRender();
}
function busRender(){
  var tb=document.getElementById('eventsbody'); if(!tb) return;
  if(!BUS_LOG.length){tb.innerHTML='<tr><td colspan="3" style="color:var(--muted)">(no activity yet &mdash; compile, load a sample, save, run, or deploy)</td></tr>';return;}
  tb.innerHTML=BUS_LOG.slice(-120).reverse().map(function(e){
    var det=''; try{det=(e.detail&&typeof e.detail==='object')?JSON.stringify(e.detail):String(e.detail==null?'':e.detail);}catch(_){det='';}
    return '<tr><td>'+e.t+'</td><td><b>'+esc(String(e.topic))+'</b></td><td>'+esc(det.slice(0,160))+'</td></tr>';
  }).join('');
}
function busClear(){BUS_LOG=[];busRender();}
if(BUS){ try{ BUS.on('**',function(detail,meta){ busRecord((meta&&(meta.topic||meta.event))||'event', detail); }); }catch(e){} }

function showView(v){
  document.querySelectorAll('.view').forEach(function(e){e.classList.remove('active');});
  document.getElementById('view-'+v).classList.add('active');
  document.querySelectorAll('.tabs .tab').forEach(function(b){b.classList.remove('active');});
  var idx={guide:0,play:1}[v]||0;
  document.querySelectorAll('.tabs .tab')[idx].classList.add('active');
  var ft=document.getElementById('flyoutTriggers');
  if(ft) ft.style.display=(v==='play')?'flex':'none';
}
var CUR_REF_SECTION=null;
var WF_LAST=null;   // last workflow JSON, so dialect excursions round-trip back
function setLang(lang){
  var oldLang=CUR_LANG;
  CUR_LANG=lang;
  document.querySelectorAll('#langToggle button').forEach(function(b){b.classList.toggle('active',b.getAttribute('data-lang')===lang);});
  document.getElementById('lang').value=lang;
  // Roundtrip editor code to new language
  if(typeof getSrc==='function'&&typeof setSrc==='function'){
    var src=getSrc();
    if(lang==='workflow'){
      // Raise the current dialect source into first-class workflow steps through
      // the shared AST (real X -> workflow round-trip). Keep the JSON if it already
      // is one; else seed a starter list only when there's nothing to raise.
      if(!(typeof looksLikeWorkflowJson==='function'&&looksLikeWorkflowJson(src))){
        var raised=null;
        try{ if(typeof PicoCompile!=='undefined'&&PicoCompile.toWorkflow&&oldLang&&oldLang!=='workflow'&&src&&src.trim()) raised=PicoCompile.toWorkflow(src,oldLang); }catch(e){}
        setSrc((raised&&looksLikeWorkflowJson(raised))?raised:((typeof WF_LAST==='string'&&WF_LAST)?WF_LAST:WF_SNIPPET));
      }
    } else if(lang==='report'){
      // The Report dialect is a visual template designer (title/columns/rows),
      // not raised from arbitrary code -- keep the template if it already is one,
      // else restore what we last left, else seed a starter template.
      if(!(typeof looksLikeReportJson==='function'&&looksLikeReportJson(src)))
        setSrc((typeof RPT_LAST==='string'&&RPT_LAST)?RPT_LAST:JSON.stringify(RPT_DEFAULT,null,2));
    } else if(oldLang==='workflow'){
      // Design in workflow, then view as text: workflow -> English -> target.
      if(typeof looksLikeWorkflowJson==='function'&&looksLikeWorkflowJson(src)){
        WF_LAST=src;   // remember the workflow so we can round-trip back to it
        try{
          var _eng=wfCompileSrc(src).source;
          var _out=(lang==='english')?_eng:((typeof PicoCompile!=='undefined'&&PicoCompile.translate)?PicoCompile.translate(_eng,'english',lang):_eng);
          if(_out) setSrc(_out);
        }catch(e){}
      }
    } else if(oldLang==='report'){
      // Design a report, then view as text: report -> English -> target.
      if(typeof looksLikeReportJson==='function'&&looksLikeReportJson(src)){
        RPT_LAST=src;
        try{
          var _eng2=rptCompileSrc(src).source;
          var _out2=(lang==='english')?_eng2:((typeof PicoCompile!=='undefined'&&PicoCompile.translate)?PicoCompile.translate(_eng2,'english',lang):_eng2);
          if(_out2) setSrc(_out2);
        }catch(e){}
      }
    } else if(src&&src.trim()&&oldLang!==lang&&typeof PicoCompile!=='undefined'&&PicoCompile.translate){
      var translated=PicoCompile.translate(src,oldLang,lang);
      if(translated)setSrc(translated);
    }
  }
  if(typeof onLangChange==='function') onLangChange();
  if(typeof wfToggle==='function') wfToggle();
  if(typeof reportToggle==='function') reportToggle();
  renderSyntaxRef(); renderSamples();
  if(CUR_REF_SECTION){showRefInline(CUR_REF_SECTION);}
  else{showGuideCard(CUR_GUIDE_CARD);}
}

var CONSTANT_NAMES=/*__CONSTANTS__*/;
function idxByTitle(t){for(var i=0;i<DATA.length;i++)if(DATA[i].title===t)return i;return -1;}
function idxList(titles){var out=[];titles.forEach(function(t){var i=idxByTitle(t);if(i>=0)out.push(i);});return out;}
var GROUPS=[
  {name:'Basics',items:idxList(['Variables & arithmetic'])},
  {name:'Constants & Locale',items:idxList(['User constants &amp; enums','Built-in constants &amp; locale metadata','Locale.SetLocale + UTC display offsets'])},
  {name:'Control Flow',items:idxList(['Conditional (if / else)','While loop','Counted loop (for)','Index iteration (foreach)','Unconditional jump (goto)','Post-test loop (do)','Early exit &amp; skip (break / skip)'])},
  {name:'Operators',items:idxList(['Operators (++ -- ?: && % )'])},
  {name:'Dispatch / State Machine',items:idxList(['Multi-way branch (switch)','Jump-table dispatch (state machine)'])},
  {name:'Subroutines',items:idxList(['Subroutine (call / gosub)','Function parameters + return (def/void)'])},
  {name:'I/O & Cards',items:idxList(['HTTP response (Net.*)','HTML streaming (TextRender.*)','Cards: create &amp; update','Cards: query records','Cards: active-record style','HTTP request: parse query + body','TCP stream: parse parameter frame','Large cards: partial slice reads'])},
  {name:'Streams & Events',items:idxList(['Stream data: slice a frame','Event handler: slice payload','Streaming: DMA ring (Device.* / Stream.*)','Remote UI: a window (Ui.* / Event.*)'])},
  {name:'AI & Hardware',items:idxList(['AI tensors: matvec + bitlinear','Model block slices'])},
  {name:'Encoding',items:idxList(['Encoding round-trips'])},
  {name:'Functions & Errors',items:idxList(['Testing: PSUnit assertions (Assert.*)','Error handling (try / except)'])},
  {name:'OS & Web Hooks',items:idxList(['OS-worker: Process + Timer','Base64 + Req.Param (web hooks)'])}
];
function buildGuideTree(){
  var html='';
  GROUPS.forEach(function(g){
    html+='<div class="group-title">'+esc(g.name)+'</div>';
    g.items.forEach(function(idx){if(idx<DATA.length) html+='<div class="tree-item'+(idx===0?' active':'')+'" data-idx="'+idx+'" onclick="showGuideCard('+idx+')">'+esc(DATA[idx].title)+'</div>';});
  });
  html+='<div class="group-title">Reference</div>';
  REF_SECTIONS.forEach(function(id,i){html+='<div class="tree-item" data-ref="'+id+'" onclick="showRefInline(\''+id+'\')">'+REF_LABELS[i]+'</div>';});
  document.getElementById('guideTree').innerHTML=html;
}
function showGuideCard(idx){
  CUR_GUIDE_CARD=idx;
  CUR_REF_SECTION=null;
  var d=DATA[idx],lang=CUR_LANG;
  var SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle',cobol:'cobstyle',report:'rptstyle',functional:'fnstyle'};
  var src;
  if(d[lang]){src=d[lang].src;}
  else if(typeof PicoCompile!=='undefined'&&PicoCompile.translate){
    var fromLang=d.c?'c':(d.basic?'basic':(d.python?'python':'english'));
    var fromSrc=d[fromLang]?d[fromLang].src:'';
    src=PicoCompile.translate(fromSrc,fromLang,lang);
    if(src===fromSrc)src=null; // translate returned unchanged = not supported
  } else {src=null;}
  var ri=document.getElementById('refInlineContent');if(ri)ri.style.display='none';
  document.querySelectorAll('.ref-section').forEach(function(s){s.style.display='none';});
  var gc=document.getElementById('guideContent');
  var cardHtml='<div class="card-title">'+(idx+1)+'. '+esc(d.title)+'</div>'+
    '<div class="card-desc">'+d.desc+'</div>'+
    (src?'<pre class="'+(SC[lang]||'cstyle')+'">'+esc(src)+'</pre>':'<pre style="color:var(--muted)">(not available in this dialect)</pre>')+
    '<div class="run-area"><button class="act" onclick="guideRun('+idx+')">Run &#9654;</button>'+
    '<button class="ghost" onclick="guideStep('+idx+')">Step</button>'+
    '<button class="ghost" onclick="guideEdit('+idx+')">Edit in WebIDE</button>'+
    '<span class="out" id="gcardout'+idx+'"></span></div>';
  if(ri){var wrapper=gc.querySelector('.guide-card-wrap');if(!wrapper){wrapper=document.createElement('div');wrapper.className='guide-card-wrap';gc.insertBefore(wrapper,ri);}wrapper.innerHTML=cardHtml;}
  else{gc.innerHTML=cardHtml;}
  document.querySelectorAll('#guideTree .tree-item').forEach(function(el){el.classList.toggle('active',parseInt(el.getAttribute('data-idx'))===idx);el.classList.remove('ref-active');});
}
function showRefInline(id){
  CUR_REF_SECTION=id;  // we're in ref mode
  var gc=document.getElementById('guideContent');
  var wrapper=gc.querySelector('.guide-card-wrap');if(wrapper)wrapper.innerHTML='';
  var ri=document.getElementById('refInlineContent');if(ri)ri.style.display='block';
  document.querySelectorAll('.ref-section').forEach(function(s){s.style.display='none';});
  var sec=document.getElementById(id);if(sec)sec.style.display='block';
  document.querySelectorAll('#guideTree .tree-item').forEach(function(el){el.classList.remove('active');el.classList.toggle('ref-active',el.getAttribute('data-ref')===id);});
}

var GDBG={words:[],disasm:[],vm:null,debug:{},src:'',bps:{}};
var DBG_LAYOUT={
  guide:{panels:'guideDbgPanels',bar:'guideDbgBar',size:'normal',pinned:{}},
  play:{panels:'playDbgPanels',bar:'playDbgBar',size:'normal',pinned:{}}
};
var DBG_HEIGHTS={shallow:120,normal:200,deep:340,dynamic:null};
function applyDbgLayout(scope){
  var cfg=DBG_LAYOUT[scope], host=document.getElementById(cfg.panels); if(!host)return;
  var any=Object.keys(cfg.pinned).some(function(k){return cfg.pinned[k];});
  host.classList.toggle('pinned-mode',any);
  host.querySelectorAll('.dbg-panel').forEach(function(p){p.classList.toggle('pinned',!!cfg.pinned[p.id]);});
  var h=DBG_HEIGHTS[cfg.size]; host.style.maxHeight=(h==null?'60vh':(h+'px'));
  var bar=document.getElementById(cfg.bar); if(bar)bar.querySelectorAll('.dbg-pin').forEach(function(b){b.classList.toggle('active',!!cfg.pinned[b.getAttribute('data-pin')]);});
}
function setDbgSize(scope,size){DBG_LAYOUT[scope].size=size||'normal';applyDbgLayout(scope);}
function togglePinnedPanel(scope,id){var p=DBG_LAYOUT[scope].pinned;p[id]=!p[id];applyDbgLayout(scope);}
function guideRun(i){
  var d=DATA[i],lang=CUR_LANG;if(!d[lang])lang='basic';
  var o=runWords(d[lang].words).outputInts();
  var el=document.getElementById('gcardout'+i);if(el)el.innerHTML=lang+' \u2192 ['+o.join(', ')+']';
  GDBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});GDBG.disasm=d[lang].disasm.slice();GDBG.src=d[lang].src||'';try{GDBG.debug=PicoCompile.compileDebug(GDBG.src,lang).debug||{};}catch(e){GDBG.debug={};}
  GDBG.vm=new PicoVM();GDBG.vm.load(GDBG.words);var g=0;while(GDBG.vm.step()&&g++<200000){if(GDBG.bps[GDBG.vm.pc])break;}gRender();
  document.getElementById('guideDbgPanels').classList.remove('collapsed');
}
function guideStep(i){var d=DATA[i],lang=CUR_LANG;if(!d[lang])lang='basic';GDBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});GDBG.disasm=d[lang].disasm.slice();GDBG.src=d[lang].src||'';try{GDBG.debug=PicoCompile.compileDebug(GDBG.src,lang).debug||{};}catch(e){GDBG.debug={};}GDBG.vm=new PicoVM();GDBG.vm.load(GDBG.words);gRender();document.getElementById('guideDbgPanels').classList.remove('collapsed');}
function guideEdit(i){
  var d=DATA[i],lang=CUR_LANG;
  if(d[lang]){document.getElementById('lang').value=lang;onLangChange();setSrc(d[lang].src);}
  else if(typeof PicoCompile!=='undefined'&&PicoCompile.translate){
    var fromLang=d.c?'c':(d.basic?'basic':'python');
    var src=PicoCompile.translate(d[fromLang].src,fromLang,lang);
    document.getElementById('lang').value=lang;onLangChange();setSrc(src);
  } else {var fl=d.c?'c':'basic';document.getElementById('lang').value=fl;onLangChange();setSrc(d[fl].src);}
  showView('play');compileSrc(false);
}
function gRender(){
  var vm=GDBG.vm;if(!vm)return;
  document.getElementById('glisting').innerHTML=GDBG.disasm.map(function(t,idx){var rec=GDBG.debug&&GDBG.debug[idx],lc=rec?PicoCompile.offsetToLineCol(GDBG.src,rec[0]):[0,0],sym=lc[0]?('  ; L'+lc[0]+':'+lc[1]+' '+PicoCompile.sourceLineText(GDBG.src,rec[0]).trim()):'';return '<div class="row'+(idx===vm.pc?' pc':'')+(GDBG.bps[idx]?' bp':'')+'" onclick="gToggleBp('+idx+')" title="click to toggle breakpoint">'+String(idx).padStart(3,' ')+'  '+esc(t+sym)+'</div>';}).join('');
  var pcrow=document.querySelector('#glisting .row.pc');if(pcrow)pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('gregs').innerHTML='<div class="r">PC <b>'+vm.pc+'</b></div>'+Array.from(vm.regs).map(function(v,idx){return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  var gw=document.getElementById('gwatches');if(gw)gw.innerHTML='<tr><td colspan="3" style="color:var(--muted)">(guide sample)</td></tr>';
  document.querySelectorAll('#guideDbgPanels .vm-state').forEach(function(el){el.textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted;});
  document.getElementById('gout').textContent='output: ['+vm.outputInts().join(', ')+']';
}
function gToggleBp(pc){GDBG.bps[pc]?delete GDBG.bps[pc]:GDBG.bps[pc]=1;gRender();}
function gToggleDbg(btn,pid){document.querySelectorAll('#guideDbgBar button:not(.dbg-pin)').forEach(function(b){b.classList.remove('active');});btn.classList.add('active');document.querySelectorAll('#guideDbgPanels .dbg-panel').forEach(function(p){p.classList.remove('active');});var el=document.getElementById(pid);if(el)el.classList.add('active');applyDbgLayout('guide');document.getElementById('guideDbgPanels').classList.remove('collapsed');}
function gCollapseDbg(){document.getElementById('guideDbgPanels').classList.toggle('collapsed');}

// playground debugger
var DBG={words:[],disasm:[],vm:null,vars:{},debug:{},src:'',pcBps:{},sourceBps:{},pcLines:{},lineToPcs:{},srcDecorations:[]};
function pToggleDbg(btn,pid){document.querySelectorAll('#playDbgBar button:not(.dbg-pin)').forEach(function(b){b.classList.remove('active');});btn.classList.add('active');document.querySelectorAll('#playDbgPanels .dbg-panel').forEach(function(p){p.classList.remove('active');});var el=document.getElementById(pid);if(el)el.classList.add('active');applyDbgLayout('play');document.getElementById('playDbgPanels').classList.remove('collapsed');}
function pCollapseDbg(){document.getElementById('playDbgPanels').classList.toggle('collapsed');}

function rebuildDebugMaps(){
  DBG.pcLines={}; DBG.lineToPcs={};
  Object.keys(DBG.debug||{}).forEach(function(k){
    var pc=parseInt(k,10), rec=DBG.debug[k], off=rec&&rec[0];
    var lc=PicoCompile.offsetToLineCol(DBG.src||'', off);
    if(lc&&lc[0]){DBG.pcLines[pc]=lc[0];(DBG.lineToPcs[lc[0]]=DBG.lineToPcs[lc[0]]||[]).push(pc);}
  });
}
function pcHasBreakpoint(pc){return !!(DBG.pcBps[pc] || DBG.sourceBps[DBG.pcLines[pc]]);}
function toggleBp(pc){DBG.pcBps[pc]?delete DBG.pcBps[pc]:DBG.pcBps[pc]=1;render();}
function toggleSourceBreakpoint(line){if(!line)return;DBG.sourceBps[line]?delete DBG.sourceBps[line]:DBG.sourceBps[line]=1;updateSourceDecorations();render();}
function updateSourceDecorations(){
  if(!EDITOR||!EDITOR.deltaDecorations)return;
  var decos=[], active=(DBG.vm&&DBG.pcLines)?DBG.pcLines[DBG.vm.pc]:0;
  Object.keys(DBG.sourceBps||{}).forEach(function(k){var ln=parseInt(k,10);if(ln>0)decos.push({range:new monaco.Range(ln,1,ln,1),options:{isWholeLine:true,className:'source-breakpoint-line',glyphMarginClassName:'breakpoint-glyph',glyphMarginHoverMessage:{value:'Breakpoint: line '+ln}}});});
  if(active>0)decos.push({range:new monaco.Range(active,1,active,1),options:{isWholeLine:true,className:'source-active-line'}});
  DBG.srcDecorations=EDITOR.deltaDecorations(DBG.srcDecorations||[],decos);
}

function runWords(hex){var vm=new PicoVM();vm.run(hex.map(function(h){return parseInt(h,16)>>>0;}));return vm;}
function buildGallery(){}
function runCard(i){
  var d=DATA[i],styles=['c','basic','python','english'],parts=[],ref=null,same=true;
  styles.forEach(function(s){if(!d[s])return;var o=runWords(d[s].words).outputInts();if(ref===null)ref=JSON.stringify(o);else if(JSON.stringify(o)!==ref)same=false;parts.push(s+' \u2192 ['+o.join(', ')+']');});
  return{parts:parts,same:same,ref:ref?JSON.parse(ref):[]};
}

// Monaco
var EDITOR=null;
function getSrc(){return EDITOR?EDITOR.getValue():document.getElementById('src').value;}
function setSrc(v){document.getElementById('src').value=v;if(EDITOR)EDITOR.setValue(v);if(typeof filesRender==='function')filesRender();}
function monacoLangId(lang){return{c:'picoc',basic:'picobasic',python:'picopython',english:'picoenglish',workflow:'json',report:'json'}[lang]||'picoc';}
function onLangChange(){if(EDITOR)monaco.editor.setModelLanguage(EDITOR.getModel(),monacoLangId(document.getElementById('lang').value));if(typeof filesRender==='function')filesRender();}

// ---- static file editing (md/json/html/etc.): own tabs, no dialect/diags ------
// A file whose kind isn't 'code' (schemas, cards, static assets) is a plain file:
// no PicoScript dialect applies, so the dialect toggle, example dropdown, compile
// controls and debugger are hidden while it's open, and Monaco gets the file's
// *own* language (markdown/json/html/...) for correct highlighting + intellisense
// (Monaco ships these natively). Opening a code file restores the normal IDE.
var STATIC_MODE=false;
function staticMonacoLang(name){
  var n=String(name||'').toLowerCase();
  if(/\.md$/.test(n)) return 'markdown';
  if(/\.json$/.test(n)) return 'json';
  if(/\.html?$/.test(n)) return 'html';
  if(/\.css$/.test(n)) return 'css';
  if(/\.js$/.test(n)) return 'javascript';
  return 'plaintext';
}
function enterStaticMode(name,kind){
  STATIC_MODE=true;
  var lt=document.getElementById('langToggle'), cc=document.getElementById('codeControlsRow'),
    ctl=document.querySelector('.controls'), cerr=document.getElementById('cerr'),
    wd=document.getElementById('wfDesigner'), rd=document.getElementById('reportDesigner'),
    bar=document.getElementById('playDbgBar'), panels=document.getElementById('playDbgPanels'),
    info=document.getElementById('staticInfo');
  if(lt) lt.style.display='none'; if(cc) cc.style.display='none'; if(ctl) ctl.style.display='none';
  if(cerr) cerr.style.display='none'; if(wd) wd.style.display='none'; if(rd) rd.style.display='none';
  if(bar) bar.style.display='none'; if(panels) panels.style.display='none';
  if(info){ info.style.display='block'; info.textContent='Editing '+name+' \u2014 a plain '+kind+' file (no dialect switching or diagnostics; Save writes it'+(kind==='card'?' and pushes it to picowal':'')+')'; }
  if(EDITOR){ try{ monaco.editor.setModelLanguage(EDITOR.getModel(),staticMonacoLang(name)); }catch(e){} }
  // A schema file gets the visual Schema Designer instead of raw JSON text (the
  // fields table is the surface; edit raw via its own { } JSON modal). Event and
  // ontology files get their own equivalent visual surfaces.
  if(typeof schemaToggle==='function') schemaToggle(kind==='schema', name);
  if(typeof eventToggle==='function') eventToggle(kind==='event', name);
  if(typeof ontologyToggle==='function') ontologyToggle(kind==='ontology', name);
}
function exitStaticMode(){
  if(!STATIC_MODE) return;
  STATIC_MODE=false;
  var lt=document.getElementById('langToggle'), cc=document.getElementById('codeControlsRow'),
    ctl=document.querySelector('.controls'), cerr=document.getElementById('cerr'),
    bar=document.getElementById('playDbgBar'), panels=document.getElementById('playDbgPanels'),
    info=document.getElementById('staticInfo');
  if(lt) lt.style.display=''; if(cc) cc.style.display=''; if(ctl) ctl.style.display='';
  if(cerr) cerr.style.display=''; if(bar) bar.style.display=''; if(panels) panels.style.display='';
  if(info) info.style.display='none';
  if(EDITOR){ try{ monaco.editor.setModelLanguage(EDITOR.getModel(),monacoLangId(document.getElementById('lang').value)); }catch(e){} }
  if(typeof schemaToggle==='function') schemaToggle(false);
  if(typeof eventToggle==='function') eventToggle(false);
  if(typeof ontologyToggle==='function') ontologyToggle(false);
  if(typeof wfToggle==='function') wfToggle();
  if(typeof reportToggle==='function') reportToggle();
}
function hookNames(){var H=(typeof PV_HOOKS!=='undefined'&&PV_HOOKS.BY_CODE)?PV_HOOKS.BY_CODE:{};var out=[];for(var k in H)out.push(H[k]);return out;}
var MONARCH={picoc:{keywords:['int','var','void','if','else','while','for','return','break','continue','print','switch','case','default','do','goto','dispatch'],tokenizer:{root:[[/\/\/.*$/,'comment'],[/\/\*/,'comment','@block'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[{}()\[\];,.]/,'delimiter'],[/[+\-*/%=<>!&|?:]+/,'operator']],block:[[/\*\//,'comment','@pop'],[/./,'comment']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}},picobasic:{ignoreCase:true,keywords:['DIM','LET','IF','THEN','ELSEIF','ELSE','ENDIF','WHILE','ENDWHILE','FOR','TO','STEP','NEXT','FOREACH','IN','ENDFOREACH','SWITCH','CASE','DEFAULT','ENDSWITCH','DISPATCH','ENDDISPATCH','GOTO','GOSUB','SUB','ENDSUB','RETURN','PRINT','AND','OR','NOT','DO','LOOP','UNTIL','BREAK','SKIP','INC','DEC','IIF','EQ','NE','LT','GT','LE','GE','MOD'],tokenizer:{root:[[/'.*$/,'comment'],[/\/\/.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[()\[\];,.:]/,'delimiter'],[/[+\-*/=<>]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}},picopython:{keywords:['if','elif','else','while','for','in','range','def','return','break','continue','pass','and','or','not','print','True','False','match','case','dispatch'],tokenizer:{root:[[/#.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/'/,'string','@str2'],[/[()\[\]:,.]/,'delimiter'],[/[+\-*/%=<>!]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']],str2:[[/[^']+/,'string'],[/'/,'string','@pop']]}},picoenglish:{ignoreCase:true,keywords:['set','let','to','be','add','subtract','from','increase','decrease','multiply','divide','by','print','show','display','if','otherwise','while','repeat','as','long','for','each','times','with','define','do','call','return','stop','break','skip','continue','choose','when','dispatch','on','is','greater','less','than','at','least','most','equal','equals','exceeds','plus','minus','modulo','mod','over','and','or','not','true','false'],tokenizer:{root:[[/#.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.\s*[A-Za-z_]\w*\s*\()/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[()\[\],.:]/,'delimiter'],[/[+\-*/%=<>]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}}};
function constantDoc(name){
  if(/^STATUS_/.test(name)||/^HTTP_STATUS_/.test(name))return'HTTP response status constant for Net.Status/Resp.Status';
  if(/^METHOD_/.test(name)||/^HTTP_METHOD_/.test(name))return'HTTP request method constant for Req.Method';
  if(/^TZ_/.test(name)||/^TIMEZONE\./.test(name))return'Timezone enum id';
  if(/^CURRENCY_/.test(name))return'ISO-4217 currency constant';
  if(/^COUNTRY_/.test(name))return'ISO-3166 country constant';
  return'PicoScript named constant';
}
function registerLang(id){monaco.languages.register({id:id});monaco.languages.setMonarchTokensProvider(id,MONARCH[id]);monaco.languages.registerCompletionItemProvider(id,{provideCompletionItems:function(model,pos){var kw=MONARCH[id].keywords||[];var sug=kw.map(function(k){return{label:k,kind:monaco.languages.CompletionItemKind.Keyword,insertText:k};});hookNames().forEach(function(name){sug.push({label:name,kind:monaco.languages.CompletionItemKind.Function,insertText:name+'('});});CONSTANT_NAMES.forEach(function(name){sug.push({label:name,kind:monaco.languages.CompletionItemKind.Constant,insertText:name,detail:constantDoc(name),documentation:'Use directly in expressions, e.g. Net.Status('+name+').' });});return{suggestions:sug};}});}
function initMonaco(){if(typeof require==='undefined'||!require.config){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';return;}try{require.config({paths:{vs:'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs'}});require(['vs/editor/editor.main'],function(){['picoc','picobasic','picopython','picoenglish'].forEach(registerLang);EDITOR=monaco.editor.create(document.getElementById('monaco'),{value:document.getElementById('src').value,language:monacoLangId(document.getElementById('lang').value),theme:'vs-dark',minimap:{enabled:false},fontSize:13,lineNumbers:'on',scrollBeyondLastLine:false,automaticLayout:true,tabSize:4,insertSpaces:true,glyphMargin:true});EDITOR.onDidChangeModelContent(function(){document.getElementById('src').value=EDITOR.getValue();filesRender();if(CUR_LANG==='workflow'){try{wfRenderDesigner();}catch(e){}}});EDITOR.onMouseDown(function(e){if(e.target&&e.target.position&&(e.target.type===monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN||e.target.type===monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS)){toggleSourceBreakpoint(e.target.position.lineNumber);}});updateSourceDecorations();},function(){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';});}catch(e){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';}}

// localStorage-backed playground files
var PS_FILES_KEY='picoscript.files.v1';
var PS_ACTIVE_FILE_KEY='picoscript.files.active';
var ACTIVE_FILE='';
function filesSafeLocalStorage(){try{return typeof localStorage!=='undefined'?localStorage:null;}catch(e){return null;}}
function filesRead(){
  var ls=filesSafeLocalStorage(); if(!ls) return {};
  try{
    var raw=ls.getItem(PS_FILES_KEY), parsed=raw?JSON.parse(raw):{}, out={};
    if(!parsed||typeof parsed!=='object'||Array.isArray(parsed)) return {};
    Object.keys(parsed).forEach(function(name){
      var f=parsed[name]||{};
      if(typeof name==='string'&&name.trim()&&typeof f.src==='string'){
        var kind=f.kind||fileKind(name);
        var lang=(['c','basic','python','english','workflow'].indexOf(f.lang)>=0)?f.lang:'';
        out[name]={kind:kind,lang:lang,src:f.src,updated:Number(f.updated)||0};
      }
    });
    return out;
  }catch(e){return {};}
}
// A file's kind is derived from its extension. code = a PicoScript program in any
// dialect; card = a picowal data card (JSON of address->int); schema = a typed
// pack/card schema (fields with types, designed visually -- see Schema Designer);
// source = a read-only input shape; static = a served asset.
function fileKind(name){
  var n=String(name||'').toLowerCase();
  if(/\.ontology\.json$/.test(n)) return 'ontology';
  if(/\.event\.json$/.test(n)) return 'event';
  if(/\.schema\.json$/.test(n)) return 'schema';
  if(/\.card\.json$/.test(n)) return 'card';
  if(/\.json$/.test(n)) return 'source';
  if(/\.(html|js|css|md|txt)$/.test(n)) return 'static';
  return 'code';
}
function fileIcon(kind){ return {folder:'\uD83D\uDCC1',card:'\uD83D\uDDC3',schema:'\uD83E\uDDE9',event:'\u26A1',ontology:'\uD83D\uDD78\uFE0F',source:'\uD83D\uDD16',static:'\uD83D\uDCC4',code:'\u2039\u203A'}[kind]||'\uD83D\uDCC4'; }
function fileBaseName(p){ var i=String(p).lastIndexOf('/'); return i<0?p:p.slice(i+1); }
// A card file writes its {addr:int} map (or {cards:{...}}) into the live picowal
// (WAL) card store so a running program/workflow can Storage.Load it.
function cardToWal(name){
  var files=filesRead(), f=files[name]; if(!f||fileKind(name)!=='card') return 0;
  var obj; try{obj=JSON.parse(f.src);}catch(e){return -1;}
  var map=(obj&&obj.cards&&typeof obj.cards==='object')?obj.cards:obj;
  if(!map||typeof map!=='object') return 0;
  var be=walBackend(), n=0;
  Object.keys(map).forEach(function(k){var a=parseInt(k,10);var v=map[k];if(!isNaN(a)&&typeof v==='number'){be.set(a,v|0);n++;}});
  if(typeof renderWal==='function') try{renderWal();}catch(e){}
  return n;
}
function filesWrite(files){var ls=filesSafeLocalStorage(); if(!ls) return; try{ls.setItem(PS_FILES_KEY,JSON.stringify(files));}catch(e){}}
function filesSetActive(name){ACTIVE_FILE=name||'';var ls=filesSafeLocalStorage();if(!ls)return;try{if(ACTIVE_FILE)ls.setItem(PS_ACTIVE_FILE_KEY,ACTIVE_FILE);else ls.removeItem(PS_ACTIVE_FILE_KEY);}catch(e){}}
function filesEscAttr(s){return esc(String(s)).replace(/"/g,'&quot;');}
function filesStatus(msg,err){var el=document.getElementById('fileStatus');if(el){el.textContent=msg||'';el.style.color=err?'var(--err)':'var(--muted)';}}
function filesUniqueName(files){
  var lang=(document.getElementById('lang')||{}).value||'basic', base='Untitled.'+lang, name=base, n=2;
  while(files[name]) name='Untitled '+(n++)+'.'+lang;
  return name;
}
function filesIsDirty(){
  if(!ACTIVE_FILE) return false;
  var f=filesRead()[ACTIVE_FILE]; if(!f) return false;
  return f.src!==getSrc()||(fileKind(ACTIVE_FILE)==='code'&&f.lang!==document.getElementById('lang').value);
}
// Render the flat path-keyed file map as a collapsible folder tree with per-kind
// icons/badges. Folder names contain '/'; a collapsed-folder set is remembered.
var FILES_COLLAPSED={};
function filesRender(){
  var list=document.getElementById('fileList'); if(!list) return;
  var files=filesRead(), names=Object.keys(files);
  if(!names.length){list.innerHTML='<div class="file-empty">No saved files yet. <a href="#" onclick="seedSampleApp();return false;">Load sample app</a></div>';return;}
  var dirty=filesIsDirty();
  // build nested tree
  var root={dirs:{},files:[]};
  names.sort().forEach(function(name){
    var parts=name.split('/'), node=root, acc='';
    for(var i=0;i<parts.length-1;i++){acc+=(acc?'/':'')+parts[i];if(!node.dirs[parts[i]])node.dirs[parts[i]]={dirs:{},files:[],path:acc};node=node.dirs[parts[i]];}
    node.files.push(name);
  });
  function fileRow(name){
    var f=files[name], isActive=name===ACTIVE_FILE, dot=isActive&&dirty?'*':'', kind=fileKind(name);
    var badge=kind==='code'?(f.lang||'code'):kind;
    return '<div class="file-item'+(isActive?' active':'')+'" data-name="'+filesEscAttr(name)+'">'+
      '<div class="file-row"><span class="file-dirty">'+dot+'</span><span class="file-ico">'+fileIcon(kind)+'</span>'+
      '<span class="file-name">'+esc(fileBaseName(name))+'</span><span class="file-badge k-'+kind+' '+esc(f.lang||'')+'">'+esc(badge)+'</span></div></div>';
  }
  function renderNode(node,depth){
    var html='';
    Object.keys(node.dirs).sort().forEach(function(d){
      var sub=node.dirs[d], collapsed=!!FILES_COLLAPSED[sub.path];
      html+='<div class="file-folder" data-path="'+filesEscAttr(sub.path)+'" style="padding-left:'+(depth*10)+'px">'+
        '<span class="file-fold-caret">'+(collapsed?'\u25B8':'\u25BE')+'</span><span class="file-ico">'+fileIcon('folder')+'</span> '+esc(d)+'</div>';
      if(!collapsed) html+='<div style="padding-left:'+((depth+1)*10)+'px">'+renderNode(sub,depth+1)+'</div>';
    });
    node.files.forEach(function(name){ html+='<div style="padding-left:'+(depth*10)+'px">'+fileRow(name)+'</div>'; });
    return html;
  }
  list.innerHTML=renderNode(root,0);
  list.querySelectorAll('.file-folder').forEach(function(el){el.onclick=function(){var p=el.getAttribute('data-path');FILES_COLLAPSED[p]=!FILES_COLLAPSED[p];filesRender();};});
  list.querySelectorAll('.file-item').forEach(function(el){el.onclick=function(){psFilesOpen(el.getAttribute('data-name'));};});
}
function filesToggle(){var el=document.getElementById('fileSidebar');if(!el)return;el.classList.toggle('collapsed');var b=el.querySelector('.file-head button');if(b)b.innerHTML=el.classList.contains('collapsed')?'&#9654;':'&#9664;';}
function psFilesList(){return filesRead();}
function fileDefaultSrc(kind){
  if(kind==='schema') return JSON.stringify({fields:[]},null,2);
  if(kind==='event') return JSON.stringify({mode:'async',fields:[],returns:null},null,2);
  if(kind==='ontology') return JSON.stringify({entities:[],relations:[]},null,2);
  return '';
}
function psFilesNew(name){
  var files=filesRead(); name=(name||((typeof prompt==='function')?prompt('New file name (folders via /, e.g. routes/orders.psc)',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  if(files[name]&&typeof confirm==='function'&&!confirm('Replace "'+name+'"?')) return null;
  var kind=fileKind(name);
  setSrc(fileDefaultSrc(kind));
  if(kind==='code'){ if(typeof exitStaticMode==='function') exitStaticMode(); } else if(typeof enterStaticMode==='function') enterStaticMode(name,kind);
  files[name]={kind:kind,lang:kind==='code'?(document.getElementById('lang').value||'basic'):'',src:getSrc(),updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender(); filesStatus('New '+kind+' '+name); return name;
}
function psFilesSave(name){
  var files=filesRead(); name=(name||ACTIVE_FILE||((typeof prompt==='function')?prompt('Save file as',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  var kind=fileKind(name);
  files[name]={kind:kind,lang:kind==='code'?(document.getElementById('lang').value||'basic'):'',src:getSrc(),updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender();
  if(kind==='card'){var n=cardToWal(name);filesStatus(n>=0?('Saved '+name+' \u2192 '+n+' picowal cards'):('Saved '+name+' (invalid card JSON)'),n<0);}
  else filesStatus('Saved '+name);
  busEmit('file.save',{name:name,kind:kind});
  return name;
}
function psFilesSaveAs(name){return psFilesSave(name||((typeof prompt==='function')?prompt('Save file as',ACTIVE_FILE||filesUniqueName(filesRead())):''));}
function psFilesOpen(name){
  var files=filesRead(), names=Object.keys(files).sort();
  name=(name||((typeof prompt==='function')?prompt('Open file',ACTIVE_FILE||names[0]||''):'')||'').trim();
  if(!name||!files[name]){filesStatus(name?'File not found: '+name:'Open cancelled',!!name);return null;}
  var kind=fileKind(name);
  if(kind==='code'){if(typeof exitStaticMode==='function')exitStaticMode(); document.getElementById('lang').value=files[name].lang||'basic'; if(typeof setLang==='function'){setLang(document.getElementById('lang').value);} else onLangChange(); setSrc(files[name].src); filesSetActive(name); filesRender(); filesStatus('Opened '+name); compileSrc(false);}
  else {setSrc(files[name].src); if(typeof enterStaticMode==='function')enterStaticMode(name,kind); filesSetActive(name); filesRender(); filesStatus('Opened '+kind+' '+name+(kind==='card'?' (Save writes it to picowal)':''));}
  return files[name];
}
function psFilesRename(oldName,newName){
  var files=filesRead(); oldName=(oldName||ACTIVE_FILE||'').trim(); if(!oldName||!files[oldName]){filesStatus('No active file to rename',true);return null;}
  newName=(newName||((typeof prompt==='function')?prompt('Rename file',oldName):'')||'').trim(); if(!newName||newName===oldName)return oldName;
  if(files[newName]){filesStatus('File already exists: '+newName,true);return null;}
  files[newName]=files[oldName]; files[newName].updated=Date.now(); delete files[oldName]; filesWrite(files); if(ACTIVE_FILE===oldName)filesSetActive(newName); filesRender(); filesStatus('Renamed to '+newName); return newName;
}
function psFilesDelete(name,skipConfirm){
  var files=filesRead(); name=(name||ACTIVE_FILE||'').trim(); if(!name||!files[name]){filesStatus('No file selected',true);return false;}
  if(!skipConfirm&&typeof confirm==='function'&&!confirm('Delete "'+name+'"?')) return false;
  delete files[name]; filesWrite(files); if(ACTIVE_FILE===name)filesSetActive(''); filesRender(); filesStatus('Deleted '+name); return true;
}

// ---- routes + card packages (deploy a tree into the running service) ---------
// A route binds an inbound (method,path) to a code file; the HTTP simulator runs
// that file's program to serve the request. Routes live alongside files so a
// package can carry them.
var PS_ROUTES_KEY='picoscript.routes.v1';
function routesRead(){var ls=filesSafeLocalStorage();if(!ls)return [];try{var r=JSON.parse(ls.getItem(PS_ROUTES_KEY)||'[]');return Array.isArray(r)?r:[];}catch(e){return [];}}
function routesWrite(rs){var ls=filesSafeLocalStorage();if(!ls)return;try{ls.setItem(PS_ROUTES_KEY,JSON.stringify(rs||[]));}catch(e){}}
function matchRoute(methodCode,path){
  var rs=routesRead();
  for(var i=0;i<rs.length;i++){var r=rs[i];
    var rm=(r.method==null||r.method==='*')?null:methodCodeName(r.method);
    var mOk=(rm==null)||(rm===methodCode);
    var pOk=(r.path==null||r.path==='*'||r.path===path);
    if(mOk&&pOk&&r.file&&filesRead()[r.file]) return r;
  }
  return null;
}
function methodCodeName(m){ if(typeof m==='number')return m; return (typeof methodCode==='function')?methodCode(String(m)):0; }
// Bundle the whole tree (code + data cards + sources + static) plus routes into a
// single portable card package (an application).
function exportPackage(){
  var files=filesRead(), pkg={name:'picoapp',version:1,files:{},routes:routesRead()};
  Object.keys(files).forEach(function(n){pkg.files[n]={kind:fileKind(n),lang:files[n].lang||'',src:files[n].src};});
  return pkg;
}
// Load a package into the running service: import its files into the tree, write
// every data card into the live picowal store, and register its routes so the
// HTTP simulator serves them.
function deployPackage(pkg,opts){
  opts=opts||{};
  if(!pkg||typeof pkg!=='object'||!pkg.files){filesStatus('invalid package',true);return false;}
  var files=opts.merge?filesRead():{};
  Object.keys(pkg.files).forEach(function(n){var f=pkg.files[n]||{};if(typeof f.src==='string')files[n]={kind:f.kind||fileKind(n),lang:f.lang||'',src:f.src,updated:Date.now()};});
  filesWrite(files);
  if(Array.isArray(pkg.routes)) routesWrite(pkg.routes);
  var nc=0; Object.keys(files).forEach(function(n){if(fileKind(n)==='card'){var k=cardToWal(n);if(k>0)nc+=k;}});
  filesRender();
  filesStatus('Deployed '+Object.keys(pkg.files).length+' files, '+nc+' picowal cards, '+((pkg.routes||[]).length)+' routes');
  busEmit('deploy',{files:Object.keys(pkg.files).length,cards:nc,routes:(pkg.routes||[]).length});
  return true;
}
function pkgExportOpen(){var m=document.getElementById('pkgModal'),t=document.getElementById('pkgText');if(!m||!t)return;t.value=JSON.stringify(exportPackage(),null,2);m.classList.add('open');document.getElementById('pkgTitle').textContent='Card package (copy to export, or paste + Deploy)';t.focus();}
function pkgModalClose(){var m=document.getElementById('pkgModal');if(m)m.classList.remove('open');}
function pkgDeployFromModal(){var t=document.getElementById('pkgText'),e=document.getElementById('pkgErr');if(!t)return;var pkg;try{pkg=JSON.parse(t.value);}catch(err){if(e){e.textContent='invalid JSON: '+String(err.message||err);e.style.color='#ff7b72';}return;}if(deployPackage(pkg,{merge:false}))pkgModalClose();}
function seedSampleApp(){ deployPackage(SAMPLE_APP,{merge:true}); var f=filesRead(); if(f['routes/orders.psc']) psFilesOpen('routes/orders.psc'); }
// A tiny sample application: a workflow route that branches on method, a picowal
// data card, a request schema, a static asset, and a route binding -- enough to
// demonstrate deploy -> the HTTP simulator serving it.
var SAMPLE_APP={name:'orders-app',version:1,
  files:{
    'routes/orders.psc':{kind:'code',lang:'workflow',src:JSON.stringify([
      {type:'LOAD',name:'method',from:'request',field:'method'},
      {type:'LOAD',name:'len',from:'request',field:'length'},
      {type:'IF',condition:'method is 2'},
      {type:'RESPOND',status:201,contentType:'application/json',body:'{"created":true}'},
      {type:'ELSE'},
      {type:'RESPOND',status:200,contentType:'application/json',body:'{"ok":true}'},
      {type:'END'}
    ],null,2)},
    'cards/config.card.json':{kind:'card',lang:'',src:JSON.stringify({cards:{'10':5,'11':2,'12':1}},null,2)},
    'schemas/request.json':{kind:'source',lang:'',src:JSON.stringify({method:'int',path:'string',body:'json'},null,2)},
    'schemas/orders.schema.json':{kind:'schema',lang:'',src:JSON.stringify({fields:[{name:'qty',type:'int'},{name:'price',type:'int'},{name:'note',type:'str'}]},null,2)},
    'static/index.html':{kind:'static',lang:'',src:'<!doctype html>\n<title>Orders</title>\n<h1>Orders app</h1>\n'},
    'README.md':{kind:'static',lang:'',src:'# Orders app\n\nA sample PicoScript card package. Deploy, then POST /orders in the HTTP simulator.\n'}
  },
  routes:[{method:'POST',path:'/orders',file:'routes/orders.psc'},{method:'GET',path:'/orders',file:'routes/orders.psc'}]
};

function compileSrc(run){
  var lang=document.getElementById('lang').value,src=getSrc(),err=document.getElementById('cerr');
  if(lang==='workflow'){
    if(typeof looksLikeWorkflowJson!=='function'||!looksLikeWorkflowJson(src)){err.textContent='workflow: editor is not a JSON step array';err.style.color='#ff7b72';return;}
    try{var wf=wfCompileSrc(src);src=wf.source;lang='english';WF_LINE_STEPS=wf.lineSteps||null;}
    catch(e){err.textContent='workflow: '+String(e.message||e);err.style.color='#ff7b72';return;}
  } else if(lang==='report'){
    if(typeof looksLikeReportJson!=='function'||!looksLikeReportJson(src)){err.textContent='report: editor is not a {title,columns,rows} template';err.style.color='#ff7b72';return;}
    try{var rr=rptCompileSrc(src);src=rr.source;lang='english';WF_LINE_STEPS=null;}
    catch(e){err.textContent='report: '+String(e.message||e);err.style.color='#ff7b72';return;}
  } else { WF_LINE_STEPS=null; }
  try{var r=PicoCompile.compileDebug(src,lang);DBG.words=r.words.map(function(w){return w>>>0;});DBG.disasm=DBG.words.map(jsDisasm);DBG.vars=r.vars||{};DBG.debug=r.debug||{};DBG.src=src;rebuildDebugMaps();err.textContent='compiled '+DBG.words.length+' words';err.style.color='#7ee787';busEmit('compile.ok',{lang:CUR_LANG,words:DBG.words.length});dbgReset();if(run)dbgRun();if(typeof renderLayout==='function')try{renderLayout();}catch(e){}}
  catch(e){err.textContent=String(e.message||e);err.style.color='#ff7b72';busEmit('compile.error',{lang:CUR_LANG,message:String(e.message||e)});}
}
function dbgReset(){DBG.vm=new PicoVM({cards:walBackend()});DBG.vm.load(DBG.words);render();renderWal();updateSourceDecorations();}
function dbgStep(){if(DBG.vm){DBG.vm.step();render();renderWal();}}
function dbgRun(){if(!DBG.vm)dbgReset();var g=0;while(DBG.vm.step()&&g++<200000){if(pcHasBreakpoint(DBG.vm.pc))break;}render();renderWal();busEmit('run',{steps:DBG.vm.steps,halted:DBG.vm.halted,status:DBG.vm.httpStatus});}
function render(){
  var vm=DBG.vm;if(!vm)return;
  document.getElementById('listing').innerHTML=DBG.disasm.map(function(t,idx){var rec=DBG.debug&&DBG.debug[idx],lc=rec?PicoCompile.offsetToLineCol(DBG.src,rec[0]):[0,0],sym=lc[0]?('  ; L'+lc[0]+':'+lc[1]+' '+PicoCompile.sourceLineText(DBG.src,rec[0]).trim()):'';return '<div class="row'+(idx===vm.pc?' pc':'')+(pcHasBreakpoint(idx)?' bp':'')+'" onclick="toggleBp('+idx+')" title="click to toggle PC breakpoint">'+String(idx).padStart(3,' ')+'  '+esc(t+sym)+'</div>';}).join('');
  var pcrow=document.querySelector('#listing .row.pc');if(pcrow)pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('regs').innerHTML='<div class="r">PC <b>'+vm.pc+'</b></div>'+Array.from(vm.regs).map(function(v,idx){return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  var wb=document.getElementById('watches');
  if(wb){var vars=DBG.vars||{},ks=Object.keys(vars);wb.innerHTML=ks.length?ks.map(function(name){var rr=vars[name];return '<tr><td>'+esc(name)+'</td><td>R'+rr+'</td><td><b>'+vm.regs[rr]+'</b></td></tr>';}).join(''):'<tr><td colspan="3" style="color:var(--muted)">(none)</td></tr>';}
  document.querySelectorAll('#playDbgPanels .vm-state').forEach(function(el){el.textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted+'  http_status='+vm.httpStatus;});
  var _txt=vm.outputText(),_pr=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_txt);
  document.getElementById('out').textContent='output: ['+vm.outputInts().join(', ')+']'+(_pr&&_txt?'\ntext: '+JSON.stringify(_txt):'');
  updateSourceDecorations();
  if(CUR_LANG==='workflow'){ try{ wfHighlightActive(); wfRenderBreakpoints(); }catch(e){} }
}
function jsDisasm(w){
  var names=["NOOP","LOAD","SAVE","PIPE","ADD","SUB","MUL","DIV","INC","JUMP","BRANCH","CALL","RETURN","WAIT","RAISE","DSP"];
  var br=["EQ","NE","LT","GT","LE","GE","Z","NZ","EOF","ERR"];
  var op=(w>>>28)&0xF,rd=(w>>>24)&0xF,rs1=(w>>>20)&0xF,rs2=(w>>>16)&0xF,imm=w&0xFFFF;
  if(op===0&&(imm&0xFF00)===0x7000)return"HOSTCALL #0x"+(imm&0xFF).toString(16);
  if(op===0&&(imm&0xF000)===0x8000)return"NET.STATUS "+(imm&0xFFF);
  if(op===0&&(imm&0xF000)===0xA000)return"NET.TYPE #0x"+imm.toString(16);
  if(op===0&&imm===0xC000)return"NET.CLOSE";
  if(op>=4&&op<=7)return names[op]+" R"+rd+", R"+rs1+(rs2===1?(", R"+(imm&0xF)):(", #"+imm));
  if(op===8)return"INC R"+rd;
  if(op===9)return"JUMP "+imm;
  if(op===10){var off=imm&0x8000?imm-0x10000:imm;return"BRANCH "+(br[rs2]||rs2)+" R"+rd+", R"+rs1+", "+(off>=0?"+":"")+off;}
  if(op===11)return"CALL "+imm;
  if(op>=1&&op<=3)return names[op]+" R"+(op===1?rd:rs1)+", [0x"+imm.toString(16)+"]";
  if(op===12)return"RETURN";
  return names[op]||("?"+op);
}

// PicoWAL
function walBackend(){return{get:function(a){var v=localStorage.getItem(WAL_PREFIX+a);return v===null?0:(parseInt(v,10)|0);},set:function(a,val){try{localStorage.setItem(WAL_PREFIX+a,String(val|0));}catch(e){}}};}
function walEntries(){var out=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(WAL_PREFIX)===0)out.push([parseInt(k.slice(WAL_PREFIX.length),10),parseInt(localStorage.getItem(k),10)]);}out.sort(function(a,b){return a[0]-b[0];});return out;}
function walClear(){var ks=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(WAL_PREFIX)===0)ks.push(k);}ks.forEach(function(k){localStorage.removeItem(k);});renderWal();}
function renderWal(){var rows=walEntries().map(function(e){return'<tr><td>'+e[0]+'</td><td>'+e[1]+'</td></tr>';}).join('');var html=rows||'<tr><td colspan="2" style="color:var(--muted)">(empty)</td></tr>';['walbody','walbody2'].forEach(function(id){var el=document.getElementById(id);if(el)el.innerHTML=html;});}

// Cards/Query
var CARD_PREFIX="picocard:";
function cardBackend(){return{get:function(k){return localStorage.getItem(CARD_PREFIX+k);},set:function(k,v){localStorage.setItem(CARD_PREFIX+k,v);},remove:function(k){localStorage.removeItem(CARD_PREFIX+k);},keys:function(){var ks=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(CARD_PREFIX)===0)ks.push(k.slice(CARD_PREFIX.length));}return ks;}};}
var STORE=new PicoStore.PicoStore(cardBackend());
function curPack(){return(document.getElementById('packname').value||'orders').trim();}
function cardMsg(m,err){var e=document.getElementById('cardmsg');e.textContent=m||'';e.style.color=err?'var(--err)':'var(--muted)';}
// ---- schema binding (Cards/Query become type-aware once a pack is bound) ----
// A pack named "orders" binds to schemas/orders.schema.json by convention (the
// same naming the sample app + Schema Designer use). When bound, Cards shows a
// typed quick-add form + typed columns instead of raw JSON, and Query offers the
// field names as clickable chips (a light "intellisense" for query authoring).
function schemaForPack(pack){
  var files=(typeof filesRead==='function')?filesRead():{};
  var f=files['schemas/'+pack+'.schema.json'];
  if(!f) return null;
  try{ var d=JSON.parse(f.src); return (d&&Array.isArray(d.fields)&&d.fields.length)?d.fields:null; }catch(e){ return null; }
}
function rowsHtml(entries,withDel,fields){
  var cols=(fields&&fields.length)?(1+fields.length+(withDel?1:0)):(withDel?3:2);
  if(!entries.length)return'<tr><td colspan="'+cols+'" style="color:var(--muted)">(none)</td></tr>';
  return entries.map(function(e){
    var id=e[0],rec=e[1]||{};
    var body;
    if(fields&&fields.length) body=fields.map(function(f){var v=rec[f.name];return '<td>'+esc(v===undefined?'':String(v))+'</td>';}).join('');
    else body='<td>'+esc(JSON.stringify(rec))+'</td>';
    var del=withDel?'<td><button class="ghost" style="padding:1px 7px" onclick="cardDelete('+id+')">&times;</button></td>':'';
    return'<tr><td>'+id+'</td>'+body+del+'</tr>';
  }).join('');
}
function cardRenderSchemaInfo(){
  var pack=curPack(), fields=schemaForPack(pack);
  var info=document.getElementById('cardSchemaInfo'), form=document.getElementById('cardTypedForm'),
    jsonBox=document.getElementById('cardjson'), head=document.getElementById('cardHead');
  if(!info) return;
  if(fields){
    info.innerHTML='bound to <b>schemas/'+esc(pack)+'.schema.json</b> ('+fields.length+' field'+(fields.length===1?'':'s')+')';
    info.style.color='var(--accent)';
    if(jsonBox) jsonBox.style.display='none';
    if(form){
      form.style.display='flex';
      form.innerHTML=fields.map(function(f){
        var isStr=sdStrType(f.type);
        return '<span style="display:inline-flex;flex-direction:column;gap:2px"><label class="muted" style="font-size:10px">'+esc(f.name)+' <span style="opacity:.6">'+esc(f.type)+'</span></label>'+
          '<input id="cf_'+esc(f.name)+'" '+(isStr?'':'type="number"')+' style="width:90px" placeholder="'+esc(f.name)+'"></span>';
      }).join('')+'<button class="act" style="align-self:flex-end" onclick="cardCreateTyped()">Create</button>';
    }
    if(head) head.innerHTML='<th>id</th>'+fields.map(function(f){return '<th>'+esc(f.name)+'</th>';}).join('')+'<th></th>';
  } else {
    info.innerHTML='unbound (free-form JSON) &mdash; add <b>schemas/'+esc(pack)+'.schema.json</b> to type this pack';
    info.style.color='var(--muted)';
    if(jsonBox) jsonBox.style.display='block';
    if(form){ form.style.display='none'; form.innerHTML=''; }
    if(head) head.innerHTML='<th>id</th><th>record</th><th></th>';
  }
}
function queryRenderHead(){
  var head=document.getElementById('qHead'), fields=schemaForPack(curPack());
  if(!head) return;
  head.innerHTML=fields?('<th>id</th>'+fields.map(function(f){return '<th>'+esc(f.name)+'</th>';}).join('')):'<th>id</th><th>record</th>';
}
function cardRender(){
  cardRenderSchemaInfo();
  var fields=schemaForPack(curPack());
  document.getElementById('cardlist').innerHTML=rowsHtml(STORE.all(curPack()),true,fields);
}
function cardCreate(){var pack=curPack(),rec;try{rec=JSON.parse(document.getElementById('cardjson').value);}catch(e){cardMsg('JSON: '+e.message,true);return;}try{var id=STORE.create(pack,rec);var hex=STORE.cardBytesHex(pack,id);document.getElementById('serout').textContent=hex;cardMsg('#'+id+' created',false);cardRender();}catch(e){cardMsg(e.message,true);}}
// Build a record from the typed quick-add form (schema-bound packs only).
function cardCreateTyped(){
  var pack=curPack(), fields=schemaForPack(pack);
  if(!fields){ cardMsg('pack is not schema-bound',true); return; }
  var rec={};
  fields.forEach(function(f){
    var el=document.getElementById('cf_'+f.name); if(!el) return;
    rec[f.name]=sdStrType(f.type)?el.value:(parseInt(el.value,10)||0);
  });
  try{ var id=STORE.create(pack,rec); var hex=STORE.cardBytesHex(pack,id); document.getElementById('serout').textContent=hex; cardMsg('#'+id+' created',false); cardRender(); }
  catch(e){ cardMsg(e.message,true); }
}
function cardSeed(){var pack=curPack();[{qty:42,sku:"ABC",status:1},{qty:7,sku:"XYZ",status:0},{qty:99,sku:"ABC",status:1},{qty:55,sku:"QRS",status:2}].forEach(function(r){STORE.create(pack,r);});cardMsg('Seeded 4',false);cardRender();}
function cardClear(){var pack=curPack();STORE.all(pack).forEach(function(e){STORE.delete(pack,e[0]);});STORE.b.remove(pack+":ids");STORE.b.remove(pack+":next");document.getElementById('qresults').innerHTML='';cardMsg('Cleared',false);cardRender();}
function cardDelete(id){STORE.delete(curPack(),id);cardRender();}
function cardQuery(){
  var pack=curPack(),q=document.getElementById('querybox').value,fields=schemaForPack(pack);
  queryRenderHead(); queryRenderChips();
  try{var res=STORE.query(pack,q);document.getElementById('qresults').innerHTML=rowsHtml(res,false,fields);cardMsg(res.length+' match'+(res.length===1?'':'es'),false);}catch(e){cardMsg(e.message,true);}
}
// Query field chips: when the pack is schema-bound, list its field names as
// clickable chips that insert into the query box (lightweight intellisense).
function queryRenderChips(){
  var host=document.getElementById('queryFieldChips'); if(!host) return;
  var fields=schemaForPack(curPack());
  if(!fields){ host.innerHTML=''; return; }
  host.innerHTML='<span class="muted" style="font-size:10px;margin-right:4px">fields:</span>'+fields.map(function(f){
    return '<button class="ghost" style="padding:1px 7px;font-size:10px" onclick="queryInsertField(\''+esc(f.name)+'\')">'+esc(f.name)+'</button>';
  }).join(' ');
}
function queryInsertField(name){
  var el=document.getElementById('querybox'); if(!el) return;
  var v=el.value||''; el.value=(v&&!/\s$/.test(v))?(v+' '+name):(v+name); el.focus();
}

// HTTP/TCP
function methodCode(m){return({GET:1,POST:2,PUT:3,DELETE:4,HEAD:5,PATCH:6,OPTIONS:7})[(m||'').toUpperCase()]||0;}
function bytesOf(s){var a=[];for(var i=0;i<s.length;i++)a.push(s.charCodeAt(i)&0xFF);return a;}
function parseRequest(text,isHex){
  var bytes=[], raw=text, path='', query='', body='';
  if(isHex){
    text.replace(/0x/gi,'').replace(/[,]/g,' ').trim().split(/\s+/).filter(Boolean).forEach(function(h){
      if(h.length>2){for(var i=0;i+1<h.length;i+=2)bytes.push(parseInt(h.substr(i,2),16)&0xFF);}
      else if(h.length)bytes.push(parseInt(h,16)&0xFF);
    });
    raw=String.fromCharCode.apply(null,bytes); body=raw; query=raw;
  }else{
    bytes=bytesOf(text);
  }
  var sum=0;bytes.forEach(function(b){sum=(sum+b)|0;});
  var method=0,pathLen=0,bodyLen=body.length;
  if(!isHex){
    var first=(text.split(/\r?\n/)[0]||''),fl=first.split(/\s+/);
    if(fl.length>=2){
      method=methodCode(fl[0]); path=fl[1]||''; pathLen=path.length;
      var qi=path.indexOf('?'); query=qi>=0?path.slice(qi+1):'';
    }
    var idx=text.indexOf('\r\n\r\n'),sep=4;if(idx<0){idx=text.indexOf('\n\n');sep=2;}
    if(idx>=0){body=text.slice(idx+sep);bodyLen=body.length;if(bodyLen<0)bodyLen=0;}
  }
  var pathOnly=path; var qmark=pathOnly.indexOf('?'); if(qmark>=0)pathOnly=pathOnly.slice(0,qmark);
  return{bytes:bytes,length:bytes.length,method:method,bodyLen:bodyLen,sum:sum,pathLen:pathLen,
         path:pathOnly,pathRaw:path,
         pathBytes:bytesOf(path),queryBytes:bytesOf(query),bodyBytes:bytesOf(body)};
}
function writeBytes(wal,base,arr,max){var n=Math.min(arr.length,max);for(var i=0;i<n;i++)wal.set(base+i,arr[i]);return n;}
function writeDescriptor(wal,req){
  wal.set(0,req.length); wal.set(1,req.method); wal.set(2,req.bodyLen); wal.set(3,req.sum); wal.set(4,req.pathLen);
  wal.set(5,req.queryBytes.length); wal.set(6,req.bodyBytes.length);
  writeBytes(wal,256,req.bytes,256); writeBytes(wal,512,req.pathBytes,256); writeBytes(wal,768,req.queryBytes,256); writeBytes(wal,1024,req.bodyBytes,512);
  /* Low-card mirrors for simple Storage.Load examples (old 5-bit card address form). */
  writeBytes(wal,10,req.queryBytes,10); writeBytes(wal,20,req.bodyBytes,12);
}
function toCompilable(){var lang=document.getElementById('lang').value,src=getSrc();if(lang==='workflow'){var wf=wfCompileSrc(src);return{src:wf.source,lang:'english'};}if(lang==='report'){var rr=rptCompileSrc(src);return{src:rr.source,lang:'english'};}return{src:src,lang:lang};}
function fileCompilable(f){if(!f)return null;if(f.lang==='workflow'){var wf=wfCompileSrc(f.src);return{src:wf.source,lang:'english'};}return{src:f.src,lang:f.lang||'basic'};}
function sendRequest(){var text=document.getElementById('reqbox').value,isHex=document.getElementById('reqmode').value==='hex';var req=parseRequest(text,isHex),wal=walBackend();writeDescriptor(wal,req);var routed=matchRoute(req.method,req.path),cl;try{cl=routed?fileCompilable(filesRead()[routed.file]):toCompilable();var r=PicoCompile.compile(cl.src,cl.lang);}catch(e){document.getElementById('respout').textContent='compile error: '+(e.message||e);return;}var vm=new PicoVM({cards:wal});vm.run(r.words);renderResponse(vm,req);if(routed){var el=document.getElementById('respout');el.textContent='\u2192 routed to '+routed.file+'\n'+el.textContent;}renderWal();busEmit('http.request',{method:req.method,path:req.path,status:vm.httpStatus,route:routed?routed.file:null});}
function sendTcp(){var text=document.getElementById('tcpbox').value;var req=parseRequest(text,true),wal=walBackend();writeDescriptor(wal,req);var cl;try{cl=toCompilable();var r=PicoCompile.compile(cl.src,cl.lang);}catch(e){document.getElementById('tcpout').textContent='compile error: '+(e.message||e);return;}var vm=new PicoVM({cards:wal});vm.run(r.words);renderTcpResponse(vm,req);renderWal();}
function responseBodyText(vm){return (typeof vm.outputDisplayText==='function')?vm.outputDisplayText():vm.outputText();}
function renderResponse(vm,req){var reasons={200:'OK',201:'Created',400:'Bad Request',404:'Not Found',500:'Error'};var el=document.getElementById('respout');el.style.color='#7ee787';var body=vm.outputInts();if(vm.httpStatus<0){el.style.color='#ffd866';el.textContent='(no Net.Status)\noutput: ['+body.join(', ')+']';return;}var L=[];L.push('HTTP/1.1 '+vm.httpStatus+' '+(reasons[vm.httpStatus]||''));L.push('Content-Type: '+(vm.httpType||'application/octet-stream'));L.push('X-Steps: '+vm.steps);L.push('');var _bt=responseBodyText(vm),_bp=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_bt);L.push(_bp&&_bt?_bt:JSON.stringify(body));el.textContent=L.join('\n');}
function renderTcpResponse(vm,req){var el=document.getElementById('tcpout');el.style.color='#7ee787';var txt=responseBodyText(vm),printable=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(txt),body=vm.outputInts();el.textContent=(vm.httpStatus>=0?('status '+vm.httpStatus+'\n'):'')+(printable&&txt?txt:('output: ['+body.join(', ')+']'));}
function loadResponder(){document.getElementById('lang').value='basic';onLangChange();setSrc(RESPONDER);compileSrc(false);}
function loadSample(){document.getElementById('reqmode').value='text';document.getElementById('reqbox').value='POST /orders HTTP/1.1\r\nHost: pico.dev\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"qty": 42}';}
function loadTcpSample(){document.getElementById('tcpbox').value='63 6d 64 3d 50 49 4e 47 26 6e 3d 33';}

// Tabbed tool panel (HTTP/TCP/Cards/Query/Spans)
var TOOL_TAB='http', TOOL_PINNED=false;
function selectToolTab(tab){
  TOOL_TAB=tab||TOOL_TAB;
  document.querySelectorAll('.tool-tab').forEach(function(p){p.classList.toggle('active',p.id==='tool-'+TOOL_TAB);});
  document.querySelectorAll('.tool-tabs button').forEach(function(b){b.classList.toggle('active',b.getAttribute('data-tool')===TOOL_TAB);});
  if(TOOL_TAB==='cards'||TOOL_TAB==='query')try{cardRender();}catch(e){}
  if(TOOL_TAB==='query')try{queryRenderHead();queryRenderChips();}catch(e){}
  if(TOOL_TAB==='report')try{renderLayout();}catch(e){}
}
function openToolPanel(tab){
  selectToolTab(tab||TOOL_TAB);
  document.getElementById('toolPanel').classList.add('open');
  document.getElementById('flyoutOverlay').classList.toggle('open',!TOOL_PINNED);
  syncToolLayout();
}
function closeToolPanel(force){
  if(TOOL_PINNED&&!force)return;
  document.getElementById('toolPanel').classList.remove('open');
  document.getElementById('flyoutOverlay').classList.remove('open');
  syncToolLayout();
}
function syncToolLayout(){
  var main=document.querySelector('.main');
  if(main) main.classList.toggle('tool-pinned',TOOL_PINNED&&document.getElementById('toolPanel').classList.contains('open'));
  if(window.EDITOR&&typeof window.EDITOR.layout==='function')setTimeout(function(){try{window.EDITOR.layout();}catch(e){}},0);
}
function toggleToolPin(){
  TOOL_PINNED=!TOOL_PINNED;
  var b=document.getElementById('toolPin');
  if(b){b.classList.toggle('active',TOOL_PINNED);b.textContent=TOOL_PINNED?'📍':'📌';b.title=TOOL_PINNED?'Unpin tools panel':'Pin tools panel';b.setAttribute('aria-label',b.title);}
  if(document.getElementById('toolPanel').classList.contains('open'))
    document.getElementById('flyoutOverlay').classList.toggle('open',!TOOL_PINNED);
  syncToolLayout();
}

// Reference tree
var REF_SECTIONS=['ref-overview','ref-syntax','ref-namespaces','ref-bindings','ref-samples','ref-internals','ref-rawdocs'];
var REF_LABELS=['Overview','Language Syntax','Namespaces','Bindings & I/O','App Samples','Internals','Source Docs'];
function buildRefTree(){var el=document.getElementById('refTree');if(!el)return;var html='<div class="group-title">Reference</div>';REF_SECTIONS.forEach(function(id,i){html+='<div class="tree-item'+(i===0?' active':'')+'" data-ref="'+id+'" onclick="showRefSection(\''+id+'\')">'+REF_LABELS[i]+(id==='ref-internals'?' <span class="badge" style="font-size:8px">impl</span>':'')+'</div>';});el.innerHTML=html;}
function showRefSection(id){showRefInline(id);} // backward compat
function showDoc(k){document.querySelectorAll('.docpanel').forEach(function(e){e.classList.remove('active');});document.querySelectorAll('.docnav-btn').forEach(function(e){e.classList.remove('active');});document.getElementById('doc-'+k).classList.add('active');var btn=document.querySelector('.docnav-btn[data-doc="'+k+'"]');if(btn)btn.classList.add('active');}

// Namespace reference
function buildNsRef(){
  var NS_DESC={Kernel:'IRQs, profiling, tracing',Req:'Read HTTP request (method, path, headers, body)',Resp:'Construct HTTP response',Queue:'Message queue ops',Random:'RNG',Memory:'Byte-addressable arena (native in toC)',Bits:'Bitwise & shift: and/or/xor/not/shl/shr/sar',Dot8:'Int8 dot product (NEON SDOT / Cortex-M33 SMLAD)',Span:'Zero-copy views',Descriptor:'Pool descriptors',Lease:'Memory leases',Storage:'PicoWAL card CRUD',Thread:'Yield hints',Io:'Direct output',Utf8Writer:'Byte/string writer',Utf8Reader:'Span scanner',Json:'JSON construction',Xml:'XML/HTML elements',String:'Arena string ops: concat/replace/find/case/trim',Number:'Int parse/format + abs/min/max',Template:'AOT {{hole}} templates: Compile (at save) + Render',Maths:'Math functions',DateTime:'Date/time',Locale:'i18n',Environment:'System info',Context:'Request context',Crypto:'Cryptography',Compress:'Compression',X509:'Certificates',Auth:'Authentication',Http:'HTTP parsing',Html:'HTML DOM'};
  var html='<div style="color:var(--muted);font-size:11.5px;margin-bottom:12px">Host namespaces callable as <code>Namespace.Method(a,b)</code>. <span style="opacity:.55">Greyed / <b>planned</b></span> = defined in the ABI but not yet runtime-dispatchable.</div>';
  var names=Object.keys(NSDATA).sort(function(a,b){var ai=NSDATA[a].some(function(m){return m.impl;}),bi=NSDATA[b].some(function(m){return m.impl;});return (ai===bi)?a.localeCompare(b):(ai?-1:1);});
  var BADGE=' <span style="font-size:9px;background:#4a3a1a;color:#ffd27a;padding:1px 5px;border-radius:3px;vertical-align:middle">planned</span>';
  names.forEach(function(ns){var anyImpl=NSDATA[ns].some(function(m){return m.impl;});html+='<div class="ns-card"'+(anyImpl?'':' style="opacity:.55"')+'><h4>'+esc(ns)+(anyImpl?'':BADGE)+'</h4><div style="color:var(--muted);font-size:11px;margin-bottom:4px">'+(NS_DESC[ns]||'')+'</div>';NSDATA[ns].forEach(function(m){html+='<div class="method"'+(m.impl?'':' style="opacity:.6"')+'>'+esc(ns)+'.'+esc(m.method)+'() <span class="code">'+m.code+'</span>'+(m.impl?'':BADGE)+'</div>';});html+='</div>';});
  document.getElementById('nsContent').innerHTML=html;
}
function renderSyntaxRef(){
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle',cobol:'cobstyle',report:'rptstyle'};
  var html='<p style="color:var(--muted);font-size:12px;margin-bottom:12px">Showing <b>'+lang.toUpperCase()+'</b>. Toggle dialect above.</p>';
  DATA.forEach(function(d,i){
    var src;
    if(d[lang]){src=d[lang].src;}
    else if(typeof PicoCompile!=='undefined'&&PicoCompile.translate){var fl=d.c?'c':(d.basic?'basic':'python');src=PicoCompile.translate(d[fl].src,fl,lang);}
    else return;
    if(!src)return;
    html+='<div style="margin-bottom:14px"><div style="font-weight:600;font-size:13px;margin-bottom:3px">'+(i+1)+'. '+esc(d.title)+'</div><pre class="'+(SC[lang]||'cstyle')+'" style="max-width:700px">'+esc(src)+'</pre></div>';
  });
  document.getElementById('syntaxContent').innerHTML=html;
}
function renderSamples(){
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle',cobol:'cobstyle',report:'rptstyle'};
  var samples=[
    {title:'PicoWAL API Server',desc:'CRUD API routing by readable HTTP constants',c:'int method = Req.Method();\nStorage.UsePack(1);\nif (method == METHOD_POST) {\n    int id = Storage.AddCard();\n    Resp.Status(STATUS_CREATED);\n    print(id);\n} else {\n    int n = Storage.QueryCard(0);\n    Resp.Status(STATUS_OK);\n    print(n);\n}\nResp.End();',basic:"DIM METHOD = Req.Method()\nStorage.UsePack(1)\nIF METHOD EQ METHOD_POST THEN\n    DIM ID = Storage.AddCard()\n    Resp.Status(STATUS_CREATED)\n    PRINT ID\nELSE\n    DIM N = Storage.QueryCard(0)\n    Resp.Status(STATUS_OK)\n    PRINT N\nENDIF\nResp.End()",python:'method = Req.Method()\nStorage.UsePack(1)\nif method == METHOD_POST:\n    id = Storage.AddCard()\n    Resp.Status(STATUS_CREATED)\n    print(id)\nelse:\n    n = Storage.QueryCard(0)\n    Resp.Status(STATUS_OK)\n    print(n)\nResp.End()',english:'Set method to Req.Method().\nStorage.UsePack(1).\nIf method is METHOD_POST:\n    Set id to Storage.AddCard().\n    Resp.Status(STATUS_CREATED).\n    Print id.\nOtherwise:\n    Set n to Storage.QueryCard(0).\n    Resp.Status(STATUS_OK).\n    Print n.\nResp.End().'},
    {title:'Template Web Server',desc:'Load template card, render page',c:'Resp.Status(200);\nResp.Header(0);\nStorage.UsePack(2);\nStorage.EditCard(1);\nint tpl = Storage.GetField(0);\nprint(tpl);\nResp.End();',basic:"Resp.Status(200)\nResp.Header(0)\nStorage.UsePack(2)\nStorage.EditCard(1)\nDIM TPL = Storage.GetField(0)\nPRINT TPL\nResp.End()",python:'Resp.Status(200)\nResp.Header(0)\nStorage.UsePack(2)\nStorage.EditCard(1)\ntpl = Storage.GetField(0)\nprint(tpl)\nResp.End()',english:'Resp.Status(200).\nResp.Header(0).\nStorage.UsePack(2).\nStorage.EditCard(1).\nSet tpl to Storage.GetField(0).\nPrint tpl.\nResp.End().'},
    {title:'App Status Page',desc:'JSON stats with request counter',c:'Resp.Status(200);\nStorage.UsePack(3);\nStorage.EditCard(1);\nint count = Storage.GetField(0);\ncount++;\nStorage.SetField(0, count);\nprint(count);\nResp.End();',basic:"Resp.Status(200)\nStorage.UsePack(3)\nStorage.EditCard(1)\nDIM COUNT = Storage.GetField(0)\nINC COUNT\nStorage.SetField(0, COUNT)\nPRINT COUNT\nResp.End()",python:'Resp.Status(200)\nStorage.UsePack(3)\nStorage.EditCard(1)\ncount = Storage.GetField(0)\ncount += 1\nStorage.SetField(0, count)\nprint(count)\nResp.End()',english:'Resp.Status(200).\nStorage.UsePack(3).\nStorage.EditCard(1).\nSet count to Storage.GetField(0).\nIncrease count by 1.\nStorage.SetField(0, count).\nPrint count.\nResp.End().'}
  ];
  var html='';samples.forEach(function(s){
    var src=s[lang];
    if(!src&&typeof PicoCompile!=='undefined'&&PicoCompile.translate){src=PicoCompile.translate(s.c,'c',lang);}
    if(!src)src=s.c;
    html+='<div style="margin-bottom:18px"><div style="font-weight:600;font-size:14px;margin-bottom:3px">'+esc(s.title)+'</div><div style="color:var(--muted);font-size:12px;margin-bottom:6px">'+esc(s.desc)+'</div><pre class="'+(SC[lang]||'cstyle')+'" style="max-width:700px">'+esc(src)+'</pre></div>';
  });
  document.getElementById('samplesContent').innerHTML=html;
}

// ---- workflow surface (visual step list -> English pre-compile) -------------
// The compiler is BareMetal.WorkflowPico (vendored from BareMetalJsTools); the
// designer below edits a JSON step list that syncs to the editor and recompiles.
var WF_SNIPPET=JSON.stringify([
  {type:'SET', name:'data', value:[10,20,30,40]},
  {type:'SET', name:'sum', value:0},
  {type:'FOREACH', var:'item', in:'data'},
  {type:'SET', name:'sum', expr:'sum + item'},
  {type:'END'},
  {type:'IF', condition:'sum >= 50'},
  {type:'LOG', message:'sum'},
  {type:'END'}
], null, 2);
function looksLikeWorkflowJson(src){
  if(!src||!src.trim()) return false;
  try{ var d=JSON.parse(src); return Array.isArray(d)||(d&&Array.isArray(d.steps)); }
  catch(e){ return false; }
}
// A Raise/On step's `eventFile` names an events/*.event.json (see the Event
// Schema Designer); resolve it to {mode,fields,returns} fresh on every compile
// (never persisted into the step JSON, mirroring the Report designer's `pack`
// binding) so WorkflowPico can lower a fixed typed payload via Map + PSC1.
function eventSchemaForFile(name){
  if(!name) return null;
  var files=(typeof filesRead==='function')?filesRead():{}, f=files[name];
  if(!f) return null;
  try{ var d=JSON.parse(f.src); return (d&&Array.isArray(d.fields))?{mode:d.mode==='sync'?'sync':'async',fields:d.fields,returns:d.returns||null}:null; }catch(e){ return null; }
}
function wfResolveEventSchemas(steps){
  return steps.map(function(s){
    if(s&&(s.type==='RAISE'||s.type==='EMIT'||s.type==='ON'||s.type==='SUBSCRIBE')&&s.eventFile){
      var sch=eventSchemaForFile(s.eventFile);
      if(sch){ var s2={}; for(var k in s) s2[k]=s[k]; s2.schema=sch; return s2; }
    }
    return s;
  });
}
// BareMetal.WorkflowPico.compile expects a steps ARRAY (or registered name); the
// editor surface is a JSON string, so parse it here before compiling.
function wfCompileSrc(src){
  var d=JSON.parse(src);
  var steps=Array.isArray(d)?d:(d&&Array.isArray(d.steps)?d.steps:null);
  if(!steps) throw new Error('workflow: expected a JSON step array');
  return BareMetal.WorkflowPico.compile(wfResolveEventSchemas(steps));
}
// ---- designer step/trace highlight + box breakpoints ------------------------
// The derived-English line the VM is executing maps back to a workflow step via
// WorkflowPico's lineSteps; steps map to designer boxes because .fc-node elements
// render in document (= flat step) order, excluding ELSE/END markers.
var WF_LINE_STEPS=null, WF_FLOW_WIRED=false;
function wfCurSteps(){ return (FLOW&&FLOW.getSteps?FLOW.getSteps():wfParseSteps())||[]; }
function wfBoxForStep(si){
  if(si==null) return null;
  var steps=wfCurSteps(), ord=-1;
  for(var i=0;i<=si&&i<steps.length;i++){var t=String(steps[i].type||'').toUpperCase();if(t!=='ELSE'&&t!=='END')ord++;}
  if(ord<0) return null;
  return document.querySelectorAll('#wfFlow .fc-node')[ord]||null;
}
function wfStepForBox(box){
  var nodes=Array.prototype.slice.call(document.querySelectorAll('#wfFlow .fc-node'));
  var ord=nodes.indexOf(box); if(ord<0) return null;
  var steps=wfCurSteps(), c=-1;
  for(var i=0;i<steps.length;i++){var t=String(steps[i].type||'').toUpperCase();if(t!=='ELSE'&&t!=='END'){c++;if(c===ord)return i;}}
  return null;
}
function wfFirstLineForStep(si){ if(!WF_LINE_STEPS||si==null)return 0; for(var L=0;L<WF_LINE_STEPS.length;L++){if(WF_LINE_STEPS[L]===si)return L+1;} return 0; }
function wfHighlightActive(){
  var host=document.getElementById('wfFlow'); if(!host) return;
  host.querySelectorAll('.fc-node.wf-active').forEach(function(n){n.classList.remove('wf-active');});
  if(CUR_LANG!=='workflow'||!DBG.vm||DBG.vm.halted||!WF_LINE_STEPS) return;
  var line=DBG.pcLines[DBG.vm.pc]; if(!line) return;
  var box=wfBoxForStep(WF_LINE_STEPS[line-1]);
  if(box){ box.classList.add('wf-active'); try{box.scrollIntoView({block:'nearest'});}catch(e){} }
}
function wfRenderBreakpoints(){
  var host=document.getElementById('wfFlow'); if(!host||CUR_LANG!=='workflow') return;
  host.querySelectorAll('.fc-node').forEach(function(box){
    var si=wfStepForBox(box), ln=wfFirstLineForStep(si);
    box.classList.toggle('wf-bp', !!(ln&&DBG.sourceBps&&DBG.sourceBps[ln]));
  });
}
function wfToggleBoxBp(box){
  var si=wfStepForBox(box); if(si==null) return;
  var ln=wfFirstLineForStep(si);
  if(!ln){ filesStatus&&filesStatus('compile the workflow first to set a breakpoint',true); return; }
  if(typeof toggleSourceBreakpoint==='function') toggleSourceBreakpoint(ln);
  else { DBG.sourceBps[ln]=!DBG.sourceBps[ln]; if(!DBG.sourceBps[ln])delete DBG.sourceBps[ln]; }
  wfRenderBreakpoints();
}
function wfWireFlowClicks(){
  if(WF_FLOW_WIRED) return; var host=document.getElementById('wfFlow'); if(!host) return;
  // Click a box's type label (e.g. "IF"/"RESPOND") to toggle a breakpoint on it.
  host.addEventListener('click', function(e){
    var lbl=e.target&&e.target.closest?e.target.closest('.fc-type'):null; if(!lbl)return;
    var box=lbl.closest('.fc-node'); if(box){ e.preventDefault(); e.stopPropagation(); wfToggleBoxBp(box); }
  }, true);
  WF_FLOW_WIRED=true;
}
var WF_EXAMPLES={
  'Array sum':[
    {type:'SET',name:'data',value:[10,20,30,40]},
    {type:'SET',name:'sum',value:0},
    {type:'FOREACH','var':'item','in':'data'},
    {type:'SET',name:'sum',expr:'sum + item'},
    {type:'END'},
    {type:'LOG',message:'sum'}
  ],
  'Filter & sum (>= 10)':[
    {type:'SET',name:'data',value:[5,12,8,20,3]},
    {type:'SET',name:'sum',value:0},
    {type:'FOREACH','var':'item','in':'data'},
    {type:'IF',condition:'item >= 10'},
    {type:'SET',name:'sum',expr:'sum + item'},
    {type:'END'},
    {type:'END'},
    {type:'LOG',message:'sum'}
  ],
  'Counted loop (1..5)':[
    {type:'SET',name:'sum',value:0},
    {type:'FOR','var':'i',from:1,to:5},
    {type:'SET',name:'sum',expr:'sum + i'},
    {type:'END'},
    {type:'LOG',message:'sum'}
  ],
  'Budget reject flow':[
    {type:'LOAD',name:'total',from:'scratch',key:0},
    {type:'LOAD',name:'budget',from:'scratch',key:1},
    {type:'IF',condition:'total > budget'},
    {type:'SAVE',name:'total',to:'scratch',key:4000},
    {type:'END'},
    {type:'LOG',message:'total'}
  ],
  'Serve HTTP':[
    {type:'SET',name:'ok',value:1},
    {type:'IF',condition:'ok is 1'},
    {type:'RESPOND',status:200,contentType:'application/json',body:'{"ok":true,"from":"workflow"}'},
    {type:'ELSE'},
    {type:'RESPOND',status:500,body:'error'},
    {type:'END'}
  ],
  'Route by method':[
    {type:'LOAD',name:'method',from:'request',field:'method'},
    {type:'LOAD',name:'len',from:'request',field:'length'},
    {type:'IF',condition:'method is 2'},
    {type:'RESPOND',status:201,contentType:'application/json',body:'{"created":true}'},
    {type:'ELSE'},
    {type:'RESPOND',status:200,contentType:'application/json',body:'{"ok":true}'},
    {type:'END'}
  ]
};
function wfParseSteps(){ try{ var d=JSON.parse(getSrc()); if(Array.isArray(d)) return d; if(d&&Array.isArray(d.steps)) return d.steps; }catch(e){} return null; }
// The rich designer is BareMetal.Workflow.Designer (vendored): a christmas-tree
// flow-chart canvas (boxes with fan-out branches for loops/choices). It owns
// #wfFlow and emits the step list on every edit; we sync that to the editor.
var FLOW=null, FLOW_OWNS=false;
function wfPopulateExamples(){
  var sel=document.getElementById('wfExample'); if(!sel||sel.__done) return;
  sel.innerHTML='<option value="">example\u2026</option>'+Object.keys(WF_EXAMPLES).map(function(n){return '<option>'+esc(n)+'</option>';}).join('');
  sel.__done=true;
}
function wfLoadExample(){
  var sel=document.getElementById('wfExample'); if(!sel||!sel.value) return;
  var ex=WF_EXAMPLES[sel.value]; if(!ex) return;
  setSrc(JSON.stringify(ex.map(function(s){return JSON.parse(JSON.stringify(s));}),null,2));
  wfRenderDesigner(); compileSrc(false);
}
function wfUpdateEng(steps){
  var engEl=document.getElementById('wfEng'), warnEl=document.getElementById('wfWarn');
  if(!engEl) return;
  try{
    var wf=BareMetal.WorkflowPico.compile(wfResolveEventSchemas(steps));
    engEl.textContent=wf.source;
    if(warnEl) warnEl.innerHTML=(wf.warnings&&wf.warnings.length)?wf.warnings.map(function(w){return '&#9888; '+esc(w);}).join('<br>'):'';
  }catch(e){ engEl.textContent=''; if(warnEl) warnEl.textContent='&#9888; '+String(e.message||e); }
}
function wfEnsureFlow(){
  var host=document.getElementById('wfFlow');
  if(!host||FLOW||typeof BareMetal==='undefined'||!BareMetal.Workflow||!BareMetal.Workflow.Designer) return;
  FLOW=BareMetal.Workflow.Designer.create(host, { steps: wfParseSteps()||[], namespaces: (typeof NSDATA!=='undefined'?NSDATA:null), onChange: function(steps){
    FLOW_OWNS=true; setSrc(JSON.stringify(steps,null,2)); FLOW_OWNS=false;
    wfUpdateEng(steps); try{ compileSrc(false); }catch(e){}
  }});
  wfWireFlowClicks();
}
function wfRenderDesigner(){
  var host=document.getElementById('wfFlow'); if(!host) return;
  wfPopulateExamples();
  var steps=wfParseSteps();
  if(!steps){
    if(FLOW){ FLOW.destroy(); FLOW=null; }
    host.innerHTML='<div class="muted" style="font-size:12px;padding:4px">(editor text is not a JSON step array &mdash; fix the JSON, load an example, or add a box)</div>';
    var e=document.getElementById('wfEng'); if(e) e.textContent='';
    return;
  }
  wfEnsureFlow();
  if(FLOW&&!FLOW_OWNS) FLOW.setSteps(steps);
  wfUpdateEng(steps);
  try{ wfHighlightActive(); wfRenderBreakpoints(); }catch(e){}
}
function wfToggle(){
  var on=(CUR_LANG==='workflow');
  var host=document.getElementById('wfDesigner');
  if(host) host.style.display=on?'block':'none';
  if(typeof STATIC_MODE!=='undefined'&&STATIC_MODE) return;
  // In workflow mode the christmas-tree designer is the main surface, so the
  // backing-JSON editor is hidden -- BUT only when the JSON is valid (the designer
  // can render it). If the text isn't a step array yet, keep the editor visible so
  // the user can always see/fix it (never a blank editor area). The { } JSON modal
  // is available regardless. Guarded against the Report designer (whichever of
  // wfToggle/reportToggle is "on" owns monaco/src visibility; the other backs off)
  // so entering/leaving either surface doesn't fight over the shared editor.
  var mon=document.getElementById('monaco'), src=document.getElementById('src');
  var validWf=on&&(typeof looksLikeWorkflowJson==='function')&&looksLikeWorkflowJson(getSrc());
  function showEditor(){ if(EDITOR){ if(mon)mon.style.display='block'; if(src)src.style.display='none'; try{EDITOR.layout();}catch(e){} } else { if(mon)mon.style.display='none'; if(src)src.style.display='block'; } }
  if(on&&validWf){ if(mon)mon.style.display='none'; if(src)src.style.display='none'; }
  else if(on) showEditor();
  else if(CUR_LANG!=='report') showEditor();
  if(!on) wfCloseJson();
  if(on){ try{ wfRenderDesigner(); }catch(e){} }
}
function wfOpenJson(){
  var m=document.getElementById('wfJsonModal'), t=document.getElementById('wfJsonText'), e=document.getElementById('wfJsonErr');
  if(!m||!t) return; if(e) e.textContent='';
  t.value=getSrc(); m.classList.add('open'); t.focus();
}
function wfCloseJson(){ var m=document.getElementById('wfJsonModal'); if(m) m.classList.remove('open'); }
function wfApplyJson(){
  var t=document.getElementById('wfJsonText'), e=document.getElementById('wfJsonErr');
  if(!t) return;
  try{ var d=JSON.parse(t.value); if(!Array.isArray(d)&&!(d&&Array.isArray(d.steps))) throw new Error('expected a JSON step array'); }
  catch(err){ if(e){ e.textContent='invalid JSON: '+String(err.message||err); e.style.color='#ff7b72'; } return; }
  setSrc(t.value); WF_LAST=t.value; wfCloseJson();
  try{ wfRenderDesigner(); }catch(_){}
  compileSrc(false);
}

// ---- report surface (visual template designer -> English pre-compile) -------
// Report is now first-class like Workflow: a visual designer (title/columns/
// aggregates/rows) whose backing artifact is a JSON template. Compile & Run
// lowers the template to English (rptCompileSrc) which composes and prints the
// real report text (header, rows, aggregate footer) via first-class string
// composition -- it doesn't just render a client-side preview of separate
// output, it generates the report.
var RPT_LAST=null;   // last report JSON, so dialect excursions round-trip back
var RPT_DEFAULT={title:'Orders', columns:[{label:'Qty',agg:'sum'},{label:'Price',agg:'sum'}], rows:[[2,10],[3,20],[1,50]]};
function looksLikeReportJson(src){
  if(!src||!src.trim()) return false;
  try{ var d=JSON.parse(src); return !!(d&&typeof d==='object'&&Array.isArray(d.columns)&&Array.isArray(d.rows)); }
  catch(e){ return false; }
}
function rptEsc(s){ return String(s==null?'':s).replace(/\\/g,'\\\\').replace(/"/g,'\\"'); }
// Lower {title,columns,rows} -> English source. No array indexing is needed: each
// row is unrolled into its own composed Print statement (string parts + int
// values via first-class `+`/`plus` composition); aggregate columns accumulate
// into a running SET and print a "sum=X sum=Y" footer, matching the Showcase
// report/form style but as a real runnable program.
function rptToEnglish(d){
  var cols=d.columns||[], rows=d.rows||[], lines=[], aggCols=[];
  cols.forEach(function(c,i){ if(c&&c.agg==='sum'){ lines.push('Set sum'+i+' to 0.'); aggCols.push(i); } });
  if(d.title) lines.push('Print "'+rptEsc(d.title)+'\\n".');
  var header=cols.map(function(c,i){ return rptEsc((c&&c.label)||('Col'+i)); }).join(' | ');
  lines.push('Print "'+header+'\\n".');
  rows.forEach(function(row){
    var parts=[];
    row.forEach(function(v,i){ parts.push(String((typeof v==='number')?(v|0):0)); if(i<row.length-1) parts.push('" | "'); });
    parts.push('"\\n"');
    lines.push('Print '+parts.join(' plus ')+'.');
    aggCols.forEach(function(i){ var v=row[i]; lines.push('Set sum'+i+' to sum'+i+' plus '+((typeof v==='number')?(v|0):0)+'.'); });
  });
  if(aggCols.length){
    var fparts=[];
    aggCols.forEach(function(i,idx){ fparts.push('"'+(idx?' sum=':'sum=')+'"'); fparts.push('sum'+i); });
    fparts.push('"\\n"');
    lines.push('Print '+fparts.join(' plus ')+'.');
  }
  return lines.join('\n')+'\n';
}
function rptCompileSrc(src){
  var d=JSON.parse(src);
  if(!d||!Array.isArray(d.columns)||!Array.isArray(d.rows)) throw new Error('report: expected {title,columns,rows}');
  return { source: rptToEnglish(d) };
}
function rptGetTemplate(){
  try{ var d=JSON.parse(getSrc()); if(d&&Array.isArray(d.columns)&&Array.isArray(d.rows)) return d; }catch(e){}
  return JSON.parse(JSON.stringify(RPT_DEFAULT));
}
function rptSetTemplate(d){ setSrc(JSON.stringify(d,null,2)); RPT_LAST=getSrc(); rptRenderDesigner(); compileSrc(false); }
function rptSetTitle(v){ var d=rptGetTemplate(); d.title=v; rptSetTemplate(d); }
function rptAddColumn(){ var d=rptGetTemplate(); d.columns.push({label:'Col'+(d.columns.length+1),agg:'none'}); d.rows.forEach(function(r){r.push(0);}); rptSetTemplate(d); }
function rptRemoveColumn(i){ var d=rptGetTemplate(); d.columns.splice(i,1); d.rows.forEach(function(r){r.splice(i,1);}); rptSetTemplate(d); }
function rptSetColLabel(i,v){ var d=rptGetTemplate(); if(d.columns[i]) d.columns[i].label=v; rptSetTemplate(d); }
function rptSetColAgg(i,v){ var d=rptGetTemplate(); if(d.columns[i]) d.columns[i].agg=v; rptSetTemplate(d); }
function rptAddRow(){ var d=rptGetTemplate(); d.rows.push(d.columns.map(function(){return 0;})); rptSetTemplate(d); }
function rptRemoveRow(i){ var d=rptGetTemplate(); d.rows.splice(i,1); rptSetTemplate(d); }
function rptSetCell(r,c,v){ var d=rptGetTemplate(); var n=parseInt(v,10); if(isNaN(n))n=0; if(d.rows[r]) d.rows[r][c]=n; rptSetTemplate(d); }
// ---- schema binding (Report can be bound to a pack's typed schema) ----------
// Optional d.pack names a picowal pack; if schemas/<pack>.schema.json exists,
// "Use schema columns" replaces the column list with the schema's fields (label
// = field name; aggregate stays a report-only choice) and "Load rows from cards"
// snapshots the pack's current PicoStore cards into literal rows. The report
// keeps generating a real runnable program either way (unrolled Print
// statements) -- this just gives a fast, deterministic way to seed it from an
// actual typed pack instead of hand-typing every row.
function rptSetPack(v){ var d=rptGetTemplate(); d.pack=v; rptSetTemplate(d); }
function rptUseSchemaColumns(){
  var d=rptGetTemplate(), fields=schemaForPack(d.pack||'');
  if(!fields){ if(typeof alert==='function') alert('pack "'+(d.pack||'')+'" has no bound schema (add schemas/'+(d.pack||'<pack>')+'.schema.json)'); return; }
  var oldCols=d.columns||[];
  d.columns=fields.map(function(f,i){ return {label:f.name, agg:(oldCols[i]&&oldCols[i].agg)||'none'}; });
  d.rows=(d.rows||[]).map(function(r){ return fields.map(function(f,i){ return (typeof r[i]==='number')?r[i]:0; }); });
  rptSetTemplate(d);
}
function rptLoadRowsFromCards(){
  var d=rptGetTemplate(), fields=schemaForPack(d.pack||'');
  if(!fields){ if(typeof alert==='function') alert('pack "'+(d.pack||'')+'" has no bound schema (add schemas/'+(d.pack||'<pack>')+'.schema.json)'); return; }
  if(typeof STORE==='undefined'||!STORE){ if(typeof alert==='function') alert('Cards store is not available'); return; }
  var entries=STORE.all(d.pack);
  d.rows=entries.map(function(e){ var rec=e[1]||{}; return fields.map(function(f){ var v=rec[f.name]; return (typeof v==='number')?(v|0):0; }); });
  rptSetTemplate(d);
}
function rptRenderDesigner(){
  var host=document.getElementById('rptForm'); if(!host) return;
  var d=rptGetTemplate();
  var fields=schemaForPack(d.pack||'');
  var html='<div style="margin-bottom:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">'+
    '<label class="muted" style="font-size:11px">title</label><input type="text" value="'+filesEscAttr(d.title||'')+'" style="width:180px" onchange="rptSetTitle(this.value)">'+
    '<label class="muted" style="font-size:11px">pack</label><input type="text" value="'+filesEscAttr(d.pack||'')+'" style="width:110px" placeholder="(optional)" onchange="rptSetPack(this.value)">'+
    (fields
      ? '<span style="color:var(--accent);font-size:11px">bound to schemas/'+esc(d.pack)+'.schema.json ('+fields.length+' field'+(fields.length===1?'':'s')+')</span>'+
        '<button class="ghost" onclick="rptUseSchemaColumns()">Use schema columns</button>'+
        '<button class="ghost" onclick="rptLoadRowsFromCards()">Load rows from cards</button>'
      : (d.pack ? '<span class="muted" style="font-size:11px">no schema at schemas/'+esc(d.pack)+'.schema.json</span>' : ''))+
    '</div>';
  html+='<table class="wal" style="margin-bottom:8px"><thead><tr>';
  d.columns.forEach(function(c,i){ html+='<th>col '+i+'</th>'; });
  html+='<th></th></tr><tr>';
  d.columns.forEach(function(c,i){
    html+='<td><input type="text" value="'+filesEscAttr(c.label||'')+'" style="width:80px" onchange="rptSetColLabel('+i+',this.value)"> '+
      '<select onchange="rptSetColAgg('+i+',this.value)"><option value="none"'+(c.agg!=='sum'?' selected':'')+'>none</option><option value="sum"'+(c.agg==='sum'?' selected':'')+'>sum</option></select> '+
      '<button class="ghost" style="padding:1px 5px" onclick="rptRemoveColumn('+i+')" title="remove column">&times;</button></td>';
  });
  html+='<td><button class="ghost" onclick="rptAddColumn()">+ col</button></td></tr></thead><tbody>';
  d.rows.forEach(function(row,ri){
    html+='<tr>';
    row.forEach(function(v,ci){ html+='<td><input type="number" value="'+esc(String(v))+'" style="width:70px" onchange="rptSetCell('+ri+','+ci+',this.value)"></td>'; });
    html+='<td><button class="ghost" onclick="rptRemoveRow('+ri+')" title="remove row">&times;</button></td></tr>';
  });
  html+='</tbody></table><button class="ghost" onclick="rptAddRow()">+ row</button>';
  host.innerHTML=html;
  var e=document.getElementById('rptEng');
  if(e){ try{ e.textContent=rptCompileSrc(getSrc()).source; }catch(err){ e.textContent=''; } }
}
function reportToggle(){
  var on=(CUR_LANG==='report');
  var host=document.getElementById('reportDesigner');
  if(host) host.style.display=on?'block':'none';
  if(typeof STATIC_MODE!=='undefined'&&STATIC_MODE) return;
  var mon=document.getElementById('monaco'), src=document.getElementById('src');
  var validRpt=on&&(typeof looksLikeReportJson==='function')&&looksLikeReportJson(getSrc());
  function showEditor(){ if(EDITOR){ if(mon)mon.style.display='block'; if(src)src.style.display='none'; try{EDITOR.layout();}catch(e){} } else { if(mon)mon.style.display='none'; if(src)src.style.display='block'; } }
  if(on&&validRpt){ if(mon)mon.style.display='none'; if(src)src.style.display='none'; }
  else if(on) showEditor();
  else if(CUR_LANG!=='workflow') showEditor();
  if(!on) rptCloseJson();
  if(on){ try{ rptRenderDesigner(); }catch(e){} }
}
function rptOpenJson(){
  var m=document.getElementById('rptJsonModal'), t=document.getElementById('rptJsonText'), e=document.getElementById('rptJsonErr');
  if(!m||!t) return; if(e) e.textContent='';
  t.value=getSrc(); m.classList.add('open'); t.focus();
}
function rptCloseJson(){ var m=document.getElementById('rptJsonModal'); if(m) m.classList.remove('open'); }
function rptApplyJson(){
  var t=document.getElementById('rptJsonText'), e=document.getElementById('rptJsonErr');
  if(!t) return;
  try{ var d=JSON.parse(t.value); if(!d||!Array.isArray(d.columns)||!Array.isArray(d.rows)) throw new Error('expected {title,columns,rows}'); }
  catch(err){ if(e){ e.textContent='invalid JSON: '+String(err.message||err); e.style.color='#ff7b72'; } return; }
  setSrc(t.value); RPT_LAST=t.value; rptCloseJson();
  try{ rptRenderDesigner(); }catch(_){}
  compileSrc(false);
}

// ---- schema designer (typed pack/card schemas) -------------------------------
// A *.schema.json file is a typed field list -- {fields:[{name,type}]} -- for a
// picowal pack/card. Same type vocabulary as the Playground's schema designer
// (BareMetal.Schema / picowal wire types) so schemas are portable between them.
// This is design-time data, not a runnable program: no dialect toggle/compile
// applies (see enterStaticMode/schemaToggle); the fields table is the surface,
// with a { } JSON modal for raw editing. Once bound (schema-bind-cards /
// schema-bind-reportform), this feeds typed rendering into Cards, Query and the
// Report/Form designer instead of guessing types from raw values.
var SD_TYPES=['int','str','bool','uint8','int16','int32','uint16','uint32','utf8','latin1','blob'];
function sdStrType(t){ return t==='str'||t==='utf8'||t==='latin1'||t==='blob'; }
function looksLikeSchemaJson(src){
  if(!src||!src.trim()) return false;
  try{ var d=JSON.parse(src); return !!(d&&typeof d==='object'&&Array.isArray(d.fields)); }
  catch(e){ return false; }
}
function schemaGetModel(){
  try{ var d=JSON.parse(getSrc()); if(d&&Array.isArray(d.fields)) return d; }catch(e){}
  return {fields:[]};
}
function schemaSetModel(d){ setSrc(JSON.stringify(d,null,2)); schemaRenderDesigner(); if(typeof filesRender==='function')filesRender(); }
function schemaAddField(){
  var nameEl=document.getElementById('schFName'), typeEl=document.getElementById('schFType');
  var name=(nameEl?nameEl.value:'').trim();
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)){ if(typeof alert==='function') alert('field name must be a simple identifier'); return; }
  var type=typeEl?typeEl.value:'int';
  var d=schemaGetModel();
  d.fields=(d.fields||[]).filter(function(f){return f.name!==name;}).concat([{name:name,type:type}]);
  schemaSetModel(d);
  if(nameEl) nameEl.value='';
}
function schemaRemoveField(name){ var d=schemaGetModel(); d.fields=(d.fields||[]).filter(function(f){return f.name!==name;}); schemaSetModel(d); }
function schemaSetFieldType(name,type){ var d=schemaGetModel(); var f=(d.fields||[]).filter(function(x){return x.name===name;})[0]; if(f){f.type=type; schemaSetModel(d);} }
function schemaRenderDesigner(){
  var host=document.getElementById('schemaFields'); if(!host) return;
  var d=schemaGetModel(), fields=d.fields||[];
  var html='<table class="wal" style="margin-bottom:8px"><thead><tr><th>field</th><th>type</th><th></th></tr></thead><tbody>';
  if(!fields.length) html+='<tr><td colspan="3" style="color:var(--muted)">no fields yet</td></tr>';
  fields.forEach(function(f){
    html+='<tr><td>'+esc(f.name)+'</td><td><select onchange="schemaSetFieldType(\''+esc(f.name)+'\',this.value)">'+
      SD_TYPES.map(function(t){return '<option'+(t===f.type?' selected':'')+'>'+t+'</option>';}).join('')+'</select></td>'+
      '<td><button class="ghost" onclick="schemaRemoveField(\''+esc(f.name)+'\')" title="remove field">&times;</button></td></tr>';
  });
  html+='</tbody></table><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'+
    '<input id="schFName" placeholder="field name" style="width:130px" onkeydown="if(event.key===\'Enter\')schemaAddField();">'+
    '<select id="schFType">'+SD_TYPES.map(function(t){return '<option>'+t+'</option>';}).join('')+'</select>'+
    '<button class="ghost" onclick="schemaAddField()">+ field</button></div>';
  host.innerHTML=html;
}
function schemaToggle(active,name){
  var host=document.getElementById('schemaDesigner');
  if(host) host.style.display=active?'block':'none';
  if(!active){ schemaCloseJson(); return; }
  var mon=document.getElementById('monaco'), src=document.getElementById('src');
  if(mon) mon.style.display='none'; if(src) src.style.display='none';
  try{ schemaRenderDesigner(); }catch(e){}
}
function schemaOpenJson(){
  var m=document.getElementById('schemaJsonModal'), t=document.getElementById('schemaJsonText'), e=document.getElementById('schemaJsonErr');
  if(!m||!t) return; if(e) e.textContent='';
  t.value=getSrc(); m.classList.add('open'); t.focus();
}
function schemaCloseJson(){ var m=document.getElementById('schemaJsonModal'); if(m) m.classList.remove('open'); }
function schemaApplyJson(){
  var t=document.getElementById('schemaJsonText'), e=document.getElementById('schemaJsonErr');
  if(!t) return;
  try{ var d=JSON.parse(t.value); if(!d||!Array.isArray(d.fields)) throw new Error('expected {fields:[{name,type}]}'); }
  catch(err){ if(e){ e.textContent='invalid JSON: '+String(err.message||err); e.style.color='#ff7b72'; } return; }
  setSrc(t.value); schemaCloseJson();
  try{ schemaRenderDesigner(); }catch(_){}
  if(typeof filesRender==='function') filesRender();
}
// Create a new schemas/*.schema.json file and open it straight into the designer.
function schemaQuickNew(){
  var files=filesRead(), base='schemas/untitled', name=base+'.schema.json', n=2;
  while(files[name]){ name=base+'-'+(n++)+'.schema.json'; }
  files[name]={kind:'schema',lang:'',src:JSON.stringify({fields:[]},null,2),updated:Date.now()};
  filesWrite(files); filesRender();
  psFilesOpen(name);
}

// ---- event schema designer (fixed-schema program exits / event handlers) ----
// A *.event.json file is {mode:'sync'|'async', fields:[{name,type}], returns}.
// It gives a workflow RAISE ("program exit" -- mode sync) or ON ("event
// handler" -- mode async) a fixed typed payload with NO new VM hooks: the
// WebIDE resolves a Raise/On box's `eventFile` field to this schema at compile
// time (see wfResolveEventSchemas) and BareMetal.WorkflowPico lowers the
// fields through a Map + the existing PSC1 card codec (Binary.SerializeCard /
// Binary.ParseCard), carried on the event record via Event.SetData/Event.Data.
// Same type vocabulary as the Schema Designer (SD_TYPES) so an event field can
// mirror a pack/card field 1:1.
function looksLikeEventJson(src){
  if(!src||!src.trim()) return false;
  try{ var d=JSON.parse(src); return !!(d&&typeof d==='object'&&Array.isArray(d.fields)&&(d.mode==='sync'||d.mode==='async')); }
  catch(e){ return false; }
}
function eventGetModel(){
  try{ var d=JSON.parse(getSrc()); if(d&&Array.isArray(d.fields)) return {mode:d.mode==='sync'?'sync':'async',fields:d.fields,returns:d.returns||null}; }catch(e){}
  return {mode:'async',fields:[],returns:null};
}
function eventSetModel(d){ setSrc(JSON.stringify(d,null,2)); eventRenderDesigner(); if(typeof filesRender==='function')filesRender(); }
function eventSetMode(mode){ var d=eventGetModel(); d.mode=(mode==='sync')?'sync':'async'; if(d.mode==='async') d.returns=null; eventSetModel(d); }
function eventSetReturnsType(type){ var d=eventGetModel(); d.returns=(type&&type!=='none')?{type:type}:null; eventSetModel(d); }
function eventAddField(){
  var nameEl=document.getElementById('evFName'), typeEl=document.getElementById('evFType');
  var name=(nameEl?nameEl.value:'').trim();
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)){ if(typeof alert==='function') alert('field name must be a simple identifier'); return; }
  var type=typeEl?typeEl.value:'int';
  var d=eventGetModel();
  d.fields=(d.fields||[]).filter(function(f){return f.name!==name;}).concat([{name:name,type:type}]);
  eventSetModel(d);
  if(nameEl) nameEl.value='';
}
function eventRemoveField(name){ var d=eventGetModel(); d.fields=(d.fields||[]).filter(function(f){return f.name!==name;}); eventSetModel(d); }
function eventSetFieldType(name,type){ var d=eventGetModel(); var f=(d.fields||[]).filter(function(x){return x.name===name;})[0]; if(f){f.type=type; eventSetModel(d);} }
function eventRenderDesigner(){
  var host=document.getElementById('eventFields'); if(!host) return;
  var d=eventGetModel(), fields=d.fields||[];
  var html='<div style="display:flex;gap:12px;align-items:center;margin-bottom:8px;flex-wrap:wrap">'+
    '<label><input type="radio" name="evMode" value="sync"'+(d.mode==='sync'?' checked':'')+' onchange="eventSetMode(this.value)"> sync (program exit)</label>'+
    '<label><input type="radio" name="evMode" value="async"'+(d.mode!=='sync'?' checked':'')+' onchange="eventSetMode(this.value)"> async (event handler)</label>';
  if(d.mode==='sync'){
    html+='<span class="muted" style="font-size:11px">returns</span><select onchange="eventSetReturnsType(this.value)">'+
      ['none'].concat(SD_TYPES).map(function(t){return '<option'+((d.returns&&d.returns.type===t)?' selected':(t==='none'&&!d.returns?' selected':''))+'>'+t+'</option>';}).join('')+'</select>';
  }
  html+='</div>';
  html+='<table class="wal" style="margin-bottom:8px"><thead><tr><th>field</th><th>type</th><th></th></tr></thead><tbody>';
  if(!fields.length) html+='<tr><td colspan="3" style="color:var(--muted)">no fields yet</td></tr>';
  fields.forEach(function(f){
    html+='<tr><td>'+esc(f.name)+'</td><td><select onchange="eventSetFieldType(\''+esc(f.name)+'\',this.value)">'+
      SD_TYPES.map(function(t){return '<option'+(t===f.type?' selected':'')+'>'+t+'</option>';}).join('')+'</select></td>'+
      '<td><button class="ghost" onclick="eventRemoveField(\''+esc(f.name)+'\')" title="remove field">&times;</button></td></tr>';
  });
  html+='</tbody></table><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'+
    '<input id="evFName" placeholder="field name" style="width:130px" onkeydown="if(event.key===\'Enter\')eventAddField();">'+
    '<select id="evFType">'+SD_TYPES.map(function(t){return '<option>'+t+'</option>';}).join('')+'</select>'+
    '<button class="ghost" onclick="eventAddField()">+ field</button></div>';
  host.innerHTML=html;
}
function eventToggle(active,name){
  var host=document.getElementById('eventDesigner');
  if(host) host.style.display=active?'block':'none';
  if(!active){ eventCloseJson(); return; }
  var mon=document.getElementById('monaco'), src=document.getElementById('src');
  if(mon) mon.style.display='none'; if(src) src.style.display='none';
  try{ eventRenderDesigner(); }catch(e){}
}
function eventOpenJson(){
  var m=document.getElementById('eventJsonModal'), t=document.getElementById('eventJsonText'), e=document.getElementById('eventJsonErr');
  if(!m||!t) return; if(e) e.textContent='';
  t.value=getSrc(); m.classList.add('open'); t.focus();
}
function eventCloseJson(){ var m=document.getElementById('eventJsonModal'); if(m) m.classList.remove('open'); }
function eventApplyJson(){
  var t=document.getElementById('eventJsonText'), e=document.getElementById('eventJsonErr');
  if(!t) return;
  try{ var d=JSON.parse(t.value); if(!d||!Array.isArray(d.fields)) throw new Error('expected {mode,fields:[{name,type}],returns}'); }
  catch(err){ if(e){ e.textContent='invalid JSON: '+String(err.message||err); e.style.color='#ff7b72'; } return; }
  setSrc(t.value); eventCloseJson();
  try{ eventRenderDesigner(); }catch(_){}
  if(typeof filesRender==='function') filesRender();
}
// Create a new events/*.event.json file and open it straight into the designer.
function eventQuickNew(){
  var files=filesRead(), base='events/untitled', name=base+'.event.json', n=2;
  while(files[name]){ name=base+'-'+(n++)+'.event.json'; }
  files[name]={kind:'event',lang:'',src:JSON.stringify({mode:'async',fields:[],returns:null},null,2),updated:Date.now()};
  filesWrite(files); filesRender();
  psFilesOpen(name);
}
// Scaffold a fixed-fields event file from an entity's bound pack schema (used
// by both the ontology "+ event" quick action and standalone use).
function eventScaffoldFromSchema(name,mode,fields){
  var files=filesRead();
  files[name]={kind:'event',lang:'',src:JSON.stringify({mode:mode,fields:fields||[],returns:null},null,2),updated:Date.now()};
  filesWrite(files);
}

// ---- ontology designer (entities + relations -> scaffolds pack events) ------
// A *.ontology.json file is {entities:[{name,pack}], relations:[{name,from,to,
// cardinality}]}. Each entity's `pack` names a schemas/<pack>.schema.json (see
// schemaForPack); a "+ event" quick action per entity scaffolds standard
// created/updated/deleted event schemas (mode:'async') bound to that entity's
// fields, giving "pack events" as a natural side effect of designing the
// ontology rather than a separate hand-authored artifact.
function ontologyGetModel(){
  try{ var d=JSON.parse(getSrc()); if(d&&(Array.isArray(d.entities)||Array.isArray(d.relations))) return {entities:d.entities||[],relations:d.relations||[]}; }catch(e){}
  return {entities:[],relations:[]};
}
function ontologySetModel(d){ setSrc(JSON.stringify(d,null,2)); ontologyRenderDesigner(); if(typeof filesRender==='function')filesRender(); }
function ontologyAddEntity(){
  var nameEl=document.getElementById('ontEName'), packEl=document.getElementById('ontEPack');
  var name=(nameEl?nameEl.value:'').trim();
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)){ if(typeof alert==='function') alert('entity name must be a simple identifier'); return; }
  var pack=(packEl?packEl.value:'').trim()||name;
  var d=ontologyGetModel();
  d.entities=(d.entities||[]).filter(function(x){return x.name!==name;}).concat([{name:name,pack:pack}]);
  ontologySetModel(d);
  if(nameEl) nameEl.value=''; if(packEl) packEl.value='';
}
function ontologyRemoveEntity(name){
  var d=ontologyGetModel();
  d.entities=(d.entities||[]).filter(function(x){return x.name!==name;});
  d.relations=(d.relations||[]).filter(function(r){return r.from!==name&&r.to!==name;});
  ontologySetModel(d);
}
function ontologyAddRelation(){
  var nameEl=document.getElementById('ontRName'), fromEl=document.getElementById('ontRFrom'), toEl=document.getElementById('ontRTo'), cardEl=document.getElementById('ontRCard');
  var name=(nameEl?nameEl.value:'').trim()||'relates';
  var from=fromEl?fromEl.value:'', to=toEl?toEl.value:'', card=cardEl?cardEl.value:'one-to-many';
  if(!from||!to){ if(typeof alert==='function') alert('pick both entities for the relation'); return; }
  var d=ontologyGetModel();
  d.relations=(d.relations||[]).concat([{name:name,from:from,to:to,cardinality:card}]);
  ontologySetModel(d);
  if(nameEl) nameEl.value='';
}
function ontologyRemoveRelation(idx){ var d=ontologyGetModel(); d.relations=(d.relations||[]).filter(function(_,i){return i!==idx;}); ontologySetModel(d); }
// "+ event" scaffolds events/<entity>.created/updated/deleted.event.json bound
// to the entity's pack schema fields (empty fields if the pack isn't found).
function ontologyScaffoldEvents(entityName){
  var d=ontologyGetModel(), ent=(d.entities||[]).filter(function(x){return x.name===entityName;})[0];
  if(!ent) return;
  var fields=schemaForPack(ent.pack||ent.name)||[];
  ['created','updated','deleted'].forEach(function(suffix){
    eventScaffoldFromSchema('events/'+entityName+'.'+suffix+'.event.json','async',fields.slice());
  });
  filesRender();
  filesStatus('Scaffolded 3 pack events for '+entityName+(fields.length?'':' (pack schema not found; fields empty)'));
}
function ontologyRenderDesigner(){
  var host=document.getElementById('ontologyBody'); if(!host) return;
  var d=ontologyGetModel(), entities=d.entities||[], relations=d.relations||[];
  var opts=entities.map(function(e){return '<option>'+esc(e.name)+'</option>';}).join('');
  var html='<div style="font-weight:600;margin-bottom:4px">Entities</div>';
  html+='<table class="wal" style="margin-bottom:8px"><thead><tr><th>entity</th><th>pack schema</th><th></th></tr></thead><tbody>';
  if(!entities.length) html+='<tr><td colspan="3" style="color:var(--muted)">no entities yet</td></tr>';
  entities.forEach(function(e){
    html+='<tr><td>'+esc(e.name)+'</td><td>'+esc(e.pack||e.name)+'</td>'+
      '<td><button class="ghost" onclick="ontologyScaffoldEvents(\''+esc(e.name)+'\')" title="scaffold created/updated/deleted pack events from this entity\'s schema">+ event</button> '+
      '<button class="ghost" onclick="ontologyRemoveEntity(\''+esc(e.name)+'\')" title="remove entity">&times;</button></td></tr>';
  });
  html+='</tbody></table><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:14px">'+
    '<input id="ontEName" placeholder="entity name" style="width:120px" onkeydown="if(event.key===\'Enter\')ontologyAddEntity();">'+
    '<input id="ontEPack" placeholder="pack (defaults to name)" style="width:160px">'+
    '<button class="ghost" onclick="ontologyAddEntity()">+ entity</button></div>';
  html+='<div style="font-weight:600;margin-bottom:4px">Relations</div>';
  html+='<table class="wal" style="margin-bottom:8px"><thead><tr><th>name</th><th>from</th><th>to</th><th>cardinality</th><th></th></tr></thead><tbody>';
  if(!relations.length) html+='<tr><td colspan="5" style="color:var(--muted)">no relations yet</td></tr>';
  relations.forEach(function(r,i){
    html+='<tr><td>'+esc(r.name)+'</td><td>'+esc(r.from)+'</td><td>'+esc(r.to)+'</td><td>'+esc(r.cardinality)+'</td>'+
      '<td><button class="ghost" onclick="ontologyRemoveRelation('+i+')" title="remove relation">&times;</button></td></tr>';
  });
  html+='</tbody></table><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'+
    '<input id="ontRName" placeholder="relation name" style="width:110px">'+
    '<select id="ontRFrom"><option value="">from&hellip;</option>'+opts+'</select>'+
    '<select id="ontRTo"><option value="">to&hellip;</option>'+opts+'</select>'+
    '<select id="ontRCard"><option>one-to-one</option><option selected>one-to-many</option><option>many-to-many</option></select>'+
    '<button class="ghost" onclick="ontologyAddRelation()">+ relation</button></div>';
  host.innerHTML=html;
}
function ontologyToggle(active,name){
  var host=document.getElementById('ontologyDesigner');
  if(host) host.style.display=active?'block':'none';
  if(!active){ ontologyCloseJson(); return; }
  var mon=document.getElementById('monaco'), src=document.getElementById('src');
  if(mon) mon.style.display='none'; if(src) src.style.display='none';
  try{ ontologyRenderDesigner(); }catch(e){}
}
function ontologyOpenJson(){
  var m=document.getElementById('ontologyJsonModal'), t=document.getElementById('ontologyJsonText'), e=document.getElementById('ontologyJsonErr');
  if(!m||!t) return; if(e) e.textContent='';
  t.value=getSrc(); m.classList.add('open'); t.focus();
}
function ontologyCloseJson(){ var m=document.getElementById('ontologyJsonModal'); if(m) m.classList.remove('open'); }
function ontologyApplyJson(){
  var t=document.getElementById('ontologyJsonText'), e=document.getElementById('ontologyJsonErr');
  if(!t) return;
  try{ var d=JSON.parse(t.value); if(!d||typeof d!=='object') throw new Error('expected {entities:[...],relations:[...]}'); }
  catch(err){ if(e){ e.textContent='invalid JSON: '+String(err.message||err); e.style.color='#ff7b72'; } return; }
  setSrc(t.value); ontologyCloseJson();
  try{ ontologyRenderDesigner(); }catch(_){}
  if(typeof filesRender==='function') filesRender();
}
// Create a new *.ontology.json file and open it straight into the designer.
function ontologyQuickNew(){
  var files=filesRead(), base='ontology', name=base+'.ontology.json', n=2;
  while(files[name]){ name=base+'-'+(n++)+'.ontology.json'; }
  files[name]={kind:'ontology',lang:'',src:JSON.stringify({entities:[],relations:[]},null,2),updated:Date.now()};
  filesWrite(files); filesRender();
  psFilesOpen(name);
}

// ---- report/form layout (stage 2 over the current program output) -----------
var LAYOUT_DEFAULT_TMPL=JSON.stringify({
  title:'Report',
  columns:[{label:'A',field:0,width:6},{label:'B',field:1,width:6}],
  aggregates:[{column:0,fn:'sum'},{column:1,fn:'sum'}]
}, null, 2);
function layoutMode(){ var r=document.querySelector('input[name="layoutMode"]:checked'); return r?r.value:'report'; }
function renderLayout(){
  var pv=document.getElementById('layoutPreview'), tx=document.getElementById('layoutText'), ta=document.getElementById('layoutTmpl');
  if(!pv||!ta) return;
  if(!ta.value||!ta.value.trim()) ta.value=LAYOUT_DEFAULT_TMPL;
  var data=(DBG.vm&&DBG.vm.outputInts)?DBG.vm.outputInts():[];
  try{
    var tmpl=JSON.parse(ta.value), mode=layoutMode();
    pv.innerHTML=BareMetal.Report.renderHtml(data,tmpl,mode);
    if(tx) tx.textContent=BareMetal.Report.renderText(data,tmpl);
  }catch(e){
    if(tx) tx.textContent='';
    pv.innerHTML='<span style="color:var(--warn)">'+esc(String(e.message||e))+'</span>';
  }
}
function layoutSave(){
  var host=document.getElementById('layoutPreview'), msg=document.getElementById('layoutSaveMsg');
  var form=host?host.querySelector('form'):null;
  if(!form){ if(msg) msg.textContent='(switch to form mode, then Render)'; return; }
  try{
    var rows=BareMetal.Report.collect(form), writes=BareMetal.Report.toWrites(rows,{base:0}), be=walBackend();
    Object.keys(writes).forEach(function(k){ be.set(parseInt(k,10),writes[k]); });
    renderWal();
    if(msg) msg.textContent='saved '+rows.length+' rows \u2192 data ABI: '+JSON.stringify(writes);
  }catch(e){ if(msg) msg.textContent=String(e.message||e); }
}

// Init
buildGuideTree();showGuideCard(0);buildRefTree();buildNsRef();renderSyntaxRef();renderSamples();applyDbgLayout('guide');applyDbgLayout('play');
(function(){var src=document.getElementById('doc-internals');var dst=document.getElementById('doc-internals-inline');if(src&&dst)dst.innerHTML=src.innerHTML;})();
(function(){var sel=document.getElementById('example');DATA.forEach(function(d,i){var o=document.createElement('option');o.value=String(i);o.textContent=(i+1)+'. '+d.title;sel.appendChild(o);});
})();
// One sample set for every language: load the sample in a dialect it ships in,
// then present it in the CURRENT view -- including Workflow, which is raised from
// the shared AST via PicoCompile.toWorkflow (full-fidelity round-trip).
function loadExample(){
  var i=parseInt(document.getElementById('example').value,10)||0;var d=DATA[i];if(!d)return;
  var cur=CUR_LANG;
  busEmit('sample.load',{title:d.title,view:cur});
  var native=d.basic?'basic':(d.c?'c':(d.python?'python':(d.english?'english':'basic')));
  if(!d[native]){compileSrc(false);return;}
  if(cur==='workflow'){
    var wf=null; try{ if(typeof PicoCompile!=='undefined'&&PicoCompile.toWorkflow) wf=PicoCompile.toWorkflow(d[native].src,native); }catch(e){}
    if(wf&&typeof looksLikeWorkflowJson==='function'&&looksLikeWorkflowJson(wf)){ setSrc(wf); WF_LAST=wf; if(typeof wfRenderDesigner==='function')try{wfRenderDesigner();}catch(e){} compileSrc(false); return; }
    setLang(native); setSrc(d[native].src); compileSrc(false); return;
  }
  if(d[cur]){setSrc(d[cur].src);compileSrc(false);return;}
  setLang(native); setSrc(d[native].src); compileSrc(false);
}
document.getElementById('example').onchange=loadExample;
document.getElementById('lang').addEventListener('change',function(){onLangChange();});
document.getElementById('src').addEventListener('input',function(){filesRender();if(CUR_LANG==='workflow'){try{wfRenderDesigner();}catch(e){}}});
document.getElementById('lang').value='basic';
document.getElementById('src').value=RESPONDER;
initMonaco();compileSrc(false);loadSample();renderWal();renderLayout();busRender();
(function(){var ls=filesSafeLocalStorage(),active='';try{active=ls?ls.getItem(PS_ACTIVE_FILE_KEY)||'':'';}catch(e){} if(active&&filesRead()[active]) psFilesOpen(active); else filesRender();})();
document.getElementById('flyoutTriggers').style.display='none';
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
