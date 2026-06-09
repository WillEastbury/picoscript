#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate METHOD_REFERENCE.md and HTML from picoscript_lang.py"""

import sys
from pathlib import Path

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))

import picoscript_lang as lang

# Extract data
ns_map = lang.NAMESPACE_MAP
hook_codes = lang.HOST_HOOK_CODES

# Conformance level mapping
conformance_map = {
    'Kernel': 'L6',
    'Queue': 'L5',
    'Random': 'L4',
    'Memory': 'L4',
    'Span': 'L4',
    'Descriptor': 'L4',
    'Lease': 'L4',
    'Storage': 'L5',
    'Thread': 'L5',
    'String': 'L2',
    'Number': 'L2',
    'Maths': 'L2',
    'DateTime': 'L2',
    'Locale': 'L2',
    'Environment': 'L3',
    'Context': 'L3',
    'Crypto': 'L6',
    'Math': 'L1',
    'Flow': 'L1',
    'Dsp': 'L0',
    'Net': 'L1',
}

# Generate markdown
markdown = """# PicoScript Method Reference

**Version:** v0.3 (Lease-first, Case-insensitive v2 Language)

## Overview

This document provides a comprehensive reference for all PicoScript methods, organized by namespace. Each method shows:

- **Opcode**: Internal bytecode instruction
- **Hook Code**: Hexadecimal encoding for host hooks (reserved imm16 range)
- **v2 Syntax**: Case-insensitive, block-structured syntax example
- **Conformance Level**: L0 (minimal) through L6 (full security/crypto)

## Table of Contents

"""

# Build TOC
for ns_name in sorted(ns_map.keys()):
    markdown += f"- [{ns_name}](#{ns_name.lower()})\n"

markdown += "\n---\n\n"

# Generate method table for each namespace
all_methods = []
for ns_name in sorted(ns_map.keys()):
    ns = ns_map[ns_name]
    conformance = conformance_map.get(ns_name, 'L0')
    
    markdown += f"## {ns_name}\n\n"
    markdown += f"**Conformance Level:** {conformance}  \n"
    markdown += f"**Methods:** {len(ns)}\n\n"
    
    # Summary
    summaries = {
        'Kernel': "Core kernel interaction: process management, IPC, system control.",
        'Queue': "Queue operations: async task enqueue/dequeue, batch operations.",
        'Random': "Cryptographically-seeded randomness from host startup.",
        'Memory': "Arena allocation and lease-based typed access primitives.",
        'Span': "Span descriptor (offset + length) for zero-copy access.",
        'Descriptor': "Data descriptor with flags, TTL, reference counting.",
        'Lease': "Lease lifecycle: acquire, validate, release, stats.",
        'Storage': "Persistent storage: pack/card schema, CRUD, query.",
        'Thread': "Thread preemption hints and cooperative yielding.",
        'String': "String manipulation: concat, substring, split, trim, case conversion.",
        'Number': "Numeric parsing, formatting, and conversion.",
        'Math': "Mathematical ALU operations: add, subtract, multiply, divide.",
        'Flow': "Control flow: jumps, branches, function calls, returns.",
        'Dsp': "Digital signal processing: neural network ops, matrix operations.",
        'Net': "HTTP response framing: status, headers, body, close.",
        'Maths': "Mathematical functions: sqrt, trig, log, GCD, LCM.",
        'DateTime': "Date/time: current, components, timestamp, formatting.",
        'Locale': "Locale management: get/set, format/parse, language/region.",
        'Environment': "System: env vars, time, memory/CPU load, hostname, version.",
        'Context': "Execution context: user, permissions, request metadata, scratch.",
        'Crypto': "Cryptography: userland hashing, kernel-wrapped keyed ops.",
    }
    
    if ns_name in summaries:
        markdown += f"{summaries[ns_name]}\n\n"
    
    # Methods table
    markdown += "| Method | Opcode | Hook Code | v2 Example |\n"
    markdown += "|--------|--------|-----------|----------|\n"
    
    for method_name in sorted(ns.keys()):
        opcode_val = ns[method_name]
        if isinstance(opcode_val, tuple):
            opcode_str = f"0x{opcode_val[0]:02X}+0x{opcode_val[1]:02X}"
        else:
            opcode_str = f"0x{opcode_val:02X}"
        
        hook_code = hook_codes.get((ns_name, method_name), None)
        if hook_code is not None:
            hook_code_str = f"0x7{hook_code:03X}"
        else:
            hook_code_str = "-"
        
        # v2 syntax example
        v2_ex = f"{ns_name}.{method_name}(...)"
        
        markdown += f"| {method_name} | {opcode_str} | {hook_code_str} | `{v2_ex}` |\n"
        all_methods.append((ns_name, method_name, hook_code, conformance))
    
    markdown += "\n"

# Add summary section
markdown += "---\n\n"
markdown += "## Summary by Conformance Level\n\n"

for level in ['L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6']:
    methods_at_level = [m for m in all_methods if m[3] == level]
    if methods_at_level:
        markdown += f"### {level}: {len(methods_at_level)} methods\n\n"
        for ns, method, hook, _ in sorted(methods_at_level):
            hook_str = f"0x7{hook:03X}" if hook else "core"
            markdown += f"- {ns}.{method} ({hook_str})\n"
        markdown += "\n"

# Add hook code allocation table
markdown += """---

## Hook Code Allocation

Host hooks use reserved imm16 range 0x7000-0x7FFF:

| Range | Namespace | Count | Purpose |
|-------|-----------|-------|---------|
| 0x7001-0x7006 | Kernel | 6 | Process, IPC |
| 0x7010-0x7014 | Queue | 5 | Task queue |
| 0x7020 | Random | 1 | RNG |
| 0x7030-0x7033 | Memory | 4 | Arena |
| 0x7040-0x7041 | Span | 2 | Spans |
| 0x7050-0x7055 | Descriptor | 6 | Descriptors |
| 0x7058-0x705D | Lease | 6 | Leases |
| 0x7060-0x7067 | Storage | 8 | Cards/packs |
| 0x7070 | Thread | 1 | Preemption |
| 0x7080-0x708B | String | 12 | Strings |
| 0x7090-0x709A | Number | 11 | Numbers |
| 0x70A0-0x70AB | Maths | 12 | Math |
| 0x70B0-0x70BA | DateTime | 11 | Date/time |
| 0x70C0-0x70C6 | Locale | 7 | Locale |
| 0x70D0-0x70D8 | Environment | 9 | Environment |
| 0x70E0-0x70EE | Context | 15 | Context |
| 0x70F0-0x70FE | Crypto | 15 | Crypto |

**Total:** {total} methods across {namespaces} namespaces.

## IDE Code Completion

### When User Types Namespace Dot:

```
String.<COMPLETIONS>
  .Concat(s1, s2) -> string
  .Length(s) -> int
  .Substring(s, start, len) -> string
  .IndexOf(s, substr) -> int
  .Split(s, delim) -> array
  ...
```

### Syntax Highlighting

```
KEYWORDS:        IF THEN ELSE ENDIF WHILE ENDWHILE FOREACH AS IN 
                 ENDFOREACH SWITCH CASE ENDSWITCH LET RETURN
NAMESPACES:      String Number Maths DateTime Locale Environment 
                 Context Crypto Kernel Queue Memory Span Descriptor Lease Storage
METHODS:         .MethodName(...) via dot notation
IDENTIFIERS:     Case-insensitive (all normalized to lowercase)
LITERALS:        "string" 123 3.14 true false
OPERATORS:       = + - * / % < > <= >= == != AND OR NOT
COMMENTS:        // rest of line
```

### Performance Annotations

Editors may color-code methods:

- **Green (FAST)**: O(1) userland - String.Length, Number.Parse
- **Yellow (LAZY)**: Cached on first call - Context.GetHeaders
- **Red (FIFO)**: Kernel IPC needed - Crypto.Sign, Kernel operations

## v2 Syntax Examples

### String Operations

```
LET s1 = "Hello"
LET s2 = "World"
LET combined = String.Concat(s1, " ", s2)
LET len = String.Length(combined)
IF len > 10 THEN
  LET upper = String.ToUpper(combined)
ENDIF
```

### Context Access (Lazy-Decoded)

```
LET user = Context.GetUser()        -- Expensive on first call
LET verb = Context.GetVerb()        -- Fast: pre-cached
LET headers = Context.GetHeaders()  -- Expensive on first call
```

### Control Flow

```
FOREACH item AS x IN items THEN
  IF String.Length(x) > 0 THEN
    Queue.Enqueue(x)
  ENDIF
ENDFOREACH
```

### Memory/Lease

```
LET lease = Memory.ArenaAlloc(1024)
LET handle = Lease.Acquire(lease)
Lease.Validate(handle)
Lease.Release(handle)
```

### Cryptography

```
-- Userland key (fast, no kernel mediation)
LET key = "my-session-token"
LET sig = Crypto.HmacSha256(key, "data")

-- System key (kernel FIFO, audit-logged)
LET sys_sig = Crypto.Sign(system_handle, "data")
```

---

Generated from `picoscript_lang.py` (v0.3)
""".replace("{total}", str(len(all_methods))).replace("{namespaces}", str(len(ns_map)))

# Write markdown
md_path = Path('docs/METHOD_REFERENCE.md')
md_path.write_text(markdown, encoding='utf-8')
print(f"[OK] {md_path}")

# Generate HTML
html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PicoScript Method Reference</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      color: #333;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      padding: 20px;
    }
    .container {
      max-width: 1400px;
      margin: 0 auto;
      background: white;
      border-radius: 12px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
      overflow: hidden;
      display: flex;
      min-height: 80vh;
    }
    .sidebar {
      width: 250px;
      background: #f5f5f5;
      border-right: 1px solid #ddd;
      overflow-y: auto;
      padding: 20px;
    }
    .sidebar h3 {
      font-size: 14px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 10px;
      margin-top: 20px;
    }
    .sidebar h3:first-child {
      margin-top: 0;
    }
    .sidebar a {
      display: block;
      padding: 8px 12px;
      color: #667eea;
      text-decoration: none;
      font-size: 13px;
      border-radius: 4px;
      margin-bottom: 4px;
      transition: all 0.2s;
    }
    .sidebar a:hover {
      background: #e0e0e0;
      color: #764ba2;
    }
    .sidebar a.active {
      background: #667eea;
      color: white;
    }
    .main {
      flex: 1;
      overflow-y: auto;
      padding: 40px;
    }
    h1 {
      color: #667eea;
      margin-bottom: 10px;
      font-size: 32px;
    }
    .version {
      color: #999;
      font-size: 13px;
      margin-bottom: 30px;
    }
    h2 {
      color: #764ba2;
      margin-top: 40px;
      margin-bottom: 20px;
      font-size: 24px;
      border-bottom: 2px solid #667eea;
      padding-bottom: 10px;
    }
    h2:first-of-type {
      margin-top: 0;
    }
    .namespace-meta {
      display: flex;
      gap: 20px;
      margin-bottom: 20px;
      font-size: 13px;
    }
    .conformance {
      padding: 4px 8px;
      background: #e3f2fd;
      color: #1976d2;
      border-radius: 4px;
      font-weight: 600;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 30px;
      font-size: 13px;
    }
    th {
      background: #f5f5f5;
      border: 1px solid #ddd;
      padding: 12px;
      text-align: left;
      font-weight: 600;
      color: #333;
    }
    td {
      border: 1px solid #eee;
      padding: 10px 12px;
      vertical-align: top;
    }
    tr:hover {
      background: #fafafa;
    }
    code {
      background: #f5f5f5;
      padding: 2px 6px;
      border-radius: 3px;
      font-family: "Monaco", "Menlo", "Ubuntu Mono", monospace;
      font-size: 12px;
      color: #d63384;
    }
    .method-name {
      font-weight: 600;
      color: #333;
    }
    .hook-code {
      font-family: monospace;
      background: #f0f0f0;
      padding: 2px 6px;
      border-radius: 3px;
      color: #666;
    }
    .summary-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 20px;
      margin: 20px 0;
    }
    .summary-item {
      background: #f9f9f9;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 15px;
    }
    .summary-item h3 {
      color: #667eea;
      margin-bottom: 10px;
      font-size: 14px;
    }
    .summary-item ul {
      list-style: none;
      font-size: 12px;
    }
    .summary-item li {
      padding: 4px 0;
      color: #666;
    }
    .summary-item li code {
      color: #764ba2;
    }
    pre {
      background: #f5f5f5;
      border: 1px solid #ddd;
      border-radius: 4px;
      padding: 12px;
      overflow-x: auto;
      font-size: 12px;
      line-height: 1.5;
      margin: 15px 0;
    }
    .nav-buttons {
      display: flex;
      gap: 10px;
      margin-top: 20px;
      padding-top: 20px;
      border-top: 1px solid #ddd;
    }
    button {
      padding: 8px 16px;
      border: 1px solid #ddd;
      border-radius: 4px;
      background: #f5f5f5;
      cursor: pointer;
      font-size: 13px;
      transition: all 0.2s;
    }
    button:hover {
      background: #e0e0e0;
      border-color: #999;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="sidebar">
      <h3>Namespaces</h3>
      <div id="namespace-links"></div>
      <h3>Conformance</h3>
      <div id="conformance-links"></div>
    </div>
    <div class="main">
      <h1>PicoScript Method Reference</h1>
      <p class="version">Version v0.3 (Lease-first, Case-insensitive v2 Language)</p>
      
      <p style="color: #666; margin-bottom: 20px;">
        Comprehensive reference for all PicoScript methods. Choose a namespace from the left sidebar or search below.
      </p>
      
      <input type="text" id="search" placeholder="Search methods..." 
        style="width: 300px; padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; margin-bottom: 20px;">
      
      <div id="content"></div>
    </div>
  </div>

  <script>
    const data = """ + __import__('json').dumps(all_methods) + """;
    
    function renderNamespaces() {
      const namespaces = [...new Set(data.map(m => m[0]))].sort();
      const sidebar = document.getElementById('namespace-links');
      namespaces.forEach(ns => {
        const a = document.createElement('a');
        a.href = '#' + ns.toLowerCase();
        a.textContent = ns;
        a.onclick = (e) => {
          e.preventDefault();
          showNamespace(ns);
          document.querySelectorAll('.sidebar a').forEach(x => x.classList.remove('active'));
          a.classList.add('active');
        };
        sidebar.appendChild(a);
      });
    }
    
    function renderConformance() {
      const levels = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6'];
      const sidebar = document.getElementById('conformance-links');
      levels.forEach(level => {
        const count = data.filter(m => m[3] === level).length;
        if (count > 0) {
          const a = document.createElement('a');
          a.href = '#';
          a.textContent = level + ' (' + count + ')';
          a.onclick = (e) => {
            e.preventDefault();
            showConformance(level);
          };
          sidebar.appendChild(a);
        }
      });
    }
    
    function showNamespace(ns) {
      const methods = data.filter(m => m[0] === ns);
      const content = document.getElementById('content');
      const conformance = methods.length > 0 ? methods[0][3] : 'L0';
      
      let html = '<h2>' + ns + '</h2>';
      html += '<div class="namespace-meta">';
      html += '<div>Methods: <strong>' + methods.length + '</strong></div>';
      html += '<div class="conformance">' + conformance + '</div>';
      html += '</div>';
      html += '<table><thead><tr><th>Method</th><th>Hook Code</th><th>v2 Example</th></tr></thead><tbody>';
      
      methods.forEach(m => {
        const [nsName, method, hook, conf] = m;
        const hookStr = hook !== null ? ('0x7' + hook.toString(16).padStart(3, '0').toUpperCase()) : '-';
        html += '<tr><td class="method-name">' + method + '</td>';
        html += '<td><code class="hook-code">' + hookStr + '</code></td>';
        html += '<td><code>' + nsName + '.' + method + '(...)</code></td></tr>';
      });
      
      html += '</tbody></table>';
      content.innerHTML = html;
    }
    
    function showConformance(level) {
      const methods = data.filter(m => m[3] === level);
      const content = document.getElementById('content');
      const byNs = {};
      
      methods.forEach(m => {
        if (!byNs[m[0]]) byNs[m[0]] = [];
        byNs[m[0]].push(m);
      });
      
      let html = '<h2>Conformance Level ' + level + '</h2>';
      html += '<p>' + methods.length + ' methods</p>';
      html += '<div class="summary-list">';
      
      Object.keys(byNs).sort().forEach(ns => {
        html += '<div class="summary-item">';
        html += '<h3>' + ns + '</h3>';
        html += '<ul>';
        byNs[ns].forEach(m => {
          html += '<li><code>' + m[1] + '</code></li>';
        });
        html += '</ul></div>';
      });
      
      html += '</div>';
      content.innerHTML = html;
    }
    
    document.getElementById('search').addEventListener('input', (e) => {
      const query = e.target.value.toLowerCase();
      const filtered = data.filter(m => 
        m[0].toLowerCase().includes(query) || 
        m[1].toLowerCase().includes(query)
      );
      
      if (filtered.length === 0) {
        document.getElementById('content').innerHTML = '<p style="color: #999;">No methods found.</p>';
        return;
      }
      
      const byNs = {};
      filtered.forEach(m => {
        if (!byNs[m[0]]) byNs[m[0]] = [];
        byNs[m[0]].push(m);
      });
      
      let html = '<p>' + filtered.length + ' methods found</p>';
      Object.keys(byNs).sort().forEach(ns => {
        html += '<h3>' + ns + '</h3><table><thead><tr><th>Method</th><th>Hook</th></tr></thead><tbody>';
        byNs[ns].forEach(m => {
          const hookStr = m[2] !== null ? ('0x7' + m[2].toString(16).padStart(3, '0').toUpperCase()) : '-';
          html += '<tr><td>' + m[1] + '</td><td><code>' + hookStr + '</code></td></tr>';
        });
        html += '</tbody></table>';
      });
      
      document.getElementById('content').innerHTML = html;
    });
    
    renderNamespaces();
    renderConformance();
    showNamespace('Kernel');
    document.querySelectorAll('.sidebar a')[0].classList.add('active');
  </script>
</body>
</html>
"""

# Write HTML
html_path = Path('docs/METHOD_REFERENCE.html')
html_path.write_text(html, encoding='utf-8')
print(f"[OK] {html_path}")

print(f"  Total: {len(all_methods)} methods")
print(f"  Namespaces: {len(ns_map)}")
print(f"  Hook codes assigned: {len([h for h in hook_codes.values() if h])}")
