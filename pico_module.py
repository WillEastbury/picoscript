#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PicoScript module container -- embedded, checked ABI version (INV-23).

Bytecode runs in-memory as a raw 32-bit word array (the VMs and the parity tests feed
that array directly, unchanged). When a program is *persisted or shipped* -- saved to a
PicoWAL card, written to disk, sent over the wire -- it must instead be wrapped in a
module container that carries, and is checked against, the ABI it was built for:

    [ MODULE_MAGIC, MODULE_ABI_VERSION, HOOK_TABLE_VERSION, word_count, <words...> ]

`load_module` refuses (raises ModuleAbiError) a container whose magic, ABI version, or
host-hook-table version does not match this runtime -- so a module built against an
older/newer ISA or a changed host-hook table cannot silently run on a mismatched VM.

The hook-table version is a content hash (FNV-1a/32) of the canonical "code:Ns.Method"
lines, so it bumps automatically whenever a hook is added, removed, or renumbered. The
SAME algorithm is implemented in vm/picovm.js (packModule/loadModule); a parity test
asserts the two runtimes compute the identical HOOK_TABLE_VERSION. (A C port follows the
same wire format; documented in docs/INVARIANTS.md.)
"""

from typing import List

from picoscript_lang import HOST_HOOK_CODES

MODULE_MAGIC = 0x50534331        # "PSC1" -- PicoScript container, format 1
MODULE_ABI_VERSION = 1           # bump on any incompatible ISA/bytecode change


class ModuleAbiError(Exception):
    """A persisted module's embedded ABI/hook-table version does not match this runtime."""


def _fnv1a32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def hook_table_version() -> int:
    """Content hash of the host-hook table. Canonical form: "code:Ns.Method" lines
    sorted by ascending code, joined by '\\n'. Mirrored byte-for-byte in vm/picovm.js."""
    lines = sorted(((code, f"{ns}.{method}") for (ns, method), code in HOST_HOOK_CODES.items()),
                   key=lambda t: t[0])
    payload = "\n".join(f"{code}:{name}" for code, name in lines)
    return _fnv1a32(payload.encode("utf-8"))


def pack_module(words: List[int]) -> List[int]:
    """Wrap a raw bytecode word array in a versioned, self-describing container."""
    return [MODULE_MAGIC & 0xFFFFFFFF,
            MODULE_ABI_VERSION & 0xFFFFFFFF,
            hook_table_version() & 0xFFFFFFFF,
            len(words) & 0xFFFFFFFF,
            *[w & 0xFFFFFFFF for w in words]]


def load_module(container: List[int]) -> List[int]:
    """Validate a module container and return its raw bytecode words, or raise
    ModuleAbiError on any magic / ABI-version / hook-table-version / length mismatch."""
    if len(container) < 4:
        raise ModuleAbiError(f"truncated module header ({len(container)} words < 4)")
    magic, abi, htv, count = container[0], container[1], container[2], container[3]
    if magic != MODULE_MAGIC:
        raise ModuleAbiError(f"bad module magic 0x{magic:08X} (expected 0x{MODULE_MAGIC:08X})")
    if abi != MODULE_ABI_VERSION:
        raise ModuleAbiError(f"ABI version mismatch: module={abi} runtime={MODULE_ABI_VERSION}")
    expect_htv = hook_table_version()
    if htv != expect_htv:
        raise ModuleAbiError(
            f"host hook table version mismatch: module=0x{htv:08X} runtime=0x{expect_htv:08X} "
            f"(the hook table changed; rebuild the module)")
    words = container[4:]
    if len(words) != count:
        raise ModuleAbiError(f"word count mismatch: header={count} actual={len(words)}")
    return list(words)
