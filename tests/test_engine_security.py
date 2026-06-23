#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Security-focused engine-output regressions."""

import os
import json
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import CAP_PRINCIPAL, PicoFault, PicoVM, PV_FAULT_CAPABILITY, PV_FAULT_CONST_WRITE  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
BUILD = os.path.join(ROOT, ".test_build_engine_security")


def cc(cfile, exe):
    r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
                        f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def run(exe):
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def run_js_words(words, caps):
    js = (
        "const PicoVM=require('./vm/picovm.js');"
        f"const words={json.dumps(words)};"
        f"const vm=new PicoVM({{caps:{caps}}});"
        "try{vm.run(words); console.log(JSON.stringify({ok:true,out:vm.output,fault:0,caps:vm.caps}));}"
        "catch(e){console.log(JSON.stringify({ok:false,out:vm.output||[],fault:e.fault||0,detail:e.detail||0,caps:vm.caps}));}"
    )
    r = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_toc_const_region_write_faults():
    il = compile_c('int s = "AB"; Memory.Set(32766, 90); Io.Write(s);')
    csrc = lower_to_c(il, func_name="pico_sec", emit_main=False)
    cfile = os.path.join(BUILD, "const_write.c")
    exe = os.path.join(BUILD, "const_write.exe")
    write(cfile, csrc + r'''
#include <stdio.h>
int main(void) {
    static uint8_t arena[520 * 1024];
    pv_ctx ctx; pv_init(&ctx);
    ctx.mem = arena; ctx.mem_size = (long)sizeof(arena);
    pico_sec(&ctx);
    printf("FAULT %d DETAIL %d OUT", ctx.fault, ctx.fault_detail);
    for (int i = 0; i < ctx.out_len; i++) printf(" %02x", ctx.out[i]);
    printf("\n");
    return 0;
}
''')
    cc(cfile, exe)
    out = run(exe)
    assert "FAULT 10 DETAIL 32766 OUT 41 42" in out, out


def test_dot8_wraps_in_c_runtime():
    cfile = os.path.join(BUILD, "dot8_wrap.c")
    exe = os.path.join(BUILD, "dot8_wrap.exe")
    write(cfile, r'''
#include "picovm.h"
#include <stdio.h>
int main(void) {
    uint8_t arena[8] = {1,2,3,4,5,6,7,8};
    pv_ctx ctx; pv_init(&ctx);
    ctx.mem = arena; ctx.mem_size = 8;
    pv_dot8_setlen(&ctx, 4);
    printf("DOT %d\n", (int)pv_dot8(&ctx, 6, 0));
    return 0;
}
''')
    cc(cfile, exe)
    assert run(exe).strip() == "DOT 34"


def test_generated_identifiers_are_sanitized_and_escaped():
    il = compile_c('Net.Type("text/plain"); print(1);')
    malicious = 'x);system("echo pwned");//'
    csrc = lower_to_c(il, func_name=malicious, emit_main=True)
    assert 'system("echo pwned")' not in csrc
    cfile = os.path.join(BUILD, "ident_escape.c")
    exe = os.path.join(BUILD, "ident_escape.exe")
    write(cfile, csrc)
    cc(cfile, exe)
    assert "OUT" in run(exe)

    jsrc = lower_to_js(il, module_name=malicious)
    assert 'system("echo pwned")' not in jsrc
    jsfile = os.path.join(BUILD, "ident_escape.js")
    write(jsfile, jsrc + "\nconst rt = module.exports.run(); console.log('OK ' + rt.output.length);\n")
    r = subprocess.run(["node", jsfile], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_capability_request_cannot_self_escalate():
    words = lower_to_bytecode_safe(compile_c("int ok = Capability.Request(8); Io.WriteByte(ok);"))
    py = PicoVM(caps=CAP_PRINCIPAL).run(words)
    assert b"".join(py.output) == bytes([0])
    assert py.host.caps == CAP_PRINCIPAL

    js = run_js_words(words, CAP_PRINCIPAL)
    assert js["ok"] is True and js["out"] == [0]
    assert js["caps"] == CAP_PRINCIPAL


def test_search_requires_storage_capability_in_js_too():
    words = lower_to_bytecode_safe(compile_c("Search.Clear();"))
    try:
        PicoVM(caps=0).run(words)
        raise AssertionError("Python VM should deny Search.* without CAP_STORAGE")
    except PicoFault as e:
        assert e.code == PV_FAULT_CAPABILITY

    js = run_js_words(words, 0)
    assert js["ok"] is False and js["fault"] == PV_FAULT_CAPABILITY


def test_setconst_cannot_rewrite_existing_const_bytes():
    words = lower_to_bytecode_safe(compile_c('int s = "AB"; Memory.SetConst(32766, 90); Io.Write(s);'))
    try:
        PicoVM().run(words)
        raise AssertionError("Python VM should reject conflicting Memory.SetConst")
    except PicoFault as e:
        assert e.code == PV_FAULT_CONST_WRITE

    js = run_js_words(words, 0xFFFFF)
    assert js["ok"] is False and js["fault"] == PV_FAULT_CONST_WRITE


def test_pv_host2_uses_deployment_host_policy():
    cfile = os.path.join(BUILD, "host2_policy.c")
    exe = os.path.join(BUILD, "host2_policy.exe")
    write(cfile, r'''
#include "picovm.h"
#include "pico_hooks.h"
#include <stdio.h>
static void deny_random(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, int imm16) {
    (void)rd; (void)rs1; (void)rs2; (void)imm16;
    if (hook == PV_HOOK_RANDOM_U32) {
        ctx->fault = PV_FAULT_CAPABILITY;
        ctx->halted = 1;
        return;
    }
    pv_default_host(ctx, hook, rd, rs1, rs2, imm16);
}
int main(void) {
    pv_ctx ctx; pv_init(&ctx);
    ctx.host = deny_random;
    (void)pv_host2(&ctx, PV_HOOK_RANDOM_U32, 0, 0);
    printf("FAULT %d\n", ctx.fault);
    return 0;
}
''')
    cc(cfile, exe)
    assert run(exe).strip() == "FAULT 8"


def test_toc_arithmetic_wraps_to_i32():
    il = compile_c("int x = INT32_MAX; x = x + 1; if (x < 0) { Io.WriteByte(1); } else { Io.WriteByte(2); }")
    csrc = lower_to_c(il, func_name="arith_wrap", emit_main=True)
    cfile = os.path.join(BUILD, "arith_wrap.c")
    exe = os.path.join(BUILD, "arith_wrap.exe")
    write(cfile, csrc)
    cc(cfile, exe)
    out = run(exe)
    assert "OUT 01" in out, out


def test_js_datetime_naive_iso_is_utc():
    words = lower_to_bytecode_safe(compile_c('int s = "1970-01-01T00:00:00"; Io.WriteByte(DateTime.Parse(s));'))
    js = run_js_words(words, 0xFFFFF)
    assert js["ok"] is True and js["out"] == [0]


def main():
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    try:
        test_toc_const_region_write_faults()
        test_dot8_wraps_in_c_runtime()
        test_generated_identifiers_are_sanitized_and_escaped()
        test_capability_request_cannot_self_escalate()
        test_search_requires_storage_capability_in_js_too()
        test_setconst_cannot_rewrite_existing_const_bytes()
        test_pv_host2_uses_deployment_host_policy()
        test_toc_arithmetic_wraps_to_i32()
        test_js_datetime_naive_iso_is_utc()
        print("PASS engine security: toC const writes fault, C Dot8 wraps safely, generated C/JS identifiers are sanitized.")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
