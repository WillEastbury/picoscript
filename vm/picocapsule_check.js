// picocapsule_check.js -- emit the canonical demo manifest via picocapsule.js so
// tests can assert byte-identical output with picocapsule.py / the handoff doc.
var C = require("./picocapsule.js");
var m = new C.Manifest("demo", "1001-20000");
m.principal = "app-user"; m.mem_kib = 4096; m.cpu_ms = 1000; m.fs = "/var/picowal/p1024";
m.process("web", C.sourceFor(1), C.codeFor(1), null, "http"); m.bindTcp(83, "web");
m.process("api", C.sourceFor(2), C.codeFor(2), null, "http"); m.bindTcp(84, "api");
m.fifo("requests", "web", "api", 64, 1024);
var text = C.serialize(m);
// parse(serialize(x)) round-trips to the same bytes.
if (C.serialize(C.parse(text)) !== text) { process.stderr.write("ROUNDTRIP FAIL\n"); process.exit(1); }
process.stdout.write(text);
