import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import PicoVM


def test_fixed_clock_matches_javascript_vm():
    words = lower_to_bytecode_safe(compile_c(
        "Io.WriteByte(DateTime.Now());"
        "Io.WriteByte(DateTime.UtcNow());"
        "Io.WriteByte(DateTime.UnixTimestamp());"
    ))
    python_vm = PicoVM(fixed_time=1704067200).run(words)
    payload = f"{len(words)}\n" + "\n".join(f"{word:08x}" for word in words) + "\n"
    script = """
const P = require('./picovm.js');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const lines = input.trim().split(/\\s+/);
  const n = Number(lines[0]);
  const words = lines.slice(1, n + 1).map(x => parseInt(x, 16));
  const vm = new P({fixedTime: 1704067200});
  vm.run(words);
  console.log('OUT ' + vm.output.map(x => x.toString(16).padStart(2, '0')).join(' '));
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=payload, capture_output=True, text=True,
        cwd=os.path.join(ROOT, "vm"),
    )
    assert result.returncode == 0, result.stderr
    js_bytes = b""
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "OUT":
            js_bytes = bytes(int(value, 16) for value in fields[1:])
    assert b"".join(python_vm.output) == js_bytes
