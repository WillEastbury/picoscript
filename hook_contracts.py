#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INV-4/INV-5 host-hook contracts.

HOOK_CONTRACTS is the machine-readable contract table for every host hook in
vm/pico_hooks.js.  Each entry declares the hook namespace, method, required
capability class, and whether the hook creates arena-backed output (a new span
or arena bytes), making hot-path allocation explicit instead of hidden.
"""

import os
import re

_CAP_BY_NS = {
    "Kernel": "KERNEL",
    "Queue": "QUEUE",
    "Random": "RANDOM",
    "Req": "NET",
    "Resp": "NET",
    "Net": "NET",
    "Storage": "STORAGE",
    "DateTime": "TIME",
    "Context": "CONTEXT",
    "Auth": "AUTH",
    "X509": "AUTH",
    "Environment": "ENV",
    "Locale": "ENV",
    "Gpio": "GPIO",
    "Pack": "CAPSULE", "Card": "CAPSULE", "Fifo": "CAPSULE",
}

_HOOK_RE = re.compile(r'0x([0-9A-Fa-f]+):\s*"([^"]+)"')
_BY_CODE_RE = re.compile(r'BY_CODE:\s*\{(?P<body>.*?)\n\s*\}', re.S)


# Hooks observed to allocate/bump arena-backed storage in the Python/JS/C VMs,
# plus conservative declarations for same-family hooks whose contract is to
# return arena bytes/spans when host-provided.
_ALLOCATING_HOOKS = frozenset({
    "Memory.ArenaAlloc",
    "Span.Make",
    "Span.Slice",
    "Span.Materialize",
    "Utf8Writer.ToSpan",
    "Storage.GetSchemaForPack",
    "Storage.GetFieldStr",
    "String.Concat",
    "String.Substring",
    "String.Replace",
    "String.ToUpper",
    "String.ToLower",
    "String.Trim",
    "String.Split",
    "String.Join",
    "Number.ToString",
    "Number.ToHex",
    "Number.ToOctal",
    "Number.ToBinary",
    "DateTime.Format",
    "Locale.GetCurrentLocale",
    "Locale.FormatCurrency",
    "Locale.FormatNumber",
    "Locale.FormatDate",
    "Locale.FormatTime",
    "Locale.Translate",
    "Environment.GetOsVersion",
    "Environment.GetHostname",
    "Environment.GetTimeZone",
    "Context.GetVerb",
    "Context.GetPath",
    "Context.GetHost",
    "Context.GetRemoteAddr",
    "Context.GetUser",
    "Context.GetPermissions",
    "Context.GetHeaders",
    "Context.GetQueryString",
    "Context.GetBody",
    "Context.GetRequestId",
    "Context.GetClientCert",
    "Context.GetTraceId",
    "Crypto.Sha256",
    "Crypto.Sha512",
    "Crypto.Blake2b",
    "Crypto.Blake3",
    "Crypto.HmacSha256",
    "Crypto.HmacSha512",
    "Crypto.Sign",
    "Crypto.Encrypt",
    "Crypto.Decrypt",
    "Crypto.GenerateKeyPair",
    "Crypto.DeriveKey",
    "Crypto.RandomBytes",
    "Crypto.Md5",
    "Crypto.Sha1",
    "Http.ParseQuery",
    "Http.ParseForm",
    "Http.ParseJson",
    "Http.EncodeJson",
    "Html.GetAttribute",
    "Html.ParseTree",
    "Html.Encode",
    "Html.Decode",
    "Html.Serialize",
    "Html.QuerySelector",
    "Template.Compile",
    "Template.Render",
    "X509.FetchCertificate",
    "X509.GenerateCSR",
    "X509.GenerateKeyPair",
    "X509.GetCertInfo",
    "X509.GetKeyHandle",
    "Auth.GetUserCredentials",
    "Auth.GetUserPermissions",
    "Auth.RequestToken",
    "Auth.GetToken",
    "Auth.RefreshToken",
    "Card.Read",
    "Card.Address",
    "Fifo.Recv",
})

_ALLOCATING_NAMESPACES = frozenset({"Compress", "Json", "Xml"})
_VALID_CAPABILITIES = frozenset({
    "KERNEL", "QUEUE", "RANDOM", "STORAGE", "TIME", "NET",
    "CONTEXT", "AUTH", "ENV", "GPIO", "CAPSULE", "pure",
})


def _split_name(name):
    ns, sep, method = name.partition(".")
    if not sep or not ns or not method:
        raise ValueError(f"invalid hook name: {name!r}")
    return ns, method


def _capability_for(ns, method):
    """Mirror picoscript_vm.hook_cap, vm/picovm.js hookCap and C pv_hook_cap."""
    if ns == "Maths" and method in ("Random", "RandomRange"):
        return "RANDOM"
    if ns == "Crypto" and method == "RandomBytes":
        return "RANDOM"
    if ns == "Http" and method in ("ReadHeader", "ReadBody", "GenerateHeaders", "GenerateResponse"):
        return "NET"
    return _CAP_BY_NS.get(ns, "pure")


def _allocates_arena(ns, method):
    name = f"{ns}.{method}"
    return ns in _ALLOCATING_NAMESPACES or name in _ALLOCATING_HOOKS


def _load_hooks_by_code():
    hooks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm", "pico_hooks.js")
    with open(hooks_path, "r", encoding="utf-8") as f:
        text = f.read()
    match = _BY_CODE_RE.search(text)
    if not match:
        raise RuntimeError(f"BY_CODE block not found in {hooks_path}")

    by_code = {}
    by_name = {}
    for code_hex, name in _HOOK_RE.findall(match.group("body")):
        code = int(code_hex, 16)
        if code in by_code:
            raise ValueError(f"duplicate hook code 0x{code:X}")
        if name in by_name:
            raise ValueError(f"duplicate hook name {name!r}")
        by_code[code] = name
        by_name[name] = code
    if not by_code:
        raise RuntimeError(f"no hooks found in {hooks_path}")
    return dict(sorted(by_code.items()))


def _build_contracts():
    contracts = {}
    for code, name in _load_hooks_by_code().items():
        ns, method = _split_name(name)
        capability = _capability_for(ns, method)
        if capability not in _VALID_CAPABILITIES:
            raise ValueError(f"invalid capability {capability!r} for {name}")
        contracts[name] = {
            "code": code,
            "namespace": ns,
            "method": method,
            "capability": capability,
            "allocates": bool(_allocates_arena(ns, method)),
        }
    return contracts


HOOK_CONTRACTS = _build_contracts()


def capability_of(name: str) -> str:
    """Return the required capability class string for a hook name."""
    return HOOK_CONTRACTS[name]["capability"]


def allocates(name: str) -> bool:
    """Return True when a hook creates arena-backed output."""
    return HOOK_CONTRACTS[name]["allocates"]
