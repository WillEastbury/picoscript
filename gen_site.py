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
from picoscript_lang import encode_card_addr

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


def main():
    verify_responder()
    gallery = []
    for c in CONSTRUCTS:
        t, d, srcs = _styles(c)
        gallery.append({"title": t, "desc": d, **build_example(srcs)})
    docnav, docbody = render_docs()

    hooks_js = open(os.path.join(ROOT, "vm", "pico_hooks.js"), encoding="utf-8").read()
    vm_js = open(os.path.join(ROOT, "vm", "picovm.js"), encoding="utf-8").read()
    picoc_js = open(os.path.join(ROOT, "vm", "picoc.js"), encoding="utf-8").read()
    ser_js = open(os.path.join(ROOT, "vm", "picoserializer.js"), encoding="utf-8").read()
    store_js = open(os.path.join(ROOT, "vm", "picostore.js"), encoding="utf-8").read()

    html = PAGE
    html = html.replace("/*__HOOKS__*/", hooks_js)
    html = html.replace("/*__VM__*/", vm_js)
    html = html.replace("/*__PICOC__*/", picoc_js)
    html = html.replace("/*__SER__*/", ser_js)
    html = html.replace("/*__STORE__*/", store_js)
    html = html.replace("/*__DATA__*/", json.dumps(gallery))
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
<title>PicoScript &mdash; Guide, Playground &amp; HTTP Simulator</title>
<style>
  :root { --accent:#667eea; --bg:#0f1117; --panel:#1a1d27; --panel2:#232734;
          --text:#e6e8ef; --muted:#9aa0ad; --c:#7ee787; --b:#79c0ff; --py:#ffd866; --en:#f0a3ff; --warn:#ffd866; --err:#ff7b72; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--text); }
  a { color:var(--b); }
  header { padding:18px 26px; background:linear-gradient(120deg,#1a1d27,#232734); border-bottom:1px solid #2c313f; }
  header h1 { margin:0; font-size:22px; color:var(--accent); }
  header p { margin:5px 0 0; color:var(--muted); font-size:12.5px; }
  .tabs { display:flex; gap:4px; padding:0 20px; background:#11141c; border-bottom:1px solid #2c313f; position:sticky; top:0; z-index:20; flex-wrap:wrap; }
  .tab { padding:13px 18px; background:none; border:none; color:var(--muted); font-weight:600; cursor:pointer; border-bottom:3px solid transparent; font-size:13px; }
  .tab.active { color:var(--accent); border-bottom-color:var(--accent); }
  .view { display:none; padding:20px 24px; max-width:1500px; margin:0 auto; }
  .view.active { display:block; }
  h2.section { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); border-bottom:1px solid #2c313f; padding-bottom:8px; }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; background:#2c313f; color:var(--muted); }
  button.act { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:7px 14px; font-weight:600; cursor:pointer; font-size:12.5px; }
  button.ghost { background:#2c313f; color:var(--text); border:none; border-radius:6px; padding:7px 14px; font-weight:600; cursor:pointer; font-size:12.5px; }
  button.act:hover, button.ghost:hover { filter:brightness(1.12); }
  select, textarea, input { background:#0c0e14; color:var(--text); border:1px solid #2c313f; border-radius:6px; padding:7px; font-family:inherit; font-size:12px; }
  textarea { font-family:"SF Mono",Consolas,monospace; width:100%; resize:vertical; }
  .controls { display:flex; gap:8px; margin:10px 0; flex-wrap:wrap; align-items:center; }
  /* gallery */
  .gal { columns:2; column-gap:18px; }
  @media (max-width:1000px){ .gal{ columns:1; } }
  .card { background:var(--panel); border:1px solid #2c313f; border-radius:10px; margin:0 0 18px; overflow:hidden; break-inside:avoid; }
  .card h3 { margin:0; padding:11px 15px; font-size:14px; background:var(--panel2); }
  .card .desc { padding:8px 15px; color:var(--muted); font-size:12px; }
  .pair { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#2c313f; }
  .quad { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#2c313f; }
  @media (max-width:640px){ .pair{ grid-template-columns:1fr; } .quad{ grid-template-columns:1fr; } }
  .pane { background:var(--panel); } .pane .lbl { font-size:10.5px; font-weight:700; padding:5px 11px; color:#0f1117; }
  .pane.cstyle .lbl { background:var(--c); } .pane.bstyle .lbl { background:var(--b); }
  .pane.pystyle .lbl { background:var(--py); } .pane.enstyle .lbl { background:var(--en); }
  pre { margin:0; padding:11px; font-family:"SF Mono",Consolas,monospace; font-size:11.5px; line-height:1.5; white-space:pre; overflow-x:auto; }
  .cstyle pre { color:#cde9c8; } .bstyle pre { color:#cfe4ff; }
  .pystyle pre { color:#f5e6a8; } .enstyle pre { color:#f3d4ff; }
  .runbar { display:flex; align-items:center; gap:10px; padding:9px 15px; background:var(--panel2); border-top:1px solid #2c313f; }
  .out { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:var(--warn); }
  /* debugger / editor */
  .grid3 { display:grid; grid-template-columns:360px 1fr 250px; gap:16px; }
  @media (max-width:1100px){ .grid3{ grid-template-columns:1fr; } }
  .listing { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; max-height:260px; overflow:auto; font-family:"SF Mono",Consolas,monospace; font-size:12px; }
  .listing .row { padding:2px 10px; white-space:pre; color:var(--muted); } .listing .row.pc { background:#2d3550; color:#fff; }
  .regs { display:grid; grid-template-columns:repeat(2,1fr); gap:3px 10px; font-family:"SF Mono",monospace; font-size:12px; }
  .regs .r { color:var(--muted); } .regs .r b { color:var(--text); }
  .state { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:var(--muted); margin-top:8px; }
  .cerr { font-family:monospace; font-size:11.5px; min-height:14px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  .wal td, .wal th { border:1px solid #2c313f; padding:3px 8px; text-align:left; font-family:"SF Mono",monospace; }
  .wal th { background:var(--panel2); color:var(--muted); }
  /* http sim */
  .respbox { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; padding:12px; font-family:"SF Mono",Consolas,monospace; font-size:12.5px; white-space:pre-wrap; min-height:120px; color:var(--c); }
  .desc-table td { border:1px solid #2c313f; padding:4px 9px; font-family:"SF Mono",monospace; font-size:12px; }
  /* docs */
  .docnav { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }
  .docnav-btn { background:#2c313f; color:var(--muted); border:none; border-radius:6px; padding:7px 12px; font-weight:600; cursor:pointer; font-size:12.5px; }
  .docnav-btn.active { background:var(--accent); color:#fff; }
  .docpanel { display:none; background:var(--panel); border:1px solid #2c313f; border-radius:10px; padding:6px 26px 26px; }
  .docpanel.active { display:block; }
  .docpanel h1,.docpanel h2,.docpanel h3 { color:var(--accent); }
  .docpanel h1 { font-size:24px; border-bottom:1px solid #2c313f; padding-bottom:8px; }
  .docpanel h2 { font-size:18px; margin-top:26px; }
  .docpanel code { background:#0c0e14; padding:1px 5px; border-radius:4px; font-size:12px; }
  .docpanel pre { background:#0c0e14; border:1px solid #2c313f; border-radius:6px; padding:12px; }
  .docpanel pre code { background:none; padding:0; }
  .docpanel table { margin:14px 0; } .docpanel td,.docpanel th { border:1px solid #2c313f; padding:6px 10px; }
  .docpanel th { background:var(--panel2); }
  ::-webkit-scrollbar { width:9px; height:9px; } ::-webkit-scrollbar-thumb { background:#3a4150; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>PicoScript</h1>
  <p>One language, four surface styles, many targets &mdash; compiled, run and debugged entirely in your browser.
     <span class="pill">C-style &#123;&#125;</span> <span class="pill">BASIC block</span>
     <span class="pill">Python block</span> <span class="pill">English prose</span>
     <span class="pill">PicoWAL via localStorage</span></p>
</header>

<div class="tabs">
  <button class="tab active" data-view="guide" onclick="showView('guide')">Language Guide</button>
  <button class="tab" data-view="play" onclick="showView('play')">Playground &amp; Debugger</button>
  <button class="tab" data-view="http" onclick="showView('http')">HTTP / TCP Simulator</button>
  <button class="tab" data-view="data" onclick="showView('data')">Cards, Query &amp; Spans</button>
  <button class="tab" data-view="docs" onclick="showView('docs')">Reference Docs</button>
</div>

<!-- ===================== GUIDE ===================== -->
<div class="view active" id="view-guide">
  <p style="color:var(--muted);font-size:13px;max-width:900px">Every construct in both surface styles, side by side. Both
     are case-insensitive for keywords and variable names and compile to the same 32-bit bytecode.
     Hit <b>Run both</b> to execute each pair on the in-browser VM, or <b>Edit in playground</b> to step through it.</p>
  <div class="gal" id="gallery"></div>
</div>

<!-- ===================== PLAYGROUND ===================== -->
<div class="view" id="view-play">
  <h2 class="section">Compile, run &amp; single-step in the browser</h2>
  <div class="grid3">
    <div>
      <div class="controls">
        <select id="example" title="prebuilt example"></select>
        <button class="ghost" onclick="loadExample('c')">Load as C</button>
        <button class="ghost" onclick="loadExample('basic')">Load as BASIC</button>
        <button class="ghost" onclick="loadLangSample('python')">Python sample</button>
        <button class="ghost" onclick="loadLangSample('english')">English sample</button>
      </div>
      <select id="lang" style="margin-bottom:6px" onchange="onLangChange()"><option value="c">C-style &#123; &#125;</option><option value="basic">BASIC block</option><option value="python">Python block</option><option value="english">English prose</option></select>
      <div id="monaco" style="height:260px;border:1px solid #2c313f;border-radius:6px;overflow:hidden"></div>
      <textarea id="src" style="height:160px;display:none" spellcheck="false"></textarea>
      <div class="controls">
        <button class="act" onclick="compileSrc(true)">Compile &amp; Run &#9654;</button>
        <button class="ghost" onclick="compileSrc(false)">Compile &amp; Step</button>
        <button class="ghost" onclick="dbgStep()">Step</button>
        <button class="ghost" onclick="dbgReset()">Reset</button>
      </div>
      <label style="font-size:11.5px;color:var(--muted)"><input type="checkbox" id="walpersist"> persist cards to PicoWAL (localStorage)</label>
      <div id="cerr" class="cerr"></div>
    </div>
    <div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">disassembly (current PC highlighted):</div>
      <div class="listing" id="listing"></div>
      <div class="state" id="state"></div>
      <div class="out" id="out" style="margin-top:6px"></div>
    </div>
    <div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">registers R0&ndash;R15:</div>
      <div class="regs" id="regs"></div>
      <div style="font-size:11px;color:var(--muted);margin:12px 0 4px">auto-watches (variable &rarr; register &rarr; value):</div>
      <div style="max-height:150px;overflow:auto"><table class="wal"><thead><tr><th>var</th><th>reg</th><th>value</th></tr></thead><tbody id="watches"></tbody></table></div>
      <div style="font-size:11px;color:var(--muted);margin:12px 0 4px">PicoWAL cards (localStorage):</div>
      <div style="max-height:160px;overflow:auto"><table class="wal"><tbody id="walbody"></tbody></table></div>
      <button class="ghost" style="margin-top:6px" onclick="walClear()">Clear PicoWAL</button>
    </div>
  </div>
</div>

<!-- ===================== HTTP SIMULATOR ===================== -->
<div class="view" id="view-http">
  <h2 class="section">Send a web request / TCP bytes to your program</h2>
  <p style="color:var(--muted);font-size:13px;max-width:980px">Paste an HTTP request (or raw hex bytes) and <b>Send</b> it. The
     simulator parses it into a <b>PicoWAL request descriptor</b> (cards in localStorage), then runs the program currently in
     the editor. Whatever it emits with <code>Net.Status</code>/<code>Net.Type</code>/<code>PRINT</code> is rendered back as the
     HTTP response. Click <b>Load responder example</b> for a program that reads the descriptor and replies.</p>
  <div class="grid3" style="grid-template-columns:1fr 1fr 280px">
    <div>
      <div class="controls">
        <select id="reqmode"><option value="text">HTTP / text</option><option value="hex">hex bytes</option></select>
        <button class="ghost" onclick="loadSample()">Sample request</button>
        <button class="ghost" onclick="loadResponder()">Load responder example</button>
      </div>
      <textarea id="reqbox" style="height:200px" spellcheck="false"></textarea>
      <div class="controls"><button class="act" onclick="sendRequest()">Send &#9654;</button></div>
    </div>
    <div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">response (from Net.* + PRINT output):</div>
      <div class="respbox" id="respout">(send a request to see the response)</div>
    </div>
    <div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">request descriptor &rarr; PicoWAL cards:</div>
      <table class="desc-table"><tbody>
        <tr><td>(0,0,0)</td><td>request length</td></tr>
        <tr><td>(0,0,1)</td><td>method (GET1 POST2 PUT3 DEL4)</td></tr>
        <tr><td>(0,0,2)</td><td>body length</td></tr>
        <tr><td>(0,0,3)</td><td>byte checksum</td></tr>
        <tr><td>(0,0,4)</td><td>path length</td></tr>
        <tr><td>(0,8,0)+</td><td>raw request bytes</td></tr>
      </tbody></table>
      <div style="font-size:11px;color:var(--muted);margin:12px 0 4px">live PicoWAL store:</div>
      <div style="max-height:200px;overflow:auto"><table class="wal"><tbody id="walbody2"></tbody></table></div>
      <button class="ghost" style="margin-top:6px" onclick="walClear()">Clear PicoWAL</button>
    </div>
  </div>
</div>

<!-- ===================== DATA / CARDS ===================== -->
<div class="view" id="view-data">
  <h2 class="section">Cards, query language &amp; spans</h2>
  <p style="color:var(--muted);font-size:13px;max-width:1000px">Cards are records serialized with the
     <b>PicoBinarySerializer</b> (byte-identical to the Python engine) and stored in packs over
     <b>localStorage</b>. Create / fetch / update / delete cards, run the <b>query language</b>, and inspect the
     raw binary. The <b>span</b> primitives (<code>Span.Make/Slice/Materialize</code>) run in the Playground &mdash;
     slice is a zero-copy view, materialize memcpys to a new contiguous region.</p>
  <div class="grid3" style="grid-template-columns:1fr 1fr 1fr">
    <div>
      <div style="font-size:12px;font-weight:600;margin-bottom:4px">Create a card (JSON record)</div>
      <input id="packname" value="orders" style="width:100%;margin-bottom:6px" title="pack name">
      <textarea id="cardjson" style="height:90px" spellcheck="false">{"qty": 42, "sku": "ABC", "status": 1}</textarea>
      <div class="controls">
        <button class="act" onclick="cardCreate()">Create &#9654;</button>
        <button class="ghost" onclick="cardSeed()">Seed samples</button>
        <button class="ghost" onclick="cardClear()">Clear pack</button>
      </div>
      <div id="cardmsg" class="cerr"></div>
      <div style="font-size:12px;font-weight:600;margin:8px 0 4px">PicoBinarySerializer bytes</div>
      <div class="respbox" id="serout" style="min-height:50px;word-break:break-all">&hellip;</div>
    </div>
    <div>
      <div style="font-size:12px;font-weight:600;margin-bottom:4px">Query</div>
      <input id="querybox" value="qty > 40 AND status = 1" style="width:100%;margin-bottom:6px">
      <div class="controls"><button class="act" onclick="cardQuery()">Run query &#9654;</button>
        <span style="font-size:11px;color:var(--muted)">= == != &lt;&gt; &lt; &gt; &lt;= &gt;= ~ &nbsp; AND OR</span></div>
      <div style="max-height:300px;overflow:auto"><table class="wal"><thead><tr><th>id</th><th>record</th></tr></thead><tbody id="qresults"></tbody></table></div>
    </div>
    <div>
      <div style="font-size:12px;font-weight:600;margin-bottom:4px">All cards in pack</div>
      <div style="max-height:360px;overflow:auto"><table class="wal"><thead><tr><th>id</th><th>record</th><th></th></tr></thead><tbody id="cardlist"></tbody></table></div>
    </div>
  </div>
</div>

<!-- ===================== DOCS ===================== -->
<div class="view" id="view-docs">
  <div class="docnav"><!--__DOCNAV__--></div>
  <!--__DOCBODY__-->
</div>

<script>/*__HOOKS__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
<script>/*__SER__*/</script>
<script>/*__STORE__*/</script>
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js"></script>
<script>
const DATA = /*__DATA__*/;
const RESPONDER = /*__RESPONDER__*/;
const WAL_PREFIX = 'picowal:';

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ---- tab + doc nav --------------------------------------------------------
function showView(v){
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.getElementById('view-'+v).classList.add('active');
  document.querySelector('.tab[data-view="'+v+'"]').classList.add('active');
  if(v==='data'){ try{ cardRender(); }catch(e){} }
}
function showDoc(k){
  document.querySelectorAll('.docpanel').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.docnav-btn').forEach(e=>e.classList.remove('active'));
  document.getElementById('doc-'+k).classList.add('active');
  document.querySelector('.docnav-btn[data-doc="'+k+'"]').classList.add('active');
}

// ---- Cards, query & serializer (PicoStore over localStorage) --------------
var CARD_PREFIX = "picocard:";
function cardBackend(){
  return {
    get:function(k){ return localStorage.getItem(CARD_PREFIX+k); },
    set:function(k,v){ localStorage.setItem(CARD_PREFIX+k, v); },
    remove:function(k){ localStorage.removeItem(CARD_PREFIX+k); },
    keys:function(){ var ks=[]; for(var i=0;i<localStorage.length;i++){ var k=localStorage.key(i); if(k&&k.indexOf(CARD_PREFIX)===0) ks.push(k.slice(CARD_PREFIX.length)); } return ks; }
  };
}
var STORE = new PicoStore.PicoStore(cardBackend());
function curPack(){ return (document.getElementById('packname').value||'orders').trim(); }
function cardMsg(m,err){ var e=document.getElementById('cardmsg'); e.textContent=m||''; e.style.color=err?'var(--err)':'var(--muted)'; }
function rowsHtml(entries, withDel){
  if(!entries.length) return '<tr><td colspan="'+(withDel?3:2)+'" style="color:var(--muted)">(none)</td></tr>';
  return entries.map(function(e){
    var id=e[0], rec=e[1];
    var del = withDel ? '<td><button class="ghost" style="padding:1px 7px" onclick="cardDelete('+id+')">&times;</button></td>' : '';
    return '<tr><td>'+id+'</td><td>'+esc(JSON.stringify(rec))+'</td>'+del+'</tr>';
  }).join('');
}
function cardRender(){
  var pack=curPack();
  document.getElementById('cardlist').innerHTML = rowsHtml(STORE.all(pack), true);
}
function cardCreate(){
  var pack=curPack(), rec;
  try { rec = JSON.parse(document.getElementById('cardjson').value); }
  catch(e){ cardMsg('JSON parse error: '+e.message, true); return; }
  try {
    var id = STORE.create(pack, rec);
    var hex = STORE.cardBytesHex(pack, id);
    document.getElementById('serout').textContent = hex + '  ('+(hex.length/2)+' bytes)';
    cardMsg('Created card #'+id+' in "'+pack+'"', false);
    cardRender();
  } catch(e){ cardMsg('serialize error: '+e.message, true); }
}
function cardSeed(){
  var pack=curPack();
  [{qty:42,sku:"ABC",status:1},{qty:7,sku:"XYZ",status:0},{qty:99,sku:"ABC",status:1},{qty:55,sku:"QRS",status:2}]
    .forEach(function(r){ STORE.create(pack, r); });
  cardMsg('Seeded 4 sample cards', false); cardRender();
}
function cardClear(){
  var pack=curPack();
  STORE.all(pack).forEach(function(e){ STORE.delete(pack, e[0]); });
  STORE.b.remove(pack+":ids"); STORE.b.remove(pack+":next");
  document.getElementById('qresults').innerHTML=''; cardMsg('Cleared pack "'+pack+'"', false); cardRender();
}
function cardDelete(id){ STORE.delete(curPack(), id); cardRender(); }
function cardQuery(){
  var pack=curPack(), q=document.getElementById('querybox').value;
  try {
    var res = STORE.query(pack, q);
    document.getElementById('qresults').innerHTML = rowsHtml(res, false);
    cardMsg(res.length+' match'+(res.length===1?'':'es'), false);
  } catch(e){ cardMsg('query error: '+e.message, true); }
}

// ---- PicoWAL (localStorage card store) ------------------------------------
function walBackend(){
  return {
    get:function(a){ var v=localStorage.getItem(WAL_PREFIX+a); return v===null?0:(parseInt(v,10)|0); },
    set:function(a,val){ try{ localStorage.setItem(WAL_PREFIX+a, String(val|0)); }catch(e){} }
  };
}
function walEntries(){
  var out=[];
  for(var i=0;i<localStorage.length;i++){ var k=localStorage.key(i);
    if(k && k.indexOf(WAL_PREFIX)===0) out.push([parseInt(k.slice(WAL_PREFIX.length),10), parseInt(localStorage.getItem(k),10)]); }
  out.sort(function(a,b){return a[0]-b[0];}); return out;
}
function walClear(){ var ks=[]; for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i); if(k&&k.indexOf(WAL_PREFIX)===0)ks.push(k);} ks.forEach(function(k){localStorage.removeItem(k);}); renderWal(); }
function renderWal(){
  var labels={0:'request length',1:'method code',2:'body length',3:'checksum',4:'path length'};
  var rows = walEntries().map(function(e){
    var lbl = labels[e[0]]!==undefined?labels[e[0]]:(e[0]>=256?('byte['+(e[0]-256)+']'):'');
    return '<tr><td>'+e[0]+'</td><td>'+e[1]+'</td><td style="color:#9aa0ad">'+lbl+'</td></tr>';
  }).join('');
  var html = rows || '<tr><td colspan="3" style="color:#9aa0ad">(empty)</td></tr>';
  ['walbody','walbody2'].forEach(function(id){ var el=document.getElementById(id); if(el) el.innerHTML=html; });
}

// ---- gallery --------------------------------------------------------------
function runWords(hex){ var vm=new PicoVM(); vm.run(hex.map(function(h){return parseInt(h,16)>>>0;})); return vm; }
function buildGallery(){
  var STYLES = [['c','C { }','cstyle'],['basic','BASIC','bstyle'],['python','PYTHON','pystyle'],['english','ENGLISH','enstyle']];
  document.getElementById('gallery').innerHTML = DATA.map(function(d,i){
    var present = STYLES.filter(function(s){return d[s[0]];});
    var panes = present.map(function(s){
      return '<div class="pane '+s[2]+'"><div class="lbl">'+s[1]+'</div><pre>'+esc(d[s[0]].src)+'</pre></div>';
    }).join('');
    var edit = present.map(function(s){
      return '<button class="ghost" onclick="editIn('+i+',\''+s[0]+'\')">Edit '+s[1].split(' ')[0]+'</button>';
    }).join('');
    return '<div class="card"><h3>'+(i+1)+'. '+esc(d.title)+'</h3>'+
      '<div class="desc">'+esc(d.desc)+'</div>'+
      '<div class="quad">'+panes+'</div>'+
      '<div class="runbar"><button class="act" onclick="runCard('+i+')">Run &#9654;</button>'+
      '<span class="out" id="cardout'+i+'">output &rarr; &hellip;</span>'+
      '<span style="margin-left:auto"></span>'+edit+'</div></div>';
  }).join('');
}
function runCard(i){
  var d=DATA[i], STYLES=['c','basic','python','english'], parts=[], ref=null, same=true;
  STYLES.forEach(function(s){ if(!d[s]) return; var o=runWords(d[s].words).outputInts();
    if(ref===null) ref=JSON.stringify(o); else if(JSON.stringify(o)!==ref) same=false;
    parts.push(s+' &rarr; ['+o.join(', ')+']'); });
  document.getElementById('cardout'+i).innerHTML=parts.join(' &nbsp; ')+' '+(same?'&#10003;':'&#9888;');
}
function editIn(i,style){
  document.getElementById('lang').value=style; onLangChange();
  setSrc(DATA[i][style].src);
  showView('play'); compileSrc(false);
}

// ---- debugger -------------------------------------------------------------
var DBG = { words:[], disasm:[], vm:null, vars:{} };

// ---- Monaco editor (with graceful textarea fallback) ----------------------
var EDITOR = null;
function getSrc(){ return EDITOR ? EDITOR.getValue() : document.getElementById('src').value; }
function setSrc(v){ document.getElementById('src').value = v; if (EDITOR) EDITOR.setValue(v); }
function monacoLangId(lang){ return { c:'picoc', basic:'picobasic', python:'picopython', english:'picoenglish' }[lang] || 'picoc'; }
function onLangChange(){ if (EDITOR) monaco.editor.setModelLanguage(EDITOR.getModel(), monacoLangId(document.getElementById('lang').value)); }
function hookNames(){ var H=(typeof PV_HOOKS!=='undefined'&&PV_HOOKS.BY_CODE)?PV_HOOKS.BY_CODE:{}; var out=[]; for(var k in H) out.push(H[k]); return out; }
var MONARCH = {
  picoc: { keywords:['int','var','void','if','else','while','for','return','break','continue','print'],
    tokenizer:{ root:[
      [/\/\/.*$/,'comment'],[/\/\*/,'comment','@block'],
      [/[A-Za-z_]\w*(?=\s*\.)/,'type'],
      [/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],
      [/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],
      [/[{}()\[\];,.]/,'delimiter'],[/[+\-*/%=<>!&|?:]+/,'operator'] ],
      block:[[/\*\//,'comment','@pop'],[/./,'comment']],
      str:[[/[^"]+/,'string'],[/"/,'string','@pop']] } },
  picobasic: { ignoreCase:true, keywords:['DIM','LET','IF','THEN','ELSEIF','ELSE','ENDIF','WHILE','ENDWHILE','FOR','TO','STEP','NEXT','FOREACH','IN','ENDFOREACH','SWITCH','CASE','DEFAULT','ENDSWITCH','GOTO','GOSUB','SUB','ENDSUB','RETURN','PRINT','AND','OR','NOT','DO','LOOP','UNTIL','BREAK','SKIP','INC','DEC','IIF','EQ','NE','LT','GT','LE','GE','MOD'],
    tokenizer:{ root:[
      [/'.*$/,'comment'],[/\/\/.*$/,'comment'],
      [/[A-Za-z_]\w*(?=\s*\.)/,'type'],
      [/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],
      [/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],
      [/[()\[\];,.:]/,'delimiter'],[/[+\-*/=<>]+/,'operator'] ],
      str:[[/[^"]+/,'string'],[/"/,'string','@pop']] } },
  picopython: { keywords:['if','elif','else','while','for','in','range','def','return','break','continue','pass','and','or','not','print','True','False'],
    tokenizer:{ root:[
      [/#.*$/,'comment'],
      [/[A-Za-z_]\w*(?=\s*\.)/,'type'],
      [/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],
      [/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],[/'/,'string','@str2'],
      [/[()\[\]:,.]/,'delimiter'],[/[+\-*/%=<>!]+/,'operator'] ],
      str:[[/[^"]+/,'string'],[/"/,'string','@pop']],
      str2:[[/[^']+/,'string'],[/'/,'string','@pop']] } },
  picoenglish: { ignoreCase:true, keywords:['set','let','to','be','add','subtract','from','increase','decrease','multiply','divide','by','print','show','display','if','otherwise','while','repeat','as','long','for','each','times','with','define','do','call','return','stop','break','skip','continue','is','greater','less','than','at','least','most','equal','equals','exceeds','plus','minus','modulo','mod','over','and','or','not','true','false'],
    tokenizer:{ root:[
      [/#.*$/,'comment'],
      [/[A-Za-z_]\w*(?=\s*\.\s*[A-Za-z_]\w*\s*\()/,'type'],
      [/[A-Za-z_]\w*/,{cases:{'@keywords':'keyword','@default':'identifier'}}],
      [/0[xX][0-9a-fA-F]+|\d+/,'number'],[/"/,'string','@str'],
      [/[()\[\],.:]/,'delimiter'],[/[+\-*/%=<>]+/,'operator'] ],
      str:[[/[^"]+/,'string'],[/"/,'string','@pop']] } }
};
function registerLang(id){
  monaco.languages.register({ id:id });
  monaco.languages.setMonarchTokensProvider(id, MONARCH[id]);
  monaco.languages.registerCompletionItemProvider(id, { provideCompletionItems:function(model,pos){
    var kw = MONARCH[id].keywords || [];
    var sug = kw.map(function(k){ return { label:k, kind:monaco.languages.CompletionItemKind.Keyword, insertText:k }; });
    hookNames().forEach(function(name){ sug.push({ label:name, kind:monaco.languages.CompletionItemKind.Function, insertText:name+'(' }); });
    return { suggestions:sug };
  }});
}
function initMonaco(){
  if (typeof require === 'undefined' || !require.config){ document.getElementById('monaco').style.display='none'; document.getElementById('src').style.display='block'; return; }
  try {
    require.config({ paths:{ vs:'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' }});
    require(['vs/editor/editor.main'], function(){
      ['picoc','picobasic','picopython','picoenglish'].forEach(registerLang);
      EDITOR = monaco.editor.create(document.getElementById('monaco'), {
        value: document.getElementById('src').value,
        language: monacoLangId(document.getElementById('lang').value),
        theme:'vs-dark', minimap:{enabled:false}, fontSize:13, lineNumbers:'on',
        scrollBeyondLastLine:false, automaticLayout:true, tabSize:4, insertSpaces:true
      });
      EDITOR.onDidChangeModelContent(function(){ document.getElementById('src').value = EDITOR.getValue(); });
    }, function(){ document.getElementById('monaco').style.display='none'; document.getElementById('src').style.display='block'; });
  } catch(e){ document.getElementById('monaco').style.display='none'; document.getElementById('src').style.display='block'; }
}

function compileSrc(run){
  var lang=document.getElementById('lang').value, src=getSrc(), err=document.getElementById('cerr');
  try {
    var r=PicoCompile.compileDebug(src,lang);
    DBG.words=r.words.map(function(w){return w>>>0;}); DBG.disasm=DBG.words.map(jsDisasm); DBG.vars=r.vars||{};
    err.textContent='compiled '+DBG.words.length+' words'; err.style.color='#7ee787';
    dbgReset(); if(run) dbgRun();
  } catch(e){ err.textContent=String(e.message||e); err.style.color='#ff7b72'; }
}
function dbgReset(){
  var persist=document.getElementById('walpersist') && document.getElementById('walpersist').checked;
  DBG.vm=new PicoVM(persist?{cards:walBackend()}:{}); DBG.vm.load(DBG.words); render(); renderWal();
}
function dbgStep(){ if(DBG.vm){ DBG.vm.step(); render(); renderWal(); } }
function dbgRun(){ if(!DBG.vm) dbgReset(); var g=0; while(DBG.vm.step() && g++<200000){} render(); renderWal(); }
function render(){
  var vm=DBG.vm; if(!vm) return;
  document.getElementById('listing').innerHTML=DBG.disasm.map(function(t,idx){
    return '<div class="row'+(idx===vm.pc?' pc':'')+'">'+String(idx).padStart(3,' ')+'  '+esc(t)+'</div>'; }).join('');
  var pcrow=document.querySelector('#listing .row.pc'); if(pcrow) pcrow.scrollIntoView({block:'nearest'});
  document.getElementById('regs').innerHTML=Array.from(vm.regs).map(function(v,idx){return '<div class="r">R'+idx+' <b>'+v+'</b></div>';}).join('');
  var wb=document.getElementById('watches');
  if(wb){ var vars=DBG.vars||{}, ks=Object.keys(vars);
    wb.innerHTML = ks.length ? ks.map(function(name){ var rr=vars[name]; return '<tr><td>'+esc(name)+'</td><td>R'+rr+'</td><td><b>'+vm.regs[rr]+'</b></td></tr>'; }).join('')
                             : '<tr><td colspan="3" style="color:var(--muted)">(no named variables)</td></tr>';
  }
  document.getElementById('state').textContent='pc='+vm.pc+'  steps='+vm.steps+'  halted='+vm.halted+'  http_status='+vm.httpStatus;
  var _txt=vm.outputText(), _pr=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_txt);
  document.getElementById('out').textContent='output: ['+vm.outputInts().join(', ')+']'+(_pr&&_txt?'\ntext: '+JSON.stringify(_txt):'');
}
function jsDisasm(w){
  var names=["NOOP","LOAD","SAVE","PIPE","ADD","SUB","MUL","DIV","INC","JUMP","BRANCH","CALL","RETURN","WAIT","RAISE","DSP"];
  var br=["EQ","NE","LT","GT","LE","GE","Z","NZ","EOF","ERR"];
  var op=(w>>>28)&0xF, rd=(w>>>24)&0xF, rs1=(w>>>20)&0xF, rs2=(w>>>16)&0xF, imm=w&0xFFFF;
  if(op===0&&(imm&0xFF00)===0x7000) return "HOSTCALL #0x"+(imm&0xFF).toString(16);
  if(op===0&&(imm&0xF000)===0x8000) return "NET.STATUS "+(imm&0xFFF);
  if(op===0&&(imm&0xF000)===0xA000) return "NET.TYPE #0x"+imm.toString(16);
  if(op===0&&imm===0xC000) return "NET.CLOSE";
  if(op>=4&&op<=7) return names[op]+" R"+rd+", R"+rs1+(rs2===1?(", R"+(imm&0xF)):(", #"+imm));
  if(op===8) return "INC R"+rd;
  if(op===9) return "JUMP "+imm;
  if(op===10){ var off=imm&0x8000?imm-0x10000:imm; return "BRANCH "+(br[rs2]||rs2)+" R"+rd+", R"+rs1+", "+(off>=0?"+":"")+off; }
  if(op===11) return "CALL "+imm;
  if(op>=1&&op<=3) return names[op]+" R"+(op===1?rd:rs1)+", [0x"+imm.toString(16)+"]";
  if(op===12) return "RETURN";
  return names[op]||("?"+op);
}

// ---- HTTP / TCP simulator --------------------------------------------------
function methodCode(m){ return ({GET:1,POST:2,PUT:3,DELETE:4,HEAD:5,PATCH:6,OPTIONS:7})[(m||'').toUpperCase()]||0; }
function parseRequest(text, isHex){
  var bytes=[];
  if(isHex){
    var hs=text.replace(/0x/gi,'').replace(/[,]/g,' ').trim().split(/\s+/).filter(Boolean);
    hs.forEach(function(h){ if(h.length>2){ for(var i=0;i+1<h.length;i+=2) bytes.push(parseInt(h.substr(i,2),16)&0xFF); } else if(h.length){ bytes.push(parseInt(h,16)&0xFF); } });
  } else { for(var i=0;i<text.length;i++) bytes.push(text.charCodeAt(i)&0xFF); }
  var sum=0; bytes.forEach(function(b){ sum=(sum+b)|0; });
  var method=0,pathLen=0,bodyLen=0;
  if(!isHex){
    var fl=(text.split(/\r?\n/)[0]||'').split(/\s+/);
    if(fl.length>=2){ method=methodCode(fl[0]); pathLen=fl[1].length; }
    var idx=text.indexOf('\r\n\r\n'); var sep=4; if(idx<0){ idx=text.indexOf('\n\n'); sep=2; }
    if(idx>=0){ bodyLen=text.length-(idx+sep); if(bodyLen<0) bodyLen=0; }
  }
  return { bytes:bytes, length:bytes.length, method:method, bodyLen:bodyLen, sum:sum, pathLen:pathLen };
}
function writeDescriptor(wal, req){
  wal.set(0,req.length); wal.set(1,req.method); wal.set(2,req.bodyLen); wal.set(3,req.sum); wal.set(4,req.pathLen);
  var n=Math.min(req.bytes.length,256); for(var i=0;i<n;i++) wal.set(256+i, req.bytes[i]);
}
function sendRequest(){
  var text=document.getElementById('reqbox').value, isHex=document.getElementById('reqmode').value==='hex';
  var req=parseRequest(text,isHex), wal=walBackend(); writeDescriptor(wal,req);
  var lang=document.getElementById('lang').value, src=getSrc(), r;
  try { r=PicoCompile.compile(src,lang); }
  catch(e){ document.getElementById('respout').textContent='compile error: '+(e.message||e); document.getElementById('respout').style.color='#ff7b72'; return; }
  var vm=new PicoVM({cards:wal}); vm.run(r.words);
  renderResponse(vm,req); renderWal();
}
function renderResponse(vm,req){
  var reasons={200:'OK',201:'Created',202:'Accepted',204:'No Content',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',500:'Internal Server Error'};
  var el=document.getElementById('respout'); el.style.color='#7ee787';
  var body=vm.outputInts();
  if(vm.httpStatus<0){ el.style.color='#ffd866'; el.textContent='(program ran '+vm.steps+' steps but did not call Net.Status)\noutput: ['+body.join(', ')+']'; return; }
  var L=[];
  L.push('HTTP/1.1 '+vm.httpStatus+' '+(reasons[vm.httpStatus]||''));
  L.push('Content-Type: '+(vm.httpType||'application/octet-stream'));
  L.push('X-PicoScript-Steps: '+vm.steps);
  L.push('X-Request-Bytes: '+req.length);
  var _bt=vm.outputText(), _bp=/^[\x09\x0a\x0d\x20-\x7e\u00a0-\uffff]*$/.test(_bt);
  L.push(''); L.push(_bp&&_bt ? _bt : JSON.stringify(body));
  el.textContent=L.join('\n');
}
function loadResponder(){ document.getElementById('lang').value='basic'; onLangChange(); setSrc(RESPONDER); compileSrc(false); }
function loadSample(){
  document.getElementById('reqmode').value='text';
  document.getElementById('reqbox').value='POST /orders HTTP/1.1\r\nHost: pico.dev\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"qty": 42}';
}

// ---- init -----------------------------------------------------------------
buildGallery();
(function () {
  var sel = document.getElementById('example');
  DATA.forEach(function (d, i) { var o = document.createElement('option'); o.value = i; o.textContent = (i + 1) + '. ' + d.title; sel.appendChild(o); });
})();
function loadExample(style) {
  var i = parseInt(document.getElementById('example').value, 10) || 0;
  document.getElementById('lang').value = style;
  onLangChange();
  setSrc(DATA[i][style].src);
  compileSrc(false);
}
var PY_SAMPLE = "# Python-style: indentation blocks, = assignment\n" +
  "total = 0\n" +
  "for i in range(1, 11):\n" +
  "    total += i\n" +
  "n = 5\n" +
  "fact = 1\n" +
  "while n > 1:\n" +
  "    fact = fact * n\n" +
  "    n -= 1\n" +
  "if total > 50:\n" +
  "    print(fact)\n" +
  "else:\n" +
  "    print(0)\n" +
  "print(total)\n";
var EN_SAMPLE = "# English prose: the same logic, in plain sentences\n" +
  "Set total to 0.\n" +
  "For each i from 1 to 10:\n" +
  "    Increase total by i.\n" +
  "Set n to 5.\n" +
  "Set fact to 1.\n" +
  "While n is greater than 1:\n" +
  "    Multiply fact by n.\n" +
  "    Decrease n by 1.\n" +
  "If total is greater than 50:\n" +
  "    Print fact.\n" +
  "Otherwise:\n" +
  "    Print 0.\n" +
  "Print total.\n";
function loadLangSample(lang) {
  document.getElementById('lang').value = lang;
  onLangChange();
  setSrc((lang === 'python') ? PY_SAMPLE : EN_SAMPLE);
  compileSrc(false);
}
document.getElementById('lang').value='basic';
document.getElementById('src').value=RESPONDER;
initMonaco();
compileSrc(false);
loadResponder();
loadSample();
renderWal();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
