#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static parity gate for host-hook namespace coverage.

This intentionally does not import or execute any PicoScript VM runtime.  It
only checks that every non-host-injected hook namespace in vm/pico_hooks.js has
at least one parity-test source reference, so new pure hook surfaces cannot be
added without making the parity runner acknowledge them.
"""

import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ROOT_PATH = Path(ROOT)
HOOKS_JS = ROOT_PATH / "vm" / "pico_hooks.js"
TESTS_DIR = ROOT_PATH / "tests"


# Host-injected or host-environment namespaces are intentionally outside this
# deterministic parity gate; see docs/NAMESPACE_STATUS.md.
ALLOWLISTED_NAMESPACES = {
    "Auth",         # Host identity provider / token state.
    "Context",      # Live request, user, certificate, and tracing context.
    "DateTime",     # Wall-clock time and date formatting depend on host state.
    "Environment",  # OS, process, CPU, memory, timezone, and hostname facts.
    "Kernel",       # IRQ, software IRQ, profiling, and tracing are PIOS hooks.
    "Locale",       # Host locale, formatting, and translation tables.
    "Net",          # Host network stack hooks; reserved even when absent here.
    "Req",          # Request metadata and body spans are supplied by the host.
    "Resp",         # Response status/header/body writes target the host stream.
    "X509",         # Certificates, trust chains, and key handles are host-owned.
    "Descriptor",   # Host descriptor metadata / bulk-transfer plumbing.
    "Lease",        # Host-enforced lease capabilities and lifetime validation.
    "Thread",       # Scheduler/preemption hints depend on the host scheduler.
}


# Pure namespaces must never be allowlisted; they need parity-test references.
PURE_NAMESPACES = {
    "Bits",
    "Compress",
    "Crypto",
    "Dot8",
    "Html",
    "Io",
    "Json",
    "Maths",
    "Memory",
    "Number",
    "Queue",
    "Span",
    "Storage",
    "String",
    "Template",
    "Utf8Reader",
    "Utf8Writer",
    "Xml",
}


def parse_hooks():
    text = HOOKS_JS.read_text(encoding="utf-8")
    by_code = re.search(r"BY_CODE:\s*\{(?P<body>.*?)\n\s*\}", text, re.S)
    assert by_code, "Could not find BY_CODE map in vm/pico_hooks.js"

    hooks = [
        (int(code, 16), name)
        for code, name in re.findall(r'0x([0-9A-Fa-f]+):\s*"([^"]+)"', by_code.group("body"))
    ]
    assert hooks, "No host hooks parsed from vm/pico_hooks.js BY_CODE"
    return sorted(hooks)


def namespace_of(hook_name):
    assert "." in hook_name, f"Hook name lacks namespace separator: {hook_name}"
    return hook_name.split(".", 1)[0]


def parity_test_text():
    gate_file = Path(__file__).resolve()
    test_files = [
        path
        for path in sorted(TESTS_DIR.glob("test_*.py"))
        if path.resolve() != gate_file
    ]
    assert test_files, "No parity test files found under tests/test_*.py"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in test_files)


def test_parity_runner_is_gatekeeper():
    illegal_allowlist = sorted(PURE_NAMESPACES & ALLOWLISTED_NAMESPACES)
    assert not illegal_allowlist, (
        "Pure namespaces must not be allowlisted: " + ", ".join(illegal_allowlist)
    )

    hooks = parse_hooks()
    text = parity_test_text()
    namespaces = sorted({namespace_of(name) for _, name in hooks})
    covered_namespaces = [
        namespace for namespace in namespaces
        if f"{namespace}." in text
    ]
    active_allowlist = sorted(set(namespaces) & ALLOWLISTED_NAMESPACES)
    missing = [
        namespace for namespace in namespaces
        if namespace not in ALLOWLISTED_NAMESPACES and namespace not in covered_namespaces
    ]

    print(
        "PARITY_GATE "
        f"total_hooks={len(hooks)} "
        f"total_namespaces={len(namespaces)} "
        f"covered_namespaces={len(covered_namespaces)} "
        f"allowlisted_namespaces={len(active_allowlist)}"
    )
    print("covered: " + ", ".join(covered_namespaces))
    print("allowlisted: " + ", ".join(active_allowlist))

    assert not missing, (
        "Host hook namespaces lack parity-test references: " + ", ".join(missing)
    )
    print("PASS: all non-allowlisted host hook namespaces have parity-test references")


if __name__ == "__main__":
    test_parity_runner_is_gatekeeper()
