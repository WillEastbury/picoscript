#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_ext_hostcall.py -- extended-hostcall (>=0x100) emit + dispatch.

Hooks >= 0x100 (Http.*, Auth.*, Html.*, Compress.*, ...) do not fit the compact
single-NOOP host-hook encoding `0x7000 | (hook & 0xFF)`: the high byte of a
>=0x100 hook collides with the 0x7000 marker, so dispatch `(imm & 0xFF00)==0x7000`
silently fails and the call is dropped.  The extended encoding uses a free NOOP
marker page `EXT_HOST_HOOK_BASE = 0x6000` carrying a 12-bit hook id (reaches
0x000..0xFFF).  Hooks <= 0xFF keep the compact 0x7000 page (byte-identical to old
bytecode/cards), so this change is purely additive; VM dispatch accepts both pages.

Run: python tests/test_ext_hostcall.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import picoscript as isa  # noqa: E402
from picoscript_lang import HOST_HOOK_BASE, EXT_HOST_HOOK_BASE, HOST_HOOK_CODES  # noqa: E402
from picoscript_il import ILBuilder, lower_to_bytecode  # noqa: E402
from picoscript_vm import PicoVM, run_words  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if cond:
        passed += 1
    else:
        failed += 1


E = isa.encode_instruction
CLOSE = E(isa.OP_NOOP, imm16=0xC000)   # Net.Close -> halt

# Pick a real >=0x100 hook and a real <=0xFF hook from the live table.
HTTP_PARSE = HOST_HOOK_CODES[("Http", "ParseQuery")]
RANDOM_U32 = HOST_HOOK_CODES[("Random", "U32")]
check("Http.ParseQuery is a >=0x100 hook", HTTP_PARSE >= 0x100)
check("Random.U32 is a <=0xFF hook", RANDOM_U32 <= 0xFF)


def host_word(words):
    """The single NOOP host-hook word in a 1-host-call program (skip net markers)."""
    for w in words:
        if (w >> 28) == isa.OP_NOOP:
            imm = w & 0xFFFF
            if (imm & 0xF000) in (EXT_HOST_HOOK_BASE, HOST_HOOK_BASE) or (imm & 0xFF00) == HOST_HOOK_BASE:
                return w
    return 0


# ---- 1. IL emit path: >=0x100 -> ext page (0x6000), <=0xFF -> compact (0x7000).
b = ILBuilder()
r = b.vreg("r")
b.host("Http", "ParseQuery", args=(r,), dst=r)
ext_imm = host_word(lower_to_bytecode(b.insts, opt=False)) & 0xFFFF
check("ext emit uses 0x6000 page", (ext_imm & 0xF000) == EXT_HOST_HOOK_BASE)
check("ext emit carries full 12-bit hook", (ext_imm & 0x0FFF) == HTTP_PARSE)

b2 = ILBuilder()
r2 = b2.vreg("r")
b2.host("Random", "U32", args=(), dst=r2)
cmp_imm = host_word(lower_to_bytecode(b2.insts, opt=False)) & 0xFFFF
check("compact emit uses 0x7000 page", (cmp_imm & 0xFF00) == HOST_HOOK_BASE)
check("compact emit unchanged (0x7000|hook)", cmp_imm == (HOST_HOOK_BASE | RANDOM_U32))


# ---- 2. VM dispatch: a >=0x100 ext hook reaches a registered host handler.
seen = {}


def on_parsequery(vm, rd, rs1, rs2, imm16):
    seen["imm"], seen["rd"], seen["rs1"], seen["rs2"] = imm16, rd, rs1, rs2
    vm.regs[rd] = 0xABCD


prog = [
    E(isa.OP_NOOP, rd=3, rs1=1, rs2=2, imm16=EXT_HOST_HOOK_BASE | HTTP_PARSE),
    CLOSE,
]
vm = PicoVM()
vm.host.register("Http", "ParseQuery", on_parsequery)
vm.run(prog)
check("ext hook dispatched to handler", seen.get("imm") == (EXT_HOST_HOOK_BASE | HTTP_PARSE))
check("ext hook decoded rd/rs1/rs2", (seen.get("rd"), seen.get("rs1"), seen.get("rs2")) == (3, 1, 2))
check("ext handler wrote result reg", vm.regs[3] == 0xABCD)
# Documents the bug this fixes: the old compact decode could never match a 0x6134 word.
check("compact decode would miss the ext word",
      ((EXT_HOST_HOOK_BASE | HTTP_PARSE) & 0xFF00) != HOST_HOOK_BASE)


# ---- 3. The ext page can also carry a <=0xFF hook; parity with the compact page.
#         Random.U32 via 0x6020 (ext) must equal 0x7020 (compact): fresh VM => same
#         RNG seed, same destination register, same produced value.
ext_rng = run_words([E(isa.OP_NOOP, rd=1, imm16=EXT_HOST_HOOK_BASE | RANDOM_U32), CLOSE])
cmp_rng = run_words([E(isa.OP_NOOP, rd=1, imm16=HOST_HOOK_BASE | RANDOM_U32), CLOSE])
check("ext Random.U32 produced a value", ext_rng.regs[1] != 0)
check("ext == compact Random.U32 (page-agnostic dispatch)", ext_rng.regs[1] == cmp_rng.regs[1])


# ---- 4. Disassembler decodes an ext-page hook (>=0x100) instead of dropping it.
from picoscript_lang import disassemble  # noqa: E402
ext_word = E(isa.OP_NOOP, rd=3, rs1=1, rs2=2, imm16=EXT_HOST_HOOK_BASE | HTTP_PARSE)
dis = disassemble([ext_word]).strip()
check("disasm decodes ext hook (non-empty)", dis != "")
check("disasm names the ext hook", "Http.ParseQuery" in dis)


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
