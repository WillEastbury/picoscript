#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_showcase.py -- build docs/showcase.html: a self-contained "PicoShop" demo
that composes the PicoScript toolchain into a small app, entirely from the repo's
own inlined vm/*.js assets (no external deps, works from file:// or GitHub Pages).

Story (hash-routed tabs):
  1. Logic   -- a visual workflow compiled to PicoScript (picoworkflow -> picoc) and
                run on the VM; shows generated English + bytecode + output.
  2. Report  -- picolayout renders the workflow output as a read-only report.
  3. Form    -- the same template rendered read-write; Save collects + writes back.
  4. Activity-- a tiny in-page event bus logs compile / load / save.

pico_hooks.js, picovm.js, picoc.js and the vendored BareMetal.WorkflowPico.js /
BareMetal.Report.js (from BareMetalJsTools) are inlined
(same pattern as gen_playground.py / gen_site.py).
"""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
VM = os.path.join(ROOT, "vm")

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PicoShop -- a PicoScript platform showcase</title>
<style>
  :root { --ink:#e6ebf5; --muted:#8b97ab; --line:#2c313f; --accent:#7c8cff; --bg:#0b0e16; --panel:#11141c; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; color: var(--ink); background: var(--bg); }
  header { background: #070a12; padding: 14px 22px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--line); }
  header h1 { font-size: 18px; margin: 0; }
  header .tag { font-size: 12px; color: var(--muted); }
  header a.home { margin-left: auto; font-size: 12px; color: var(--accent); text-decoration: none; }
  nav { display: flex; gap: 4px; padding: 10px 22px 0; background: var(--panel); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  nav a { padding: 8px 14px; font-size: 14px; text-decoration: none; color: var(--muted); border-bottom: 2px solid transparent; }
  nav a.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
  main { max-width: 1000px; margin: 0 auto; padding: 22px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 18px; }
  .card h2 { font-size: 15px; margin: 0 0 4px; }
  .card p.sub { margin: 0 0 12px; color: var(--muted); font-size: 13px; }
  .pill { display: inline-block; font-size: 11px; background: #1b2340; color: var(--accent); padding: 2px 8px; border-radius: 999px; margin-left: 6px; }
  button.bt { background: var(--accent); color: #0b0e16; border: none; border-radius: 6px; padding: 7px 12px; font-size: 13px; cursor: pointer; font-weight: 600; }
  pre { background: #070a12; color: #9fb0c8; padding: 10px; border-radius: 8px; font-size: 12px; overflow: auto; white-space: pre-wrap; }
  table.pico-report { border-collapse: collapse; font-size: 14px; width: 100%; }
  table.pico-report th, table.pico-report td { border: 1px solid var(--line); padding: 5px 12px; text-align: left; }
  table.pico-report thead th { background: #161b2b; }
  table.pico-report tfoot td { font-weight: 700; background: #131829; }
  .pico-form-row { display: flex; gap: 14px; flex-wrap: wrap; margin: 6px 0; }
  .pico-field { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); }
  .pico-field input { border: 1px solid #34405e; border-radius: 5px; padding: 4px 6px; font-size: 14px; background:#0b0e16; color: var(--ink); }
  .log { font-size: 12px; font-family: ui-monospace, monospace; }
  .log div { padding: 3px 0; border-bottom: 1px dashed var(--line); }
  .log .ev { color: var(--accent); font-weight: 600; }
  .muted { color: var(--muted); font-size: 12px; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
</style>
</head>
<body>
<header>
  <h1>&#128295; PicoShop</h1>
  <span class="tag">a small app built from the PicoScript toolchain &mdash; one deterministic bytecode underneath</span>
  <a class="home" href="index.html">&#8592; back to the PicoScript site</a>
</header>
<nav id="nav">
  <a href="#/build">1 &middot; Logic (Workflow &rarr; PicoScript)</a>
  <a href="#/report">2 &middot; Report</a>
  <a href="#/form">3 &middot; Form (edit &amp; save)</a>
  <a href="#/activity">4 &middot; Activity (events)</a>
</nav>
<main id="view"></main>

<script>/*__HOOKS__*/</script>
<script>/*__VM__*/</script>
<script>/*__PICOC__*/</script>
<script>/*__WF__*/</script>
<script>/*__LAYOUT__*/</script>
<script>
(function () {
  'use strict';
  var WF = BareMetal.WorkflowPico, L = BareMetal.Report;   // vendored from BareMetalJsTools

  // Stage 1: an "orders" workflow that materialises line items [qty, price] and
  // emits them -- the data producer (the logic layer).
  var ordersFlow = [
    { type: 'SET', name: 'lines', value: [2, 10, 3, 20, 1, 50] },
    { type: 'FOREACH', var: 'v', in: 'lines' },
    { type: 'LOG', message: 'v' },
    { type: 'END' }
  ];
  // Stage 2: the report/form layout template.
  var template = {
    title: 'Orders', mode: 'report',
    columns: [
      { label: 'Qty', field: 0, width: 6, format: 'int', editable: true },
      { label: 'Price', field: 1, width: 7, format: 'int', editable: true }
    ],
    aggregates: [{ column: 0, fn: 'sum' }, { column: 1, fn: 'sum' }]
  };
  var orderData = [2, 10, 3, 20, 1, 50];
  var activity = [];

  // A tiny in-page event bus (the same RAISE/ON idea, minimal + dependency-free).
  var subs = [];
  var Bus = {
    on: function (fn) { subs.push(fn); },
    emit: function (topic, data) {
      activity.unshift({ ev: topic, msg: typeof data === 'object' ? JSON.stringify(data) : String(data), t: new Date().toLocaleTimeString() });
      subs.forEach(function (fn) { try { fn(topic, data); } catch (e) {} });
    }
  };

  var view = document.getElementById('view');
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  // VM output buffer -> ints (Print writes 4-byte big-endian ints).
  function decodeInts(output) {
    var bytes = [];
    (output || []).forEach(function (c) {
      if (Array.isArray(c) || (typeof Uint8Array !== 'undefined' && c instanceof Uint8Array)) { for (var i = 0; i < c.length; i++) bytes.push(c[i] & 255); }
      else bytes.push(c & 255);
    });
    var out = [];
    for (var j = 0; j + 4 <= bytes.length; j += 4) out.push(((bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3]) | 0);
    return out;
  }

  function runFlow() {
    var eng = WF.compile(ordersFlow);
    var r = PicoCompile.compileDebug(eng.source, 'english');
    var vm = new PicoVM(); vm.run(r.words.map(function (w) { return w >>> 0; }));
    return { source: eng.source, words: r.words, output: vm.outputInts ? vm.outputInts() : decodeInts(vm.output), warnings: eng.warnings };
  }

  function viewBuild() {
    view.innerHTML =
      '<div class="card"><h2>The data producer is a <b>visual workflow</b> <span class="pill">picoworkflow</span></h2>' +
      '<p class="sub">These steps materialise the order lines into PicoScript <code>Memory</code> and emit each value. ' +
      '<b>Compile</b> turns them into deterministic bytecode that runs on the same VM as bare metal, PIOS and the C# host.</p>' +
      '<pre>' + esc(JSON.stringify(ordersFlow, null, 2)) + '</pre>' +
      '<div class="row"><button class="bt" id="compile">Compile &amp; Run &#9654;</button>' +
      '<span class="muted">Workflow &rarr; English PicoScript &rarr; bytecode &rarr; VM output</span></div>' +
      '<div id="out"></div></div>';
    document.getElementById('compile').onclick = function () {
      var res;
      try { res = runFlow(); } catch (e) { document.getElementById('out').innerHTML = '<pre style="color:#f88">' + esc(e.message) + '</pre>'; return; }
      orderData = res.output;
      document.getElementById('out').innerHTML =
        '<h2 style="margin-top:14px">Generated PicoScript (English dialect)</h2><pre>' + esc(res.source) + '</pre>' +
        '<h2>VM output (the order data)</h2><pre>[' + orderData.join(', ') + ']</pre>';
      Bus.emit('flow.compiled', { words: res.words.length, values: orderData.length });
    };
  }

  function viewReport() {
    var html = L.renderHtml(orderData, template, 'report');
    view.innerHTML =
      '<div class="card"><h2>Render a <b>report</b> over the workflow output <span class="pill">picolayout</span></h2>' +
      '<p class="sub">The same template renders a read-only table with an aggregate footer. Byte-identical to the ' +
      'in-browser and native toolchain engines.</p>' + html + '</div>';
    Bus.emit('report.rendered', { rows: Math.ceil(orderData.length / template.columns.length) });
  }

  function viewForm() {
    view.innerHTML =
      '<div class="card"><h2>Edit an order as a <b>form</b> <span class="pill">read-write layout</span></h2>' +
      '<p class="sub">The exact same template, rendered read-write. <b>Save</b> collects the inputs and maps them ' +
      'back through the data ABI.</p>' +
      '<div id="formHost">' + L.renderHtml(orderData, template, 'form') + '</div>' +
      '<div class="row" style="margin-top:12px"><button class="bt" id="save">Save order &#128190;</button>' +
      '<span class="muted" id="savemsg"></span></div></div>';
    document.getElementById('save').onclick = function () {
      var rows = L.collect(document.querySelector('#formHost form'));
      orderData = L.flatten(rows);
      var writes = L.toWrites(rows, { base: 0 });
      document.getElementById('savemsg').textContent = 'saved ' + rows.length + ' rows \u2192 data ABI: ' + JSON.stringify(writes);
      Bus.emit('order.saved', { rows: rows.length, total: orderData.reduce(function (a, b) { return a + b; }, 0) });
    };
  }

  function viewActivity() {
    view.innerHTML =
      '<div class="card"><h2>Everything is wired with <b>events</b> <span class="pill">RAISE / ON</span></h2>' +
      '<p class="sub">Compiling, rendering and saving all publish to one bus &mdash; the same event model workflows ' +
      'RAISE/ON. This log is a live subscriber.</p><div class="log" id="log"></div></div>';
    renderLog();
  }
  function renderLog() {
    var el = document.getElementById('log');
    if (!el) return;
    el.innerHTML = activity.length
      ? activity.map(function (a) { return '<div><span class="ev">' + esc(a.ev) + '</span> &middot; ' + esc(a.msg) + ' <span class="muted">' + a.t + '</span></div>'; }).join('')
      : '<div class="muted">No activity yet &mdash; compile the workflow, view the report, or save the form.</div>';
  }
  Bus.on(function () { renderLog(); });

  var routes = { '/build': viewBuild, '/report': viewReport, '/form': viewForm, '/activity': viewActivity };
  function route() {
    var hash = (location.hash || '#/build').slice(1);
    (routes[hash] || viewBuild)();
    document.querySelectorAll('#nav a').forEach(function (a) { a.classList.toggle('active', a.getAttribute('href') === (location.hash || '#/build')); });
  }
  window.addEventListener('hashchange', route);
  if (!location.hash) location.hash = '#/build';
  route();
})();
</script>
</body>
</html>
"""


def main():
    html = PAGE
    for marker, fname in [
        ("/*__HOOKS__*/", "pico_hooks.js"),
        ("/*__VM__*/", "picovm.js"),
        ("/*__PICOC__*/", "picoc.js"),
        ("/*__WF__*/", os.path.join("vendor", "BareMetal.WorkflowPico.js")),
        ("/*__LAYOUT__*/", os.path.join("vendor", "BareMetal.Report.js")),
    ]:
        with open(os.path.join(VM, fname), encoding="utf-8") as f:
            html = html.replace(marker, f.read())
    out_path = os.path.join(ROOT, "docs", "showcase.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out_path} ({len(html)//1024} KB)")


if __name__ == "__main__":
    main()
