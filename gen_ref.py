#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picoscript_lang import NAMESPACE_MAP, HOST_HOOK_CODES
from datetime import datetime

LANGUAGE_SYNTAX = [
    'IF', 'SWITCH', 'FOREACH', 'FOR', 'WHILE', 'GOTO', 'GOSUB', 'RETURN'
]

METHOD_DOCS = {
    'Memory': {
        'Peek': {
            'desc': 'Read typed memory at a memory offset',
            'sig': 'Peek(offset: u32, type: u8) -> value',
            'conformance': 'L1',
            'example': 'Memory.Peek(0x1000, 1);  // Read u32 at 0x1000',
        },
        'Poke': {
            'desc': 'Write typed memory at a memory offset',
            'sig': 'Poke(offset: u32, type: u8, value: i64) -> void',
            'conformance': 'L1',
            'example': 'Memory.Poke(0x1000, 1, 42);  // Write u32 42 at 0x1000',
        },
    },
    'Http': {
        'ReadHeader': {
            'desc': 'Read HTTP header from request descriptor',
            'sig': 'ReadHeader(header_name: string, descriptor: u32) -> value',
            'conformance': 'L5',
            'example': 'Http.ReadHeader("Content-Type", R0);',
        },
        'ReadBody': {
            'desc': 'Read HTTP body from request descriptor',
            'sig': 'ReadBody(descriptor: u32, offset: u32, length: u32) -> bytes',
            'conformance': 'L5',
            'example': 'Http.ReadBody(R0, 0, 1024);',
        },
    },
}

LANGUAGE_SYNTAX = [
    'IF', 'SWITCH', 'FOREACH', 'FOR', 'WHILE', 'GOTO', 'GOSUB', 'RETURN'
]

html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PicoScript Reference</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f5; display: flex; flex-direction: column; }
.top-bar { background: white; padding: 20px 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.top-bar h1 { color: #667eea; margin: 0; font-size: 28px; }
.top-bar p { color: #666; margin: 5px 0 0 0; font-size: 12px; }
.main-container { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 280px; background: white; border-right: 1px solid #e0e0e0; overflow-y: auto; padding: 20px 0; box-shadow: 2px 0 8px rgba(0,0,0,0.05); }
.content-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.tabs { display: flex; gap: 0; border-bottom: 1px solid #e0e0e0; background: white; padding: 0 20px; }
.tab-btn { padding: 14px 20px; background: none; border: none; cursor: pointer; font-weight: 600; color: #666; border-bottom: 3px solid transparent; }
.tab-btn.active { color: #667eea; border-bottom-color: #667eea; }
.tab-content { display: none; flex: 1; overflow-y: auto; background: white; padding: 30px; }
.tab-content.active { display: block; }
.tree-section { padding: 0 20px; margin-bottom: 15px; }
.tree-title { font-weight: 700; color: #333; padding: 8px 0; font-size: 13px; display: flex; align-items: center; cursor: pointer; user-select: none; }
.tree-title:hover { color: #667eea; }
.tree-toggle { display: inline-block; width: 16px; text-align: center; color: #667eea; margin-right: 6px; }
.tree-items { margin-top: 5px; }
.tree-item { padding: 6px 20px; font-size: 12px; color: #555; cursor: pointer; border-left: 2px solid transparent; display: flex; align-items: center; }
.tree-item:hover { background: #f5f5f5; color: #667eea; }
.tree-item.active { background: #e8eaf6; color: #667eea; border-left-color: #667eea; font-weight: 600; }
.tree-item.parent { cursor: pointer; }
.tree-item .toggle { display: inline-block; width: 14px; text-align: center; margin-right: 4px; color: #999; }
.tree-item .indent { margin-left: 16px; }
.stat-box { display: inline-block; margin: 10px 20px 10px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #667eea; }
.stat-num { font-size: 24px; font-weight: bold; color: #667eea; }
.stat-label { font-size: 12px; color: #666; margin-top: 5px; }
.namespace { margin: 30px 0; }
.namespace h2 { color: #667eea; border-bottom: 2px solid #667eea; padding-bottom: 10px; margin-bottom: 20px; }
.method { margin: 15px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #667eea; }
.method h3 { color: #333; margin: 0 0 10px 0; }
.method-sig { background: white; padding: 10px; border-radius: 4px; font-family: monospace; margin: 10px 0; }
.conformance { display: inline-block; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; background: #e0e7ff; color: #667eea; margin: 5px 5px 5px 0; }
.code-example { background: #2d2d2d; color: #f8f8f2; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-top: 10px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; margin: 20px 0; }
th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
th { background: #667eea; color: white; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: #f1f1f1; }
::-webkit-scrollbar-thumb { background: #888; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #555; }
</style>
</head>
<body>
<div class="top-bar">
<h1>PicoScript Complete Reference Manual</h1>
<p>Generated: ''' + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '''</p>
</div>

<div class="main-container">
<div class="sidebar" id="sidebar">
<div class="tree-section">
<div class="tree-title" onclick="toggleSection(this)">
<span class="tree-toggle">v</span>
<span>LANGUAGE SYNTAX</span>
</div>
<div class="tree-items" style="display: block;">
'''

for keyword in LANGUAGE_SYNTAX:
    html_content += f'<div class="tree-item" onclick="selectItem(this, \'{keyword}\')">{keyword}</div>'

html_content += '''
</div>
</div>

<div class="tree-section">
<div class="tree-title" onclick="toggleSection(this)">
<span class="tree-toggle">v</span>
<span>NAMESPACES & METHODS</span>
</div>
<div class="tree-items" style="display: block;" id="namespace-tree">
'''

for ns in sorted(NAMESPACE_MAP.keys()):
    methods = sorted(NAMESPACE_MAP[ns].keys())
    html_content += f'''<div class="tree-item parent" onclick="toggleNamespace(this, '{ns}')">
<span class="toggle">></span>
<span>{ns}</span>
</div>
<div class="tree-items" id="ns-{ns}" style="display: none;">
'''
    for method in methods:
        html_content += f'<div class="tree-item indent" onclick="selectItem(this, \'{ns}.{method}\')">{method}</div>'
    html_content += '</div>'

html_content += '''
</div>
</div>
</div>

<div class="content-area">
<div class="tabs" id="tabs"></div>

<div id="overview" class="tab-content active">
<h2>Overview</h2>
<div>
<div class="stat-box"><div class="stat-num">26</div><div class="stat-label">Namespaces</div></div>
<div class="stat-box"><div class="stat-num">213</div><div class="stat-label">Methods</div></div>
<div class="stat-box"><div class="stat-num">8</div><div class="stat-label">Language Keywords</div></div>
<div class="stat-box"><div class="stat-num">6</div><div class="stat-label">Conformance Levels</div></div>
</div>
<p>PicoScript is a deterministic bytecode language for userland message processing in the PIOS kernel. Each method compiles to a single 32-bit instruction (OP_NOOP + hook code).</p>
<h3>Hook Code Allocation</h3>
<table>
<thead><tr><th>Range</th><th>Category</th><th>Methods</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td>0x01-0x70</td><td>Core (Hardware)</td><td>44</td><td>Kernel, Memory, Threading</td></tr>
<tr><td>0x80-0xC6</td><td>Standard Library</td><td>63</td><td>String, Number, Math</td></tr>
<tr><td>0xD0-0xFE</td><td>Context/System</td><td>39</td><td>Environment, Crypto</td></tr>
<tr><td>0x0100-0x0149</td><td>Application Features</td><td>44</td><td>Http, Auth, Html, X509</td></tr>
</tbody>
</table>
</div>

<div id="namespaces" class="tab-content">
<h2>Namespaces and Methods</h2>
'''

ns_count = 0
for ns in sorted(NAMESPACE_MAP.keys()):
    methods = list(NAMESPACE_MAP[ns].keys())
    ns_count += 1
    html_content += f'<div class="namespace"><h2>{ns} ({len(methods)} methods)</h2>'
    for method in sorted(methods):
        hook_key = (ns, method)
        hook_code = HOST_HOOK_CODES.get(hook_key, None)
        hook_hex = f'0x{hook_code:04X}' if hook_code is not None else 'N/A'
        
        docs = METHOD_DOCS.get(ns, {}).get(method, {})
        method_id = f'{ns}-{method}'
        html_content += f'<div class="method" id="{method_id}"><h3>{method}</h3>'
        html_content += f'<div class="method-sig"><code>{docs.get("sig", ns + "." + method + "(...)")}</code></div>'
        html_content += f'<p>{docs.get("desc", "")}</p>'
        html_content += f'<span class="conformance">{docs.get("conformance", "L1")}</span>'
        html_content += f'<span class="conformance">{hook_hex}</span>'
        if 'example' in docs:
            html_content += f'<div class="code-example">{docs["example"]}</div>'
        html_content += '</div>'
    html_content += '</div>'

html_content += '''
</div>

<div id="conformance" class="tab-content">
<h2>Conformance Levels</h2>
<table>
<thead><tr><th>Level</th><th>Description</th><th>Examples</th></tr></thead>
<tbody>
<tr><td>L1</td><td>Memory, threading, basic I/O</td><td>Memory.Peek, Thread.Create, Queue.Send</td></tr>
<tr><td>L2</td><td>String operations and utilities</td><td>String.Length, String.Concat, String.Slice</td></tr>
<tr><td>L3</td><td>Math and DSP operations</td><td>Math.Add, Math.Mul, Dsp.Correlate</td></tr>
<tr><td>L4</td><td>Compression and binary ops</td><td>Compress.Brotli, Compress.Picocompress</td></tr>
<tr><td>L5</td><td>HTTP and web features</td><td>Http.ReadHeader, Http.ReadBody, Html.ParseTree</td></tr>
<tr><td>L6</td><td>Cryptography and PKI</td><td>Crypto.SHA256, X509.Sign, X509.Verify</td></tr>
</tbody>
</table>
</div>

<script>
let selectedItem = null;
let expandedNamespaces = {};

// Initialize all namespaces as expanded
const namespaces = document.querySelectorAll('[id^="ns-"]');
namespaces.forEach(ns => {
    const nsName = ns.id.replace('ns-', '');
    expandedNamespaces[nsName] = true;
    ns.style.display = 'block';
});

// Update toggles to show 'v' for expanded
document.querySelectorAll('.tree-item.parent').forEach(item => {
    item.querySelector('.toggle').textContent = 'v';
});

function toggleSection(titleEl) {
    const itemsDiv = titleEl.nextElementSibling;
    if (itemsDiv) {
        const isHidden = itemsDiv.style.display === 'none';
        itemsDiv.style.display = isHidden ? 'block' : 'none';
        titleEl.querySelector('.tree-toggle').textContent = isHidden ? 'v' : '>';
    }
}

function toggleNamespace(parentEl, nsName) {
    const nsDiv = document.getElementById('ns-' + nsName);
    if (nsDiv) {
        const isHidden = nsDiv.style.display === 'none';
        nsDiv.style.display = isHidden ? 'block' : 'none';
        parentEl.querySelector('.toggle').textContent = isHidden ? 'v' : '>';
        expandedNamespaces[nsName] = isHidden;
    }
}

function selectItem(itemEl, identifier) {
    if (selectedItem) {
        selectedItem.classList.remove('active');
    }
    itemEl.classList.add('active');
    selectedItem = itemEl;
    
    // Scroll to corresponding section if it exists
    const targetId = identifier.replace('.', '-');
    const targetEl = document.getElementById(targetId);
    if (targetEl) {
        targetEl.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
}

const tabs = ['overview', 'namespaces', 'conformance'];
const tabsContainer = document.getElementById('tabs');
tabs.forEach(t => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (t === 'overview' ? ' active' : '');
    btn.textContent = t.charAt(0).toUpperCase() + t.slice(1);
    btn.onclick = () => {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        document.getElementById(t).classList.add('active');
        btn.classList.add('active');
    };
    tabsContainer.appendChild(btn);
});
</script>
</body>
</html>
'''

with open('docs/PICOSCRIPT_REFERENCE.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f'Generated docs/PICOSCRIPT_REFERENCE.html ({ns_count} namespaces, 213 methods)')
