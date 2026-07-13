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

    html = PAGE
    html = html.replace("/*__HOOKS__*/", hooks_js)
    html = html.replace("/*__PCZ__*/", pcz_js)
    html = html.replace("/*__PBZ__*/", pbz_js)
    html = html.replace("/*__VM__*/", vm_js)
    html = html.replace("/*__PICOC__*/", picoc_js)
    html = html.replace("/*__WF__*/", wf_js)
    html = html.replace("/*__LAYOUT__*/", layout_js)
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
  .lang-toggle button[data-lang="functional"].active { background:var(--fn); color:#0f1117; }
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
    <button data-lang="functional" onclick="setLang('functional')">Functional</button>
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
              <b>PicoScript</b> compiles from seven interchangeable surface syntaxes (C, BASIC, Python, English, COBOL, Report, Functional)
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
          </div>
          <div class="file-list" id="fileList"></div>
          <div class="file-status" id="fileStatus"></div>
        </div>
      </div>
      <div class="ide-editor">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <select id="example" style="width:auto"></select>
          <input type="hidden" id="lang" value="basic">
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
        <div id="wfDesigner" class="wf-designer" style="display:none"></div>
      </div>
    </div>
    <div class="dbg-bar" id="playDbgBar">
      <button class="active" onclick="pToggleDbg(this,'pdbg-debug')">Debug</button>
      <button class="dbg-pin" data-pin="pdbg-debug" onclick="togglePinnedPanel('play','pdbg-debug')" title="Pin Debug">📌</button>
      <button onclick="pToggleDbg(this,'pdbg-cards')">Cards</button>
      <button class="dbg-pin" data-pin="pdbg-cards" onclick="togglePinnedPanel('play','pdbg-cards')" title="Pin Cards">📌</button>
      <button onclick="pToggleDbg(this,'pdbg-output')">Output</button>
      <button class="dbg-pin" data-pin="pdbg-output" onclick="togglePinnedPanel('play','pdbg-output')" title="Pin Output">📌</button>
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
        <input id="packname" value="orders" style="width:100%;margin-bottom:6px" placeholder="pack name">
        <textarea id="cardjson" style="height:50px" spellcheck="false">{"qty": 42, "sku": "ABC", "status": 1}</textarea>
        <div style="display:flex;gap:6px;margin:6px 0"><button class="act" onclick="cardCreate()">Create</button><button class="ghost" onclick="cardSeed()">Seed</button><button class="ghost" onclick="cardClear()">Clear</button></div>
        <div id="cardmsg" class="cerr"></div>
        <div class="respbox" id="serout" style="min-height:24px;font-size:10px">&hellip;</div>
        <div style="flex:1;overflow:auto;margin-top:6px"><table class="wal"><tbody id="cardlist"></tbody></table></div>
      </div>
      <div class="tool-tab" id="tool-query">
        <h3>Query</h3>
        <input id="querybox" value="qty > 40 AND status = 1" style="width:100%;margin-bottom:6px">
        <button class="act" onclick="cardQuery()">Run &#9654;</button>
        <div style="flex:1;overflow:auto;margin-top:8px"><table class="wal"><thead><tr><th>id</th><th>record</th></tr></thead><tbody id="qresults"></tbody></table></div>
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
function setLang(lang){
  var oldLang=CUR_LANG;
  CUR_LANG=lang;
  document.querySelectorAll('#langToggle button').forEach(function(b){b.classList.toggle('active',b.getAttribute('data-lang')===lang);});
  document.getElementById('lang').value=lang;
  // Roundtrip editor code to new language
  if(typeof getSrc==='function'&&typeof setSrc==='function'){
    var src=getSrc();
    if(lang==='workflow'){
      // Workflow is a visual/JSON surface: seed a starter step list if needed.
      if(typeof looksLikeWorkflowJson==='function'&&!looksLikeWorkflowJson(src)) setSrc(WF_SNIPPET);
    } else if(oldLang==='workflow'){
      // Design in workflow, then view as text: workflow -> English -> target.
      if(typeof looksLikeWorkflowJson==='function'&&looksLikeWorkflowJson(src)){
        try{
          var _eng=wfCompileSrc(src).source;
          var _out=(lang==='english')?_eng:((typeof PicoCompile!=='undefined'&&PicoCompile.translate)?PicoCompile.translate(_eng,'english',lang):_eng);
          if(_out) setSrc(_out);
        }catch(e){}
      }
    } else if(src&&src.trim()&&oldLang!==lang&&typeof PicoCompile!=='undefined'&&PicoCompile.translate){
      var translated=PicoCompile.translate(src,oldLang,lang);
      if(translated)setSrc(translated);
    }
  }
  if(typeof onLangChange==='function') onLangChange();
  if(typeof wfToggle==='function') wfToggle();
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
function monacoLangId(lang){return{c:'picoc',basic:'picobasic',python:'picopython',english:'picoenglish'}[lang]||'picoc';}
function onLangChange(){if(EDITOR)monaco.editor.setModelLanguage(EDITOR.getModel(),monacoLangId(document.getElementById('lang').value));if(typeof filesRender==='function')filesRender();}
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
  if(lang==='workflow'){
    if(typeof looksLikeWorkflowJson!=='function'||!looksLikeWorkflowJson(src)){err.textContent='workflow: editor is not a JSON step array';err.style.color='#ff7b72';return;}
    try{var wf=wfCompileSrc(src);src=wf.source;lang='english';}
    catch(e){err.textContent='workflow: '+String(e.message||e);err.style.color='#ff7b72';return;}
  }
  try{var r=PicoCompile.compileDebug(src,lang);DBG.words=r.words.map(function(w){return w>>>0;});DBG.disasm=DBG.words.map(jsDisasm);DBG.vars=r.vars||{};DBG.debug=r.debug||{};DBG.src=src;rebuildDebugMaps();err.textContent='compiled '+DBG.words.length+' words';err.style.color='#7ee787';dbgReset();if(run)dbgRun();if(typeof renderLayout==='function')try{renderLayout();}catch(e){}}
  catch(e){err.textContent=String(e.message||e);err.style.color='#ff7b72';}
}
function dbgReset(){DBG.vm=new PicoVM({cards:walBackend()});DBG.vm.load(DBG.words);render();renderWal();updateSourceDecorations();}
function dbgStep(){if(DBG.vm){DBG.vm.step();render();renderWal();}}
function dbgRun(){if(!DBG.vm)dbgReset();var g=0;while(DBG.vm.step()&&g++<200000){if(pcHasBreakpoint(DBG.vm.pc))break;}render();renderWal();}
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
  return{bytes:bytes,length:bytes.length,method:method,bodyLen:bodyLen,sum:sum,pathLen:pathLen,
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
function sendRequest(){var text=document.getElementById('reqbox').value,isHex=document.getElementById('reqmode').value==='hex';var req=parseRequest(text,isHex),wal=walBackend();writeDescriptor(wal,req);var lang=document.getElementById('lang').value,src=getSrc();try{var r=PicoCompile.compile(src,lang);}catch(e){document.getElementById('respout').textContent='compile error: '+(e.message||e);return;}var vm=new PicoVM({cards:wal});vm.run(r.words);renderResponse(vm,req);renderWal();}
function sendTcp(){var text=document.getElementById('tcpbox').value;var req=parseRequest(text,true),wal=walBackend();writeDescriptor(wal,req);var lang=document.getElementById('lang').value,src=getSrc();try{var r=PicoCompile.compile(src,lang);}catch(e){document.getElementById('tcpout').textContent='compile error: '+(e.message||e);return;}var vm=new PicoVM({cards:wal});vm.run(r.words);renderTcpResponse(vm,req);renderWal();}
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
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle',cobol:'cobstyle',report:'rptstyle',functional:'fnstyle'};
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
  var lang=CUR_LANG,SC={c:'cstyle',basic:'bstyle',python:'pystyle',english:'enstyle',cobol:'cobstyle',report:'rptstyle',functional:'fnstyle'};
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
// BareMetal.WorkflowPico.compile expects a steps ARRAY (or registered name); the
// editor surface is a JSON string, so parse it here before compiling.
function wfCompileSrc(src){
  var d=JSON.parse(src);
  var steps=Array.isArray(d)?d:(d&&Array.isArray(d.steps)?d.steps:null);
  if(!steps) throw new Error('workflow: expected a JSON step array');
  return BareMetal.WorkflowPico.compile(steps);
}
var WF_TYPES=['SET','IF','ELSE','END','FOR','FOREACH','FOREACHP','LOG','WAIT','RAISE','ON','LOAD','SAVE','WEB','CALL'];
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
  ]
};
function wfTemplate(type){
  switch(type){
    case 'SET': return {type:'SET',name:'x',value:0};
    case 'IF': return {type:'IF',condition:'x >= 1'};
    case 'ELSE': return {type:'ELSE'};
    case 'END': return {type:'END'};
    case 'FOR': return {type:'FOR','var':'i',from:1,to:5};
    case 'FOREACH': return {type:'FOREACH','var':'item','in':'data'};
    case 'FOREACHP': return {type:'FOREACHP','var':'item','in':'data'};
    case 'LOG': return {type:'LOG',message:'x'};
    case 'WAIT': return {type:'WAIT',ms:100};
    case 'RAISE': return {type:'RAISE',event:1,target:0};
    case 'ON': return {type:'ON',event:1};
    case 'LOAD': return {type:'LOAD',name:'x',from:'memory',key:0};
    case 'SAVE': return {type:'SAVE',name:'x',to:'memory',key:0};
    case 'WEB': return {type:'WEB',method:'GET',url:'/api'};
    case 'CALL': return {type:'CALL',workflow:'other'};
    default: return {type:type};
  }
}
function wfSummary(s){
  var t=(s.type||'').toUpperCase();
  try{
    if(t==='SET') return s.name+' = '+(('expr' in s)?s.expr:JSON.stringify(s.value));
    if(t==='IF') return s.condition||'';
    if(t==='FOR') return s['var']+' = '+s.from+'..'+s.to+(s.step!=null?' by '+s.step:'');
    if(t==='FOREACH'||t==='FOREACHP') return s['var']+' in '+JSON.stringify(s['in']);
    if(t==='LOG') return String(s.message==null?'':s.message);
    if(t==='LOAD') return s.name+' <- '+s.from+(s.key!=null?' ['+s.key+']':'');
    if(t==='SAVE') return s.name+' -> '+s.to+(s.key!=null?' ['+s.key+']':'');
    if(t==='WAIT') return (s.ms||0)+'ms';
    if(t==='RAISE'||t==='EMIT') return 'event '+(s.event==null?'':s.event)+(s.target!=null?' -> '+s.target:'');
    if(t==='ON'||t==='SUBSCRIBE') return 'on event '+(s.event==null?'':s.event);
    if(t==='WEB') return (s.method||'GET')+' '+(s.url||'');
    if(t==='CALL') return s.workflow||'';
  }catch(e){}
  return '';
}
function wfParseSteps(){ try{ var d=JSON.parse(getSrc()); if(Array.isArray(d)) return d; if(d&&Array.isArray(d.steps)) return d.steps; }catch(e){} return null; }
function wfWriteSteps(steps){ setSrc(JSON.stringify(steps,null,2)); wfRenderDesigner(); compileSrc(false); }
function wfAddStep(){ var sel=document.getElementById('wfAddType'); if(!sel) return; var steps=wfParseSteps()||[]; steps.push(wfTemplate(sel.value)); wfWriteSteps(steps); }
function wfDelStep(i){ var steps=wfParseSteps(); if(!steps) return; steps.splice(i,1); wfWriteSteps(steps); }
function wfMove(i,d){ var steps=wfParseSteps(); if(!steps) return; var j=i+d; if(j<0||j>=steps.length) return; var tmp=steps[i]; steps[i]=steps[j]; steps[j]=tmp; wfWriteSteps(steps); }
function wfEditStep(i){ var steps=wfParseSteps(); if(!steps) return; var v=prompt('Edit step JSON',JSON.stringify(steps[i])); if(v==null) return; var parsed; try{ parsed=JSON.parse(v); }catch(e){ alert('Invalid JSON: '+e.message); return; } steps[i]=parsed; wfWriteSteps(steps); }
function wfLoadExample(){ var sel=document.getElementById('wfExample'); if(!sel||!sel.value) return; var ex=WF_EXAMPLES[sel.value]; if(!ex) return; wfWriteSteps(ex.map(function(s){return JSON.parse(JSON.stringify(s));})); }
function wfRenderDesigner(){
  var host=document.getElementById('wfDesigner'); if(!host) return;
  var add='<div class="wf-add"><select id="wfAddType">'+WF_TYPES.map(function(t){return '<option>'+t+'</option>';}).join('')+'</select>'+
    '<button class="ghost" onclick="wfAddStep()">+ Add step</button>'+
    '<select id="wfExample"><option value="">example\u2026</option>'+Object.keys(WF_EXAMPLES).map(function(n){return '<option>'+esc(n)+'</option>';}).join('')+'</select>'+
    '<button class="ghost" onclick="wfLoadExample()">Load</button>'+
    '<span class="muted" style="font-size:11px">visual designer &mdash; edits sync to the JSON above &amp; recompile</span></div>';
  var steps=wfParseSteps();
  if(!steps){ host.innerHTML=add+'<div class="muted" style="font-size:12px;padding:4px">(editor text is not a JSON step array &mdash; edit the JSON or add a step)</div>'; return; }
  var indent=0, rows='';
  for(var i=0;i<steps.length;i++){ var s=steps[i]||{}, t=(s.type||'?').toUpperCase();
    if(t==='ELSE'||t==='END') indent=Math.max(0,indent-1);
    rows+='<div class="wf-row" style="margin-left:'+(indent*18)+'px">'+
      '<span class="wf-badge">'+esc(t)+'</span>'+
      '<span class="wf-sum">'+esc(wfSummary(s))+'</span>'+
      '<span class="wf-acts">'+
        '<button class="ghost" onclick="wfMove('+i+',-1)" title="move up">&#8593;</button>'+
        '<button class="ghost" onclick="wfMove('+i+',1)" title="move down">&#8595;</button>'+
        '<button class="ghost" onclick="wfEditStep('+i+')" title="edit JSON">&#9998;</button>'+
        '<button class="ghost" onclick="wfDelStep('+i+')" title="delete">&#10005;</button>'+
      '</span></div>';
    if(t==='IF'||t==='FOR'||t==='FOREACH'||t==='FOREACHP'||t==='ON'||t==='ELSE') indent++;
  }
  var eng='';
  try{
    var wf=wfCompileSrc(getSrc());
    var warn=(wf.warnings&&wf.warnings.length)?('<div class="wf-warn">'+wf.warnings.map(function(w){return '&#9888; '+esc(w);}).join('<br>')+'</div>'):'';
    eng='<div class="wf-eng-h">derived English (compiles &amp; runs; IL / bytecode / output in the debugger below)</div>'+
        '<pre class="wf-eng">'+esc(wf.source)+'</pre>'+warn;
  }catch(e){ eng='<div class="wf-warn">&#9888; '+esc(String(e.message||e))+'</div>'; }
  host.innerHTML=add+rows+eng;
}
function wfToggle(){
  var on=(CUR_LANG==='workflow');
  var host=document.getElementById('wfDesigner');
  if(host) host.style.display=on?'block':'none';
  if(on){ try{ wfRenderDesigner(); }catch(e){} }
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
(function(){var sel=document.getElementById('example');DATA.forEach(function(d,i){var o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+d.title;sel.appendChild(o);});})();
function loadExample(){var i=parseInt(document.getElementById('example').value,10)||0;var lang=document.getElementById('lang').value;var d=DATA[i];if(!d[lang])lang='basic';setSrc(d[lang].src);compileSrc(false);}
document.getElementById('example').onchange=loadExample;
document.getElementById('lang').addEventListener('change',function(){onLangChange();});
document.getElementById('src').addEventListener('input',function(){filesRender();if(CUR_LANG==='workflow'){try{wfRenderDesigner();}catch(e){}}});
document.getElementById('lang').value='basic';
document.getElementById('src').value=RESPONDER;
initMonaco();compileSrc(false);loadSample();renderWal();renderLayout();
(function(){var ls=filesSafeLocalStorage(),active='';try{active=ls?ls.getItem(PS_ACTIVE_FILE_KEY)||'':'';}catch(e){} if(active&&filesRead()[active]) psFilesOpen(active); else filesRender();})();
document.getElementById('flyoutTriggers').style.display='none';
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
