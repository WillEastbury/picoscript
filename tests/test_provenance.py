#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generated toC/toJS artefacts carry deterministic PicoScript provenance."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import (  # noqa: E402
    PICOSCRIPT_ABI_VERSION,
    PICOSCRIPT_COMPILER_VERSION,
    lower_to_c,
    lower_to_js,
)

HASH_RE = re.compile(r"source_hash=([0-9a-f]{16})")


def source_hash(text: str) -> str:
    m = HASH_RE.search(text)
    assert m, f"missing 16-hex source hash in:\n{text[:200]}"
    return m.group(1)


def assert_header(text: str, target_profile: str) -> str:
    header = "\n".join(text.splitlines()[:3])
    assert PICOSCRIPT_COMPILER_VERSION in header, header
    assert f"abi_version={PICOSCRIPT_ABI_VERSION}" in header, header
    assert f"target_profile={target_profile}" in header, header
    return source_hash(header)


def main():
    prog = "int x=5; Io.WriteByte(x);"
    changed = "int x=6; Io.WriteByte(x);"

    il = compile_c(prog)
    c_hash = assert_header(lower_to_c(il, func_name="pico_prov", emit_main=True), "c")
    js_hash = assert_header(lower_to_js(il, module_name="pico_prov"), "js")
    assert c_hash == js_hash, (c_hash, js_hash)

    c_hash_again = assert_header(
        lower_to_c(compile_c(prog), func_name="pico_prov_again", emit_main=True), "c"
    )
    js_hash_again = assert_header(lower_to_js(compile_c(prog), module_name="pico_prov_again"), "js")
    assert c_hash == c_hash_again == js_hash_again, (c_hash, c_hash_again, js_hash_again)

    changed_hash = assert_header(
        lower_to_c(compile_c(changed), func_name="pico_prov_changed", emit_main=True), "c"
    )
    assert changed_hash != c_hash, (changed_hash, c_hash)

    print(
        f"PASS provenance: compiler={PICOSCRIPT_COMPILER_VERSION} "
        f"abi={PICOSCRIPT_ABI_VERSION} source_hash={c_hash}"
    )


if __name__ == "__main__":
    main()
