// picovm.js -- PicoScript 16-opcode VM in JavaScript (browser + Node).
//
// Mirrors picoscript_vm.PicoVM and vm/picovm.c exactly (int32 semantics) so the
// same bytecode yields the same register file, output bytes and HTTP status in
// the browser as on host and bare metal. Exposes a step API for debugging.
//
// Usage:
//   const vm = new PicoVM();          // optional: new PicoVM({hooks: PV_HOOKS})
//   vm.load(words);                   // words: array of uint32
//   vm.run();                         // or: while (vm.step()) { inspect vm.regs }
//   vm.regs, vm.output, vm.httpStatus, vm.pc, vm.steps, vm.halted
(function (root, factory) {
  var hooks = (typeof module !== "undefined" && module.exports)
    ? require("./pico_hooks.js")
    : root.PV_HOOKS;
  var PicoVM = factory(hooks);
  if (typeof module !== "undefined" && module.exports) module.exports = PicoVM;
  else root.PicoVM = PicoVM;
})(typeof globalThis !== "undefined" ? globalThis : this, function (PV_HOOKS) {
  "use strict";

  var OP = {
    NOOP: 0x0, LOAD: 0x1, SAVE: 0x2, PIPE: 0x3, ADD: 0x4, SUB: 0x5, MUL: 0x6,
    DIV: 0x7, INC: 0x8, JUMP: 0x9, BRANCH: 0xA, CALL: 0xB, RETURN: 0xC,
    WAIT: 0xD, RAISE: 0xE, DSP: 0xF
  };
  var ADDR_REG = 0x1;
  var BR = { EQ: 0, NE: 1, LT: 2, GT: 3, LE: 4, GE: 5, Z: 6, NZ: 7, EOF: 8, ERR: 9 };

  function sx16(v) { v &= 0xFFFF; return (v & 0x8000) ? v - 0x10000 : v; }

  function PicoVM(opts) {
    opts = opts || {};
    this.hooks = opts.hooks || PV_HOOKS || { HOST_HOOK_BASE: 0x7000,
      NET_STATUS_BASE: 0x8000, NET_BODY_MARKER: 0xB000, NET_CLOSE_MARKER: 0xC000,
      NET_HEADER_BASE: 0x9000, CONTENT_TYPES: {}, BY_CODE: {} };
    this.maxSteps = opts.maxSteps || 1000000;
    // Optional external card store (PicoWAL). Must expose get(addr)->int and
    // set(addr,int); when present it persists across reset()/load(), modelling a
    // disk-backed card store. Default is an in-memory Map (VM parity unchanged).
    this._extCards = opts.cards || null;
    this.reset();
  }

  PicoVM.prototype.reset = function () {
    this.regs = new Int32Array(16);
    this.cards = this._extCards || new Map();   // PicoWAL store persists if external
    this.callStack = [];
    this.output = [];          // array of byte values (0..255)
    this.httpStatus = -1;
    this.httpType = null;
    this.queues = {};
    this.rng = 0x4F6CDD1D >>> 0;
    this.mem = new Uint8Array(65536);   // process arena (byte-addressable)
    this.arenaTop = 0x8000;             // bump pointer for Span.Materialize copies
    this.spans = [null];                // span table; handle = index (1-based)
    this.pc = 0;
    this.steps = 0;
    this.halted = false;
    this.waiting = false;
    this.program = [];
    this.log = [];
  };

  PicoVM.prototype.load = function (words) {
    this.reset();
    this.program = Array.prototype.slice.call(words);
  };

  PicoVM.prototype.run = function (words) {
    if (words) this.load(words);
    while (!this.halted && this.pc < this.program.length) {
      if (this.steps >= this.maxSteps) throw new Error("step budget exceeded");
      this.step();
    }
    return this;
  };

  // Execute one instruction. Returns false when halted / past end (for steppers).
  PicoVM.prototype.step = function () {
    if (this.halted || this.pc >= this.program.length) { this.halted = true; return false; }
    this.steps++;
    var w = this.program[this.pc] >>> 0;
    var op = (w >>> 28) & 0xF;
    var rd = (w >>> 24) & 0xF;
    var rs1 = (w >>> 20) & 0xF;
    var rs2 = (w >>> 16) & 0xF;
    var imm = w & 0xFFFF;
    var cur = this.pc;
    this.pc++;
    var r = this.regs;

    switch (op) {
      case OP.NOOP: this._noop(rd, rs1, rs2, imm); break;
      case OP.LOAD: r[rd] = this.cards.get(imm) | 0; break;
      case OP.SAVE: this.cards.set(imm, r[rs1] | 0); break;
      case OP.PIPE: this._emit(this.cards.get(imm) | 0); break;
      case OP.ADD: case OP.SUB: case OP.MUL: case OP.DIV: {
        var a = r[rs1] | 0;
        var b = (rs2 === ADDR_REG) ? (r[imm & 0xF] | 0) : sx16(imm);
        var res;
        if (op === OP.ADD) res = (a + b) | 0;
        else if (op === OP.SUB) res = (a - b) | 0;
        else if (op === OP.MUL) res = Math.imul(a, b);
        else res = (b !== 0) ? ((a / b) | 0) : 0;
        r[rd] = res;
        break;
      }
      case OP.INC: r[rd] = (r[rd] + 1) | 0; break;
      case OP.JUMP: this.pc = imm; break;
      case OP.BRANCH: if (this._cond(rs2, r[rd], r[rs1])) this.pc = cur + sx16(imm); break;
      case OP.CALL: this.callStack.push(this.pc); this.pc = imm; break;
      case OP.RETURN:
        if (this.callStack.length) this.pc = this.callStack.pop();
        else this.halted = true;
        break;
      case OP.WAIT: this.waiting = true; this.halted = true; break;
      case OP.RAISE: this.log.push("raise " + imm); break;
      case OP.DSP: this._dsp(rd, rs1, rs2, imm); break;
      default: this.halted = true; break;
    }
    return !this.halted;
  };

  PicoVM.prototype._cond = function (mode, a, b) {
    a = a | 0; b = b | 0;
    switch (mode) {
      case BR.EQ: return a === b;
      case BR.NE: return a !== b;
      case BR.LT: return a < b;
      case BR.GT: return a > b;
      case BR.LE: return a <= b;
      case BR.GE: return a >= b;
      case BR.Z: return a === 0;
      case BR.NZ: return a !== 0;
      default: return false;
    }
  };

  PicoVM.prototype._emit = function (val) {
    var v = val >>> 0;
    this.output.push((v >>> 24) & 0xFF, (v >>> 16) & 0xFF, (v >>> 8) & 0xFF, v & 0xFF);
  };

  PicoVM.prototype._noop = function (rd, rs1, rs2, imm) {
    var H = this.hooks;
    if ((imm & 0xFF00) === H.HOST_HOOK_BASE) {
      this._host(imm & 0xFF, rd, rs1, rs2, imm);
    } else if ((imm & 0xF000) === H.NET_STATUS_BASE) {
      this.httpStatus = imm & 0x0FFF;
    } else if ((imm & 0xF000) === 0xA000) {
      this.httpType = (H.CONTENT_TYPES && H.CONTENT_TYPES[imm]) || "application/octet-stream";
    } else if (imm === H.NET_CLOSE_MARKER) {
      this.halted = true;
    }
    // body / header / genuine noop: nothing
  };

  PicoVM.prototype._host = function (code, rd, rs1, rs2, imm) {
    var name = (this.hooks.BY_CODE && this.hooks.BY_CODE[code]) || ("hook_" + code);
    if (name === "Random.U32") {
      var x = this.rng >>> 0;
      x ^= (x << 13); x >>>= 0;
      x ^= (x >>> 7);
      x ^= (x << 17); x >>>= 0;
      this.rng = x >>> 0;
      this.regs[rd] = this.rng | 0;
      return;
    }
    // ---- memory + span / slice / materialize -----------------------------
    if (name === "Memory.Set") { this.mem[this.regs[rs1] & 0xFFFF] = this.regs[rs2] & 0xFF; return; }
    if (name === "Memory.Get") { this.regs[rd] = this.mem[this.regs[rs1] & 0xFFFF]; return; }
    if (name === "Span.Make") {
      this.spans.push({ ptr: this.regs[rs1] & 0xFFFF, len: Math.max(0, this.regs[rs2] | 0) });
      this.regs[rd] = this.spans.length - 1; return;
    }
    if (name === "Span.Slice") {                       // zero-copy sub-span VIEW
      var s = this.spans[this.regs[rs1]] || { ptr: 0, len: 0 };
      var off = Math.max(0, Math.min(this.regs[rs2] | 0, s.len));
      this.spans.push({ ptr: s.ptr + off, len: s.len - off });
      this.regs[rd] = this.spans.length - 1; return;
    }
    if (name === "Span.Materialize") {                 // memcpy to a new region (COPY)
      var sm = this.spans[this.regs[rs1]] || { ptr: 0, len: 0 };
      var dst = this.arenaTop; this.arenaTop += sm.len;
      for (var i = 0; i < sm.len; i++) this.mem[dst + i] = this.mem[sm.ptr + i];
      this.spans.push({ ptr: dst, len: sm.len });
      this.regs[rd] = this.spans.length - 1; return;
    }
    if (name === "Span.Len") { var sl = this.spans[this.regs[rs1]]; this.regs[rd] = sl ? sl.len : 0; return; }
    if (name === "Span.Get") {
      var sg = this.spans[this.regs[rs1]] || { ptr: 0, len: 0 };
      var idx = this.regs[rs2] | 0;
      this.regs[rd] = (idx >= 0 && idx < sg.len) ? this.mem[sg.ptr + idx] : 0; return;
    }
    if (name === "Queue.Enqueue") {
      (this.queues[rs1] = this.queues[rs1] || []).push(this.regs[rd] | 0);
      return;
    }
    if (name === "Queue.Dequeue") {
      var q = this.queues[rs1] || [];
      this.regs[rd] = q.length ? q.shift() : 0;
      return;
    }
    if (name === "Queue.Depth") {
      this.regs[rd] = (this.queues[rs1] || []).length;
      return;
    }
    // ---- program-level card store: Storage.* over a PicoStore --------------
    if (name.indexOf("Storage.") === 0) {
      if (this._storage(name.slice(8), rd, rs1, rs2)) return;
    }
    this.log.push("host " + name + " R" + rd + " R" + rs1);
  };

  // Resolve the optional PicoStore library (Node require / browser global).
  function storeLib() {
    if (typeof module !== "undefined" && module.exports) return require("./picostore.js");
    var g = (typeof globalThis !== "undefined") ? globalThis
          : (typeof self !== "undefined") ? self : this;
    return g.PicoStore;
  }

  PicoVM.prototype._spanStr = function (handle) {
    if (handle <= 0 || handle >= this.spans.length) return "";
    var s = this.spans[handle];
    if (!s) return "";
    return new TextDecoder("utf-8").decode(this.mem.subarray(s.ptr, s.ptr + s.len));
  };

  PicoVM.prototype._strSpan = function (text) {
    var b = new TextEncoder().encode(text);
    var dst = this.arenaTop; this.arenaTop += b.length;
    for (var i = 0; i < b.length; i++) this.mem[dst + i] = b[i];
    this.spans.push({ ptr: dst, len: b.length });
    return this.spans.length - 1;
  };

  // Mirrors picoscript_vm HostApi._storage. Context model (cur pack + card)
  // keeps every op within the 2-in/1-out host ABI; field names and queries are
  // UTF-8 byte-spans the program builds in arena memory.
  PicoVM.prototype._storage = function (method, rd, rs1, rs2) {
    if (!this._st) {
      var ST = storeLib();
      this._st = { store: new ST.PicoStore(), pack: 0, card: 0, results: [] };
    }
    var st = this._st;
    var pack = String(st.pack);
    if (method === "UsePack") { st.pack = this.regs[rs1] | 0; this.regs[rd] = st.pack; return true; }
    if (method === "AddCard") { var cid = st.store.create(pack, {}); st.card = cid; this.regs[rd] = cid; return true; }
    if (method === "EditCard") {
      var eid = this.regs[rs1] | 0, ok = st.store.read(pack, eid) !== null;
      st.card = ok ? eid : 0; this.regs[rd] = ok ? eid : 0; return true;
    }
    if (method === "DeleteCard") {
      var did = this.regs[rs1] | 0, dok = st.store.delete(pack, did);
      if (did === st.card) st.card = 0;
      this.regs[rd] = dok ? 1 : 0; return true;
    }
    if (method === "GetField") {
      var grec = st.store.read(pack, st.card) || {}, gn = this._spanStr(this.regs[rs1]);
      var gv = grec.hasOwnProperty(gn) ? grec[gn] : 0;
      this.regs[rd] = (typeof gv === "number") ? (gv | 0) : 0; return true;
    }
    if (method === "SetField") {
      var sn = this._spanStr(this.regs[rs1]), srec = st.store.read(pack, st.card);
      if (srec === null) { this.regs[rd] = 0; return true; }
      srec[sn] = this.regs[rs2] | 0;
      this.regs[rd] = st.store.update(pack, st.card, srec) ? 1 : 0; return true;
    }
    if (method === "SetFieldStr") {
      var tn = this._spanStr(this.regs[rs1]), trec = st.store.read(pack, st.card);
      if (trec === null) { this.regs[rd] = 0; return true; }
      trec[tn] = this._spanStr(this.regs[rs2]);
      this.regs[rd] = st.store.update(pack, st.card, trec) ? 1 : 0; return true;
    }
    if (method === "GetFieldStr") {
      var frec = st.store.read(pack, st.card) || {}, fn = this._spanStr(this.regs[rs1]);
      var fv = frec.hasOwnProperty(fn) ? frec[fn] : "";
      this.regs[rd] = this._strSpan(typeof fv === "string" ? fv : String(fv)); return true;
    }
    if (method === "QueryCard") {
      var q = this._spanStr(this.regs[rs1]);
      st.results = st.store.query(pack, q).map(function (e) { return e[0]; });
      this.regs[rd] = st.results.length; return true;
    }
    if (method === "QueryResult") {
      var qi = this.regs[rs1] | 0;
      this.regs[rd] = (qi >= 0 && qi < st.results.length) ? st.results[qi] : 0; return true;
    }
    return false;
  };

  PicoVM.prototype._dsp = function (rd, rs1, rs2, imm) {
    var a = this.regs[rs1] | 0;
    if (rs2 === 0x4) this.regs[rd] = a < 0 ? 0 : a;          // RELU
    else if (rs2 === 0x3) this.regs[rd] = Math.imul(a, sx16(imm)); // SCALE
    else if (rs2 === 0x9) this.regs[rd] = (a + (this.regs[imm & 0xF] | 0)) | 0; // VADD
    else this.log.push("dsp " + rs2);
  };

  // Convenience: signed-32 PRINT/PIPE output as integers (4-byte big-endian).
  PicoVM.prototype.outputInts = function () {
    var out = [];
    for (var i = 0; i + 3 < this.output.length; i += 4) {
      var v = ((this.output[i] << 24) | (this.output[i + 1] << 16) |
               (this.output[i + 2] << 8) | this.output[i + 3]) | 0;
      out.push(v);
    }
    return out;
  };

  return PicoVM;
});
