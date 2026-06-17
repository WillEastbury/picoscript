#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build PicoScript sources into one native server binary or Node.js server."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from picoscript_basic import compile_basic
from picoscript_cfront import compile_c
from picoscript_cobol import compile_cobol
from picoscript_english import compile_english
from picoscript_functional import compile_functional
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js
from picoscript_python import compile_python
from picoscript_report import compile_report

ROOT = Path(__file__).resolve().parent
VM_DIR = ROOT / "vm"
BUILD_DIR = ROOT / "build"

COMPILERS = {
    "c": compile_c,
    "basic": compile_basic,
    "python": compile_python,
    "english": compile_english,
    "cobol": compile_cobol,
    "report": compile_report,
    "functional": compile_functional,
}
EXT_TO_LANG = {
    ".pc": "c", ".ps": "c", ".c": "c",
    ".bas": "basic", ".basic": "basic", ".pbas": "basic",
    ".py": "python", ".ppy": "python",
    ".eng": "english", ".english": "english",
    ".cob": "cobol",
    ".rpt": "report",
    ".fn": "functional",
}


@dataclass
class Module:
    source: Path
    func_name: str
    c_text: str
    js_text: str
    words: int
    routes: list[str]


def detect_language(path: Path) -> str:
    try:
        return EXT_TO_LANG[path.suffix.lower()]
    except KeyError as exc:
        raise SystemExit(f"unsupported source extension: {path}") from exc


def sanitize(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text)


def rel_no_ext(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
        return rel.with_suffix("").as_posix()
    except ValueError:
        return path.stem


def route_aliases(path: Path, stem_counts: dict[str, int]) -> list[str]:
    aliases = {"/" + path.name, "/" + rel_no_ext(path), "/" + path.stem}
    if stem_counts.get(path.stem, 0) > 1:
        aliases.discard("/" + path.stem)
    return sorted(a.replace("\\", "/") for a in aliases)


def auto_detect_cc() -> str | None:
    order = ["cl", "gcc", "clang"] if os.name == "nt" else ["gcc", "clang", "cc"]
    for name in order:
        found = shutil.which(name)
        if found:
            return found
    return None


def format_cmd(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([str(x) for x in cmd])
    return subprocess.list2cmdline([str(x) for x in cmd])


def output_path(name: str, js: bool) -> Path:
    out = Path(name)
    if not out.is_absolute():
        out = BUILD_DIR / out
    if js:
        return out if out.suffix == ".js" else out.with_suffix(".js")
    if os.name == "nt" and not out.suffix:
        return out.with_suffix(".exe")
    return out


def js_require_path(target: Path, source: Path) -> str:
    rel = os.path.relpath(source, target.parent).replace("\\", "/")
    return rel if rel.startswith(".") else "./" + rel


def compile_module(path: Path, stem_counts: dict[str, int]) -> Module:
    lang = detect_language(path)
    func_name = "handler_" + sanitize(rel_no_ext(path))
    source = path.read_text(encoding="utf-8")
    il = COMPILERS[lang](source)
    return Module(
        source=path,
        func_name=func_name,
        c_text=lower_to_c(il, func_name=func_name),
        js_text=lower_to_js(il, module_name=func_name),
        words=len(lower_to_bytecode_safe(il)),
        routes=route_aliases(path, stem_counts),
    )


def render_main_c(modules: list[Module], default_port: int, default_workers: int) -> str:
    externs = "\n".join(f"extern int64_t {m.func_name}(pv_ctx *ctx);" for m in modules)
    dispatch_lines = []
    for i, mod in enumerate(modules):
        cond = " || ".join(f'route_eq(path, "{route}")' for route in mod.routes)
        prefix = "if" if i == 0 else "else if"
        dispatch_lines.append(f"    {prefix} ({cond}) return {mod.func_name}(ctx);")
    dispatch = "\n".join(dispatch_lines) or "    (void)ctx;"
    return f"""#include "picovm.h"
#include "picovm_pool.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

{externs}

static int route_eq(const char *path, const char *want) {{
    size_t pn = strlen(path), wn = strlen(want);
    while (pn > 1 && path[pn - 1] == '/') pn--;
    while (wn > 1 && want[wn - 1] == '/') wn--;
    return pn == wn && strncmp(path, want, pn) == 0;
}}

static void plain_response(pv_ctx *ctx, int status, const char *text) {{
    size_t n = strlen(text);
    if (n > sizeof(ctx->out)) n = sizeof(ctx->out);
    ctx->http_status = status;
    ctx->http_type = 0xA001;
    memcpy(ctx->out, text, n);
    ctx->out_len = (int)n;
}}

static int extract_path(pv_socket_t fd, char *path, size_t cap) {{
    char req[2048];
    int n = pv_socket_read(fd, req, (int)sizeof(req) - 1);
    char *sp1, *sp2, *q;
    size_t plen;
    if (n <= 0 || cap == 0) return -1;
    req[n] = '\\0';
    sp1 = strchr(req, ' ');
    if (!sp1) return -1;
    sp2 = strchr(sp1 + 1, ' ');
    if (!sp2) return -1;
    plen = (size_t)(sp2 - sp1 - 1);
    if (plen == 0) {{
        strncpy(path, "/", cap - 1);
        path[cap - 1] = '\\0';
        return 0;
    }}
    if (plen >= cap) plen = cap - 1;
    memcpy(path, sp1 + 1, plen);
    path[plen] = '\\0';
    q = strchr(path, '?');
    if (q) *q = '\\0';
    return 0;
}}

static int64_t pv_dispatch(pv_ctx *ctx) {{
    char path[256];
    pv_socket_t fd = (pv_socket_t)(intptr_t)ctx->regs[0];
    if (extract_path(fd, path, sizeof(path)) != 0) {{
        plain_response(ctx, 400, "bad request");
        return 0;
    }}
{dispatch}
    plain_response(ctx, 404, "not found");
    return 0;
}}

int main(int argc, char **argv) {{
    int port = {default_port};
    int workers = {default_workers};
    pv_pool pool;
    int i;
    for (i = 1; i < argc; i++) {{
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) workers = atoi(argv[++i]);
    }}
    if (pv_pool_init(&pool, port, workers, pv_dispatch) != 0) {{
        fprintf(stderr, "native server init failed\\n");
        return 1;
    }}
    printf("PicoForge native: port=%d workers=%d routes=%d\\n", port, workers, {len(modules)});
    pv_pool_run(&pool);
    pv_pool_stop(&pool);
    return 0;
}}
"""


def render_server_js(modules: list[Module], default_port: int, default_workers: int, runtime_path: str) -> str:
    loaded = []
    dispatch_lines = []
    for i, mod in enumerate(modules):
        loaded.append(
            f"const {mod.func_name} = (() => {{\n"
            "  const module = { exports: {} };\n"
            "  const exports = module.exports;\n"
            f"{mod.js_text.replace('./picovm.js', runtime_path)}\n"
            "  return module.exports;\n"
            "})();\n"
        )
        cond = " || ".join(f'routeEq(path, "{route}")' for route in mod.routes)
        prefix = "if" if i == 0 else "else if"
        dispatch_lines.append(f"  {prefix} ({cond}) {{ {mod.func_name}.run(makeVmRuntime(vm)); return; }}")
    dispatch = "\n".join(dispatch_lines)
    return f"""#!/usr/bin/env node
'use strict';
const PicoVM = require('../vm/picovm.js');

{''.join(loaded)}
function routeEq(path, want) {{
  const norm = s => (s.length > 1 && s.endsWith('/')) ? s.replace(/\\/+$/, '') : s;
  return norm(path) === norm(want);
}}

function spanText(vm, handle) {{
  return Buffer.from(vm._spanBytes(handle || 0)).toString('utf8') || '/';
}}

function makeVmRuntime(vm) {{
  const imm = c => c <= 0xff ? (0x7000 | c) : (0x6000 | (c & 0xfff));
  const cards = Object.create(null);
  return {{
    cards,
    output: vm.output,
    httpStatus: vm.httpStatus,
    httpType: vm.httpType,
    closed: false,
    mem: vm.mem,
    dotLen: 0,
    load: a => cards[a] | 0,
    save: (a, v) => {{ cards[a] = v | 0; }},
    pipe: (a, v) => {{ const x = v >>> 0; vm.output.push((x>>>24)&255, (x>>>16)&255, (x>>>8)&255, x&255); }},
    netStatus: c => {{ vm.httpStatus = c & 0xFFF; }},
    netType: t => {{ vm.httpType = t; }},
    netBody: () => {{}},
    netHeader: () => {{}},
    netClose: function () {{ this.closed = true; }},
    memGet: a => {{ vm.regs[1] = a | 0; vm.regs[2] = 0; vm.regs[0] = 0; vm._host(0x37, 0, 1, 2, imm(0x37)); return vm.regs[0] | 0; }},
    memSet: (a, v) => {{ vm.regs[1] = a | 0; vm.regs[2] = v | 0; vm.regs[0] = 0; vm._host(0x36, 0, 1, 2, imm(0x36)); }},
    ioWrite: b => {{ vm.regs[1] = b | 0; vm.regs[2] = 0; vm.regs[0] = 0; vm._host(0x72, 0, 1, 2, imm(0x72)); }},
    dotLenSet: n => {{ vm.regs[1] = n | 0; vm.regs[2] = 0; vm.regs[0] = 0; vm._host(0x56, 0, 1, 2, imm(0x56)); }},
    dot8: (w, a) => {{ vm.regs[1] = w | 0; vm.regs[2] = a | 0; vm.regs[0] = 0; vm._host(0x57, 0, 1, 2, imm(0x57)); return vm.regs[0] | 0; }},
    dsp: (s, a, b) => s === 4 ? (a < 0 ? 0 : a) : (s === 3 ? Math.imul(a, b) : (s === 9 ? ((a + b) | 0) : 0)),
    host: (c, a, b) => {{ vm.regs[1] = a | 0; vm.regs[2] = b | 0; vm.regs[0] = 0; vm._host(c, 0, 1, 2, imm(c)); return vm.regs[0] | 0; }},
    outputInts: () => []
  }};
}}

async function dispatch(vm) {{
  const path = spanText(vm, vm.requestContext && vm.requestContext.path);
{dispatch}
  vm.httpStatus = 404;
  vm.httpType = 'text/plain; charset=utf-8';
  vm.output.push(...Buffer.from('not found', 'utf8'));
}}

let port = {default_port};
let workers = {default_workers};
for (let i = 2; i < process.argv.length; i++) {{
  if (process.argv[i] === '--port' && i + 1 < process.argv.length) port = parseInt(process.argv[++i], 10) || port;
  else if (process.argv[i] === '--workers' && i + 1 < process.argv.length) workers = parseInt(process.argv[++i], 10) || workers;
}}
const pool = new PicoVM.Pool({{ workers, handler: dispatch }});
if (!pool.available) throw new Error('PicoVM.Pool requires Node.js');
pool.listen(port, err => {{
  if (err) throw err;
  console.log(`PicoForge node: port=${{port}} workers=${{workers}} routes={len(modules)}`);
}});
"""


def compile_command(cc: str, sources: list[Path], out: Path, platform: str) -> list[str]:
    base = Path(cc).name.lower()
    if base in {"cl", "cl.exe"}:
        cmd = [cc, "/nologo", "/O2", f"/I{VM_DIR}", f"/Fe:{out}"]
        if platform == "pios":
            cmd.append("/DPIOS")
        cmd.extend(str(s) for s in sources)
        return cmd
    cmd = [cc, "-std=c99", "-O2", f"-I{VM_DIR}"]
    if platform == "pios":
        cmd.append("-DPIOS")
    if os.name == "nt":
        cmd.append("-lws2_32")
    else:
        cmd.append("-pthread")
    cmd.extend(str(s) for s in sources)
    cmd.extend(["-o", str(out)])
    return cmd


def print_plan(modules: list[Module], out: Path, args, cc: str | None) -> None:
    print(f"mode: {'js' if args.js else args.platform}")
    print(f"output: {out}")
    print(f"files: {len(modules)} total_words: {sum(m.words for m in modules)}")
    for mod in modules:
        print(f"  - {mod.source.name}: {mod.func_name} routes={', '.join(mod.routes)} words={mod.words}")
    if not args.js:
        print(f"compiler: {cc or '<not found>'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", help="PicoScript source files")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", default="picoforge")
    parser.add_argument("--platform", choices=["native", "pios"], default="native")
    parser.add_argument("--cc", help="C compiler to use (auto-detects cl/gcc/clang)")
    parser.add_argument("--dry-run", action="store_true", help="show plan without invoking the compiler")
    parser.add_argument("--js", action="store_true", help="emit a single Node.js server instead of a native binary")
    args = parser.parse_args(argv)

    srcs = [Path(s).resolve() for s in args.sources]
    missing = [str(s) for s in srcs if not s.exists()]
    if missing:
        raise SystemExit("missing source file(s): " + ", ".join(missing))
    stem_counts: dict[str, int] = {}
    for src in srcs:
        stem_counts[src.stem] = stem_counts.get(src.stem, 0) + 1

    modules = []
    for src in srcs:
        try:
            modules.append(compile_module(src, stem_counts))
        except Exception as exc:
            print(f"[compile error] {src}: {exc}", file=sys.stderr)
            return 1

    out = output_path(args.output, args.js)
    cc = None if args.js else (args.cc or auto_detect_cc())
    if args.dry_run:
        print_plan(modules, out, args, cc)
        if not args.js and cc:
            work_dir = BUILD_DIR / f"_{out.stem}_native"
            src_files = [work_dir / "main.c"] + [work_dir / f"{m.func_name}.c" for m in modules] + [VM_DIR / "picovm.c", VM_DIR / "picovm_pool.c"]
            print("command:", format_cmd(compile_command(cc, src_files, out, args.platform)))
        return 0
    if not args.js and not cc:
        print("no C compiler found (tried cl/gcc/clang); pass --cc explicitly", file=sys.stderr)
        return 1

    if args.js:
        BUILD_DIR.mkdir(exist_ok=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            render_server_js(modules, args.port, args.workers, js_require_path(out, VM_DIR / "picovm.js")),
            encoding="utf-8",
        )
        size = out.stat().st_size
        print(f"built {out} ({len(modules)} files, {sum(m.words for m in modules)} words, {size} bytes)")
        return 0

    BUILD_DIR.mkdir(exist_ok=True)
    work_dir = BUILD_DIR / f"_{out.stem}_native"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    for mod in modules:
        (work_dir / f"{mod.func_name}.c").write_text(mod.c_text + "\n", encoding="utf-8")
    (work_dir / "main.c").write_text(render_main_c(modules, args.port, args.workers) + "\n", encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = compile_command(
        cc,
        [work_dir / "main.c"] + [work_dir / f"{m.func_name}.c" for m in modules] + [VM_DIR / "picovm.c", VM_DIR / "picovm_pool.c"],
        out,
        args.platform,
    )
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        print("native build failed", file=sys.stderr)
        print(format_cmd(cmd), file=sys.stderr)
        if run.stdout.strip():
            print(run.stdout, file=sys.stderr)
        if run.stderr.strip():
            print(run.stderr, file=sys.stderr)
        return 1
    size = out.stat().st_size
    print(f"built {out} ({len(modules)} files, {sum(m.words for m in modules)} words, {size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
