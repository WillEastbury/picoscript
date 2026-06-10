#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picoscript_lang import NAMESPACE_MAP, HOST_HOOK_CODES
from datetime import datetime

LANGUAGE_SYNTAX = {
    'IF': {
        'syntax': 'IF (condition) { statements }',
        'desc': 'Conditional execution. Executes the block if condition evaluates to true.',
        'example': 'IF (x > 5) { Memory.Poke(0x1000, 1, 42); }'
    },
    'SWITCH': {
        'syntax': 'SWITCH (expr) { CASE val: statements; DEFAULT: statements; }',
        'desc': 'Multi-way branching based on expression value.',
        'example': 'SWITCH (status) { CASE 200: handle_ok; CASE 404: handle_not_found; }'
    },
    'FOREACH': {
        'syntax': 'FOREACH (item IN collection) { statements }',
        'desc': 'Iterate over elements in a collection or descriptor array.',
        'example': 'FOREACH (header IN headers) { process_header(header); }'
    },
    'FOR': {
        'syntax': 'FOR (init; condition; increment) { statements }',
        'desc': 'C-style loop with init, condition, and increment.',
        'example': 'FOR (i = 0; i < 100; i++) { Memory.Poke(addr + i, 1, 0); }'
    },
    'WHILE': {
        'syntax': 'WHILE (condition) { statements }',
        'desc': 'Loop that executes while condition is true.',
        'example': 'WHILE (remaining > 0) { chunk = read_next(); remaining--; }'
    },
    'GOTO': {
        'syntax': 'GOTO label_name;',
        'desc': 'Jump to a labeled location in code.',
        'example': 'GOTO error_handler;'
    },
    'GOSUB': {
        'syntax': 'GOSUB subroutine_name;',
        'desc': 'Call a subroutine; returns to next instruction after call.',
        'example': 'GOSUB validate_request;'
    },
    'RETURN': {
        'syntax': 'RETURN [value];',
        'desc': 'Exit function/subroutine with optional return value.',
        'example': 'RETURN Http.GenerateResponse(200, headers, body);'
    }
}

# Comprehensive method documentation
METHOD_DOCS = {}

# Core method documentation examples
doc_data = {
    'Auth': {
        'GetUserCredentials': {
            'desc': 'Retrieve user credentials from the authentication context.',
            'sig': 'GetUserCredentials(user_id: string) -> credentials_descriptor',
            'conformance': 'L6',
            'calling': 'creds = Auth.GetUserCredentials("user_42");',
            'params': 'user_id: The user identifier to fetch credentials for.',
            'returns': 'Descriptor containing encrypted credentials.'
        },
        'ValidateCredentials': {
            'desc': 'Validate a username/password pair against the system credential store.',
            'sig': 'ValidateCredentials(username: string, password: string) -> bool',
            'conformance': 'L6',
            'calling': 'valid = Auth.ValidateCredentials("alice", "secret");',
            'params': 'username: Login name; password: User password (clear text during call).',
            'returns': 'true if credentials are valid, false otherwise.'
        },
        'SwitchUserContext': {
            'desc': 'Atomically switch the execution context to run as authenticated user.',
            'sig': 'SwitchUserContext(user_id: string) -> bool',
            'conformance': 'L6',
            'calling': 'Auth.SwitchUserContext("user_42");',
            'params': 'user_id: User identifier to switch to.',
            'returns': 'true if context switch succeeded.'
        },
    },
    'Memory': {
        'Peek': {
            'desc': 'Read typed memory at a memory offset. Type determines byte width.',
            'sig': 'Peek(offset: u32, type: u8) -> value',
            'conformance': 'L1',
            'calling': 'val = Memory.Peek(0x1000, 1);',
            'params': 'offset: Memory address to read from; type: Data type (0=u8, 1=u32, 2=u64).',
            'returns': 'Value read from memory as specified type.'
        },
        'Poke': {
            'desc': 'Write typed memory at a memory offset.',
            'sig': 'Poke(offset: u32, type: u8, value: i64) -> void',
            'conformance': 'L1',
            'calling': 'Memory.Poke(0x1000, 1, 42);',
            'params': 'offset: Address to write; type: Data type; value: Value to write.',
            'returns': 'No return value.'
        },
    },
    'Http': {
        'ReadHeader': {
            'desc': 'Read an HTTP header value from the incoming request descriptor.',
            'sig': 'ReadHeader(header_name: string, descriptor: u32) -> string',
            'conformance': 'L5',
            'calling': 'ct = Http.ReadHeader("Content-Type", R0);',
            'params': 'header_name: HTTP header name (case-insensitive); descriptor: Request descriptor handle.',
            'returns': 'Header value as string, or empty if not present.'
        },
        'ReadBody': {
            'desc': 'Read HTTP body from request descriptor with optional offset and length.',
            'sig': 'ReadBody(descriptor: u32, offset: u32, length: u32) -> bytes',
            'conformance': 'L5',
            'calling': 'body = Http.ReadBody(R0, 0, 1024);',
            'params': 'descriptor: Request handle; offset: Start position in body; length: Bytes to read.',
            'returns': 'Byte array containing body data.'
        },
        'GenerateResponse': {
            'desc': 'Generate HTTP response with status code, headers, and body.',
            'sig': 'GenerateResponse(status: u32, headers: descriptor[], body: descriptor) -> void',
            'conformance': 'L5',
            'calling': 'Http.GenerateResponse(200, header_list, body);',
            'params': 'status: HTTP status (200, 404, etc.); headers: Array of header descriptors; body: Response body descriptor.',
            'returns': 'No return value; response is queued for transmission.'
        },
    },
    'String': {
        'Length': {
            'desc': 'Get the length of a string in bytes.',
            'sig': 'Length(str: string) -> u32',
            'conformance': 'L2',
            'calling': 'len = String.Length("hello");',
            'params': 'str: String to measure.',
            'returns': 'Length in bytes.'
        },
        'Concat': {
            'desc': 'Concatenate two strings.',
            'sig': 'Concat(str1: string, str2: string) -> string',
            'conformance': 'L2',
            'calling': 'result = String.Concat("hello", "world");',
            'params': 'str1: First string; str2: Second string.',
            'returns': 'Concatenated result.'
        },
    },
    'Crypto': {
        'Sha256': {
            'desc': 'Compute SHA-256 hash of input data (hardware-accelerated).',
            'sig': 'Sha256(data: bytes) -> bytes[32]',
            'conformance': 'L6',
            'calling': 'hash = Crypto.Sha256(payload);',
            'params': 'data: Bytes to hash.',
            'returns': '32-byte SHA-256 hash.'
        },
        'Sha512': {
            'desc': 'Compute SHA-512 hash of input data (hardware-accelerated).',
            'sig': 'Sha512(data: bytes) -> bytes[64]',
            'conformance': 'L6',
            'calling': 'hash = Crypto.Sha512(payload);',
            'params': 'data: Bytes to hash.',
            'returns': '64-byte SHA-512 hash.'
        },
    },
}

# Build METHOD_DOCS by merging data
for ns, methods in sorted(doc_data.items()):
    METHOD_DOCS[ns] = methods

# Generate comprehensive HTML
html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PicoScript Complete Reference</title>
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
.tabs { display: flex; gap: 0; border-bottom: 1px solid #e0e0e0; background: white; padding: 0 20px; overflow-x: auto; }
.tab-btn { padding: 14px 20px; background: none; border: none; cursor: pointer; font-weight: 600; color: #666; border-bottom: 3px solid transparent; white-space: nowrap; }
.tab-btn.active { color: #667eea; border-bottom-color: #667eea; }
.tab-content { display: none; flex: 1; overflow-y: auto; background: white; padding: 30px; }
.tab-content.active { display: block; }
.tree-section { padding: 0 15px; margin: 5px 0; }
.tree-title { font-weight: 700; color: #333; padding: 10px 8px; font-size: 13px; display: flex; align-items: center; cursor: pointer; user-select: none; }
.tree-title:hover { color: #667eea; }
.tree-toggle { display: inline-block; width: 16px; text-align: center; color: #667eea; margin-right: 6px; font-size: 12px; }
.tree-items { }
.tree-item { padding: 6px 20px; font-size: 12px; color: #555; cursor: pointer; border-left: 2px solid transparent; display: flex; align-items: center; user-select: none; }
.tree-item:hover { background: #f0f0f0; color: #667eea; }
.tree-item.active { background: #e8eaf6; color: #667eea; border-left-color: #667eea; font-weight: 600; }
.tree-item.parent { padding-left: 8px; }
.tree-item .toggle { display: inline-block; width: 14px; text-align: center; margin-right: 4px; color: #999; font-size: 12px; }
.tree-item .indent { margin-left: 20px; }
.stat-box { display: inline-block; margin: 10px 20px 10px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #667eea; }
.stat-num { font-size: 24px; font-weight: bold; color: #667eea; }
.stat-label { font-size: 12px; color: #666; margin-top: 5px; }
.namespace { margin: 30px 0; }
.namespace h2 { color: #667eea; border-bottom: 2px solid #667eea; padding-bottom: 10px; margin-bottom: 20px; }
.method { margin: 15px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #667eea; }
.method.highlight { background: #fff3cd; border-left-color: #ffc107; }
.method h3 { color: #333; margin: 0 0 10px 0; }
.method-sig { background: white; padding: 10px; border-radius: 4px; font-family: monospace; margin: 10px 0; font-size: 11px; border-left: 3px solid #667eea; }
.method-desc { color: #555; margin: 10px 0; font-size: 14px; }
.method-section { margin: 8px 0; padding: 8px 0; }
.method-section-title { font-weight: 600; color: #333; font-size: 12px; }
.method-section-content { font-size: 12px; color: #666; margin-left: 10px; }
.code-example { background: #2d2d2d; color: #f8f8f2; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-top: 8px; overflow-x: auto; }
.conformance { display: inline-block; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; background: #e0e7ff; color: #667eea; margin: 5px 5px 5px 0; }
.syntax-item { margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #667eea; }
.syntax-item h3 { color: #333; margin: 0 0 10px 0; }
.syntax-syntax { background: white; padding: 10px; border-radius: 4px; font-family: monospace; margin: 10px 0; font-size: 11px; border-left: 3px solid #667eea; }
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
<div class="tree-title" onclick="toggleLanguageSyntax(event)">
<span class="tree-toggle">v</span>
<span>LANGUAGE SYNTAX</span>
</div>
<div class="tree-items" id="lang-items">
'''

for keyword in sorted(LANGUAGE_SYNTAX.keys()):
    html_content += f'<div class="tree-item" onclick="selectSyntax(event, \'{keyword}\')">{keyword}</div>\n'

html_content += '''
</div>
</div>

<div class="tree-section">
<div class="tree-title" onclick="toggleNamespacesSection(event)">
<span class="tree-toggle">v</span>
<span>NAMESPACES & METHODS</span>
</div>
<div class="tree-items" id="namespaces-items">
'''

for ns in sorted(NAMESPACE_MAP.keys()):
    methods = sorted(NAMESPACE_MAP[ns].keys())
    html_content += f'''<div class="tree-item parent" onclick="toggleNamespace(event, '{ns}')">
<span class="toggle">v</span>
<span>{ns}</span>
</div>
<div class="tree-items tree-ns-methods" id="tree-ns-{ns}">
'''
    for method in methods:
        html_content += f'<div class="tree-item indent" onclick="selectMethod(event, \'{ns}\', \'{method}\')">{method}</div>\n'
    html_content += '</div>\n'

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

<div id="syntax" class="tab-content">
<h2>Language Syntax & Keywords</h2>
'''

for keyword in sorted(LANGUAGE_SYNTAX.keys()):
    data = LANGUAGE_SYNTAX[keyword]
    syntax_id = f'syntax-{keyword}'
    html_content += f'''<div class="syntax-item" id="{syntax_id}">
<h3>{keyword}</h3>
<div class="syntax-syntax"><code>{data['syntax']}</code></div>
<div class="method-section">
<div class="method-section-title">Description:</div>
<div class="method-section-content">{data['desc']}</div>
</div>
<div class="method-section">
<div class="method-section-title">Example:</div>
<div class="code-example">{data['example']}</div>
</div>
</div>
'''

html_content += '''
</div>

<div id="namespaces" class="tab-content">
<h2>Namespaces and Methods</h2>
'''

for ns in sorted(NAMESPACE_MAP.keys()):
    methods = sorted(NAMESPACE_MAP[ns].keys())
    html_content += f'<div class="namespace" id="ns-{ns}"><h2>{ns} ({len(methods)} methods)</h2>'
    for method in sorted(methods):
        hook_key = (ns, method)
        hook_code = HOST_HOOK_CODES.get(hook_key, None)
        hook_hex = f'0x{hook_code:04X}' if hook_code is not None else 'N/A'
        
        docs = METHOD_DOCS.get(ns, {}).get(method, {})
        safe_id = f'{ns}_{method}'
        html_content += f'<div class="method" id="method-{safe_id}"><h3>{method}</h3>'
        
        # Hook code and conformance
        html_content += f'<span class="conformance">{docs.get("conformance", "L1")}</span>'
        html_content += f'<span class="conformance">{hook_hex}</span>'
        
        # Description
        if 'desc' in docs:
            html_content += f'<div class="method-desc">{docs["desc"]}</div>'
        
        # Signature
        if 'sig' in docs:
            html_content += f'<div class="method-section"><div class="method-section-title">Signature:</div>'
            html_content += f'<div class="method-sig"><code>{docs["sig"]}</code></div></div>'
        
        # Calling syntax
        if 'calling' in docs:
            html_content += f'<div class="method-section"><div class="method-section-title">Calling Syntax:</div>'
            html_content += f'<div class="code-example">{docs["calling"]}</div></div>'
        
        # Parameters
        if 'params' in docs:
            html_content += f'<div class="method-section"><div class="method-section-title">Parameters:</div>'
            html_content += f'<div class="method-section-content">{docs["params"]}</div></div>'
        
        # Returns
        if 'returns' in docs:
            html_content += f'<div class="method-section"><div class="method-section-title">Returns:</div>'
            html_content += f'<div class="method-section-content">{docs["returns"]}</div></div>'
        
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
</div>
</div>

<script>
let selectedItem = null;
let nsExpanded = {};

// Initialize namespace expand state
'''

for ns in sorted(NAMESPACE_MAP.keys()):
    html_content += f"nsExpanded['{ns}'] = true;\n"

html_content += '''

function toggleLanguageSyntax(e) {
    e.stopPropagation();
    const items = document.getElementById('lang-items');
    const toggle = e.currentTarget.querySelector('.tree-toggle');
    if (items.style.display === 'none') {
        items.style.display = 'block';
        toggle.textContent = 'v';
    } else {
        items.style.display = 'none';
        toggle.textContent = '>';
    }
}

function toggleNamespacesSection(e) {
    e.stopPropagation();
    const items = document.getElementById('namespaces-items');
    const toggle = e.currentTarget.querySelector('.tree-toggle');
    if (items.style.display === 'none') {
        items.style.display = 'block';
        toggle.textContent = 'v';
    } else {
        items.style.display = 'none';
        toggle.textContent = '>';
    }
}

function toggleNamespace(e, ns) {
    e.stopPropagation();
    const methodsDiv = document.getElementById('tree-ns-' + ns);
    const toggle = e.currentTarget.querySelector('.toggle');
    
    if (methodsDiv) {
        if (nsExpanded[ns]) {
            methodsDiv.style.display = 'none';
            toggle.textContent = '>';
            nsExpanded[ns] = false;
        } else {
            methodsDiv.style.display = 'block';
            toggle.textContent = 'v';
            nsExpanded[ns] = true;
        }
    }
}

function selectSyntax(e, keyword) {
    e.stopPropagation();
    clearSelection();
    e.currentTarget.classList.add('active');
    selectedItem = e.currentTarget;
    
    // Switch to syntax tab
    const syntaxTab = document.querySelector('[data-tab="syntax"]');
    if (syntaxTab) {
        syntaxTab.click();
    }
    
    // Scroll to syntax item
    const syntaxId = 'syntax-' + keyword;
    const syntaxEl = document.getElementById(syntaxId);
    if (syntaxEl) {
        syntaxEl.classList.add('highlight');
        syntaxEl.scrollIntoView({behavior: 'smooth', block: 'center'});
        setTimeout(() => {
            syntaxEl.classList.remove('highlight');
        }, 2000);
    }
}

function selectMethod(e, ns, method) {
    e.stopPropagation();
    clearSelection();
    e.currentTarget.classList.add('active');
    selectedItem = e.currentTarget;
    
    // Switch to namespaces tab
    const nsTab = document.querySelector('[data-tab="namespaces"]');
    if (nsTab) {
        nsTab.click();
    }
    
    // Scroll to method
    const methodId = 'method-' + ns + '_' + method;
    const methodEl = document.getElementById(methodId);
    if (methodEl) {
        methodEl.classList.add('highlight');
        methodEl.scrollIntoView({behavior: 'smooth', block: 'center'});
        setTimeout(() => {
            methodEl.classList.remove('highlight');
        }, 2000);
    }
}

function clearSelection() {
    if (selectedItem) {
        selectedItem.classList.remove('active');
    }
}

// Initialize tabs
const tabs = [
    {id: 'overview', label: 'Overview'},
    {id: 'syntax', label: 'Syntax'},
    {id: 'namespaces', label: 'Namespaces'},
    {id: 'conformance', label: 'Conformance'}
];
const tabsContainer = document.getElementById('tabs');
tabs.forEach((t, idx) => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (idx === 0 ? ' active' : '');
    btn.textContent = t.label;
    btn.setAttribute('data-tab', t.id);
    btn.onclick = () => {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        document.getElementById(t.id).classList.add('active');
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

print('Generated docs/PICOSCRIPT_REFERENCE.html with syntax tab and full documentation')
