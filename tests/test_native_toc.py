#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""First-class native parity: Python VM == C interpreter == toC-native == toJS-native.

The span/string namespaces lower to first-class native code (a direct code-keyed
host call, no bytecode VM, no string dispatch) on BOTH the toC and toJS backends.
For each construct this builds FOUR runtimes from one source -- the Python reference
VM, the portable C interpreter (vm/picovm_run.exe running bytecode), a standalone
native binary emitted by lower_to_c, and an emitted JS module (lower_to_js) whose
runtime delegates to the shared JS host -- and asserts all four emit byte-identical
output plus the known answer.
"""

import os
import shutil
import subprocess
import sys
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe, lower_to_c, lower_to_js  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402

VM_DIR = os.path.join(ROOT, "vm")
VM_EXE = os.path.join(VM_DIR, "picovm_run.exe")
BUILD = os.path.join(ROOT, ".test_build_toc")


def build_c_vm():
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           os.path.join(VM_DIR, "picovm.c"), os.path.join(VM_DIR, "picovm_run.c"), "-o", VM_EXE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def parse_out_bytes(text):
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "OUT":
            return bytes(int(x, 16) for x in p[1:])
    return b""


def c_interp_out(words):
    inp = f"{len(words)}\n" + "\n".join(f"{w:08x}" for w in words) + "\n"
    out = subprocess.run([VM_EXE], input=inp, capture_output=True, text=True).stdout
    return parse_out_bytes(out)


def c_native_out(il, slot):
    csrc = lower_to_c(il, func_name=f"pico_{slot}", emit_main=True)
    cfile = os.path.join(BUILD, f"{slot}.c")
    exe = os.path.join(BUILD, f"{slot}.exe")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(csrc)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O2",
           f"-I{VM_DIR}", cfile, os.path.join(VM_DIR, "picovm.c"), "-o", exe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def js_native_out(il, slot):
    # Emitted JS resolves picovm.js (copied into BUILD) so its runtime delegates to
    # the shared JS host -> full namespace parity, no VM loop.
    jsfile = os.path.join(BUILD, f"{slot}.js")
    with open(jsfile, "w", encoding="utf-8") as f:
        f.write(lower_to_js(il, module_name=f"pico_{slot}"))
    runner = os.path.join(BUILD, f"run_{slot}.js")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(f"const p = require('./{slot}.js');\n"
                "const rt = p.run();\n"
                "console.log('OUT ' + rt.output.map(b => b.toString(16).padStart(2, '0')).join(' '));\n")
    out = subprocess.run(["node", runner], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return parse_out_bytes(out.stdout)


def check(prog, expected, slot):
    words = lower_to_bytecode_safe(compile_c(prog))
    py = b"".join(PicoVM().run(words).output)
    ci = c_interp_out(words)
    cn = c_native_out(compile_c(prog), slot)
    jn = js_native_out(compile_c(prog), slot)
    assert py == expected, f"[{slot}] Python {py!r} != {expected!r}"
    assert ci == expected, f"[{slot}] C-interp {ci!r} != {expected!r}"
    assert cn == expected, f"[{slot}] toC-native {cn!r} != {expected!r}"
    assert jn == expected, f"[{slot}] toJS-native {jn!r} != {expected!r}"


def setbytes(base, data):
    return "".join(f"Memory.Set({base + i}, {b});" for i, b in enumerate(data))


def tpl_prog(tmpl, model):
    return (setbytes(1000, tmpl) + setbytes(2000, model) +
            f"int t = Span.Make(1000, {len(tmpl)}); int pl = Template.Compile(t);"
            f"int m = Span.Make(2000, {len(model)}); int o = Template.Render(pl, m); Io.Write(o);")


def hmac_prog(key, msg):
    return (setbytes(1000, key) + setbytes(2000, msg) +
            f"int k = Span.Make(1000, {len(key)}); int m = Span.Make(2000, {len(msg)});"
            f"int d = Crypto.HmacSha256(k, m); Io.Write(d);")


def main():
    build_c_vm()
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)
    for dep in ("picovm.js", "pico_hooks.js", "picostore.js"):
        src = os.path.join(VM_DIR, dep)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(BUILD, dep))
    try:
        # String.* core.
        s = (setbytes(1000, b"abc") + setbytes(1003, b"bc") + setbytes(1005, b"XY") +
             "int s1 = Span.Make(1000, 3); int s2 = Span.Make(1003, 2); int s3 = Span.Make(1005, 2); int r = 0;"
             "r = String.Length(s1); Io.WriteByte(r);"
             "r = String.IndexOf(s1, s2); Io.WriteByte(r);"
             "r = String.Concat(s1, s2); Io.Write(r);"
             "r = String.ToUpper(s1); Io.Write(r);"
             "String.SetReplace(s3);"
             "r = String.Replace(s1, s2); Io.Write(r);")
        check(s, bytes([3, 1]) + b"abcbc" + b"ABC" + b"aXY", "string_core")

        # Substring / ToLower / Trim.
        s2p = (setbytes(1000, b"Hello") + setbytes(1010, b"  hi \t") +
               "int h = Span.Make(1000, 5);"
               "int sub = String.Substring(h, 2); Io.Write(sub); Io.WriteByte(124);"
               "int up = String.ToUpper(h); int lo = String.ToLower(up); Io.Write(lo); Io.WriteByte(124);"
               "int w = Span.Make(1010, 6); int t = String.Trim(w); Io.Write(t);")
        check(s2p, b"llo|hello|hi", "substr_trim")

        # Span.Slice / Materialize / Len / Get.
        sp = (setbytes(1000, b"abcdef") +
              "int h = Span.Make(1000, 6);"
              "int sl = Span.Slice(h, 2); Io.Write(sl); Io.WriteByte(124);"
              "int m = Span.Materialize(sl); int ml = Span.Len(m); Io.WriteByte(ml);"
              "int g0 = Span.Get(m, 0); Io.WriteByte(g0);")
        check(sp, b"cdef|" + bytes([4, 99]), "span_ops")

        # Number.* : Parse / ToString / ToHex / Abs.
        nm = (setbytes(1000, b"255") + setbytes(1020, b"-9") +
              "int qsp = Span.Make(1000, 3); int q = Number.Parse(qsp); Io.WriteByte(Number.Abs(q));"
              "int hx = Number.ToHex(q); Io.Write(hx); Io.WriteByte(124);"
              "int ts = Number.ToString(q); Io.Write(ts);")
        check(nm, bytes([255]) + b"ff|255", "number")

        # Maths.Power / Sqrt.
        ma = ("int b = 2; int e = 10; int p = Maths.Power(b, e); Io.WriteByte(Number.Abs(0));"
              "int n = 144; int s = Maths.Sqrt(n); Io.WriteByte(s);"
              "int ts = Number.ToString(p); Io.Write(ts);")
        # 2^10 = 1024 -> ToString "1024"; sqrt(144)=12
        check(ma, bytes([0, 12]) + b"1024", "maths")

        # Compress (RLE round-trip) + Html (entities, incl. tricky &amp;lt;).
        cz = (setbytes(1000, b"aaabbbbc") +
              "int s = Span.Make(1000, 8);"
              "int c = Compress.PicoCompress(s); Io.WriteByte(String.Length(c));"
              "int d = Compress.PicoDecompress(c); Io.Write(d);")
        check(cz, bytes([6]) + b"aaabbbbc", "compress")

        hz = (setbytes(1000, b"<b>&'\"") + setbytes(1100, b"&amp;lt;") +
              "int s = Span.Make(1000, 6);"
              "int e = Html.Encode(s); Io.Write(e); Io.WriteByte(124);"
              "int x = Span.Make(1100, 8); int d = Html.Decode(x); Io.Write(d);")
        check(hz, b"&lt;b&gt;&amp;&#39;&quot;|&lt;", "html")

        # Http.ParseQuery (url-decode -> model).
        qsrc = b"q=hello+world&x=%41"
        hq = (setbytes(1000, qsrc) +
              f"int s = Span.Make(1000, {len(qsrc)}); int m = Http.ParseQuery(s); Io.Write(m);")
        check(hq, b"q=hello world\nx=A\n", "http_query")

        # Http.EncodeJson (model -> JSON).
        ejsrc = b"name=Bob\nrole=admin"
        ej = (setbytes(1000, ejsrc) +
              f"int s = Span.Make(1000, {len(ejsrc)}); int j = Http.EncodeJson(s); Io.Write(j);")
        check(ej, b'{"name":"Bob","role":"admin"}', "http_encodejson")

        # Http.ParseJson (nested JSON -> dotted {{#each}} model).
        pjsrc = b'{"items":[{"name":"A"},{"name":"B"}]}'
        pj = (setbytes(1000, pjsrc) +
              f"int s = Span.Make(1000, {len(pjsrc)}); int m = Http.ParseJson(s); Io.Write(m);")
        check(pj, b"items.0.name=A\nitems.1.name=B\n", "http_parsejson")

        # Crypto.Sha256: canonical "abc" digest + a multi-block input (padding across blocks).
        sh = (setbytes(1000, b"abc") +
              "int s = Span.Make(1000, 3); int d = Crypto.Sha256(s); Io.Write(d);")
        check(sh, hashlib.sha256(b"abc").digest(), "sha256_abc")
        msg = bytes((i * 7 + 3) & 0xFF for i in range(130))
        shl = (setbytes(1000, msg) +
               f"int s = Span.Make(1000, {len(msg)}); int d = Crypto.Sha256(s); Io.Write(d);")
        check(shl, hashlib.sha256(msg).digest(), "sha256_multiblock")

        # Crypto.HmacSha256 (RFC 4231) -- short key, ASCII key/data, and a >64-byte key
        # (exercises the key-is-hashed-first branch). Two input spans (key, message).
        import hmac as _hmac
        def _hm(key, m): return _hmac.new(key, m, hashlib.sha256).digest()
        tc1k, tc1m = bytes([0x0b]) * 20, b"Hi There"
        check(hmac_prog(tc1k, tc1m), _hm(tc1k, tc1m), "hmac_rfc_tc1")
        tc2k, tc2m = b"Jefe", b"what do ya want for nothing?"
        check(hmac_prog(tc2k, tc2m), _hm(tc2k, tc2m), "hmac_rfc_tc2")
        tc6k = bytes([0xaa]) * 131
        tc6m = b"Test Using Larger Than Block-Size Key - Hash Key First"
        check(hmac_prog(tc6k, tc6m), _hm(tc6k, tc6m), "hmac_rfc_tc6_bigkey")

        # Signed division / modulo: truncate toward zero, identical on every path (INV-14).
        check("int a = 0 - 7; int b = a / 2; Io.WriteByte(b);", bytes([253]), "div_neg_num")    # -3
        check("int a = 7; int b = a / (0 - 2); Io.WriteByte(b);", bytes([253]), "div_neg_den")  # -3
        check("int a = 0 - 7; int b = a / (0 - 2); Io.WriteByte(b);", bytes([3]), "div_neg_both")  # 3
        check("int a = 0 - 8; int b = a / 3; Io.WriteByte(b);", bytes([254]), "div_neg_trunc")  # -2 (floor would be -3)
        check("int a = 0 - 7; int b = a % 2; Io.WriteByte(b);", bytes([255]), "mod_neg")        # -1

        # JSON nesting bound (INV-20): a shallow scalar emits; a leaf nested beyond the
        # depth-64 cap is truncated -- identically on every path (silent, no fault).
        deep = b'{"deep":1}'
        for _ in range(70):
            deep = b'{"a":' + deep + b'}'
        bigjson = b'{"top":7,"nested":' + deep + b'}'
        jd = (setbytes(1000, bigjson) +
              f"int s = Span.Make(1000, {len(bigjson)}); int m = Http.ParseJson(s); Io.Write(m);")
        check(jd, b"top=7\n", "json_depth_cap")

        # Queue.* parity (INV-24 coverage): enqueue increments depth identically on every
        # path. (Queue.Enqueue's VALUE uses a non-standard register ABI -- rd is an input --
        # so it round-trips only via direct bytecode/IPC, not the frontend; Depth is the
        # frontend-observable, parity-clean behaviour.)
        q = ("Queue.Enqueue(0, 1); Queue.Enqueue(0, 1); Queue.Enqueue(0, 1);"
             "int d = Queue.Depth(0); Io.WriteByte(d);")
        check(q, bytes([3]), "queue_depth")

        # Status.Last (INV-18): typed out-of-band status of the last fallible hook -- 5-path
        # identical. Number.Parse sets 0 (OK) / 2 (PARSE_ERROR); the primary return is unchanged.
        check('int x = Number.Parse("abc"); int s = Status.Last(); Io.WriteByte(s);',
              bytes([2]), "status_parse_err")
        check('int x = Number.Parse("9"); int s = Status.Last(); Io.WriteByte(s);',
              bytes([0]), "status_parse_ok")

        # Template.* : holes, sections, inverted, nesting, {{#each}} object + scalar.
        check(tpl_prog(b"Hi {{name}}!", b"name=Bob"), b"Hi Bob!", "tpl_hole")
        check(tpl_prog(b"{{#show}}yes{{/show}}", b"show=1"), b"yes", "tpl_section")
        check(tpl_prog(b"{{^show}}no{{/show}}", b"show="), b"no", "tpl_inverted")
        check(tpl_prog(b"{{#a}}A{{#b}}B{{/b}}C{{/a}}", b"a=1\nb=1"), b"ABC", "tpl_nested")
        check(tpl_prog(b"{{#each items}}<li>{{name}}</li>{{/each}}", b"items.0.name=A\nitems.1.name=B"),
              b"<li>A</li><li>B</li>", "tpl_each_obj")
        check(tpl_prog(b"{{#each xs}}[{{.}}]{{/each}}", b"xs.0=1\nxs.1=2\nxs.2=3"),
              b"[1][2][3]", "tpl_each_scalar")

        print("PASS first-class native: Python VM == C interpreter == toC-compiled == toJS-compiled, "
              "byte-exact -- Span/String/Number/Maths/Compress/Html/Http/Crypto(Sha256+HmacSha256)/Template "
              "(compiled C and JS both skip the bytecode VM)")
    finally:
        shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == "__main__":
    main()
