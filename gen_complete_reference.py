#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate comprehensive PicoScript reference documentation."""

import json
import os
from collections import defaultdict
from datetime import datetime
from picoscript_lang import NAMESPACE_MAP, HOST_HOOK_CODES

# Method documentation database
METHOD_DOCS = {
    "Memory": {
        "Peek": {
            "desc": "Read typed memory at a memory offset",
            "sig": "Peek(offset: u32, type: u8) -> value",
            "params": [
                ("offset", "u32", "Memory address to read from"),
                ("type", "u8", "Type hint (0=u8, 1=u32, 2=u64, etc.)"),
            ],
            "returns": "Typed value read from memory",
            "conformance": "L1",
            "example": "Memory.Peek(0x1000, 1);  // Read u32 at 0x1000",
        },
        "Poke": {
            "desc": "Write typed memory at a memory offset",
            "sig": "Poke(offset: u32, type: u8, value: i64) -> void",
            "params": [
                ("offset", "u32", "Memory address to write to"),
                ("type", "u8", "Type hint (0=u8, 1=u32, 2=u64, etc.)"),
                ("value", "i64", "Value to write"),
            ],
            "returns": "None",
            "conformance": "L1",
            "example": "Memory.Poke(0x1000, 1, 42);  // Write u32 42 at 0x1000",
        },
    },
    "String": {
        "Concat": {
            "desc": "Concatenate two strings",
            "sig": "Concat(a: string, b: string) -> string",
            "params": [("a", "string", "First string"), ("b", "string", "Second string")],
            "returns": "Concatenated result",
            "conformance": "L2",
            "example": 'String.Concat("Hello", "World");  // => "HelloWorld"',
        },
        "Length": {
            "desc": "Get length of a string",
            "sig": "Length(s: string) -> u32",
            "params": [("s", "string", "Input string")],
            "returns": "String length in bytes",
            "conformance": "L2",
            "example": 'String.Length("Hello");  // => 5',
        },
    },
    "Http": {
        "ReadHeader": {
            "desc": "Read HTTP header from request descriptor",
            "sig": "ReadHeader(header_name: string, descriptor: u32) -> value",
            "params": [
                ("header_name", "string", "Name of header to read (e.g. 'Content-Type')"),
                ("descriptor", "u32", "Request descriptor handle"),
            ],
            "returns": "Header value or null",
            "conformance": "L5",
            "example": 'Http.ReadHeader("Content-Type", R0);  // Lazy: decoded only if needed',
        },
        "ReadBody": {
            "desc": "Read HTTP body from request descriptor",
            "sig": "ReadBody(descriptor: u32, offset: u32, length: u32) -> bytes",
            "params": [
                ("descriptor", "u32", "Request descriptor handle"),
                ("offset", "u32", "Byte offset into body"),
                ("length", "u32", "Bytes to read"),
            ],
            "returns": "Body slice as descriptor",
            "conformance": "L5",
            "example": "Http.ReadBody(R0, 0, 1024);  // Read first 1024 bytes of body",
        },
        "GenerateResponse": {
            "desc": "Generate HTTP response with status, headers, and body",
            "sig": "GenerateResponse(status: u32, headers: descriptor[], body: descriptor) -> void",
            "params": [
                ("status", "u32", "HTTP status code (200, 404, etc.)"),
                ("headers", "descriptor[]", "Array of header descriptors"),
                ("body", "descriptor", "Body descriptor"),
            ],
            "returns": "None",
            "conformance": "L5",
            "example": "Http.GenerateResponse(200, headers, body);",
        },
    },
    "Auth": {
        "ValidateCredentials": {
            "desc": "Validate user credentials (username + password)",
            "sig": "ValidateCredentials(user: string, pass: string) -> bool",
            "params": [
                ("user", "string", "Username or email"),
                ("pass", "string", "Password (will be hashed internally)"),
            ],
            "returns": "true if valid, false otherwise",
            "conformance": "L6",
            "example": 'Auth.ValidateCredentials("alice", "secret123");',
        },
        "SwitchUserContext": {
            "desc": "Atomically switch execution context to authenticated user",
            "sig": "SwitchUserContext(user_id: string) -> bool",
            "params": [
                ("user_id", "string", "Target user identifier"),
            ],
            "returns": "true if switch succeeded, false if user invalid",
            "conformance": "L6",
            "example": 'Auth.SwitchUserContext("user_42");  // All subsequent Context.* calls reflect user_42',
        },
        "ValidateToken": {
            "desc": "Validate JWT or opaque token",
            "sig": "ValidateToken(token: string, scheme: string) -> bool",
            "params": [
                ("token", "string", "Token string (Bearer or similar)"),
                ("scheme", "string", "Scheme: 'Bearer', 'Basic', 'Digest'"),
            ],
            "returns": "true if token is valid and not expired",
            "conformance": "L6",
            "example": 'Auth.ValidateToken("eyJhbGc...", "Bearer");',
        },
    },
    "Html": {
        "CreateNode": {
            "desc": "Create a new HTML DOM node",
            "sig": "CreateNode(tag_name: string) -> node_handle",
            "params": [
                ("tag_name", "string", "HTML tag name (div, span, p, etc.)"),
            ],
            "returns": "Handle to new DOM node",
            "conformance": "L5",
            "example": 'Html.CreateNode("div");  // Returns handle to <div>',
        },
        "AddChildNode": {
            "desc": "Add a child node to a parent node",
            "sig": "AddChildNode(parent: node_handle, child: node_handle) -> void",
            "params": [
                ("parent", "node_handle", "Parent node"),
                ("child", "node_handle", "Child node to append"),
            ],
            "returns": "None",
            "conformance": "L5",
            "example": "Html.AddChildNode(parent_div, child_span);",
        },
        "SetAttribute": {
            "desc": "Set an HTML attribute on a node",
            "sig": "SetAttribute(node: node_handle, attr: string, value: string) -> void",
            "params": [
                ("node", "node_handle", "Target node"),
                ("attr", "string", "Attribute name (class, id, data-*, etc.)"),
                ("value", "string", "Attribute value"),
            ],
            "returns": "None",
            "conformance": "L5",
            "example": 'Html.SetAttribute(div, "class", "container");',
        },
    },
    "X509": {
        "FetchCertificate": {
            "desc": "Fetch X.509 certificate from secure kernel storage",
            "sig": "FetchCertificate(cert_id: string) -> cert_data",
            "params": [
                ("cert_id", "string", "Certificate identifier (CN, serial, or thumbprint)"),
            ],
            "returns": "Certificate data (DER or PEM descriptor)",
            "conformance": "L6",
            "example": 'X509.FetchCertificate("*.example.com");  // Fetches mTLS cert',
        },
        "GenerateKeyPair": {
            "desc": "Generate RSA or ECDSA keypair in kernel",
            "sig": "GenerateKeyPair(alg: string, bits: u32) -> key_handle",
            "params": [
                ("alg", "string", "Algorithm: 'RSA', 'ECDSA', 'Ed25519'"),
                ("bits", "u32", "Key size (2048, 4096 for RSA; 256, 384, 521 for ECDSA)"),
            ],
            "returns": "Opaque key handle (private key never exposed)",
            "conformance": "L6",
            "example": 'X509.GenerateKeyPair("RSA", 2048);',
        },
    },
    "Compress": {
        "BrotliCompress": {
            "desc": "Compress data using Brotli algorithm",
            "sig": "BrotliCompress(data: descriptor, level: u8) -> compressed",
            "params": [
                ("data", "descriptor", "Input data descriptor"),
                ("level", "u8", "Compression level (0-11; 6 is default)"),
            ],
            "returns": "Compressed data as descriptor",
            "conformance": "L4",
            "example": "Compress.BrotliCompress(input, 6);  // Brotli level 6",
        },
        "BrotliDecompress": {
            "desc": "Decompress Brotli-compressed data",
            "sig": "BrotliDecompress(compressed: descriptor) -> decompressed",
            "params": [
                ("compressed", "descriptor", "Compressed data descriptor"),
            ],
            "returns": "Decompressed data as descriptor",
            "conformance": "L4",
            "example": "Compress.BrotliDecompress(compressed);",
        },
    },
}

def get_conformance_color(level):
    """Map conformance level to color."""
    colors = {
        "L0": "#E8F4F8", "L1": "#D0E8F2", "L2": "#B8DCEC", 
        "L3": "#A0D0E6", "L4": "#88C4E0", "L5": "#7CB8DA",
        "L6": "#7CB8D0",
    }
    return colors.get(level, "#F0F0F0")

def generate_html_reference():
    """Generate comprehensive HTML reference."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PicoScript Complete Reference Manual</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
            line-height: 1.6;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header {
            background: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .header h1 { color: #667eea; margin-bottom: 10px; }
        .header p { color: #666; font-size: 14px; }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .tab-btn {
            padding: 12px 20px;
            background: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .tab-btn.active {
            background: #667eea;
            color: white;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }
        .tab-btn:hover { transform: translateY(-2px); }
        .tab-content {
            display: none;
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .tab-content.active { display: block; }
        .search-box {
            margin-bottom: 20px;
            position: relative;
        }
        .search-box input {
            width: 100%;
            padding: 12px 40px 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        .search-box input:focus { border-color: #667eea; outline: none; }
        .namespace-tree {
            display: flex;
            gap: 20px;
        }
        .tree-sidebar {
            flex: 0 0 220px;
            background: #f8f9fa;
            border-radius: 6px;
            padding: 15px;
            max-height: 600px;
            overflow-y: auto;
        }
        .tree-item {
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 4px;
            margin-bottom: 4px;
            transition: background 0.3s;
            font-size: 14px;
        }
        .tree-item:hover { background: #e0e0e0; }
        .tree-item.active { background: #667eea; color: white; font-weight: 600; }
        .method-list { flex: 1; }
        .method-card {
            border-left: 4px solid #667eea;
            padding: 16px;
            margin-bottom: 16px;
            background: #f8f9fa;
            border-radius: 4px;
            transition: all 0.3s;
        }
        .method-card:hover { background: #eef1fd; box-shadow: 0 2px 8px rgba(102, 126, 234, 0.2); }
        .method-card h3 {
            color: #667eea;
            margin-bottom: 8px;
            font-size: 16px;
        }
        .method-desc { color: #555; margin-bottom: 12px; font-size: 14px; }
        .method-sig {
            background: white;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 12px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            color: #C71585;
            overflow-x: auto;
        }
        .method-params {
            margin-bottom: 12px;
        }
        .param-label { font-weight: 600; color: #667eea; font-size: 13px; }
        .param-item {
            margin-left: 12px;
            padding: 4px 0;
            font-size: 13px;
            color: #555;
        }
        .conformance-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 8px;
        }
        .hook-code {
            display: inline-block;
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            color: #C71585;
            margin-left: 8px;
        }
        .table-of-contents {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .table-of-contents h3 { margin-bottom: 12px; color: #667eea; }
        .toc-list { columns: 3; gap: 20px; list-style: none; }
        .toc-list li { margin-bottom: 8px; }
        .toc-list a { color: #667eea; text-decoration: none; font-size: 14px; }
        .toc-list a:hover { text-decoration: underline; }
        .hook-table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            font-size: 14px;
        }
        .hook-table th, .hook-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #e0e0e0;
        }
        .hook-table th {
            background: #667eea;
            color: white;
            font-weight: 600;
        }
        .hook-table tr:hover { background: #f8f9fa; }
        .code-block {
            background: #2d2d2d;
            color: #f8f8f2;
            padding: 12px;
            border-radius: 4px;
            overflow-x: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            margin-top: 8px;
        }
        .code-block .keyword { color: #66d9ef; }
        .code-block .string { color: #e6db74; }
        .code-block .number { color: #ae81ff; }
        .footer { text-align: center; margin-top: 40px; color: #999; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 PicoScript Complete Reference Manual</h1>
            <p>Comprehensive documentation for all 213 methods across 26 namespaces. Generated """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """.</p>
        </div>

        <div class="tabs" id="tabButtons"></div>

        <!-- Overview Tab -->
        <div id="overview" class="tab-content active">
            <h2>Language Overview</h2>
            <p>PicoScript is a deterministic bytecode language for userland message processing in the PIOS kernel stack. All methods compile to a single 32-bit instruction (OP_NOOP + hook code), ensuring predictable execution and minimal bytecode size.</p>
            
            <div class="table-of-contents">
                <h3>Namespace Summary (26 total)</h3>
                <ul class="toc-list" id="namespaceSummary"></ul>
            </div>

            <h3>Hook Code Allocation Strategy</h3>
            <table class="hook-table">
                <thead>
                    <tr>
                        <th>Range</th>
                        <th>Category</th>
                        <th>Namespaces</th>
                        <th>Methods</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>0x01–0x70</td>
                        <td>Core (Hardware)</td>
                        <td>Kernel, Queue, Random, Memory, Span, Descriptor, Lease, Storage, Thread</td>
                        <td>44</td>
                    </tr>
                    <tr>
                        <td>0x80–0xC6</td>
                        <td>Standard Library</td>
                        <td>String, Number, Maths, DateTime, Locale</td>
                        <td>63</td>
                    </tr>
                    <tr>
                        <td>0xD0–0xFE</td>
                        <td>Context/System</td>
                        <td>Environment, Context, Crypto</td>
                        <td>39</td>
                    </tr>
                    <tr>
                        <td>0x0100–0x0149</td>
                        <td>Application Features</td>
                        <td>Compress, X509, Auth, Http, Html</td>
                        <td>44</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- Namespaces Tab -->
        <div id="namespaces" class="tab-content">
            <div class="search-box">
                <input type="text" id="methodSearch" placeholder="Search methods by name...">
            </div>
            <div class="namespace-tree">
                <div class="tree-sidebar" id="namespaceSidebar"></div>
                <div class="method-list" id="methodList"></div>
            </div>
        </div>

        <!-- Conformance Tab -->
        <div id="conformance" class="tab-content">
            <h2>Conformance Levels (L0–L6)</h2>
            <p>PicoScript defines 7 conformance tiers for feature availability and security boundaries:</p>
            <div id="conformanceLevels"></div>
        </div>

        <!-- Examples Tab -->
        <div id="examples" class="tab-content">
            <h2>Code Examples</h2>
            <div id="examplesContent"></div>
        </div>
    </div>

    <div class="container footer">
        <p>© 2026 PicoScript Compiler. All namespaces compile to deterministic bytecode. No optimizations. What you write is what executes.</p>
    </div>

    <script>
        const allMethods = """ + json.dumps(METHOD_DOCS) + """;
        const allNamespaces = """ + json.dumps({ns: list(methods.keys()) for ns, methods in NAMESPACE_MAP.items()}) + """;
        const hookCodes = """ + json.dumps({ns: {m: hex(c) for m, c in hook_dict.items()} for ns, hook_dict in {ns: {m: HOST_HOOK_CODES.get((ns, m), 0) for m in methods} for ns, methods in NAMESPACE_MAP.items()}.items()}) + """;
        
        function init() {
            const tabs = ['overview', 'namespaces', 'conformance', 'examples'];
            const tabsContainer = document.getElementById('tabButtons');
            
            tabs.forEach(tab => {
                const btn = document.createElement('button');
                btn.className = 'tab-btn' + (tab === 'overview' ? ' active' : '');
                btn.textContent = tab.charAt(0).toUpperCase() + tab.slice(1);
                btn.onclick = () => switchTab(tab);
                tabsContainer.appendChild(btn);
            });

            populateNamespaceSummary();
            populateNamespaceSidebar();
            populateConformanceLevels();
            populateExamples();
            setupSearch();
        }

        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');
            event.target.classList.add('active');
        }

        function populateNamespaceSummary() {
            const list = document.getElementById('namespaceSummary');
            Object.entries(allNamespaces).sort().forEach(([ns, methods]) => {
                const li = document.createElement('li');
                li.innerHTML = `<strong>${ns}</strong> (${methods.length} methods)`;
                list.appendChild(li);
            });
        }

        function populateNamespaceSidebar() {
            const sidebar = document.getElementById('namespaceSidebar');
            Object.keys(allNamespaces).sort().forEach(ns => {
                const item = document.createElement('div');
                item.className = 'tree-item';
                item.textContent = ns;
                item.onclick = () => showNamespace(ns);
                sidebar.appendChild(item);
            });
        }

        function showNamespace(nsName) {
            document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
            event.target.classList.add('active');

            const methods = allNamespaces[nsName] || [];
            const methodList = document.getElementById('methodList');
            methodList.innerHTML = '<h3>' + nsName + ' Namespace (' + methods.length + ' methods)</h3>';

            methods.forEach(method => {
                const hook = hookCodes[nsName] ? (hookCodes[nsName][method] || 'N/A') : 'N/A';
                const docs = allMethods[nsName] ? (allMethods[nsName][method] || {}) : {};
                
                const card = document.createElement('div');
                card.className = 'method-card';
                card.innerHTML = `
                    <h3>${method} <span class="hook-code">${hook}</span></h3>
                    <p class="method-desc">${docs.desc || 'Method documentation pending.'}</p>
                    <div class="method-sig">${docs.sig || nsName + '.' + method + '(...)'}</div>
                    ${docs.conformance ? '<span class="conformance-badge" style="background: ' + getConformanceColor(docs.conformance) + ';">' + docs.conformance + '</span>' : ''}
                    ${docs.example ? '<div class="code-block">' + docs.example + '</div>' : ''}
                `;
                methodList.appendChild(card);
            });
        }

        function populateConformanceLevels() {
            const conformanceLevels = {
                'L0': 'Core instruction set (no host hooks)',
                'L1': 'Memory, threading, and basic I/O',
                'L2': 'String operations and basic utilities',
                'L3': 'Math and DSP operations',
                'L4': 'Compression and binary manipulation',
                'L5': 'HTTP protocol and HTML templating',
                'L6': 'Cryptography, PKI, and authentication',
            };

            const container = document.getElementById('conformanceLevels');
            Object.entries(conformanceLevels).forEach(([level, desc]) => {
                const div = document.createElement('div');
                div.style.padding = '12px';
                div.style.marginBottom = '12px';
                div.style.background = getConformanceColor(level);
                div.style.borderRadius = '4px';
                div.style.borderLeft = '4px solid #667eea';
                div.innerHTML = `<strong>${level}:</strong> ${desc}`;
                container.appendChild(div);
            });
        }

        function populateExamples() {
            const examples = [
                { title: 'HTTP Request Handling', code: `Context.GetVerb(R0);           // GET, POST, etc.
Context.GetPath(R1);           // /api/users
Http.ReadHeader("Content-Type", R2);  // Lazy decode
Http.ReadBody(R3, 0, 4096);    // First 4KB of body
Http.GenerateResponse(200, headers[], body);` },
                { title: 'Authentication Flow', code: `Auth.ValidateCredentials("alice", "pass");
IF Auth.ValidateCredentials(...) THEN
  Auth.SwitchUserContext("user_42");
  Context.GetUser(R0);          // Now returns user_42
ENDIF` },
                { title: 'Compression', code: `Compress.BrotliCompress(input, 6);
Storage.Pipe(...);             // Send compressed
Compress.BrotliDecompress(data);  // On client side` },
                { title: 'Cryptographic Hashing', code: `Crypto.Sha256(input, R0);     // Hash userland-owned key
Crypto.HmacSha256(key, data, R1);  // Signed
// For system crypto, goes over FIFO via kernel API` },
            ];

            const container = document.getElementById('examplesContent');
            examples.forEach(ex => {
                const div = document.createElement('div');
                div.innerHTML = `
                    <h3>${ex.title}</h3>
                    <div class="code-block">${ex.code}</div>
                `;
                container.appendChild(div);
            });
        }

        function setupSearch() {
            const searchBox = document.getElementById('methodSearch');
            if (!searchBox) return;
            searchBox.addEventListener('input', (e) => {
                const query = e.target.value.toLowerCase();
                document.querySelectorAll('.method-card').forEach(card => {
                    const text = card.textContent.toLowerCase();
                    card.style.display = text.includes(query) ? 'block' : 'none';
                });
            });
        }

        function getConformanceColor(level) {
            const colors = {
                'L0': '#E8F4F8', 'L1': '#D0E8F2', 'L2': '#B8DCEC',
                'L3': '#A0D0E6', 'L4': '#88C4E0', 'L5': '#7CB8DA',
                'L6': '#7CB8D0',
            };
            return colors[level] || '#F0F0F0';
        }

        init();
    </script>
</body>
</html>
"""
    return html

print("Generating HTML reference...")
html_content = generate_html_reference()
with open("docs/PICOSCRIPT_REFERENCE.html", "w") as f:
    f.write(html_content)
print("✓ Generated docs/PICOSCRIPT_REFERENCE.html")
