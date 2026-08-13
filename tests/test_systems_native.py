"""Slow native compile/run contract for PicoScript systems instructions.

This is a build_c_vm-equivalent compiler test and is selected by --runslow.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_basic import compile_basic  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_il import lower_to_c, lower_to_js  # noqa: E402


def test_systems_program_lowers_to_and_runs_as_native_c(tmp_path):
    source = """
int fact(int n) {
    uint64_t evidence = 0x100000000ULL;
    int scratch[2]; scratch[0] = n;
    evidence = evidence + 3;
    if (n <= 1) { return 1; }
    return scratch[0] * fact(n - 1);
}
print(fact(5));
"""
    cfile = tmp_path / "systems.c"
    exe = tmp_path / "systems.exe"
    cfile.write_text(lower_to_c(compile_c(source), func_name="pico_systems",
                                emit_main=True), encoding="utf-8")
    vm_dir = os.path.join(ROOT, "vm")
    host_dir = os.path.join(ROOT, "host")
    clang = r"C:\Program Files\LLVM\bin\clang.exe"
    cmd = [clang, "-std=c99", "-O1", "-msse4.2", f"-I{vm_dir}", str(cfile),
           os.path.join(vm_dir, "picovm.c"), os.path.join(vm_dir, "picovm_emu.c"),
           os.path.join(vm_dir, "picovm_crypto_ext.c"), os.path.join(host_dir, "pv_auth_store.c"),
           "-o", str(exe)]
    built = subprocess.run(cmd, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert "OUT 00 00 00 78" in ran.stdout


def test_systems_program_runs_in_browser_javascript_profile(tmp_path):
    source = "int a[2]; a[1]=7; uint64_t x=0x100000000ULL; x=x+3; print(a[1]);"
    module = tmp_path / "systems.js"
    runner = tmp_path / "run.js"
    module.write_text(lower_to_js(compile_c(source), module_name="systems"), encoding="utf-8")
    runner.write_text("const p=require('./systems.js'); const r=p.run(); "
                      "console.log(r.output.map(x=>x.toString(16).padStart(2,'0')).join(' '));",
                      encoding="utf-8")
    ran = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "00 00 00 07"


def test_two_word_systems_bytecode_runs_in_embeddable_c_vm(tmp_path):
    source = """\
DIM value AS U64 = u64(40) + 2
PRINT value
DIM wal AS WAL = 0
DIM field AS INDEX = Index.Open(wal, 1)
PRINT Index.Upsert(field, 7, 99)
PRINT Index.Exact(field, 99)
PRINT Index.Result(field, 0)
"""
    words = lower_to_bytecode_safe(compile_basic(source))
    exe = tmp_path / "picovm_systems.exe"
    vm_dir = os.path.join(ROOT, "vm"); host_dir = os.path.join(ROOT, "host")
    clang = r"C:\Program Files\LLVM\bin\clang.exe"
    cmd = [clang, "-std=c99", "-O1", "-msse4.2", f"-I{vm_dir}",
           os.path.join(vm_dir, "picovm.c"), os.path.join(vm_dir, "picovm_run.c"),
           os.path.join(vm_dir, "picovm_emu.c"), os.path.join(vm_dir, "picovm_crypto_ext.c"),
           os.path.join(host_dir, "pv_auth_store.c"), "-o", str(exe)]
    built = subprocess.run(cmd, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    payload = f"{len(words)}\n" + "\n".join(f"{word:08x}" for word in words) + "\n"
    ran = subprocess.run([str(exe)], input=payload, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert "FAULT 0 0 0" in ran.stdout
    assert "OUT 00 00 00 2a 00 00 00 01 00 00 00 01 00 00 00 07" in ran.stdout
    node = subprocess.run(["node", os.path.join(vm_dir, "picovm_run.js")],
                          input=payload, capture_output=True, text=True)
    assert node.returncode == 0, node.stderr
    assert "OUT 00 00 00 2a 00 00 00 01 00 00 00 01 00 00 00 07" in node.stdout


def test_weighted_graph_path_runs_in_generated_and_bytecode_javascript(tmp_path):
    source = """\
DIM wal AS WAL = 0
DIM edges AS WAL = Wal.Pack(wal, 7)
DIM graph AS GRAPH = Graph.Open(edges, 3)
PRINT Graph.Add(graph, 1, 2, 5)
PRINT Graph.Add(graph, 2, 3, 7)
PRINT Graph.Add(graph, 1, 3, 20)
PRINT Graph.Path(graph, 1, 3)
PRINT Graph.ResultNode(graph, 2)
PRINT Graph.ResultWeight(graph, 2)
"""
    insts = compile_basic(source)
    module = tmp_path / "graph.js"
    runner = tmp_path / "run_graph.js"
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js", "picoserializer.js",
                "picocompress.js", "picobrotli.js"):
        shutil.copy2(os.path.join(ROOT, "vm", dep), tmp_path / dep)
    module.write_text(lower_to_js(insts, module_name="graph"), encoding="utf-8")
    runner.write_text("const p=require('./graph.js'); const r=p.run(); "
                      "console.log(r.outputInts().join(' '));", encoding="utf-8")
    generated = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert generated.returncode == 0, generated.stderr
    assert generated.stdout.strip() == "1 1 1 3 3 12"

    words = lower_to_bytecode_safe(insts)
    payload = f"{len(words)}\n" + "\n".join(f"{word:08x}" for word in words) + "\n"
    bytecode = subprocess.run(["node", os.path.join(ROOT, "vm", "picovm_run.js")],
                              input=payload, capture_output=True, text=True)
    assert bytecode.returncode == 0, bytecode.stderr
    assert "OUT 00 00 00 01 00 00 00 01 00 00 00 01 00 00 00 03 00 00 00 03 00 00 00 0c" in bytecode.stdout
