#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_build.py -- unified driver for the PicoScript toolchain.

One entry point from source to any target (LANGUAGE_SPEC.md sec 10):

    picoscript_build.py run    prog.pc            # compile + execute on PicoVM
    picoscript_build.py run    prog.pbas --print  # show PRINT/PIPE output
    picoscript_build.py emit   prog.pc  --as il    # dump PicoIL
    picoscript_build.py emit   prog.pc  --as bytecode --hex
    picoscript_build.py emit   prog.pbas --as c -o prog.c   # native (Thumb/AArch64)
    picoscript_build.py native prog.pc  -o prog.exe         # emit C + cc (zig)

Frontend is chosen by extension (.pc = C-syntax, .pbas/.bas = BASIC-like,
.ppy = Python-style, .eng/.english = natural-English, .pico = v1
namespace/method, .ast/.astjson = AST-as-JSON) or forced with
--lang {c,basic,python,english,v1,ast}.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

from picoscript_il import (
    lower_to_bytecode_safe, lower_to_c, lower_to_js, il_to_text, optimize,
)
from picoscript_vm import PicoVM
from picoscript import disassemble

VM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm")

# Deploy presets: name -> (zig -target, -mcpu).  The mcpu turns on the hardware
# Dot8 paths: cortex_a76 => AArch64 NEON SDOT (Pi 5), cortex_m33+dsp => SMLAD
# (Pico 2), native => the host CPU's SIMD (AVX/NEON) for desktop runs.
PROFILES = {
    "host":  (None, "native"),
    "pi5":   ("aarch64-linux-gnu", "cortex_a76"),
    "pico2": ("thumb-freestanding-eabi", "cortex_m33+dsp"),
}


def windows_vcvars_path():
    if os.name != "nt":
        return None
    candidates = sorted(
        glob.glob(
            r"C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat"
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_cuda_command(arguments):
    vcvars = windows_vcvars_path()
    if vcvars:
        arguments = list(arguments)
        script = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".cmd", delete=False, encoding="utf-8"
            ) as handle:
                script = handle.name
                handle.write("@echo off\n")
                handle.write(f'call "{vcvars}" >nul\n')
                handle.write(
                    f'"{arguments[0]}" {subprocess.list2cmdline(arguments[1:])}\n'
                )
                handle.write("exit /b %errorlevel%\n")
            return subprocess.run(
                [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", script],
                capture_output=True,
                text=True,
            )
        finally:
            if script:
                try:
                    os.remove(script)
                except OSError:
                    pass
    return subprocess.run(arguments, capture_output=True, text=True)


def detect_lang(path: str, forced: str | None) -> str:
    if forced:
        return forced
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pbas", ".bas"):
        return "basic"
    if ext in (".ppy",):
        return "python"
    if ext in (".eng", ".english"):
        return "english"
    if ext == ".pico":
        return "v1"
    if ext in (".wf", ".workflow"):
        return "workflow"
    if ext in (".rptmodel", ".reportmodel"):
        return "report-model"
    if ext in (".ast", ".astjson"):
        return "ast"
    return "c"


def to_il(source: str, lang: str):
    if lang == "c":
        from picoscript_cfront import compile_c
        return compile_c(source)
    if lang == "basic":
        from picoscript_basic import compile_basic
        return compile_basic(source)
    if lang == "python":
        from picoscript_python import compile_python
        return compile_python(source)
    if lang == "english":
        from picoscript_english import compile_english
        return compile_english(source)
    if lang == "workflow":
        from picoscript_workflow import compile_workflow
        return compile_workflow(source)
    if lang == "report-model":
        from picoscript_reportmodel import compile_report_model
        return compile_report_model(source)
    if lang == "ast":
        from picoscript_ast import compile_ast
        return compile_ast(source)
    raise ValueError(f"frontend {lang!r} has no IL stage (v1 compiles straight to bytecode)")


# Frontends whose AST is the shared picoscript_basic dataclasses (and so is
# representable as canonical AST-JSON, see picoscript_ast.py). c/cobol/report/
# functional/v1 each have their own distinct AST shape and aren't included.
_AST_CAPABLE_LANGS = {"basic", "python", "english", "workflow", "ast"}


def to_ast_json(source: str, lang: str) -> str:
    """Raw (pre-lowering) AST for `lang`, pretty-printed as canonical AST-JSON
    (picoscript_ast.ast_to_json). Lets you inspect/edit any of these surface
    dialects as a structural tree (docs/ast_designer_spike.html) and, for
    `workflow` in particular, escape its flat step-list vocabulary onto the
    full grammar. `emit --as ast` on a `.ast`/`.astjson` file just reformats.
    """
    if lang not in _AST_CAPABLE_LANGS:
        raise ValueError(
            f"frontend {lang!r} has no shared AST-JSON representation "
            f"(only {sorted(_AST_CAPABLE_LANGS)} do)"
        )
    from picoscript_ast import ast_to_json, json_to_ast
    import json as _json
    if lang == "ast":
        prog = json_to_ast(_json.loads(source))
    elif lang == "basic":
        from picoscript_basic import tokenize, Parser
        prog = Parser(tokenize(source)).parse_program()
    elif lang == "python":
        from picoscript_python import tokenize, Parser
        prog = Parser(tokenize(source)).parse_program()
    elif lang == "english":
        from picoscript_english import tokenize, Parser
        prog = Parser(tokenize(source)).parse_program()
    else:  # workflow
        from picoscript_workflow import workflow_to_ast
        prog, _warnings = workflow_to_ast(source)
    return _json.dumps(ast_to_json(prog), indent=2)


def to_bytecode(source: str, lang: str, opt: bool = True):
    if lang == "v1":
        from picoscript_lang import Compiler
        return Compiler().compile(source)
    return lower_to_bytecode_safe(to_il(source, lang), opt=opt)


def decode_output(vm: PicoVM):
    def s32(v):
        return v - 0x100000000 if v & 0x80000000 else v
    return [s32(int.from_bytes(b, "big")) for b in vm.output]


def cmd_run(args):
    source = open(args.file, encoding="utf-8").read()
    lang = detect_lang(args.file, args.lang)
    words = to_bytecode(source, lang, opt=not args.no_opt)
    vm = PicoVM(max_steps=args.max_steps).run(words)
    if args.print:
        print("output:", decode_output(vm))
    if args.regs:
        print("regs:", {f"R{i}": vm.regs[i] for i in range(16) if vm.regs[i]})
    if vm.http_status is not None:
        print("http_status:", vm.http_status)
    print(f"[ran {vm.steps} steps, {len(words)} words]")


def cmd_emit(args):
    source = open(args.file, encoding="utf-8").read()
    lang = detect_lang(args.file, args.lang)
    out = ""
    if args.as_ == "il":
        out = il_to_text(to_il(source, lang))
    elif args.as_ == "bytecode":
        words = to_bytecode(source, lang, opt=not args.no_opt)
        if args.hex:
            out = "\n".join(f"{w:08x}" for w in words)
        else:
            out = disassemble(words)
    elif args.as_ == "c":
        name = args.funcname or "pico_main"
        out = lower_to_c(to_il(source, lang), func_name=name,
                         opt=not args.no_opt, emit_main=args.with_main)
    elif args.as_ == "js":
        name = args.funcname or "pico"
        out = lower_to_js(to_il(source, lang), module_name=name, opt=not args.no_opt)
    elif args.as_ == "ast":
        out = to_ast_json(source, lang)
    else:
        raise SystemExit(f"unknown emit target {args.as_}")
    if args.o:
        open(args.o, "w", encoding="utf-8").write(out + "\n")
        print(f"wrote {args.o}")
    else:
        print(out)


def cmd_native(args):
    source = open(args.file, encoding="utf-8").read()
    lang = detect_lang(args.file, args.lang)
    prof_target, prof_mcpu = PROFILES.get(args.profile, (None, None))
    target = args.target or prof_target
    mcpu = args.mcpu or prof_mcpu
    opt = args.opt
    freestanding = bool(target) and "freestanding" in target
    providers = list(dict.fromkeys(args.provider or []))
    has_catq = "catq" in providers or "catq-cuda" in providers
    if freestanding and providers:
        raise SystemExit("native providers require a hosted target")
    # Host builds get a runnable main(); freestanding cross builds emit a
    # linkable object (the emitted pico_main() is called from your firmware).
    csrc = lower_to_c(to_il(source, lang), func_name="pico_main",
                      emit_main=not freestanding and not providers)
    if providers and not freestanding:
        includes = ["#include <stdio.h>"]
        init = []
        cleanup = []
        if has_catq:
            includes.append('#include "picovm_catq.h"')
            init.append("    if (!pv_catq_install()) return 2;")
            cleanup.append("    pv_catq_cleanup();")
        if "net" in providers:
            includes.append('#include "picovm_net.h"')
            init.append("    if (!pv_net_install_socket_provider()) return 3;")
            cleanup.append("    pv_net_socket_cleanup();")
        csrc += "\n" + "\n".join(includes) + "\n"
        csrc += "int main(void) {\n"
        csrc += "    static uint8_t pico_arena[520 * 1024];\n"
        csrc += "    pv_ctx ctx; pv_init(&ctx);\n"
        csrc += "    ctx.mem = pico_arena; ctx.mem_size = (long)sizeof(pico_arena);\n"
        csrc += "\n".join(init) + "\n"
        csrc += "    pico_main(&ctx);\n"
        csrc += '    printf("STEPS %ld\\n", ctx.steps);\n'
        csrc += '    printf("FAULT %d %d %d\\n", ctx.fault, ctx.fault_pc, ctx.fault_detail);\n'
        csrc += '    printf("ERROR %d %d %d\\n", ctx.err_code, ctx.raise_active, ctx.host_status);\n'
        csrc += '    printf("STATUS %d\\n", ctx.http_status);\n'
        csrc += '    printf("REGS");\n'
        csrc += '    for (int i = 0; i < PV_NUM_REGS; i++) printf(" %d", ctx.regs[i]);\n'
        csrc += '    printf("\\nOUT");\n'
        csrc += '    for (int i = 0; i < ctx.out_len; i++) printf(" %02x", ctx.out[i]);\n'
        csrc += '    printf("\\n");\n'
        csrc += "\n".join(cleanup) + "\n"
        csrc += "    return 0;\n}\n"
    default_ext = ".o" if freestanding else ".exe"
    out_obj = args.o or (os.path.splitext(args.file)[0] + default_ext)
    cfile = out_obj + ".c"
    open(cfile, "w", encoding="utf-8").write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", f"-O{opt}", f"-I{VM_DIR}"]
    if target:
        cmd += ["-target", target]
    if mcpu:
        cmd += [f"-mcpu={mcpu}"]
    runtime_sources = [cfile, os.path.join(VM_DIR, "picovm.c")]
    if has_catq:
        runtime_sources.append(os.path.join(VM_DIR, "picovm_catq.c"))
    if "net" in providers:
        runtime_sources.append(os.path.join(VM_DIR, "picovm_net.c"))
    if "catq-cuda" in providers:
        if freestanding or target:
            raise SystemExit("catq-cuda currently supports the native hosted target only")
        nvcc = shutil.which("nvcc")
        if not nvcc:
            raise SystemExit("catq-cuda requires nvcc on PATH")
        objects = []
        compile_sources = list(runtime_sources)
        for index, source_path in enumerate(compile_sources):
            obj = out_obj + (f".{index}.obj" if os.name == "nt" else f".{index}.o")
            if os.name == "nt":
                msvc_opt = "/O2" if opt != "0" else "/Od"
                compile_cmd = [
                    "cl.exe", "/nologo", msvc_opt, "/TC", f"/I{VM_DIR}",
                    "/DPV_CATQ_ENABLE_CUDA", "/c", source_path, f"/Fo{obj}",
                ]
                result = run_cuda_command(compile_cmd)
            else:
                compile_cmd = [
                    sys.executable, "-m", "ziglang", "cc", "-std=c99", f"-O{opt}",
                    f"-I{VM_DIR}", "-DPV_CATQ_ENABLE_CUDA", "-c", source_path, "-o", obj,
                ]
                if mcpu:
                    compile_cmd += [f"-mcpu={mcpu}"]
                result = subprocess.run(compile_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stderr)
                raise SystemExit("native CUDA C build failed")
            objects.append(obj)
        cuda_obj = out_obj + ".cuda.o"
        cuda_cmd = [
            nvcc, "-O3", f"-arch={args.cuda_arch}", f"-I{VM_DIR}",
            "-c", os.path.join(VM_DIR, "picovm_catq_cuda.cu"), "-o", cuda_obj,
        ]
        result = run_cuda_command(cuda_cmd)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            raise SystemExit("CUDA kernel build failed")
        objects.append(cuda_obj)
        link_cmd = [nvcc, "-O3", f"-arch={args.cuda_arch}"] + objects
        if "net" in providers and os.name == "nt":
            link_cmd += ["ws2_32.lib"]
        link_cmd += ["-o", out_obj]
        result = run_cuda_command(link_cmd)
        for path in objects:
            try:
                os.remove(path)
            except OSError:
                pass
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            raise SystemExit("CUDA native link failed")
        print(f"wrote {out_obj} (executable via nvcc -arch={args.cuda_arch})")
        return
    if freestanding:
        cmd += ["-c"] + runtime_sources + ["-o", out_obj]
    else:
        cmd += runtime_sources
        if has_catq:
            cmd += ["-lm"]
            if os.name != "nt" or (target and "linux" in target):
                cmd += ["-pthread"]
        if "net" in providers and os.name == "nt":
            cmd += ["-lws2_32"]
        cmd += ["-o", out_obj]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("native build failed")
    kind = "object" if freestanding else "executable"
    flags = f" -O{opt}" + (f" -target {target}" if target else "") + (f" -mcpu={mcpu}" if mcpu else "")
    print(f"wrote {out_obj} ({kind} via zig cc{flags})")


def cmd_stats(args):
    from picoscript_metrics import measure, format_metrics
    source = open(args.file, encoding="utf-8").read()
    lang = detect_lang(args.file, args.lang)
    m = measure(source, lang, backend=args.backend, run=args.run, opt=not args.no_opt)
    print(format_metrics(m, title=os.path.basename(args.file) + f"  [lang={lang}]"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="picoscript_build", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("file")
    common.add_argument("--lang", choices=["c", "basic", "python", "english", "v1", "ast"], help="force frontend")
    common.add_argument("--no-opt", action="store_true", help="disable IL optimizer")

    pr = sub.add_parser("run", parents=[common], help="compile and execute on PicoVM")
    pr.add_argument("--print", action="store_true", help="show PRINT/PIPE output")
    pr.add_argument("--regs", action="store_true", help="show non-zero registers")
    pr.add_argument("--max-steps", type=int, default=1_000_000)
    pr.set_defaults(func=cmd_run)

    pe = sub.add_parser("emit", parents=[common], help="emit IL / bytecode / C")
    pe.add_argument("--as", dest="as_", choices=["il", "bytecode", "c", "js", "ast"], required=True)
    pe.add_argument("--hex", action="store_true", help="bytecode as hex words")
    pe.add_argument("--with-main", action="store_true", help="(c) append a runnable main()")
    pe.add_argument("--func", dest="funcname", help="(c) emitted function name")
    pe.add_argument("-o", help="output file")
    pe.set_defaults(func=cmd_emit)

    pn = sub.add_parser("native", parents=[common], help="emit C and compile with zig cc")
    pn.add_argument("-o", help="output executable")
    pn.add_argument("--target", help="zig cross target, e.g. thumb-freestanding-eabi, aarch64-linux-gnu")
    pn.add_argument("--mcpu", help="zig -mcpu, e.g. cortex_a76 (Pi5 NEON SDOT), cortex_m33+dsp (Pico2 SMLAD), native")
    pn.add_argument("--profile", choices=list(PROFILES),
                    help="deploy preset: host (native SIMD), pi5 (NEON SDOT), pico2 (SMLAD)")
    pn.add_argument("--provider", action="append", choices=["catq", "catq-cuda", "net"],
                    help="link and initialize a hosted runtime provider (repeatable)")
    pn.add_argument("--cuda-arch", default="sm_86",
                    help="CUDA architecture for catq-cuda (default: sm_86)")
    pn.add_argument("--opt", default="3", help="optimization level passed as -O<opt> (default 3)")
    pn.set_defaults(func=cmd_native)

    ps = sub.add_parser("stats", parents=[common], help="IL/bytecode/cycle metrics across backends")
    ps.add_argument("--backend", choices=["bytecode", "c", "js"], default="bytecode",
                    help="backend to highlight as 'chosen'")
    ps.add_argument("--run", action="store_true", help="also profile a run for dynamic counts")
    ps.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
