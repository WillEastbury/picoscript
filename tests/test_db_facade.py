import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_lang import Compiler, disassemble
from picoscript_vm import PicoVM


def test_db_assembly_namespace_and_python_crud():
    assert "Db.Read" in disassemble(Compiler().compile("Db.Read(R1, R2, R3);"))
    words = lower_to_bytecode_safe(
        compile_c('int id = Db.Insert(7, "abc"); int body = Db.Read(7, id); Io.Write(body);')
    )
    assert b"".join(PicoVM().run(words).output) == b"abc"


def test_db_javascript_crud_matches_python():
    script = r"""
const PicoVM = require('./vm/picovm.js');
const vm = new PicoVM();
vm.regs[1] = 7;
vm.mem[100] = 97; vm.mem[101] = 98; vm.mem[102] = 99;
vm.spans.push({ptr: 100, len: 3});
vm.regs[2] = 1;
vm._db("Insert", 3, 1, 2);
const id = vm.regs[3];
vm.regs[1] = 7; vm.regs[2] = id;
vm._db("Read", 4, 1, 2);
const span = vm.spans[vm.regs[4]];
process.stdout.write(String.fromCharCode(...vm.mem.slice(span.ptr, span.ptr + span.len)));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "abc"


def test_db_cursor_current_and_card_id():
    source = """
int a = Db.Insert(7, "one");
int b = Db.Insert(7, "two");
Db.Query(7);
int first = Db.Next();
int body = Db.Current(7);
Io.WriteByte(Db.CardId());
Io.Write(body);
int second = Db.Next();
Io.WriteByte(Db.CardId());
"""
    vm = PicoVM().run(lower_to_bytecode_safe(compile_c(source)))
    assert b"".join(vm.output) == b"\x01one\x02"
