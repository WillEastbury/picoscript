#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INV-4/INV-5 hook contract table checks."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hook_contracts import HOOK_CONTRACTS, allocates, capability_of  # noqa: E402

HOOK_RE = re.compile(r'0x([0-9A-Fa-f]+):\s*"([^"]+)"')
BY_CODE_RE = re.compile(r'BY_CODE:\s*\{(?P<body>.*?)\n\s*\}', re.S)
VALID_CAPABILITIES = {
    "KERNEL", "QUEUE", "RANDOM", "STORAGE", "TIME", "NET",
    "CONTEXT", "AUTH", "ENV", "GPIO", "CAPSULE", "DEVICE", "DMA", "EVENT", "UI", "pure",
}


def hooks_from_js():
    with open(os.path.join(ROOT, "vm", "pico_hooks.js"), "r", encoding="utf-8") as f:
        text = f.read()
    match = BY_CODE_RE.search(text)
    assert match, "BY_CODE block not found"
    return {name: int(code, 16) for code, name in HOOK_RE.findall(match.group("body"))}


def assert_contract_shape(name, code, contract):
    ns, method = name.split(".", 1)
    assert contract["code"] == code, f"{name} code mismatch"
    assert contract["namespace"] == ns, f"{name} namespace mismatch"
    assert contract["method"] == method, f"{name} method mismatch"
    assert contract["capability"] in VALID_CAPABILITIES, f"{name} invalid capability"
    assert isinstance(contract["allocates"], bool), f"{name} allocates is not bool"


def main():
    hooks = hooks_from_js()
    missing = sorted(set(hooks) - set(HOOK_CONTRACTS))
    extra = sorted(set(HOOK_CONTRACTS) - set(hooks))
    assert not missing, f"missing hook contracts: {missing}"
    assert not extra, f"extra hook contracts: {extra}"

    for name, code in hooks.items():
        assert_contract_shape(name, code, HOOK_CONTRACTS[name])

    checks = {
        "Storage.AddCard": ("STORAGE", False),
        "String.Concat": ("pure", True),
        "Bits.And": ("pure", False),
        "Maths.Random": ("RANDOM", False),
        "Crypto.RandomBytes": ("RANDOM", True),
        "Crypto.Sha256": ("pure", True),
        "Number.Parse": ("pure", False),
    }
    for name, (capability, does_allocate) in checks.items():
        assert capability_of(name) == capability, f"{name} capability"
        assert allocates(name) is does_allocate, f"{name} allocates"

    print(f"PASS hook contracts: {len(HOOK_CONTRACTS)} hooks covered")



def test_main():
    main()

if __name__ == "__main__":
    main()
