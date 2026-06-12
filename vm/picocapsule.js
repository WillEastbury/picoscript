// picocapsule.js -- capsule manifest model + deterministic canonical-text
// serializer/parser + pack/card address helpers, for the browser/editor.
//
// Byte-identical to picocapsule.py: serialize() must emit exactly the bytes that
// land in capsule card 0 regardless of which runtime authored the manifest. See
// docs/PIOS_CAPSULE_HANDOFF.md section 3 (the frozen canonical format).
(function (root, factory) {
  var P = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = P;
  else root.PicoCapsule = P;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Capsule pack-id range (defaults; the manifest is authoritative).
  var CAPSULE_PACK_MIN = 1024, CAPSULE_PACK_MAX = 4095;
  // Default source/bytecode card pairing for program N.
  var SOURCE_BASE = 1000, CODE_BASE = 10000;
  var NAME_RE = /^[A-Za-z0-9_-]+$/;

  function sourceFor(n) { return SOURCE_BASE + n; }   // program N source card
  function codeFor(n) { return CODE_BASE + n; }       // program N bytecode card

  // ---- pack/card addressing -------------------------------------------------
  function formatAddress(pack, card) { return pack + "/" + card; }
  function parseAddress(text) {
    var s = ("" + text).trim();
    var slash = s.indexOf("/");
    if (slash < 0) throw new Error("bad address " + JSON.stringify(text) + ": expected 'pack/card'");
    var left = s.slice(0, slash), right = s.slice(slash + 1);
    var pack, card;
    if (left.indexOf(":") >= 0) {           // typed: capsule:1024/card:10001
      pack = parseInt(left.split(":")[1], 10);
      card = parseInt(right.split(":")[1], 10);
    } else {
      pack = parseInt(left, 10); card = parseInt(right, 10);
    }
    return [pack, card];
  }
  function isCapsulePack(pack) { return pack >= CAPSULE_PACK_MIN && pack <= CAPSULE_PACK_MAX; }

  // ---- manifest model -------------------------------------------------------
  function Manifest(name, cards) {
    this.name = name;
    this.cards = cards || "1001-20000";
    this.principal = null; this.mem_kib = null; this.cpu_ms = null; this.fs = null;
    this.processes = []; this.fifos = [];
  }
  Manifest.prototype.process = function (name, source, bytecode, io, entry) {
    this.processes.push({ name: name, source: source, bytecode: bytecode,
                          io: (io === undefined ? null : io), entry: (entry === undefined ? null : entry) });
    return this;
  };
  Manifest.prototype.bindTcp = function (port, process) {
    for (var i = 0; i < this.processes.length; i++) {
      if (this.processes[i].name === process) { this.processes[i].io = "tcp/" + port; return this; }
    }
    throw new Error("bind: no process named " + process);
  };
  Manifest.prototype.fifo = function (name, frm, to, depth, frameMax) {
    this.fifos.push({ name: name, frm: frm, to: to, depth: depth, frame_max: frameMax });
    return this;
  };

  function checkName(kind, name) {
    if (!NAME_RE.test(name)) throw new Error(kind + " name " + JSON.stringify(name) + " must match [A-Za-z0-9_-]+");
  }

  // ---- deterministic canonical-text serializer (handoff doc section 3) ------
  function serialize(m) {
    checkName("capsule", m.name);
    var out = ["capsule = on", "name = " + m.name];
    if (m.principal != null) out.push("principal = " + m.principal);
    if (m.mem_kib != null) out.push("mem_kib = " + m.mem_kib);
    if (m.cpu_ms != null) out.push("cpu_ms = " + m.cpu_ms);
    if (m.fs != null) out.push("fs = " + m.fs);
    out.push("cards = " + m.cards);
    for (var i = 0; i < m.processes.length; i++) {
      var p = m.processes[i]; checkName("process", p.name);
      out.push(""); out.push("process = " + p.name);
      out.push("  source = " + p.source);
      out.push("  bytecode = " + p.bytecode);
      if (p.io != null) out.push("  io = " + p.io);
      if (p.entry != null) out.push("  entry = " + p.entry);
    }
    for (var j = 0; j < m.fifos.length; j++) {
      var f = m.fifos[j]; checkName("ipc_fifo", f.name);
      out.push(""); out.push("ipc_fifo = " + f.name);
      out.push("  from = " + f.frm);
      out.push("  to = " + f.to);
      out.push("  depth = " + f.depth);
      out.push("  frame_max = " + f.frame_max);
    }
    return out.join("\n") + "\n";
  }

  function parse(text) {
    var m = new Manifest("");
    var cur = null, curType = null;
    var lines = text.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].replace(/\r$/, "");
      if (line.trim() === "") continue;
      var indented = line.slice(0, 2) === "  ";
      var eq = line.indexOf("=");
      if (eq < 0) throw new Error("bad manifest line " + JSON.stringify(line) + ": expected 'key = value'");
      var key = line.slice(0, eq).trim(), val = line.slice(eq + 1).trim();
      if (!indented) {
        if (key === "capsule") continue;
        else if (key === "name") m.name = val;
        else if (key === "principal") m.principal = val;
        else if (key === "mem_kib") m.mem_kib = parseInt(val, 10);
        else if (key === "cpu_ms") m.cpu_ms = parseInt(val, 10);
        else if (key === "fs") m.fs = val;
        else if (key === "cards") m.cards = val;
        else if (key === "process") { cur = { name: val, source: 0, bytecode: 0, io: null, entry: null }; m.processes.push(cur); curType = "process"; }
        else if (key === "ipc_fifo") { cur = { name: val, frm: "", to: "", depth: 0, frame_max: 0 }; m.fifos.push(cur); curType = "fifo"; }
        else throw new Error("unknown manifest key " + key);
      } else if (curType === "process") {
        if (key === "source") cur.source = parseInt(val, 10);
        else if (key === "bytecode") cur.bytecode = parseInt(val, 10);
        else if (key === "io") cur.io = val;
        else if (key === "entry") cur.entry = val;
        else throw new Error("unknown process key " + key);
      } else if (curType === "fifo") {
        if (key === "from") cur.frm = val;
        else if (key === "to") cur.to = val;
        else if (key === "depth") cur.depth = parseInt(val, 10);
        else if (key === "frame_max") cur.frame_max = parseInt(val, 10);
        else throw new Error("unknown ipc_fifo key " + key);
      } else {
        throw new Error("indented line " + JSON.stringify(line) + " outside a block");
      }
    }
    return m;
  }

  return {
    Manifest: Manifest, serialize: serialize, parse: parse,
    formatAddress: formatAddress, parseAddress: parseAddress,
    sourceFor: sourceFor, codeFor: codeFor, isCapsulePack: isCapsulePack,
    CAPSULE_PACK_MIN: CAPSULE_PACK_MIN, CAPSULE_PACK_MAX: CAPSULE_PACK_MAX
  };
});
