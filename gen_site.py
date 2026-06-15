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
from picoscript_lang import encode_card_addr, HOST_HOOK_CODES

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

    html = PAGE
    html = html.replace("/*__HOOKS__*/", hooks_js)
    html = html.replace("/*__PCZ__*/", pcz_js)
    html = html.replace("/*__VM__*/", vm_js)
    html = html.replace("/*__PICOC__*/", picoc_js)
    html = html.replace("/*__SER__*/", ser_js)
    html = html.replace("/*__STORE__*/", store_js)
    html = html.replace("/*__DATA__*/", json.dumps(gallery))
    html = html.replace("/*__NSDATA__*/", json.dumps(ns_data))
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
          --text:#e6e8ef; --muted:#9aa0ad; --c:#7ee787; --b:#79c0ff; --py:#ffd866; --en:#f0a3ff;
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
  .pill { display:inline-block; padding:2px 7px; border-radius:10px; font-size:10px; font-weight:600;
          background:#2c313f; color:var(--muted); }
  .main { display:flex; flex:1; overflow:hidden; position:relative; }
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
  .listing { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; max-height:170px;
    overflow:auto; font-family:"SF Mono",Consolas,monospace; font-size:11.5px; }
  .listing .row { padding:2px 10px; white-space:pre; color:var(--muted); }
  .listing .row.pc { background:#2d3550; color:#fff; }
  .regs { display:grid; grid-template-columns:repeat(4,1fr); gap:2px 8px; font-family:"SF Mono",monospace; font-size:11.5px; }
  .regs .r { color:var(--muted); } .regs .r b { color:var(--text); }
  .state { font-family:"SF Mono",Consolas,monospace; font-size:11px; color:var(--muted); margin-top:4px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  .wal td,.wal th { border:1px solid #2c313f; padding:3px 8px; text-align:left; font-family:"SF Mono",monospace; }
  .wal th { background:var(--panel2); color:var(--muted); }
  .flyout-overlay { display:none; position:absolute; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,.4); z-index:40; }
  .flyout-overlay.open { display:block; }
  .flyout { position:absolute; top:0; right:0; width:var(--flyout-w); height:100%;
    background:var(--panel); border-left:1px solid #2c313f; z-index:50;
    overflow-y:auto; padding:16px; display:none; flex-direction:column; }
  .flyout.open { display:flex; }
  .flyout h3 { font-size:14px; color:var(--accent); margin-bottom:10px; }
  .flyout .close-btn { position:absolute; top:10px; right:12px; background:none; border:none;
    color:var(--muted); font-size:18px; cursor:pointer; }
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
  @media (max-width:900px){ .sidebar{width:180px;} .flyout{width:320px;} .regs{grid-template-columns:repeat(2,1fr);} }
</style>
</head>
<body>
<div class="topbar">
  <h1>PicoScript</h1>
  <div class="tabs">
    <button class="tab active" onclick="showView('guide')">Guide</button>
    <button class="tab" onclick="showView('play')">Playground</button>
    <button class="tab" onclick="showView('ref')">Reference</button>
  </div>
  <div class="lang-toggle" id="langToggle">
    <button data-lang="c" class="active" onclick="setLang('c')">C &#123;&#125;</button>
    <button data-lang="basic" onclick="setLang('basic')">BASIC</button>
    <button data-lang="python" onclick="setLang('python')">Python</button>
    <button data-lang="english" onclick="setLang('english')">English</button>
  </div>
</div>
<div class="main">
  <!-- GUIDE VIEW -->
  <div class="view active" id="view-guide">
    <div style="display:flex;flex:1;overflow:hidden">
      <div class="sidebar" id="guideTree"></div>
      <div class="card-area" id="guideContent"></div>
    </div>
    <div class="dbg-bar" id="guideDbgBar">
      <button class="active" onclick="gToggleDbg(this,'gdbg-disasm')">Disassembly</button>
      <button onclick="gToggleDbg(this,'gdbg-regs')">Registers</button>
      <button onclick="gToggleDbg(this,'gdbg-output')">Output</button>
      <button style="margin-left:auto" onclick="gCollapseDbg()">&#9660;</button>
    </div>
    <div class="dbg-panels" id="guideDbgPanels" style="max-height:180px">
      <div class="dbg-panel active" id="gdbg-disasm"><div class="listing" id="glisting"></div><div class="state" id="gstate"></div></div>
      <div class="dbg-panel" id="gdbg-regs"><div class="regs" id="gregs"></div></div>
      <div class="dbg-panel" id="gdbg-output"><div class="out" id="gout"></div></div>
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
          </div>
          <div class="file-list" id="fileList"></div>
          <div class="file-status" id="fileStatus"></div>
        </div>
      </div>
      <div class="ide-editor">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <select id="example" style="width:auto"></select>
          <select id="lang" style="width:auto"><option value="c">C-style</option><option value="basic">BASIC</option><option value="python">Python</option><option value="english">English</option></select>
          <label style="font-size:11px;color:var(--muted)"><input type="checkbox" id="walpersist"> PicoWAL</label>
        </div>
        <div id="monaco" style="flex:1;border:1px solid #2c313f;border-radius:6px;overflow:hidden;min-height:120px"></div>
        <textarea id="src" style="flex:1;display:none;min-height:120px" spellcheck="false"></textarea>
        <div class="controls">
          <button class="act" onclick="compileSrc(true)">Compile &amp; Run &#9654;</button>
          <button class="ghost" onclick="compileSrc(false)">Compile &amp; Step</button>
          <button class="ghost" onclick="dbgStep()">Step</button>
          <button class="ghost" onclick="dbgReset()">Reset</button>
        </div>
        <div id="cerr" class="cerr"></div>
      </div>
    </div>
    <div class="dbg-bar" id="playDbgBar">
      <button class="active" onclick="pToggleDbg(this,'pdbg-disasm')">Disassembly</button>
      <button onclick="pToggleDbg(this,'pdbg-regs')">Registers</button>
      <button onclick="pToggleDbg(this,'pdbg-watches')">Watches</button>
      <button onclick="pToggleDbg(this,'pdbg-cards')">Cards</button>
      <button onclick="pToggleDbg(this,'pdbg-output')">Output</button>
      <button style="margin-left:auto" onclick="pCollapseDbg()">&#9660;</button>
    </div>
    <div class="dbg-panels" id="playDbgPanels" style="max-height:200px">
      <div class="dbg-panel active" id="pdbg-disasm"><div class="listing" id="listing"></div><div class="state" id="state"></div></div>
      <div class="dbg-panel" id="pdbg-regs"><div class="regs" id="regs"></div></div>
      <div class="dbg-panel" id="pdbg-watches"><table class="wal"><thead><tr><th>var</th><th>reg</th><th>value</th></tr></thead><tbody id="watches"></tbody></table></div>
      <div class="dbg-panel" id="pdbg-cards"><table class="wal"><tbody id="walbody"></tbody></table><button class="ghost" style="margin-top:6px" onclick="walClear()">Clear</button></div>
      <div class="dbg-panel" id="pdbg-output"><div class="out" id="out"></div></div>
    </div>
  </div>
  <!-- REFERENCE VIEW -->
  <div class="view" id="view-ref">
    <div class="ref-wrap">
      <div class="sidebar" id="refTree"></div>
      <div class="ref-content" id="refContent">
        <div class="ref-section active" id="ref-overview">
          <h2 style="color:var(--accent);margin-bottom:10px">Overview</h2>
          <p style="color:var(--muted);font-size:13px;max-width:750px;line-height:1.7">
            <b>PicoScript</b> compiles from four interchangeable surface syntaxes (C-style, BASIC, Python, English)
            to a single 16-opcode 32-bit bytecode. Programs run on embedded VMs, can be AOT-compiled to C or JS,
            and are designed for request-response workloads on bare-metal or browser runtimes.<br><br>
            <b>Why?</b> One semantic model, four reading styles. A diverse team picks the syntax they know and
            produces byte-identical output. The compiler is ~3KB JS, zero dependencies.<br><br>
            <b>Use case:</b> Sandboxed, deterministic compute capsules that handle HTTP requests, store data in
            PicoWAL cards, and produce responses with memory safety enforced by the kernel.
          </p>
        </div>
        <div class="ref-section" id="ref-syntax"><h2 style="color:var(--accent);margin-bottom:10px">Language Syntax</h2><div id="syntaxContent"></div></div>
        <div class="ref-section" id="ref-namespaces"><h2 style="color:var(--accent);margin-bottom:10px">Namespaces &amp; Methods</h2><p style="color:var(--muted);font-size:12px;margin-bottom:14px">Every built-in namespace and method. Call as <code>Namespace.Method(args)</code>.</p><div id="nsContent"></div></div>
        <div class="ref-section" id="ref-bindings">
          <h2 style="color:var(--accent);margin-bottom:10px">Bindings &amp; I/O</h2>
          <div style="color:var(--muted);font-size:13px;max-width:750px;line-height:1.7">
            <p><b>Network binding:</b> A capsule is bound to a port by the kernel. The kernel handles TCP/TLS, parses HTTP, and delivers a request descriptor via FIFO. Read with <code>Req.*</code>, respond with <code>Resp.*</code>.</p>
            <p style="margin-top:10px"><b>PicoWAL cards:</b> Persistent card packs via PicoBinarySerializer. Use <code>Storage.UsePack/AddCard/SetField/QueryCard</code>.</p>
            <p style="margin-top:10px"><b>FIFO:</b> Inbound (request descriptors) and outbound (response graph). All inter-capsule communication is kernel-mediated.</p>
            <p style="margin-top:10px"><b>Zero-copy:</b> The kernel manages memory via descriptors and leases. <code>Lease.Acquire/GetSpan</code> reads without copying. The programmer does <b>not</b> manage DMA directly. Invariants: one owner (I2), sealed immutability (I3), validated leases (I4), eventual release (I8).</p>
            <p style="margin-top:14px"><b>GPIO (devices):</b> Pins carry an analog value in [0,1024]. Hooks <code>Gpio.SetDir/GetDir/SetPull/GetPull/Write/Read/Count</code>. Readable BASIC DSL: <code>GPIO DIR 2 = OUT</code>, <code>GPIO PULL 5 = UP</code>, <code>GPIO WRITE 2 = 1024</code>; reads <code>GPIO READ 2</code> / <code>GPIO DIR 2</code> / <code>GPIO COUNT</code>. The playground renders a live labelled pin header.</p>
            <p style="margin-top:10px"><b>Cards / storage DSL:</b> a readable BASIC split &mdash; <b>STORE</b> writes, <b>LOAD</b> reads. Writes: <code>STORE USE PACK 1</code>, <code>DIM A NEW CARD</code>, <code>STORE SET "qty" = 42</code>, <code>STORE DELETE CARD id</code>. Reads: <code>DIM A = LOAD CARD id</code>, <code>LOAD "qty"</code> / <code>LOAD "qty" AS TEXT</code>, <code>LOAD QUERY "qty &gt; 40"</code>, <code>LOAD RESULT 0</code>. Typed pack schemas via <code>Storage.GetSchemaForPack/SetSchemaForPack</code> + the playground schema designer.</p>
            <p style="margin-top:10px"><b>Server entry:</b> mark an endpoint with <code>Server.Main { ... }</code> (C) or <code>SERVER ... ENDSERVER</code> (BASIC); the compiler guarantees the request-context + status + body bytecode the kernel worker expects.</p>
            <p style="margin-top:10px"><b>Capsules:</b> a capsule is a pack namespace (packs 1024&ndash;4095). Card 0 holds a deterministic manifest; source = card 1000+N, bytecode = 10000+N. Runtime hooks <code>Pack.Use/Card.Read/Write/Address/Fifo.Open/Send/Recv/Poll</code> (capability <code>CAP_CAPSULE</code>). Author manifests with <code>picocapsule</code> (Python + byte-identical <code>vm/picocapsule.js</code>); full contract in <code>docs/PIOS_CAPSULE_HANDOFF.md</code>.</p>
          </div>
        </div>
        <div class="ref-section" id="ref-samples"><h2 style="color:var(--accent);margin-bottom:10px">Application Samples</h2><div id="samplesContent"></div></div>
        <div class="ref-section" id="ref-internals"><h2 style="color:var(--accent);margin-bottom:10px">Deep Technical Internals <span class="badge">implementer</span></h2><p style="color:var(--muted);font-size:12px;margin-bottom:10px">For contributors. Not required for app authors.</p><div class="docpanel active" style="display:block" id="doc-internals-inline"></div></div>
        <div class="ref-section" id="ref-rawdocs"><h2 style="color:var(--accent);margin-bottom:10px">Source Documentation</h2><div class="docnav"><!--__DOCNAV__--></div><!--__DOCBODY__--></div>
      </div>
    </div>
  </div>
  <!-- Flyout triggers -->
  <div class="flyout-trigger" id="flyoutTriggers">
    <button onclick="openFlyout('fly-http')">HTTP</button>
    <button onclick="openFlyout('fly-tcp')">TCP</button>
    <button onclick="openFlyout('fly-cards')">Cards</button>
    <button onclick="openFlyout('fly-query')">Query</button>
    <button onclick="openFlyout('fly-spans')">Spans</button>
  </div>
  <div class="flyout-overlay" id="flyoutOverlay" onclick="closeFlyout()"></div>
  <!-- HTTP flyout -->
  <div class="flyout" id="fly-http">
    <button class="close-btn" onclick="closeFlyout()">&times;</button>
    <h3>HTTP Simulator</h3>
    <select id="reqmode" style="margin-bottom:6px"><option value="text">HTTP/text</option><option value="hex">hex</option></select>
    <textarea id="reqbox" style="height:90px" spellcheck="false"></textarea>
    <div style="display:flex;gap:6px;margin:8px 0"><button class="act" onclick="sendRequest()">Send &#9654;</button><button class="ghost" onclick="loadSample()">Sample</button><button class="ghost" onclick="loadResponder()">Responder</button></div>
    <div class="respbox" id="respout">(send a request)</div>
  </div>
  <!-- TCP flyout -->
  <div class="flyout" id="fly-tcp">
    <button class="close-btn" onclick="closeFlyout()">&times;</button>
    <h3>TCP / Raw Bytes</h3>
    <textarea id="tcpbox" style="height:60px" placeholder="48 65 6c 6c 6f" spellcheck="false"></textarea>
    <div style="display:flex;gap:6px;margin:8px 0"><button class="act" onclick="sendTcp()">Send &#9654;</button></div>
    <div style="max-height:150px;overflow:auto"><table class="wal"><tbody id="walbody2"></tbody></table></div>
    <button class="ghost" style="margin-top:6px" onclick="walClear()">Clear</button>
  </div>
  <!-- Cards flyout -->
  <div class="flyout" id="fly-cards">
    <button class="close-btn" onclick="closeFlyout()">&times;</button>
    <h3>Cards (PicoStore)</h3>
    <input id="packname" value="orders" style="width:100%;margin-bottom:6px" placeholder="pack name">
    <textarea id="cardjson" style="height:50px" spellcheck="false">{"qty": 42, "sku": "ABC", "status": 1}</textarea>
    <div style="display:flex;gap:6px;margin:6px 0"><button class="act" onclick="cardCreate()">Create</button><button class="ghost" onclick="cardSeed()">Seed</button><button class="ghost" onclick="cardClear()">Clear</button></div>
    <div id="cardmsg" class="cerr"></div>
    <div class="respbox" id="serout" style="min-height:24px;font-size:10px">&hellip;</div>
    <div style="flex:1;overflow:auto;margin-top:6px"><table class="wal"><tbody id="cardlist"></tbody></table></div>
  </div>
  <!-- Query flyout -->
  <div class="flyout" id="fly-query">
    <button class="close-btn" onclick="closeFlyout()">&times;</button>
    <h3>Query</h3>
    <input id="querybox" value="qty > 40 AND status = 1" style="width:100%;margin-bottom:6px">
    <button class="act" onclick="cardQuery()">Run &#9654;</button>
    <div style="flex:1;overflow:auto;margin-top:8px"><table class="wal"><thead><tr><th>id</th><th>record</th></tr></thead><tbody id="qresults"></tbody></table></div>
  </div>
  <!-- Spans flyout -->
  <div class="flyout" id="fly-spans">
    <button class="close-btn" onclick="closeFlyout()">&times;</button>
    <h3>Spans &amp; Memory</h3>
    <p style="color:var(--muted);font-size:12px;line-height:1.6">
      <b>Memory.Set(addr, byte)</b> write to arena<br><b>Span.Make(addr, len)</b> create span<br>
      <b>Span.Slice(span, off, len)</b> zero-copy view<br><b>Span.Materialize(span)</b> copy to new region<br>
      <b>Span.Len/Get</b> length and indexed read
    </p>
  </div>
</div>

<script>/*__HOOKS__*/</script>
<script>/*__PCZ__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
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

function showView(v){
  document.querySelectorAll('.view').forEach(function(e){e.classList.remove('active');});
  document.getElementById('view-'+v).classList.add('active');
  document.querySelectorAll('.tabs .tab').forEach(function(b){b.classList.remove('active');});
  var idx={guide:0,play:1,ref:2}[v]||0;
  document.querySelectorAll('.tabs .tab')[idx].classList.add('active');
  var ft=document.getElementById('flyoutTriggers');
  if(ft) ft.style.display=(v==='play')?'flex':'none';
}
function setLang(lang){
  CUR_LANG=lang;
  document.querySelectorAll('#langToggle button').forEach(function(b){b.classList.toggle('active',b.getAttribute('data-lang')===lang);});
  document.getElementById('lang').value=lang;
  if(typeof onLangChange==='function') onLangChange();
  showGuideCard(CUR_GUIDE_CARD);
  renderSyntaxRef(); renderSamples();
}

var GROUPS=[{name:'Basics',items:[0]},{name:'Control Flow',items:[1,2,3,4,10,11]},{name:'Operators',items:[5]},{name:'Dispatch / State Machine',items:[6,7]},{name:'Subroutines',items:[8,9]},{name:'I/O & Cards',items:[12,13]}];
function buildGuideTree(){
  var html='';
  GROUPS.forEach(function(g){
    html+='<div class="group-title">'+esc(g.name)+'</div>';
    g.items.forEach(function(idx){if(idx<DATA.length) html+='<div class="tree-item'+(idx===0?' active':'')+'" data-idx="'+idx+'" onclick="showGuideCard('+idx+')">'+esc(DATA[idx].title)+'</div>';});
  });
  document.getElementById('guideTree').innerHTML=html;
}
function showGuideCard(idx){
  CUR_GUIDE_CARD=idx;
  var d=DATA[idx],lang=CUR_LANG;if(!d[lang]) lang=d.c?'c':'basic';
  var SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle'};
  document.getElementById('guideContent').innerHTML=
    '<div class="card-title">'+(idx+1)+'. '+esc(d.title)+'</div>'+
    '<div class="card-desc">'+d.desc+'</div>'+
    (d[lang]?'<pre class="'+SC[lang]+'">'+esc(d[lang].src)+'</pre>':'<pre style="color:var(--muted)">(not available)</pre>')+
    '<div class="run-area"><button class="act" onclick="guideRun('+idx+')">Run &#9654;</button>'+
    '<button class="ghost" onclick="guideStep('+idx+')">Step</button>'+
    '<button class="ghost" onclick="guideEdit('+idx+')">Edit in Playground</button>'+
    '<span class="out" id="gcardout'+idx+'"></span></div>';
  document.querySelectorAll('#guideTree .tree-item').forEach(function(el){el.classList.toggle('active',parseInt(el.getAttribute('data-idx'))===idx);});
}

var GDBG={words:[],disasm:[],vm:null};
function guideRun(i){
  var d=DATA[i],styles=['c','basic','python','english'],parts=[],ref=null,same=true;
  styles.forEach(function(s){if(!d[s])return;var o=runWords(d[s].words).outputInts();if(ref===null)ref=JSON.stringify(o);else if(JSON.stringify(o)!==ref)same=false;parts.push(s+' \u2192 ['+o.join(', ')+']');});
  var el=document.getElementById('gcardout'+i);if(el)el.innerHTML=parts.join(' &nbsp; ')+'  '+(same?'&#10003;':'&#9888;');
  var lang=CUR_LANG;if(!d[lang])lang='basic';
  GDBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});GDBG.disasm=d[lang].disasm.slice();
  GDBG.vm=new PicoVM();GDBG.vm.load(GDBG.words);var g=0;while(GDBG.vm.step()&&g++<200000){}gRender();
  document.getElementById('guideDbgPanels').classList.remove('collapsed');
}
function guideStep(i){var d=DATA[i],lang=CUR_LANG;if(!d[lang])lang='basic';GDBG.words=d[lang].words.map(function(h){return parseInt(h,16)>>>0;});GDBG.disasm=d[lang].disasm.slice();GDBG.vm=new PicoVM();GDBG.vm.load(GDBG.words);gRender();document.getElementById('guideDbgPanels').classList.remove('collapsed');}
function guideEdit(i){var d=DATA[i],lang=CUR_LANG;if(!d[lang])lang='basic';document.getElementById('lang').value=lang;onLangChange();setSrc(d[lang].src);showView('play');compileSrc(false);}
function gRender(){
  var vm=GDBG.vm;if(!vm)return;
  document.getElementById('glisting').innerHTML=GDBG.disasm.map(function(t,idx){return '<div class="row'+(idx===vm.pc?' pc':'')+'">'+String(idx).padStart(3,' ')+'  '+esc(t)+'</div>';}).join('');
  var pcrow=document.querySelector('#glisting .row.pc');if(pcrow)pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('gregs').innerHTML=Array.from(vm.regs).map(function(v,idx){return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  document.getElementById('gstate').textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted;
  document.getElementById('gout').textContent='output: ['+vm.outputInts().join(', ')+']';
}
function gToggleDbg(btn,pid){document.querySelectorAll('#guideDbgBar button').forEach(function(b){b.classList.remove('active');});btn.classList.add('active');document.querySelectorAll('#guideDbgPanels .dbg-panel').forEach(function(p){p.classList.remove('active');});var el=document.getElementById(pid);if(el)el.classList.add('active');document.getElementById('guideDbgPanels').classList.remove('collapsed');}
function gCollapseDbg(){document.getElementById('guideDbgPanels').classList.toggle('collapsed');}

// playground debugger
var DBG={words:[],disasm:[],vm:null,vars:{}};
function pToggleDbg(btn,pid){document.querySelectorAll('#playDbgBar button').forEach(function(b){b.classList.remove('active');});btn.classList.add('active');document.querySelectorAll('#playDbgPanels .dbg-panel').forEach(function(p){p.classList.remove('active');});var el=document.getElementById(pid);if(el)el.classList.add('active');document.getElementById('playDbgPanels').classList.remove('collapsed');}
function pCollapseDbg(){document.getElementById('playDbgPanels').classList.toggle('collapsed');}

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
function monacoLangId(lang){return{c:'picoc',basic:'picobasic',python:'picopython',english:'picoenglish'}[lang]||'picoc';}
function onLangChange(){if(EDITOR)monaco.editor.setModelLanguage(EDITOR.getModel(),monacoLangId(document.getElementById('lang').value));if(typeof filesRender==='function')filesRender();}
function hookNames(){var H=(typeof PV_HOOKS!=='undefined'&&PV_HOOKS.BY_CODE)?PV_HOOKS.BY_CODE:{};var out=[];for(var k in H)out.push(H[k]);return out;}
var MONARCH={picoc:{keywords:['int','var','void','if','else','while','for','return','break','continue','print','switch','case','default','do','goto','dispatch'],tokenizer:{root:[[/\/\/.*$/,'comment'],[/\/\*/,'comment','@block'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[{}()\[\];,.]/,'delimiter'],[/[+\-*/%=<>!&|?:]+/,'operator']],block:[[/\*\//,'comment','@pop'],[/./,'comment']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}},picobasic:{ignoreCase:true,keywords:['DIM','LET','IF','THEN','ELSEIF','ELSE','ENDIF','WHILE','ENDWHILE','FOR','TO','STEP','NEXT','FOREACH','IN','ENDFOREACH','SWITCH','CASE','DEFAULT','ENDSWITCH','DISPATCH','ENDDISPATCH','GOTO','GOSUB','SUB','ENDSUB','RETURN','PRINT','AND','OR','NOT','DO','LOOP','UNTIL','BREAK','SKIP','INC','DEC','IIF','EQ','NE','LT','GT','LE','GE','MOD'],tokenizer:{root:[[/'.*$/,'comment'],[/\/\/.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[()\[\];,.:]/,'delimiter'],[/[+\-*/=<>]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}},picopython:{keywords:['if','elif','else','while','for','in','range','def','return','break','continue','pass','and','or','not','print','True','False','match','case','dispatch'],tokenizer:{root:[[/#.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.)/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/'/,'string','@str2'],[/[()\[\]:,.]/,'delimiter'],[/[+\-*/%=<>!]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']],str2:[[/[^']+/,'string'],[/'/,'string','@pop']]}},picoenglish:{ignoreCase:true,keywords:['set','let','to','be','add','subtract','from','increase','decrease','multiply','divide','by','print','show','display','if','otherwise','while','repeat','as','long','for','each','times','with','define','do','call','return','stop','break','skip','continue','choose','when','dispatch','on','is','greater','less','than','at','least','most','equal','equals','exceeds','plus','minus','modulo','mod','over','and','or','not','true','false'],tokenizer:{root:[[/#.*$/,'comment'],[/[A-Za-z_]\w*(?=\s*\.\s*[A-Za-z_]\w*\s*\()/,'type'],[/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],[/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/[()\[\],.:]/,'delimiter'],[/[+\-*/%=<>]+/,'operator']],str:[[/[^"]+/,'string'],[/"/,'string','@pop']]}}};
function registerLang(id){monaco.languages.register({id:id});monaco.languages.setMonarchTokensProvider(id,MONARCH[id]);monaco.languages.registerCompletionItemProvider(id,{provideCompletionItems:function(model,pos){var kw=MONARCH[id].keywords||[];var sug=kw.map(function(k){return{label:k,kind:monaco.languages.CompletionItemKind.Keyword,insertText:k};});hookNames().forEach(function(name){sug.push({label:name,kind:monaco.languages.CompletionItemKind.Function,insertText:name+'('});});return{suggestions:sug};}});}
function initMonaco(){if(typeof require==='undefined'||!require.config){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';return;}try{require.config({paths:{vs:'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs'}});require(['vs/editor/editor.main'],function(){['picoc','picobasic','picopython','picoenglish'].forEach(registerLang);EDITOR=monaco.editor.create(document.getElementById('monaco'),{value:document.getElementById('src').value,language:monacoLangId(document.getElementById('lang').value),theme:'vs-dark',minimap:{enabled:false},fontSize:13,lineNumbers:'on',scrollBeyondLastLine:false,automaticLayout:true,tabSize:4,insertSpaces:true});EDITOR.onDidChangeModelContent(function(){document.getElementById('src').value=EDITOR.getValue();filesRender();});},function(){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';});}catch(e){document.getElementById('monaco').style.display='none';document.getElementById('src').style.display='block';}}

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
      if(typeof name==='string'&&name.trim()&&typeof f.src==='string'&&['c','basic','python','english'].indexOf(f.lang)>=0){
        out[name]={lang:f.lang,src:f.src,updated:Number(f.updated)||0};
      }
    });
    return out;
  }catch(e){return {};}
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
  return f.src!==getSrc()||f.lang!==document.getElementById('lang').value;
}
function filesRender(){
  var list=document.getElementById('fileList'); if(!list) return;
  var files=filesRead(), names=Object.keys(files).sort(function(a,b){return (files[b].updated||0)-(files[a].updated||0)||a.localeCompare(b);});
  if(!names.length){list.innerHTML='<div class="file-empty">No saved files yet.</div>';return;}
  var dirty=filesIsDirty();
  list.innerHTML=names.map(function(name){
    var f=files[name], isActive=name===ACTIVE_FILE, dot=isActive&&dirty?'*':'';
    var when=f.updated?new Date(f.updated).toLocaleString():'';
    return '<div class="file-item'+(isActive?' active':'')+'" data-name="'+filesEscAttr(name)+'">'+
      '<div class="file-row"><span class="file-dirty">'+dot+'</span><span class="file-name">'+esc(name)+'</span><span class="file-badge '+f.lang+'">'+f.lang+'</span></div>'+
      '<div class="file-meta">'+(when?esc(when):'saved')+'</div></div>';
  }).join('');
  list.querySelectorAll('.file-item').forEach(function(el){el.onclick=function(){psFilesOpen(el.getAttribute('data-name'));};});
}
function filesToggle(){var el=document.getElementById('fileSidebar');if(!el)return;el.classList.toggle('collapsed');var b=el.querySelector('.file-head button');if(b)b.innerHTML=el.classList.contains('collapsed')?'&#9654;':'&#9664;';}
function psFilesList(){return filesRead();}
function psFilesNew(name){
  var files=filesRead(); name=(name||((typeof prompt==='function')?prompt('New file name',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  if(files[name]&&typeof confirm==='function'&&!confirm('Replace "'+name+'"?')) return null;
  setSrc(''); files[name]={lang:document.getElementById('lang').value||'basic',src:'',updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender(); filesStatus('New file '+name); return name;
}
function psFilesSave(name){
  var files=filesRead(); name=(name||ACTIVE_FILE||((typeof prompt==='function')?prompt('Save file as',filesUniqueName(files)):filesUniqueName(files))||'').trim();
  if(!name) return null;
  files[name]={lang:document.getElementById('lang').value||'basic',src:getSrc(),updated:Date.now()}; filesWrite(files); filesSetActive(name); filesRender(); filesStatus('Saved '+name); return name;
}
function psFilesSaveAs(name){return psFilesSave(name||((typeof prompt==='function')?prompt('Save file as',ACTIVE_FILE||filesUniqueName(filesRead())):''));}
function psFilesOpen(name){
  var files=filesRead(), names=Object.keys(files).sort();
  name=(name||((typeof prompt==='function')?prompt('Open file',ACTIVE_FILE||names[0]||''):'')||'').trim();
  if(!name||!files[name]){filesStatus(name?'File not found: '+name:'Open cancelled',!!name);return null;}
  document.getElementById('lang').value=files[name].lang; onLangChange(); setSrc(files[name].src); filesSetActive(name); filesRender(); filesStatus('Opened '+name); compileSrc(false); return files[name];
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

function compileSrc(run){
  var lang=document.getElementById('lang').value,src=getSrc(),err=document.getElementById('cerr');
  try{var r=PicoCompile.compileDebug(src,lang);DBG.words=r.words.map(function(w){return w>>>0;});DBG.disasm=DBG.words.map(jsDisasm);DBG.vars=r.vars||{};err.textContent='compiled '+DBG.words.length+' words';err.style.color='#7ee787';dbgReset();if(run)dbgRun();}
  catch(e){err.textContent=String(e.message||e);err.style.color='#ff7b72';}
}
function dbgReset(){var persist=document.getElementById('walpersist')&&document.getElementById('walpersist').checked;DBG.vm=new PicoVM(persist?{cards:walBackend()}:{});DBG.vm.load(DBG.words);render();renderWal();}
function dbgStep(){if(DBG.vm){DBG.vm.step();render();renderWal();}}
function dbgRun(){if(!DBG.vm)dbgReset();var g=0;while(DBG.vm.step()&&g++<200000){}render();renderWal();}
function render(){
  var vm=DBG.vm;if(!vm)return;
  document.getElementById('listing').innerHTML=DBG.disasm.map(function(t,idx){return '<div class="row'+(idx===vm.pc?' pc':'')+'">'+String(idx).padStart(3,' ')+'  '+esc(t)+'</div>';}).join('');
  var pcrow=document.querySelector('#listing .row.pc');if(pcrow)pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('regs').innerHTML=Array.from(vm.regs).map(function(v,idx){return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  var wb=document.getElementById('watches');
  if(wb){var vars=DBG.vars||{},ks=Object.keys(vars);wb.innerHTML=ks.length?ks.map(function(name){var rr=vars[name];return '<tr><td>'+esc(name)+'</td><td>R'+rr+'</td><td><b>'+vm.regs[rr]+'</b></td></tr>';}).join(''):'<tr><td colspan="3" style="color:var(--muted)">(none)</td></tr>';}
  document.getElementById('state').textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted+'  http_status='+vm.httpStatus;
  var _txt=vm.outputText(),_pr=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_txt);
  document.getElementById('out').textContent='output: ['+vm.outputInts().join(', ')+']'+(_pr&&_txt?'\ntext: '+JSON.stringify(_txt):'');
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
function rowsHtml(entries,withDel){if(!entries.length)return'<tr><td colspan="'+(withDel?3:2)+'" style="color:var(--muted)">(none)</td></tr>';return entries.map(function(e){var id=e[0],rec=e[1];var del=withDel?'<td><button class="ghost" style="padding:1px 7px" onclick="cardDelete('+id+')">&times;</button></td>':'';return'<tr><td>'+id+'</td><td>'+esc(JSON.stringify(rec))+'</td>'+del+'</tr>';}).join('');}
function cardRender(){document.getElementById('cardlist').innerHTML=rowsHtml(STORE.all(curPack()),true);}
function cardCreate(){var pack=curPack(),rec;try{rec=JSON.parse(document.getElementById('cardjson').value);}catch(e){cardMsg('JSON: '+e.message,true);return;}try{var id=STORE.create(pack,rec);var hex=STORE.cardBytesHex(pack,id);document.getElementById('serout').textContent=hex;cardMsg('#'+id+' created',false);cardRender();}catch(e){cardMsg(e.message,true);}}
function cardSeed(){var pack=curPack();[{qty:42,sku:"ABC",status:1},{qty:7,sku:"XYZ",status:0},{qty:99,sku:"ABC",status:1},{qty:55,sku:"QRS",status:2}].forEach(function(r){STORE.create(pack,r);});cardMsg('Seeded 4',false);cardRender();}
function cardClear(){var pack=curPack();STORE.all(pack).forEach(function(e){STORE.delete(pack,e[0]);});STORE.b.remove(pack+":ids");STORE.b.remove(pack+":next");document.getElementById('qresults').innerHTML='';cardMsg('Cleared',false);cardRender();}
function cardDelete(id){STORE.delete(curPack(),id);cardRender();}
function cardQuery(){var pack=curPack(),q=document.getElementById('querybox').value;try{var res=STORE.query(pack,q);document.getElementById('qresults').innerHTML=rowsHtml(res,false);cardMsg(res.length+' match'+(res.length===1?'':'es'),false);}catch(e){cardMsg(e.message,true);}}

// HTTP/TCP
function methodCode(m){return({GET:1,POST:2,PUT:3,DELETE:4,HEAD:5,PATCH:6,OPTIONS:7})[(m||'').toUpperCase()]||0;}
function parseRequest(text,isHex){var bytes=[];if(isHex){text.replace(/0x/gi,'').replace(/[,]/g,' ').trim().split(/\s+/).filter(Boolean).forEach(function(h){if(h.length>2){for(var i=0;i+1<h.length;i+=2)bytes.push(parseInt(h.substr(i,2),16)&0xFF);}else if(h.length)bytes.push(parseInt(h,16)&0xFF);});}else{for(var i=0;i<text.length;i++)bytes.push(text.charCodeAt(i)&0xFF);}var sum=0;bytes.forEach(function(b){sum=(sum+b)|0;});var method=0,pathLen=0,bodyLen=0;if(!isHex){var fl=(text.split(/\r?\n/)[0]||'').split(/\s+/);if(fl.length>=2){method=methodCode(fl[0]);pathLen=fl[1].length;}var idx=text.indexOf('\r\n\r\n');var sep=4;if(idx<0){idx=text.indexOf('\n\n');sep=2;}if(idx>=0){bodyLen=text.length-(idx+sep);if(bodyLen<0)bodyLen=0;}}return{bytes:bytes,length:bytes.length,method:method,bodyLen:bodyLen,sum:sum,pathLen:pathLen};}
function writeDescriptor(wal,req){wal.set(0,req.length);wal.set(1,req.method);wal.set(2,req.bodyLen);wal.set(3,req.sum);wal.set(4,req.pathLen);var n=Math.min(req.bytes.length,256);for(var i=0;i<n;i++)wal.set(256+i,req.bytes[i]);}
function sendRequest(){var text=document.getElementById('reqbox').value,isHex=document.getElementById('reqmode').value==='hex';var req=parseRequest(text,isHex),wal=walBackend();writeDescriptor(wal,req);var lang=document.getElementById('lang').value,src=getSrc();try{var r=PicoCompile.compile(src,lang);}catch(e){document.getElementById('respout').textContent='compile error: '+(e.message||e);return;}var vm=new PicoVM({cards:wal});vm.run(r.words);renderResponse(vm,req);renderWal();}
function sendTcp(){var text=document.getElementById('tcpbox').value;var req=parseRequest(text,true),wal=walBackend();writeDescriptor(wal,req);renderWal();}
function renderResponse(vm,req){var reasons={200:'OK',201:'Created',400:'Bad Request',404:'Not Found',500:'Error'};var el=document.getElementById('respout');el.style.color='#7ee787';var body=vm.outputInts();if(vm.httpStatus<0){el.style.color='#ffd866';el.textContent='(no Net.Status)\noutput: ['+body.join(', ')+']';return;}var L=[];L.push('HTTP/1.1 '+vm.httpStatus+' '+(reasons[vm.httpStatus]||''));L.push('Content-Type: '+(vm.httpType||'application/octet-stream'));L.push('X-Steps: '+vm.steps);L.push('');var _bt=vm.outputText(),_bp=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_bt);L.push(_bp&&_bt?_bt:JSON.stringify(body));el.textContent=L.join('\n');}
function loadResponder(){document.getElementById('lang').value='basic';onLangChange();setSrc(RESPONDER);compileSrc(false);}
function loadSample(){document.getElementById('reqmode').value='text';document.getElementById('reqbox').value='POST /orders HTTP/1.1\r\nHost: pico.dev\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"qty": 42}';}

// Flyouts
function openFlyout(id){closeFlyout();document.getElementById('flyoutOverlay').classList.add('open');document.getElementById(id).classList.add('open');if(id==='fly-cards')try{cardRender();}catch(e){}}
function closeFlyout(){document.getElementById('flyoutOverlay').classList.remove('open');document.querySelectorAll('.flyout').forEach(function(f){f.classList.remove('open');});}

// Reference tree
var REF_SECTIONS=['ref-overview','ref-syntax','ref-namespaces','ref-bindings','ref-samples','ref-internals','ref-rawdocs'];
var REF_LABELS=['Overview','Language Syntax','Namespaces','Bindings & I/O','App Samples','Internals','Source Docs'];
function buildRefTree(){var html='<div class="group-title">Reference</div>';REF_SECTIONS.forEach(function(id,i){html+='<div class="tree-item'+(i===0?' active':'')+'" data-ref="'+id+'" onclick="showRefSection(\''+id+'\')">'+REF_LABELS[i]+(id==='ref-internals'?' <span class="badge" style="font-size:8px">impl</span>':'')+'</div>';});document.getElementById('refTree').innerHTML=html;}
function showRefSection(id){document.querySelectorAll('.ref-section').forEach(function(s){s.classList.remove('active');});document.getElementById(id).classList.add('active');document.querySelectorAll('#refTree .tree-item').forEach(function(el){el.classList.toggle('active',el.getAttribute('data-ref')===id);});}
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
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle'};
  var html='<p style="color:var(--muted);font-size:12px;margin-bottom:12px">Showing <b>'+lang.toUpperCase()+'</b>. Toggle dialect above.</p>';
  DATA.forEach(function(d,i){if(!d[lang])return;html+='<div style="margin-bottom:14px"><div style="font-weight:600;font-size:13px;margin-bottom:3px">'+(i+1)+'. '+esc(d.title)+'</div><pre class="'+SC[lang]+'" style="max-width:700px">'+esc(d[lang].src)+'</pre></div>';});
  document.getElementById('syntaxContent').innerHTML=html;
}
function renderSamples(){
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle'};
  var samples=[
    {title:'PicoWAL API Server',desc:'CRUD API routing by method',c:'int method = Req.Method();\nStorage.UsePack(1);\nif (method == 2) {\n    int id = Storage.AddCard();\n    Resp.Status(201);\n    print(id);\n} else {\n    int n = Storage.QueryCard(0);\n    Resp.Status(200);\n    print(n);\n}\nResp.End();',basic:"DIM METHOD = Req.Method()\nStorage.UsePack(1)\nIF METHOD EQ 2 THEN\n    DIM ID = Storage.AddCard()\n    Resp.Status(201)\n    PRINT ID\nELSE\n    DIM N = Storage.QueryCard(0)\n    Resp.Status(200)\n    PRINT N\nENDIF\nResp.End()",python:'method = Req.Method()\nStorage.UsePack(1)\nif method == 2:\n    id = Storage.AddCard()\n    Resp.Status(201)\n    print(id)\nelse:\n    n = Storage.QueryCard(0)\n    Resp.Status(200)\n    print(n)\nResp.End()',english:'Set method to Req.Method().\nStorage.UsePack(1).\nIf method is 2:\n    Set id to Storage.AddCard().\n    Resp.Status(201).\n    Print id.\nOtherwise:\n    Set n to Storage.QueryCard(0).\n    Resp.Status(200).\n    Print n.\nResp.End().'},
    {title:'Template Web Server',desc:'Load template card, render page',c:'Resp.Status(200);\nResp.Header(0);\nStorage.UsePack(2);\nStorage.EditCard(1);\nint tpl = Storage.GetField(0);\nprint(tpl);\nResp.End();',basic:"Resp.Status(200)\nResp.Header(0)\nStorage.UsePack(2)\nStorage.EditCard(1)\nDIM TPL = Storage.GetField(0)\nPRINT TPL\nResp.End()",python:'Resp.Status(200)\nResp.Header(0)\nStorage.UsePack(2)\nStorage.EditCard(1)\ntpl = Storage.GetField(0)\nprint(tpl)\nResp.End()',english:'Resp.Status(200).\nResp.Header(0).\nStorage.UsePack(2).\nStorage.EditCard(1).\nSet tpl to Storage.GetField(0).\nPrint tpl.\nResp.End().'},
    {title:'App Status Page',desc:'JSON stats with request counter',c:'Resp.Status(200);\nStorage.UsePack(3);\nStorage.EditCard(1);\nint count = Storage.GetField(0);\ncount++;\nStorage.SetField(0, count);\nprint(count);\nResp.End();',basic:"Resp.Status(200)\nStorage.UsePack(3)\nStorage.EditCard(1)\nDIM COUNT = Storage.GetField(0)\nINC COUNT\nStorage.SetField(0, COUNT)\nPRINT COUNT\nResp.End()",python:'Resp.Status(200)\nStorage.UsePack(3)\nStorage.EditCard(1)\ncount = Storage.GetField(0)\ncount += 1\nStorage.SetField(0, count)\nprint(count)\nResp.End()',english:'Resp.Status(200).\nStorage.UsePack(3).\nStorage.EditCard(1).\nSet count to Storage.GetField(0).\nIncrease count by 1.\nStorage.SetField(0, count).\nPrint count.\nResp.End().'}
  ];
  var html='';samples.forEach(function(s){var src=s[lang]||s.c;html+='<div style="margin-bottom:18px"><div style="font-weight:600;font-size:14px;margin-bottom:3px">'+esc(s.title)+'</div><div style="color:var(--muted);font-size:12px;margin-bottom:6px">'+esc(s.desc)+'</div><pre class="'+SC[lang]+'" style="max-width:700px">'+esc(src)+'</pre></div>';});
  document.getElementById('samplesContent').innerHTML=html;
}

// Init
buildGuideTree();showGuideCard(0);buildRefTree();buildNsRef();renderSyntaxRef();renderSamples();
(function(){var src=document.getElementById('doc-internals');var dst=document.getElementById('doc-internals-inline');if(src&&dst)dst.innerHTML=src.innerHTML;})();
(function(){var sel=document.getElementById('example');DATA.forEach(function(d,i){var o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+d.title;sel.appendChild(o);});})();
function loadExample(){var i=parseInt(document.getElementById('example').value,10)||0;var lang=document.getElementById('lang').value;var d=DATA[i];if(!d[lang])lang='basic';setSrc(d[lang].src);compileSrc(false);}
document.getElementById('example').onchange=loadExample;
document.getElementById('lang').addEventListener('change',function(){onLangChange();});
document.getElementById('src').addEventListener('input',filesRender);
document.getElementById('lang').value='basic';
document.getElementById('src').value=RESPONDER;
initMonaco();compileSrc(false);loadSample();renderWal();
(function(){var ls=filesSafeLocalStorage(),active='';try{active=ls?ls.getItem(PS_ACTIVE_FILE_KEY)||'':'';}catch(e){} if(active&&filesRead()[active]) psFilesOpen(active); else filesRender();})();
document.getElementById('flyoutTriggers').style.display='none';
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
