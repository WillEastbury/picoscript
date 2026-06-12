#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picocapsule.py -- capsule manifest model + deterministic canonical-text
serializer/parser, and pack/card address helpers.

This is the PicoScript reference implementation of the capsule contract handed
to the PIOS build agent in docs/PIOS_CAPSULE_HANDOFF.md.  A capsule is a pack
namespace; card 0 holds the manifest in the frozen canonical text format (see
that doc, section 3).  serialize() is fully deterministic so card 0 is byte
stable, and parse(serialize(m)) == m for any manifest.

The browser mirror (vm/picocapsule.js) must emit byte-identical text so a
manifest authored in either runtime produces the same card 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# Capsule pack-id range (defaults; a deployment may widen these -- the manifest
# is authoritative, see the handoff doc).
CAPSULE_PACK_MIN = 1024
CAPSULE_PACK_MAX = 4095

# Default source/bytecode card pairing convention: program N.
SOURCE_BASE = 1000
CODE_BASE = 10000

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def source_for(n: int) -> int:
    """Default source card for program N (1000 + N)."""
    return SOURCE_BASE + n


def code_for(n: int) -> int:
    """Default bytecode card for program N (10000 + N)."""
    return CODE_BASE + n


# ── pack/card addressing ─────────────────────────────────────────────────────

def format_address(pack: int, card: int) -> str:
    """Canonical address: 'pack/card' (e.g. '1024/10001')."""
    return f"{pack}/{card}"


def parse_address(text: str) -> tuple:
    """Parse 'pack/card' or typed 'capsule:P/card:C' -> (pack, card)."""
    s = text.strip()
    left, sep, right = s.partition("/")
    if not sep:
        raise ValueError(f"bad address {text!r}: expected 'pack/card'")
    if ":" in left:                      # typed form: capsule:1024/card:10001
        pack = int(left.split(":", 1)[1])
        card = int(right.split(":", 1)[1])
    else:
        pack, card = int(left), int(right)
    return pack, card


def is_capsule_pack(pack: int) -> bool:
    return CAPSULE_PACK_MIN <= pack <= CAPSULE_PACK_MAX


# ── manifest model ───────────────────────────────────────────────────────────

@dataclass
class Process:
    name: str
    source: int
    bytecode: int
    io: Optional[str] = None        # "tcp/83" | "fifo/requests"
    entry: Optional[str] = None     # "http", ...


@dataclass
class Fifo:
    name: str
    frm: str
    to: str
    depth: int
    frame_max: int


@dataclass
class Manifest:
    name: str
    cards: str = "1001-20000"       # "<lo>-<hi>"
    principal: Optional[str] = None
    mem_kib: Optional[int] = None
    cpu_ms: Optional[int] = None
    fs: Optional[str] = None
    processes: List[Process] = field(default_factory=list)
    fifos: List[Fifo] = field(default_factory=list)

    # builder helpers (mirror the Capsule.* / Manifest.* primitives) -----------
    def process(self, name: str, source: int, bytecode: int,
                io: Optional[str] = None, entry: Optional[str] = None) -> "Manifest":
        self.processes.append(Process(name, source, bytecode, io, entry))
        return self

    def bind_tcp(self, port: int, process: str) -> "Manifest":
        for p in self.processes:
            if p.name == process:
                p.io = f"tcp/{port}"
                return self
        raise ValueError(f"bind: no process named {process!r}")

    def fifo(self, name: str, frm: str, to: str, depth: int, frame_max: int) -> "Manifest":
        self.fifos.append(Fifo(name, frm, to, depth, frame_max))
        return self


# ── deterministic canonical-text serializer (handoff doc section 3) ──────────

def _check_name(kind: str, name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"{kind} name {name!r} must match [A-Za-z0-9_-]+")


def serialize(m: Manifest) -> str:
    """Emit the frozen canonical manifest text (UTF-8, '\\n' endings).

    Header key order: capsule, name, [principal, mem_kib, cpu_ms, fs], cards.
    Then process blocks (declaration order), then ipc_fifo blocks. Trailing \\n.
    """
    _check_name("capsule", m.name)
    out: List[str] = ["capsule = on", f"name = {m.name}"]
    if m.principal is not None:
        out.append(f"principal = {m.principal}")
    if m.mem_kib is not None:
        out.append(f"mem_kib = {m.mem_kib}")
    if m.cpu_ms is not None:
        out.append(f"cpu_ms = {m.cpu_ms}")
    if m.fs is not None:
        out.append(f"fs = {m.fs}")
    out.append(f"cards = {m.cards}")
    for p in m.processes:
        _check_name("process", p.name)
        out.append("")
        out.append(f"process = {p.name}")
        out.append(f"  source = {p.source}")
        out.append(f"  bytecode = {p.bytecode}")
        if p.io is not None:
            out.append(f"  io = {p.io}")
        if p.entry is not None:
            out.append(f"  entry = {p.entry}")
    for fo in m.fifos:
        _check_name("ipc_fifo", fo.name)
        out.append("")
        out.append(f"ipc_fifo = {fo.name}")
        out.append(f"  from = {fo.frm}")
        out.append(f"  to = {fo.to}")
        out.append(f"  depth = {fo.depth}")
        out.append(f"  frame_max = {fo.frame_max}")
    return "\n".join(out) + "\n"


def parse(text: str) -> Manifest:
    """Parse canonical manifest text back into a Manifest (inverse of serialize)."""
    m = Manifest(name="")
    cur = None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.strip() == "":
            continue
        indented = line.startswith("  ")
        key, sep, val = line.strip().partition("=")
        if not sep:
            raise ValueError(f"bad manifest line {line!r}: expected 'key = value'")
        key, val = key.strip(), val.strip()
        if not indented:
            if key == "capsule":
                continue
            elif key == "name":
                m.name = val
            elif key == "principal":
                m.principal = val
            elif key == "mem_kib":
                m.mem_kib = int(val)
            elif key == "cpu_ms":
                m.cpu_ms = int(val)
            elif key == "fs":
                m.fs = val
            elif key == "cards":
                m.cards = val
            elif key == "process":
                cur = Process(val, 0, 0)
                m.processes.append(cur)
            elif key == "ipc_fifo":
                cur = Fifo(val, "", "", 0, 0)
                m.fifos.append(cur)
            else:
                raise ValueError(f"unknown manifest key {key!r}")
        else:
            if isinstance(cur, Process):
                if key == "source":
                    cur.source = int(val)
                elif key == "bytecode":
                    cur.bytecode = int(val)
                elif key == "io":
                    cur.io = val
                elif key == "entry":
                    cur.entry = val
                else:
                    raise ValueError(f"unknown process key {key!r}")
            elif isinstance(cur, Fifo):
                if key == "from":
                    cur.frm = val
                elif key == "to":
                    cur.to = val
                elif key == "depth":
                    cur.depth = int(val)
                elif key == "frame_max":
                    cur.frame_max = int(val)
                else:
                    raise ValueError(f"unknown ipc_fifo key {key!r}")
            else:
                raise ValueError(f"indented line {line!r} outside a block")
    return m
