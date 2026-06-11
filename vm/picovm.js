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
  var ADDR_REG_OFF = 0x3;
  var BR = { EQ: 0, NE: 1, LT: 2, GT: 3, LE: 4, GE: 5, Z: 6, NZ: 7, EOF: 8, ERR: 9 };
  var FAULT = { STEP_BUDGET: 1, BAD_OPCODE: 2, BAD_JUMP: 3, TEMPLATE: 7, CAPABILITY: 8, ALLOC: 9, CONST_WRITE: 10 };

  function picoFault(code, pc, detail, msg) {
    var e = new Error(msg);
    e.fault = code;
    e.pc = (pc == null) ? 0 : pc;
    e.detail = (detail == null) ? 0 : detail;
    return e;
  }

  function sx16(v) { v &= 0xFFFF; return (v & 0x8000) ? v - 0x10000 : v; }

  // Binding capability classes (INV-17). Bit values match vm/picovm.h PV_CAP_* and
  // picoscript_vm.CAP_*; pure computation needs none (class 0, always allowed).
  var CAP = { KERNEL: 1, QUEUE: 2, RANDOM: 4, STORAGE: 8, TIME: 16, NET: 32, CONTEXT: 64, AUTH: 128, ENV: 256 };
  var CAP_ALL = 0x1FF;
  var CAP_BY_NS = { Kernel: CAP.KERNEL, Queue: CAP.QUEUE, Random: CAP.RANDOM,
    Req: CAP.NET, Resp: CAP.NET, Net: CAP.NET, Storage: CAP.STORAGE, DateTime: CAP.TIME,
    Context: CAP.CONTEXT, Auth: CAP.AUTH, X509: CAP.AUTH, Environment: CAP.ENV, Locale: CAP.ENV };
  function hookCap(name) {   // "Ns.Method" -> required capability class (0 = pure)
    var dot = name.indexOf("."), ns = name.slice(0, dot), m = name.slice(dot + 1);
    if (ns === "Maths" && (m === "Random" || m === "RandomRange")) return CAP.RANDOM;
    if (ns === "Crypto" && m === "RandomBytes") return CAP.RANDOM;
    if (ns === "Http" && (m === "ReadHeader" || m === "ReadBody" || m === "GenerateHeaders" || m === "GenerateResponse")) return CAP.NET;
    return CAP_BY_NS[ns] || 0;
  }

  function PicoVM(opts) {
    opts = opts || {};
    this.hooks = opts.hooks || PV_HOOKS || { HOST_HOOK_BASE: 0x7000,
      EXT_HOST_HOOK_BASE: 0x6000,
      NET_STATUS_BASE: 0x8000, NET_BODY_MARKER: 0xB000, NET_CLOSE_MARKER: 0xC000,
      NET_HEADER_BASE: 0x9000, CONTENT_TYPES: {}, BY_CODE: {} };
    this.maxSteps = opts.maxSteps || 1000000;
    this.caps = (opts.caps !== undefined) ? (opts.caps >>> 0) : CAP_ALL;  // granted bindings (INV-17)
    this._seed = (opts.seed !== undefined) ? (opts.seed >>> 0) : null;     // host-injected Random.U32 seed (INV-15)
    this.noAlloc = !!opts.noAlloc;          // hot-path no-allocation mode (INV-5)
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
    this.rng = (this._seed !== null) ? this._seed : (0x4F6CDD1D >>> 0);
    this.hostStatus = 0;                      // INV-18: typed status of the last fallible hook
    this.constFloor = 0x8000;                 // INV-9: lowest literal const-pool address ([floor,0x8000) RO)
    this.mem = new Uint8Array(520 * 1024);   // process arena = RP2350 (Pico 2) 520 KB SRAM
    this.dotLen = 0;                          // active span length for Dot8.Of
    this.arenaTop = 0x8000;             // bump pointer for Span.Materialize copies
    this.spans = [null];                // span table; handle = index (1-based)
    this.pc = 0;
    this.curPc = 0;
    this.steps = 0;
    this.halted = false;
    this.waiting = false;
    this.program = [];
    this.log = [];
    // Simulated PIOS I/O binding: one bound request context (I4) and one
    // response descriptor graph builder (I2) per VM invocation.
    this.requestContext = null;
    this.responseGraph = [];
    this.responseSealed = false;
    this.responseEnded = false;
    this.responseMode = null;            // 'unary' | 'stream' (set at Seal / terminal verb)
    this.responseBodyStarted = false;    // first Resp.Write opens the body phase
    this.responseStreamClosed = false;   // Resp.EndStream closes the stream/body phase
    this._handlerMark = null;   // per-request arena scope (auto rewind on each request)
  };

  PicoVM.prototype.load = function (words) {
    this.reset();
    this.program = Array.prototype.slice.call(words);
  };

  // INV-10: static verification before execution -- reject out-of-range immediate
  // JUMP/CALL/BRANCH targets (register/indexed jumps are dynamic -> runtime-checked).
  PicoVM.prototype.verify = function () {
    var n = this.program.length;
    for (var i = 0; i < n; i++) {
      var w = this.program[i] >>> 0;
      var op = (w >>> 28) & 0xF, rs2 = (w >>> 16) & 0xF, imm = w & 0xFFFF, tgt;
      if (op === OP.JUMP && rs2 === 0) tgt = imm;
      else if (op === OP.CALL) tgt = imm;
      else if (op === OP.BRANCH) tgt = i + sx16(imm);
      else continue;
      if (tgt < 0 || tgt > n) throw picoFault(FAULT.BAD_JUMP, i, tgt, "bad static target " + tgt + " at pc=" + i);
    }
  };

  PicoVM.prototype.run = function (words) {
    if (words) this.load(words);
    this.verify();                       // INV-10: verify before execution
    while (!this.halted && this.pc < this.program.length) {
      if (this.steps >= this.maxSteps) throw picoFault(FAULT.STEP_BUDGET, this.pc, 0, "step budget exceeded");
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
    this.curPc = cur;
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
      case OP.JUMP: {
        var jt;
        if (rs2 === ADDR_REG) jt = r[rs1] & 0xFFFF;
        else if (rs2 === ADDR_REG_OFF) jt = (r[rs1] + imm) & 0xFFFF;
        else jt = imm;
        if (jt < 0 || jt > this.program.length) throw picoFault(FAULT.BAD_JUMP, cur, jt, "bad jump target " + jt);  // INV-11
        this.pc = jt;
        break;
      }
      case OP.BRANCH:
        if (this._cond(rs2, r[rd], r[rs1])) {
          var bt = cur + sx16(imm);
          if (bt < 0 || bt > this.program.length) throw picoFault(FAULT.BAD_JUMP, cur, bt, "bad branch target " + bt);
          this.pc = bt;
        }
        break;
      case OP.CALL:
        if (imm < 0 || imm > this.program.length) throw picoFault(FAULT.BAD_JUMP, cur, imm, "bad call target " + imm);
        this.callStack.push(this.pc); this.pc = imm;
        break;
      case OP.RETURN:
        if (this.callStack.length) this.pc = this.callStack.pop();
        else this.halted = true;
        break;
      case OP.WAIT: this.waiting = true; this.halted = true; break;
      case OP.RAISE: this.log.push("raise " + imm); break;
      case OP.DSP: this._dsp(rd, rs1, rs2, imm); break;
      default: throw picoFault(FAULT.BAD_OPCODE, cur, op, "bad opcode " + op);   // INV-10: unknown opcode faults (was silent halt)
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
    } else if ((imm & 0xF000) === (H.EXT_HOST_HOOK_BASE || 0x6000)) {
      this._host(imm & 0xFFF, rd, rs1, rs2, imm);
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
    // INV-17: bindings are not ambient -- deny the hook unless its class is granted.
    var need = hookCap(name);
    if (need && !(this.caps & need)) throw picoFault(FAULT.CAPABILITY, this.curPc, code, "capability denied: " + name);
    if (name === "Status.Last") { this.regs[rd] = this.hostStatus | 0; return; }   // INV-18
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
    if (name === "Memory.Set") {
      var ma = (this.regs[rs1] >>> 0) % (520 * 1024);
      if (ma >= this.constFloor && ma < 0x8000) throw picoFault(FAULT.CONST_WRITE, this.curPc, ma, "write to read-only literal const region");  // INV-9
      this.mem[ma] = this.regs[rs2] & 0xFF; return;
    }
    if (name === "Memory.SetConst") {   // INV-9: compiler-only literal write
      var mc = (this.regs[rs1] >>> 0) % (520 * 1024);
      this.mem[mc] = this.regs[rs2] & 0xFF;
      if (mc < this.constFloor) this.constFloor = mc;
      return;
    }
    if (name === "Memory.Get") { this.regs[rd] = this.mem[(this.regs[rs1] >>> 0) % (520 * 1024)]; return; }
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
    // ---- Arena scopes: Mark / Rewind / Reset the bump arena ----------------
    if (name === "Arena.Mark") {
      this.regs[rd] = ((((this.spans.length & 0x7FF) << 20) | (this.arenaTop & 0xFFFFF)) | 0); return;
    }
    if (name === "Arena.Rewind") {
      var mk = this.regs[rs1] >>> 0;
      this.arenaTop = mk & 0xFFFFF;
      var ac = (mk >>> 20) & 0x7FF; if (ac < 1) ac = 1;
      if (ac < this.spans.length) this.spans.length = ac;
      return;
    }
    if (name === "Arena.Reset") { this.arenaTop = 0x8000; this.spans = [null]; return; }
    // ---- EL0-facing PIOS request/response hooks ---------------------------
    if (name.indexOf("Req.") === 0) {
      if (this._req(name.slice(4), rd, rs1, rs2)) return;
    }
    if (name.indexOf("Resp.") === 0) {
      if (this._resp(name.slice(5), rd, rs1, rs2)) return;
    }
    if (name === "Queue.Enqueue") {
      (this.queues[rs1] = this.queues[rs1] || []).push(this.regs[rd] | 0);
      return;
    }
    if (name === "Queue.Dequeue") {
      var q = this.queues[rs1] || [];
      this.hostStatus = q.length ? 0 : 3;     // INV-18: EMPTY
      this.regs[rd] = q.length ? q.shift() : 0;
      return;
    }
    if (name === "Queue.Depth") {
      this.regs[rd] = (this.queues[rs1] || []).length;
      return;
    }
    if (name.indexOf("Bits.") === 0) {
      var ba = this.regs[rs1] | 0;
      var bb = this.regs[rs2] | 0;
      var bs = bb & 31;
      if (name === "Bits.And") { this.regs[rd] = (ba & bb) | 0; return; }
      if (name === "Bits.Or")  { this.regs[rd] = (ba | bb) | 0; return; }
      if (name === "Bits.Xor") { this.regs[rd] = (ba ^ bb) | 0; return; }
      if (name === "Bits.Shl") { this.regs[rd] = (ba << bs) | 0; return; }
      if (name === "Bits.Shr") { this.regs[rd] = (ba >>> bs) | 0; return; }
      if (name === "Bits.Sar") { this.regs[rd] = (ba >> bs) | 0; return; }
      if (name === "Bits.Not") { this.regs[rd] = (~ba) | 0; return; }
    }
    if (name.indexOf("Dot8.") === 0) {
      if (name === "Dot8.Len") { this.dotLen = this.regs[rs1] >>> 0; return; }
      if (name === "Dot8.Of") {
        var n = this.dotLen | 0, sz = this.mem.length;
        var wp = (this.regs[rs1] >>> 0) % sz, ap = (this.regs[rs2] >>> 0) % sz;
        var acc = 0;
        for (var di = 0; di < n; di++) {
          var w8 = this.mem[(wp + di) % sz]; if (w8 > 127) w8 -= 256;
          var a8 = this.mem[(ap + di) % sz]; if (a8 > 127) a8 -= 256;
          acc = (acc + w8 * a8) | 0;
        }
        this.regs[rd] = acc | 0;
        return;
      }
    }
    // ---- program-level card store: Storage.* over a PicoStore --------------
    if (name.indexOf("Storage.") === 0) {
      if (this._storage(name.slice(8), rd, rs1, rs2)) return;
    }
    // ---- String.* arena string library -------------------------------------
    if (name.indexOf("String.") === 0) {
      if (this._stringlib(name.slice(7), rd, rs1, rs2)) return;
    }
    // ---- Number.* integer/format library -----------------------------------
    if (name.indexOf("Number.") === 0) {
      if (this._numberlib(name.slice(7), rd, rs1, rs2)) return;
    }
    // ---- Template.* (AOT compile-at-save + render) -------------------------
    if (name.indexOf("Template.") === 0) {
      if (this._templatelib(name.slice(9), rd, rs1, rs2)) return;
    }
    // ---- Maths.* pure-integer ops (Power/Sqrt) -----------------------------
    if (name.indexOf("Maths.") === 0) {
      if (this._mathslib(name.slice(6), rd, rs1, rs2)) return;
    }
    // ---- Compress.* (RLE) / Crypto.* (Sha256) / Html.* (entities) ----------
    if (name.indexOf("Compress.") === 0) { if (this._compresslib(name.slice(9), rd, rs1, rs2)) return; }
    if (name.indexOf("Crypto.") === 0) { if (this._cryptolib(name.slice(7), rd, rs1, rs2)) return; }
    if (name.indexOf("Html.") === 0) { if (this._htmllib(name.slice(5), rd, rs1, rs2)) return; }
    if (name.indexOf("Http.") === 0) { if (this._httplib(name.slice(5), rd, rs1, rs2)) return; }
    // ---- Io: write raw bytes (UTF-8 strings) to the output buffer ----------
    if (name === "Io.Write") {
      var sw = this.spans[this.regs[rs1]];
      if (sw) { for (var iw = 0; iw < sw.len; iw++) this.output.push(this.mem[sw.ptr + iw]); }
      return;
    }
    if (name === "Io.WriteByte") { this.output.push(this.regs[rs1] & 0xFF); return; }
    // ---- text/binary primitives: Utf8Writer / Utf8Reader / Json / Xml -----
    if (name.indexOf("Utf8Writer.") === 0 || name.indexOf("Utf8Reader.") === 0 ||
        name.indexOf("Json.") === 0 || name.indexOf("Xml.") === 0) {
      var dot = name.indexOf(".");
      if (this._textio(name.slice(0, dot), name.slice(dot + 1), rd, rs1, rs2)) return;
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

  function _bcmp(a, b, off) { for (var k = 0; k < b.length; k++) if (a[off + k] !== b[k]) return false; return true; }
  function _bfind(a, n) { if (!n.length) return 0; for (var i = 0; i + n.length <= a.length; i++) if (_bcmp(a, n, i)) return i; return -1; }
  function _bfind2(a, b0, b1, start) { for (var i = start; i + 1 < a.length; i++) if (a[i] === b0 && a[i + 1] === b1) return i; return -1; }
  function _ws(c) { return c === 32 || c === 9 || c === 10 || c === 13; }
  function _keystr(arr) { var s = ""; for (var i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]); return s; }

  PicoVM.prototype._spanBytes = function (h) {
    if (h <= 0 || h >= this.spans.length || !this.spans[h]) return [];
    var s = this.spans[h], out = new Array(s.len);
    for (var i = 0; i < s.len; i++) out[i] = this.mem[s.ptr + i];
    return out;
  };
  PicoVM.prototype._newSpanBytes = function (bytes) {
    if (this.noAlloc) { var e = new Error("arena allocation in no-alloc mode"); e.fault = 9; e.pc = this.pc | 0; e.detail = bytes.length | 0; throw e; }  // INV-5
    var dst = this.arenaTop; this.arenaTop += bytes.length;
    for (var i = 0; i < bytes.length; i++) this.mem[dst + i] = bytes[i] & 255;
    this.spans.push({ ptr: dst, len: bytes.length });
    return this.spans.length - 1;
  };
  PicoVM.prototype._stringlib = function (method, rd, rs1, rs2) {
    var a = this._spanBytes(this.regs[rs1]);
    if (method === "Length") { this.regs[rd] = a.length; return true; }
    if (method === "Concat") { this.regs[rd] = this._newSpanBytes(a.concat(this._spanBytes(this.regs[rs2]))); return true; }
    if (method === "Substring") { var st = Math.max(0, this.regs[rs2] | 0); this.regs[rd] = this._newSpanBytes(a.slice(st)); return true; }
    if (method === "IndexOf") { var ix = _bfind(a, this._spanBytes(this.regs[rs2])); this.hostStatus = ix >= 0 ? 0 : 1; this.regs[rd] = ix | 0; return true; }
    if (method === "StartsWith") { var p = this._spanBytes(this.regs[rs2]); this.regs[rd] = (p.length <= a.length && _bcmp(a, p, 0)) ? 1 : 0; return true; }
    if (method === "EndsWith") { var su = this._spanBytes(this.regs[rs2]); this.regs[rd] = (su.length <= a.length && _bcmp(a, su, a.length - su.length)) ? 1 : 0; return true; }
    if (method === "ToUpper") { this.regs[rd] = this._newSpanBytes(a.map(function (c) { return (c >= 97 && c <= 122) ? c - 32 : c; })); return true; }
    if (method === "ToLower") { this.regs[rd] = this._newSpanBytes(a.map(function (c) { return (c >= 65 && c <= 90) ? c + 32 : c; })); return true; }
    if (method === "Trim") { var i = 0, j = a.length; while (i < j && _ws(a[i])) i++; while (j > i && _ws(a[j - 1])) j--; this.regs[rd] = this._newSpanBytes(a.slice(i, j)); return true; }
    if (method === "SetReplace") { this._strRepl = a; return true; }
    if (method === "Replace") {
      var needle = this._spanBytes(this.regs[rs2]), repl = this._strRepl || [], out = [], k = 0;
      if (needle.length === 0) { this.regs[rd] = this._newSpanBytes(a); return true; }
      while (k < a.length) {
        if (k + needle.length <= a.length && _bcmp(a, needle, k)) { for (var m = 0; m < repl.length; m++) out.push(repl[m]); k += needle.length; }
        else { out.push(a[k]); k++; }
      }
      this.regs[rd] = this._newSpanBytes(out); return true;
    }
    return false;
  };

  function _strBytes(s) { var o = []; for (var i = 0; i < s.length; i++) o.push(s.charCodeAt(i) & 255); return o; }

  PicoVM.prototype._numberlib = function (method, rd, rs1, rs2) {
    if (method === "Parse") {
      var bb = this._spanBytes(this.regs[rs1]);
      var str = String.fromCharCode.apply(null, bb).trim();
      var ok = /^[+-]?\d+$/.test(str);
      this.hostStatus = ok ? 0 : 2;            // INV-18: PARSE_ERROR
      this.regs[rd] = (ok ? parseInt(str, 10) : 0) | 0;
      return true;
    }
    var a = this.regs[rs1] | 0, b = this.regs[rs2] | 0;
    if (method === "Abs") { this.regs[rd] = (a < 0 ? -a : a) | 0; return true; }
    if (method === "Min") { this.regs[rd] = (a < b ? a : b) | 0; return true; }
    if (method === "Max") { this.regs[rd] = (a > b ? a : b) | 0; return true; }
    if (method === "Floor" || method === "Ceiling" || method === "Round") { this.regs[rd] = a | 0; return true; }
    if (method === "ToString") { this.regs[rd] = this._newSpanBytes(_strBytes(String(a))); return true; }
    if (method === "ToHex") { this.regs[rd] = this._newSpanBytes(_strBytes((a >>> 0).toString(16))); return true; }
    if (method === "ToOctal") { this.regs[rd] = this._newSpanBytes(_strBytes((a >>> 0).toString(8))); return true; }
    if (method === "ToBinary") { this.regs[rd] = this._newSpanBytes(_strBytes((a >>> 0).toString(2))); return true; }
    return false;
  };

  // ── Q16.16 fixed-point CORDIC (Maths.Sin/Cos/Tan, ...) ──────────────────────
  // All-integer; constants/iteration count shared verbatim with picoscript_vm.py
  // (_q16_*) and vm/picovm.c so results are byte-identical on every path.
  var Q16_ONE = 65536, Q16_HALF_PI = 102944, Q16_PI = 205887, Q16_TWO_PI = 411775, Q16_GAIN_INV = 39797;
  var Q16_ATAN = [51472, 30386, 16055, 8150, 4091, 2047, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2];
  function q16Sincos(angle) {
    var a = (angle | 0) % Q16_TWO_PI;
    if (a < 0) a += Q16_TWO_PI;
    var q = (a / Q16_HALF_PI) | 0;
    var r = a - q * Q16_HALF_PI;
    var x = Q16_GAIN_INV, y = 0, z = r, i, dx, dy;
    for (i = 0; i < 16; i++) {
      dx = x >> i; dy = y >> i;
      if (z >= 0) { x = (x - dy) | 0; y = (y + dx) | 0; z -= Q16_ATAN[i]; }
      else { x = (x + dy) | 0; y = (y - dx) | 0; z += Q16_ATAN[i]; }
    }
    if (q === 0) return [y, x];
    if (q === 1) return [x, (-y) | 0];
    if (q === 2) return [(-y) | 0, (-x) | 0];
    return [(-x) | 0, y];
  }
  function q16Tan(angle) {
    var sc = q16Sincos(angle), s = sc[0], c = sc[1];
    if (c === 0) return s >= 0 ? 0x7FFFFFFF : -0x80000000;
    return Number(BigInt.asIntN(32, (BigInt(s) * 65536n) / BigInt(c)));   // trunc toward zero
  }

  PicoVM.prototype._mathslib = function (method, rd, rs1, rs2) {
    if (method === "Sin") { this.regs[rd] = q16Sincos(this.regs[rs1] | 0)[0] | 0; return true; }
    if (method === "Cos") { this.regs[rd] = q16Sincos(this.regs[rs1] | 0)[1] | 0; return true; }
    if (method === "Tan") { this.regs[rd] = q16Tan(this.regs[rs1] | 0) | 0; return true; }
    if (method === "Power") {
      var base = this.regs[rs1] | 0, exp = this.regs[rs2] | 0;
      if (exp <= 0) { this.regs[rd] = (exp === 0 ? 1 : 0) | 0; return true; }
      var r = 1, cap = exp < 0xFFFF ? exp : 0xFFFF;
      for (var t = 0; t < cap; t++) r = Math.imul(r, base);   // 32-bit modular multiply
      this.regs[rd] = r | 0; return true;
    }
    if (method === "Sqrt") {
      var n = this.regs[rs1] | 0;
      if (n <= 0) { this.regs[rd] = 0; return true; }
      var x = n, res = 0, bit = 1 << 30;
      while (bit > n) bit >>= 2;
      while (bit) {
        if (x >= res + bit) { x -= res + bit; res = (res >> 1) + bit; }
        else { res >>= 1; }
        bit >>= 2;
      }
      this.regs[rd] = res | 0; return true;
    }
    return false;
  };

  PicoVM.prototype._compresslib = function (method, rd, rs1, rs2) {
    var src = this._spanBytes(this.regs[rs1]);
    if (method === "PicoCompress") {
      var out = [], i = 0;
      while (i < src.length) {
        var c = 1;
        while (i + c < src.length && src[i + c] === src[i] && c < 255) c++;
        out.push(c, src[i]); i += c;
      }
      this.regs[rd] = this._newSpanBytes(out); return true;
    }
    if (method === "PicoDecompress") {
      var out = [], i = 0;
      while (i + 1 < src.length) { var cnt = src[i], b = src[i + 1]; i += 2; for (var t = 0; t < cnt; t++) out.push(b); }
      this.regs[rd] = this._newSpanBytes(out); return true;
    }
    return false;
  };

  PicoVM.prototype._cryptolib = function (method, rd, rs1, rs2) {
    if (method === "Sha256") { this.regs[rd] = this._newSpanBytes(_sha256(this._spanBytes(this.regs[rs1]))); return true; }
    if (method === "HmacSha256") { this.regs[rd] = this._newSpanBytes(_hmacSha256(this._spanBytes(this.regs[rs1]), this._spanBytes(this.regs[rs2]))); return true; }
    return false;
  };

  PicoVM.prototype._htmllib = function (method, rd, rs1, rs2) {
    var s = _keystr(this._spanBytes(this.regs[rs1]));
    if (method === "Encode") {
      s = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
      this.regs[rd] = this._newSpanBytes(_strBytes(s)); return true;
    }
    if (method === "Decode") {
      s = s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
      this.regs[rd] = this._newSpanBytes(_strBytes(s)); return true;
    }
    return false;
  };

  function _urldecode(b) {
    var out = [], i = 0;
    while (i < b.length) {
      var c = b[i];
      if (c === 0x2b) { out.push(0x20); i += 1; }
      else if (c === 0x25 && i + 2 < b.length) {
        var hx = String.fromCharCode(b[i + 1], b[i + 2]);
        if (/^[0-9a-fA-F]{2}$/.test(hx)) { out.push(parseInt(hx, 16)); i += 3; }
        else { out.push(c); i += 1; }
      } else { out.push(c); i += 1; }
    }
    return out;
  }

  function _jsonesc(b) {
    var out = [];
    for (var i = 0; i < b.length; i++) {
      var c = b[i];
      if (c === 0x22) { out.push(0x5c, 0x22); }
      else if (c === 0x5c) { out.push(0x5c, 0x5c); }
      else if (c === 0x0a) { out.push(0x5c, 0x6e); }
      else if (c === 0x0d) { out.push(0x5c, 0x72); }
      else if (c === 0x09) { out.push(0x5c, 0x74); }
      else if (c < 0x20) {
        var hx = ("0000" + c.toString(16)).slice(-4);
        out.push(0x5c, 0x75);
        for (var j = 0; j < 4; j++) out.push(hx.charCodeAt(j));
      } else { out.push(c); }
    }
    return out;
  }

  PicoVM.prototype._parseJsonToModel = function (s) {
    var n = s.length, pos = [0], out = [];
    function isws(c) { return c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d; }
    function hx(c) { return (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x46) || (c >= 0x61 && c <= 0x66); }
    function skipws() { while (pos[0] < n && isws(s[pos[0]])) pos[0] += 1; }
    function pushAll(dst, arr) { for (var k = 0; k < arr.length; k++) dst.push(arr[k]); }
    function parseString() {
      var b = []; pos[0] += 1;
      while (pos[0] < n) {
        var c = s[pos[0]]; pos[0] += 1;
        if (c === 0x22) break;
        if (c === 0x5c && pos[0] < n) {
          var e = s[pos[0]]; pos[0] += 1;
          if (e === 0x6e) b.push(0x0a);
          else if (e === 0x74) b.push(0x09);
          else if (e === 0x72) b.push(0x0d);
          else if (e === 0x62) b.push(0x08);
          else if (e === 0x66) b.push(0x0c);
          else if (e === 0x75 && pos[0] + 4 <= n && hx(s[pos[0]]) && hx(s[pos[0] + 1]) && hx(s[pos[0] + 2]) && hx(s[pos[0] + 3])) {
            var cp = parseInt(String.fromCharCode(s[pos[0]], s[pos[0] + 1], s[pos[0] + 2], s[pos[0] + 3]), 16); pos[0] += 4;
            if (cp < 0x80) b.push(cp);
            else if (cp < 0x800) { b.push(0xC0 | (cp >> 6)); b.push(0x80 | (cp & 0x3F)); }
            else { b.push(0xE0 | (cp >> 12)); b.push(0x80 | ((cp >> 6) & 0x3F)); b.push(0x80 | (cp & 0x3F)); }
          } else b.push(e);
        } else b.push(c);
      }
      return b;
    }
    function childKey(prefix, key) {
      if (prefix.length === 0) return key.slice();
      var nk = prefix.slice(); nk.push(0x2e); pushAll(nk, key); return nk;
    }
    function emit(prefix, depth) {
      if (depth > 64) return;   // INV-20: bound JSON nesting depth (matches C pjs_emit depth>64)
      skipws();
      if (pos[0] >= n) return;
      var c = s[pos[0]];
      if (c === 0x7b) {
        pos[0] += 1; skipws();
        if (pos[0] < n && s[pos[0]] === 0x7d) { pos[0] += 1; return; }
        while (pos[0] < n) {
          skipws();
          if (pos[0] >= n || s[pos[0]] !== 0x22) break;
          var key = parseString(); skipws();
          if (pos[0] < n && s[pos[0]] === 0x3a) pos[0] += 1;
          emit(childKey(prefix, key), depth + 1); skipws();
          if (pos[0] < n && s[pos[0]] === 0x2c) { pos[0] += 1; continue; }
          if (pos[0] < n && s[pos[0]] === 0x7d) pos[0] += 1;
          break;
        }
      } else if (c === 0x5b) {
        pos[0] += 1; skipws();
        if (pos[0] < n && s[pos[0]] === 0x5d) { pos[0] += 1; return; }
        var idx = 0;
        while (pos[0] < n) {
          var ik = _strBytes(String(idx));
          emit(childKey(prefix, ik), depth + 1); idx += 1; skipws();
          if (pos[0] < n && s[pos[0]] === 0x2c) { pos[0] += 1; continue; }
          if (pos[0] < n && s[pos[0]] === 0x5d) pos[0] += 1;
          break;
        }
      } else if (c === 0x22) {
        var v = parseString(); pushAll(out, prefix); out.push(0x3d); pushAll(out, v); out.push(0x0a);
      } else {
        var start = pos[0];
        while (pos[0] < n) { var cc = s[pos[0]]; if (cc === 0x2c || cc === 0x7d || cc === 0x5d || isws(cc)) break; pos[0] += 1; }
        pushAll(out, prefix); out.push(0x3d); for (var q = start; q < pos[0]; q++) out.push(s[q]); out.push(0x0a);
      }
    }
    skipws(); emit([], 0);
    return out;
  };

  PicoVM.prototype._httplib = function (method, rd, rs1, rs2) {
    var src = this._spanBytes(this.regs[rs1]);
    if (method === "ParseQuery" || method === "ParseForm") {
      var out = [], pairs = [], cur = [];
      for (var p = 0; p < src.length; p++) { if (src[p] === 0x26) { pairs.push(cur); cur = []; } else cur.push(src[p]); }
      pairs.push(cur);
      for (var pi = 0; pi < pairs.length; pi++) {
        var pr = pairs[pi]; if (!pr.length) continue;
        var eq = pr.indexOf(0x3d), k, v;
        if (eq >= 0) { k = pr.slice(0, eq); v = pr.slice(eq + 1); } else { k = pr; v = []; }
        var dk = _urldecode(k), dv = _urldecode(v), a;
        for (a = 0; a < dk.length; a++) out.push(dk[a]);
        out.push(0x3d);
        for (a = 0; a < dv.length; a++) out.push(dv[a]);
        out.push(0x0a);
      }
      this.regs[rd] = this._newSpanBytes(out); return true;
    }
    if (method === "EncodeJson") {
      var lines = [], ln = [], i2;
      for (i2 = 0; i2 < src.length; i2++) { if (src[i2] === 0x0a) { lines.push(ln); ln = []; } else ln.push(src[i2]); }
      lines.push(ln);
      var jo = [0x7b], first = true;
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li], eq2 = line.indexOf(0x3d);
        if (eq2 < 0) continue;
        var ek = _jsonesc(line.slice(0, eq2)), ev = _jsonesc(line.slice(eq2 + 1)), a2;
        if (!first) jo.push(0x2c);
        first = false;
        jo.push(0x22); for (a2 = 0; a2 < ek.length; a2++) jo.push(ek[a2]);
        jo.push(0x22, 0x3a, 0x22); for (a2 = 0; a2 < ev.length; a2++) jo.push(ev[a2]);
        jo.push(0x22);
      }
      jo.push(0x7d);
      this.regs[rd] = this._newSpanBytes(jo); return true;
    }
    if (method === "ParseJson") {
      this.regs[rd] = this._newSpanBytes(this._parseJsonToModel(src)); return true;
    }
    return false;
  };

  // Compact pure-JS SHA-256 (32-bit ops, browser-safe; matches Python hashlib).
  var _SHA_K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  function _sha256(bytes) {
    function rotr(n, x) { return (x >>> n) | (x << (32 - n)); }
    var H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    var m = bytes.slice(); var bitLen = m.length * 8;
    m.push(0x80);
    while (m.length % 64 !== 56) m.push(0);
    for (var p = 7; p >= 0; p--) m.push(Math.floor(bitLen / Math.pow(2, 8 * p)) & 0xff);
    var w = new Array(64);
    for (var off = 0; off < m.length; off += 64) {
      for (var t = 0; t < 16; t++) w[t] = ((m[off + 4 * t] << 24) | (m[off + 4 * t + 1] << 16) | (m[off + 4 * t + 2] << 8) | m[off + 4 * t + 3]) | 0;
      for (t = 16; t < 64; t++) {
        var s0 = rotr(7, w[t - 15]) ^ rotr(18, w[t - 15]) ^ (w[t - 15] >>> 3);
        var s1 = rotr(17, w[t - 2]) ^ rotr(19, w[t - 2]) ^ (w[t - 2] >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (t = 0; t < 64; t++) {
        var S1 = rotr(6, e) ^ rotr(11, e) ^ rotr(25, e);
        var ch = (e & f) ^ (~e & g);
        var temp1 = (h + S1 + ch + _SHA_K[t] + w[t]) | 0;
        var S0 = rotr(2, a) ^ rotr(13, a) ^ rotr(22, a);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var temp2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + temp1) | 0; d = c; c = b; b = a; a = (temp1 + temp2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    var out = [];
    for (var i = 0; i < 8; i++) out.push((H[i] >>> 24) & 255, (H[i] >>> 16) & 255, (H[i] >>> 8) & 255, H[i] & 255);
    return out;
  }

  // HMAC-SHA256 (RFC 2104) over the canonical _sha256 -> == Python hmac == C runtime.
  function _hmacSha256(key, msg) {
    if (key.length > 64) key = _sha256(key);
    var k = key.slice(); while (k.length < 64) k.push(0);
    var ipad = [], opad = [];
    for (var i = 0; i < 64; i++) { ipad.push(k[i] ^ 0x36); opad.push(k[i] ^ 0x5c); }
    var inner = _sha256(ipad.concat(msg));
    return _sha256(opad.concat(inner));
  }

  PicoVM.prototype._templatelib = function (method, rd, rs1, rs2) {
    var trim = function (a) { var p = 0, q = a.length; while (p < q && _ws(a[p])) p++; while (q > p && _ws(a[q - 1])) q--; return a.slice(p, q); };
    if (method === "Compile") {
      var src = this._spanBytes(this.regs[rs1]), plan = [], i = 0, n = src.length;
      var lit = function (b) { if (b.length) { plan.push(0x01, (b.length >> 8) & 255, b.length & 255); for (var x = 0; x < b.length; x++) plan.push(b[x]); } };
      var emitKey = function (op, key) { if (key.length > 255) key = key.slice(0, 255); plan.push(op, key.length); for (var y = 0; y < key.length; y++) plan.push(key[y]); };
      while (i < n) {
        var j = _bfind2(src, 0x7b, 0x7b, i);
        if (j < 0) { lit(src.slice(i)); break; }
        lit(src.slice(i, j));
        var k = _bfind2(src, 0x7d, 0x7d, j + 2);
        if (k < 0) { lit(src.slice(j)); break; }
        var inner = trim(src.slice(j + 2, k));
        var first = inner.length ? inner[0] : 0;
        if (first === 0x23) {                            // '#' section or '#each list'
          var rest = trim(inner.slice(1));
          if (rest.length >= 4 && rest[0] === 0x65 && rest[1] === 0x61 && rest[2] === 0x63 && rest[3] === 0x68 && (rest.length === 4 || _ws(rest[4]))) {
            emitKey(0x06, trim(rest.slice(4)));
          } else {
            emitKey(0x03, rest);
          }
        } else if (first === 0x5e) {                     // '^' inverted
          emitKey(0x04, trim(inner.slice(1)));
        } else if (first === 0x2f) {                     // '/' end
          plan.push(0x05);
        } else {                                         // hole
          emitKey(0x02, inner);
        }
        i = k + 2;
      }
      this.regs[rd] = this._newSpanBytes(plan); return true;
    }
    if (method === "Render") {
      var plan = this._spanBytes(this.regs[rs1]), mb = this._spanBytes(this.regs[rs2]), model = {}, cur = [];
      var self = this, mcount = 0;
      var commit = function (ln) {
        var eq = ln.indexOf(0x3d);
        if (eq >= 0) {
          if (++mcount > 512) throw picoFault(FAULT.TEMPLATE, self.curPc, mcount, "template model exceeded");  // INV-19
          model[_keystr(ln.slice(0, eq))] = ln.slice(eq + 1);
        }
      };
      for (var p = 0; p < mb.length; p++) { if (mb[p] === 0x0a) { commit(cur); cur = []; } else cur.push(mb[p]); }
      commit(cur);
      var resolve = function (keyArr, prefix) {
        var ks = _keystr(keyArr);
        if (ks === ".") return model[prefix] || [];
        if (prefix) { var v = model[prefix + "." + ks]; if (v !== undefined) return v; }
        return model[ks] || [];
      };
      var countList = function (full) {
        var c = 0;
        for (;;) {
          var base = full + "." + c, has = (model[base] !== undefined);
          if (!has) { var bp = base + "."; for (var kk in model) { if (kk.indexOf(bp) === 0) { has = true; break; } } }
          if (has) c++; else return c;
        }
      };
      var skipBlock = function (pp) {
        var depth = 1;
        while (pp < plan.length && depth > 0) {
          var o = plan[pp++];
          if (o === 0x01) { pp += 2 + ((plan[pp] << 8) | plan[pp + 1]); }
          else if (o === 0x02) { pp += 1 + plan[pp]; }
          else if (o === 0x03 || o === 0x04 || o === 0x06) { pp += 1 + plan[pp]; depth++; }
          else if (o === 0x05) { depth--; }
        }
        return pp;
      };
      var out = [], prefix = "", stack = [], i = 0, n = plan.length;
      while (i < n) {
        if (out.length > 262144) throw picoFault(FAULT.TEMPLATE, this.curPc, out.length, "template output exceeded");  // INV-19
        var op = plan[i++];
        if (op === 0x01) { var ln2 = (plan[i] << 8) | plan[i + 1]; i += 2; for (var q = 0; q < ln2; q++) out.push(plan[i + q]); i += ln2; }
        else if (op === 0x02) { var kl = plan[i++]; var v = resolve(plan.slice(i, i + kl), prefix); i += kl; for (var r = 0; r < v.length; r++) out.push(v[r]); }
        else if (op === 0x03 || op === 0x04) {
          var kl2 = plan[i++], key = plan.slice(i, i + kl2); i += kl2;
          var truthy = resolve(key, prefix).length > 0;
          if (op === 0x03 ? truthy : !truthy) {
            if (stack.length >= 32) throw picoFault(FAULT.TEMPLATE, this.curPc, 0, "template depth exceeded");  // INV-19: TPL_MAXDEPTH
            stack.push(["sec", prefix, 0, 0, "", 0]);
          } else i = skipBlock(i);
        }
        else if (op === 0x06) {
          var kl3 = plan[i++], lk = _keystr(plan.slice(i, i + kl3)); i += kl3;
          var full = prefix ? (prefix + "." + lk) : lk, cnt = countList(full);
          if (cnt > 100000) throw picoFault(FAULT.TEMPLATE, this.curPc, cnt, "template each-count exceeded");  // INV-19
          if (cnt === 0) i = skipBlock(i);
          else {
            if (stack.length >= 32) throw picoFault(FAULT.TEMPLATE, this.curPc, 0, "template depth exceeded");
            stack.push(["each", prefix, i, cnt, full, 0]); prefix = full + ".0";
          }
        }
        else if (op === 0x05) {
          if (stack.length) {
            var fr = stack[stack.length - 1];
            if (fr[0] === "each") {
              fr[5]++;
              if (fr[5] < fr[3]) { prefix = fr[4] + "." + fr[5]; i = fr[2]; }
              else { prefix = fr[1]; stack.pop(); }
            } else { prefix = fr[1]; stack.pop(); }
          }
        }
        else break;
      }
      this.regs[rd] = this._newSpanBytes(out); return true;
    }
    return false;
  };

  // ---- PIOS Req.*/Resp.* simulated host backend --------------------------
  PicoVM.prototype.setRequestContext = function (ctx) {
    ctx = ctx || {};
    // Automatic per-request arena scope: reclaim the previous request's spans,
    // then re-take the post-setup base (mirrors picoscript_vm.install_request_context).
    if (this._handlerMark) {
      this.arenaTop = this._handlerMark[0];
      if (this._handlerMark[1] < this.spans.length) this.spans.length = this._handlerMark[1];
    }
    this._handlerMark = [this.arenaTop, this.spans.length];
    var headers = ctx.headers || {}, body = ctx.body || [], hdr = {};
    Object.keys(headers).forEach(function (k) {
      hdr[String(k).toLowerCase()] = {
        name: this._strSpan(String(k)),
        value: this._strSpan(String(headers[k]))
      };
    }, this);
    this.requestContext = {
      seq: (ctx.seq || 0) >>> 0,
      principal: this._strSpan(String(ctx.principal || "")),
      method: this._strSpan(String(ctx.method || "GET")),
      path: this._strSpan(String(ctx.path || "/")),
      headers: hdr,
      bodyMode: (ctx.bodyMode || ctx.body_mode || 0) >>> 0,
      body: body.map(function (chunk) { return this._strSpan(String(chunk)); }, this)
    };
    this.responseGraph = [];
    this.responseSealed = false;
    this.responseEnded = false;
    this.responseMode = null;
    this.responseBodyStarted = false;
    this.responseStreamClosed = false;
  };
  PicoVM.prototype.installRequestContext = PicoVM.prototype.setRequestContext;
  PicoVM.prototype.setArenaBase = function () { this._handlerMark = [this.arenaTop, this.spans.length]; };
  PicoVM.prototype.getResponseGraph = function () {
    return this.responseGraph.map(function (d) {
      return { kind: d.kind, subtype: d.subtype, payload: d.payload };
    });
  };
  PicoVM.prototype._requireRequestContext = function () {
    // I4: Req.* reads are confined to the kernel-installed bound context.
    if (!this.requestContext) throw new Error("I4 violation: Req.* without installed request context");
    return this.requestContext;
  };
  PicoVM.prototype._ensureResponseOpen = function () {
    // I2: there is exactly one response graph being built; End closes it.
    if (this.responseEnded) throw new Error("I2 violation: response graph already finalized");
  };
  PicoVM.prototype._ensurePreambleMutable = function () {
    // I3: after Seal, the preamble and headers are immutable/frozen.
    if (this.responseSealed) throw new Error("I3 violation: response preamble/headers sealed");
  };
  PicoVM.prototype._ensureHeaderPhase = function () {
    // I6: headers belong to the preamble/header phase, which precedes the body
    // phase. Status may still be set last, but a header may not follow a body write.
    if (this.responseBodyStarted) throw new Error("I6 violation: header after body phase started");
  };
  PicoVM.prototype._ensureStreamOpen = function () {
    // I6: body writes are illegal once the stream phase is closed (Resp.EndStream).
    if (this.responseStreamClosed) throw new Error("I6 violation: body write after stream phase closed");
  };
  PicoVM.prototype._desc = function (kind, subtype, payload) {
    return { kind: kind, subtype: subtype == null ? null : subtype, payload: payload == null ? null : payload };
  };
  PicoVM.prototype._spanPayload = function (handle) {
    var s = (handle > 0 && handle < this.spans.length) ? this.spans[handle] : { ptr: 0, len: 0 };
    return { span: handle | 0, text: this._spanStr(handle | 0), ptr: s.ptr, len: s.len };
  };
  PicoVM.prototype._respStatus = function (code) {
    this._ensureResponseOpen();
    this._ensurePreambleMutable();
    var desc = this._desc("DESC_PREAMBLE", "STATUS", { code: code | 0 });
    for (var i = 0; i < this.responseGraph.length; i++) {
      if (this.responseGraph[i].kind === "DESC_PREAMBLE" && this.responseGraph[i].subtype === "STATUS") {
        this.responseGraph[i] = desc; return;
      }
    }
    this.responseGraph.push(desc);
  };
  PicoVM.prototype._respSeal = function (explicit) {
    this._ensureResponseOpen();
    if (this.responseSealed) {
      // I3 (use-after-seal): re-sealing via the explicit verb is rejected;
      // Respond's internal seal is idempotent.
      if (explicit) throw new Error("I3 violation: response already sealed");
      return;
    }
    this.responseGraph.push(this._desc("DESC_COMMIT", "SEAL", null));
    this.responseSealed = true;
    if (explicit && this.responseMode === null) this.responseMode = "stream";
  };
  PicoVM.prototype._respEnd = function () {
    this._ensureResponseOpen();
    if (this.responseMode === null) this.responseMode = "unary";
    this.responseGraph.push(this._desc("DESC_COMMIT", "END", null));
    this.responseEnded = true;
  };
  PicoVM.prototype._req = function (method, rd, rs1, rs2) {
    var ctx = this._requireRequestContext(), R = this.regs;
    if (method === "Seq") { R[rd] = ctx.seq | 0; return true; }
    if (method === "Principal") { R[rd] = ctx.principal | 0; return true; }
    if (method === "Method") { R[rd] = ctx.method | 0; return true; }
    if (method === "Path") { R[rd] = ctx.path | 0; return true; }
    if (method === "Header") { var h = ctx.headers[this._spanStr(R[rs1]).toLowerCase()]; R[rd] = h ? h.value : 0; return true; }
    if (method === "BodyMode") { R[rd] = ctx.bodyMode | 0; return true; }
    if (method === "BodyCount") { R[rd] = ctx.body.length | 0; return true; }
    if (method === "BodySpan") { var idx = R[rs1] | 0; R[rd] = (idx >= 0 && idx < ctx.body.length) ? ctx.body[idx] : 0; return true; }
    return false;
  };
  PicoVM.prototype._resp = function (method, rd, rs1, rs2) {
    var R = this.regs;
    if (method === "Status") { this._respStatus(R[rs1]); return true; }
    if (method === "Header") {
      this._ensureResponseOpen(); this._ensurePreambleMutable(); this._ensureHeaderPhase();
      this.responseGraph.push(this._desc("DESC_HEADER", null, { name: this._spanPayload(R[rs1]), value: this._spanPayload(R[rs2]) }));
      return true;
    }
    if (method === "Write") {
      this._ensureResponseOpen(); this._ensureStreamOpen();
      this.responseBodyStarted = true;
      this.responseGraph.push(this._desc("DESC_BODY", null, this._spanPayload(R[rs1])));
      return true;
    }
    if (method === "Trailer") {
      this._ensureResponseOpen();
      this.responseGraph.push(this._desc("DESC_TRAILER", null, { name: this._spanPayload(R[rs1]), value: this._spanPayload(R[rs2]) }));
      return true;
    }
    if (method === "Seal") { this._respSeal(true); return true; }
    if (method === "End") { this._respEnd(); return true; }
    if (method === "Respond") { this._respStatus(R[rs1]); this._respSeal(false); this._respEnd(); return true; }
    if (method === "Flush") { this._ensureResponseOpen(); this.responseGraph.push(this._desc("DESC_CONTROL", "FLUSH", null)); return true; }
    if (method === "Continue") { this._ensureResponseOpen(); this.responseGraph.push(this._desc("DESC_CONTROL", "CONTINUE_100", null)); return true; }
    if (method === "EndStream") {
      this._ensureResponseOpen(); this._ensureStreamOpen();
      if (this.responseMode !== "stream") throw new Error("I6 violation: EndStream outside stream mode (no open stream phase)");
      this.responseGraph.push(this._desc("DESC_CONTROL", "END_STREAM", null));
      this.responseStreamClosed = true;
      return true;
    }
    if (method === "Upgrade") { this._ensureResponseOpen(); this.responseGraph.push(this._desc("DESC_UPGRADE", null, this._spanPayload(R[rs1]))); return true; }
    if (method === "Abort") { this._ensureResponseOpen(); this.responseGraph.push(this._desc("DESC_ABORT", null, { code: R[rs1] | 0 })); this.responseEnded = true; return true; }
    if (method === "EarlyHints") { this._ensureResponseOpen(); this.responseGraph.push(this._desc("DESC_CONTROL", "EARLY_HINTS_103", null)); return true; }
    return false;
  };

  // Mirrors picoscript_vm HostApi._textio: arena-backed Utf8Writer/Reader + Json/Xml.
  PicoVM.prototype._wByte = function (w, b) { if (w.pos < w.cap) { this.mem[w.ptr + w.pos] = b & 0xFF; w.pos++; } };
  PicoVM.prototype._wText = function (w, text) { var b = new TextEncoder().encode(text); for (var i = 0; i < b.length; i++) this._wByte(w, b[i]); };
  PicoVM.prototype._wSpan = function (w, h) { var s = (h > 0 && h < this.spans.length) ? this.spans[h] : null; if (s) { for (var i = 0; i < s.len; i++) this._wByte(w, this.mem[s.ptr + i]); } };
  function jsonEsc(s) {
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var ch = s[i], o = s.charCodeAt(i);
      if (ch === '"') out += '\\"';
      else if (ch === '\\') out += '\\\\';
      else if (ch === '\n') out += '\\n';
      else if (ch === '\r') out += '\\r';
      else if (ch === '\t') out += '\\t';
      else if (o < 0x20) out += '\\u' + ('000' + o.toString(16)).slice(-4);
      else out += ch;
    }
    return out;
  }
  function xmlEsc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  PicoVM.prototype._jsonPre = function (w) {
    if (!w.stack.length) return;
    var st = w.stack[w.stack.length - 1];
    if (st.afterKey) st.afterKey = false;
    else if (st.count > 0) this._wByte(w, 0x2C);
  };
  PicoVM.prototype._jsonPost = function (w) { if (w.stack.length) w.stack[w.stack.length - 1].count += 1; };
  PicoVM.prototype._textio = function (ns, method, rd, rs1, rs2) {
    if (!this._tio) this._tio = { writers: {}, readers: {}, nextW: 1, nextR: 1 };
    var T = this._tio, R = this.regs;
    if (ns === "Utf8Writer") {
      if (method === "New") { var h = T.nextW++; T.writers[h] = { ptr: R[rs1] & 0xFFFF, cap: R[rs2] & 0xFFFF, pos: 0, stack: [] }; R[rd] = h; return true; }
      var w = T.writers[R[rs1]]; if (!w) { R[rd] = 0; return true; }
      if (method === "Byte") { this._wByte(w, R[rs2]); return true; }
      if (method === "Int") { this._wText(w, String(R[rs2] | 0)); return true; }
      if (method === "Span") { this._wSpan(w, R[rs2]); return true; }
      if (method === "ToSpan") { this.spans.push({ ptr: w.ptr, len: w.pos }); R[rd] = this.spans.length - 1; return true; }
      if (method === "Len") { R[rd] = w.pos; return true; }
      if (method === "Reset") { w.pos = 0; w.stack = []; return true; }
      return false;
    }
    if (ns === "Utf8Reader") {
      if (method === "New") { var s = (R[rs1] > 0 && R[rs1] < this.spans.length) ? this.spans[R[rs1]] : { ptr: 0, len: 0 }; var rh = T.nextR++; T.readers[rh] = { ptr: s.ptr, len: s.len, pos: 0 }; R[rd] = rh; return true; }
      var r = T.readers[R[rs1]]; if (!r) { R[rd] = 0; return true; }
      if (method === "Peek") { R[rd] = r.pos < r.len ? this.mem[r.ptr + r.pos] : 0; return true; }
      if (method === "Next") { R[rd] = r.pos < r.len ? this.mem[r.ptr + r.pos] : 0; if (r.pos < r.len) r.pos++; return true; }
      if (method === "SkipWs") { while (r.pos < r.len && (this.mem[r.ptr + r.pos] === 32 || this.mem[r.ptr + r.pos] === 9 || this.mem[r.ptr + r.pos] === 10 || this.mem[r.ptr + r.pos] === 13)) r.pos++; return true; }
      if (method === "Eof") { R[rd] = r.pos >= r.len ? 1 : 0; return true; }
      if (method === "Pos") { R[rd] = r.pos; return true; }
      if (method === "Match") { if (r.pos < r.len && this.mem[r.ptr + r.pos] === (R[rs2] & 0xFF)) { r.pos++; R[rd] = 1; } else R[rd] = 0; return true; }
      if (method === "Int") {
        while (r.pos < r.len && (this.mem[r.ptr + r.pos] === 32 || this.mem[r.ptr + r.pos] === 9 || this.mem[r.ptr + r.pos] === 10 || this.mem[r.ptr + r.pos] === 13)) r.pos++;
        var sign = 1; if (r.pos < r.len && this.mem[r.ptr + r.pos] === 0x2D) { sign = -1; r.pos++; }
        var n = 0; while (r.pos < r.len && this.mem[r.ptr + r.pos] >= 0x30 && this.mem[r.ptr + r.pos] <= 0x39) { n = n * 10 + (this.mem[r.ptr + r.pos] - 0x30); r.pos++; }
        R[rd] = (sign * n) | 0; return true;
      }
      return false;
    }
    if (ns === "Json") {
      var jw = T.writers[R[rs1]]; if (!jw) { R[rd] = 0; return true; }
      if (method === "BeginObject" || method === "BeginArray") {
        this._jsonPre(jw); this._wByte(jw, method === "BeginObject" ? 0x7B : 0x5B);
        if (jw.stack.length) jw.stack[jw.stack.length - 1].count += 1;
        jw.stack.push({ count: 0, afterKey: false }); return true;
      }
      if (method === "EndObject" || method === "EndArray") { if (jw.stack.length) jw.stack.pop(); this._wByte(jw, method === "EndObject" ? 0x7D : 0x5D); return true; }
      if (method === "Key") {
        var st = jw.stack.length ? jw.stack[jw.stack.length - 1] : null;
        if (st && st.count > 0) this._wByte(jw, 0x2C);
        this._wByte(jw, 0x22); this._wText(jw, jsonEsc(this._spanStr(R[rs2]))); this._wByte(jw, 0x22); this._wByte(jw, 0x3A);
        if (st) st.afterKey = true; return true;
      }
      if (method === "Str") { this._jsonPre(jw); this._wByte(jw, 0x22); this._wText(jw, jsonEsc(this._spanStr(R[rs2]))); this._wByte(jw, 0x22); this._jsonPost(jw); return true; }
      if (method === "Int") { this._jsonPre(jw); this._wText(jw, String(R[rs2] | 0)); this._jsonPost(jw); return true; }
      if (method === "Bool") { this._jsonPre(jw); this._wText(jw, R[rs2] ? "true" : "false"); this._jsonPost(jw); return true; }
      if (method === "Null") { this._jsonPre(jw); this._wText(jw, "null"); this._jsonPost(jw); return true; }
      if (method === "Raw") { this._jsonPre(jw); this._wSpan(jw, R[rs2]); this._jsonPost(jw); return true; }
      return false;
    }
    if (ns === "Xml") {
      var xw = T.writers[R[rs1]]; if (!xw) { R[rd] = 0; return true; }
      if (method === "Open") { this._wByte(xw, 0x3C); this._wSpan(xw, R[rs2]); return true; }
      if (method === "AttrName") { this._wByte(xw, 0x20); this._wSpan(xw, R[rs2]); this._wByte(xw, 0x3D); this._wByte(xw, 0x22); return true; }
      if (method === "AttrValue") { this._wText(xw, xmlEsc(this._spanStr(R[rs2]))); this._wByte(xw, 0x22); return true; }
      if (method === "OpenEnd") { this._wByte(xw, 0x3E); return true; }
      if (method === "Text") { this._wText(xw, xmlEsc(this._spanStr(R[rs2]))); return true; }
      if (method === "Close") { this._wByte(xw, 0x3C); this._wByte(xw, 0x2F); this._wSpan(xw, R[rs2]); this._wByte(xw, 0x3E); return true; }
      if (method === "Empty") { this._wByte(xw, 0x2F); this._wByte(xw, 0x3E); return true; }
      return false;
    }
    return false;
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

  // Decode the output buffer (PIPE ints + Io.Write bytes) as UTF-8 text.
  PicoVM.prototype.outputText = function () {
    return new TextDecoder("utf-8").decode(new Uint8Array(this.output));
  };

  // ── module container: embedded + checked ABI version (INV-23) ─────────────
  // Mirrors pico_module.py byte-for-byte: [MAGIC, ABI, HOOK_TABLE_VERSION, count, ...words].
  // load refuses a module whose magic/ABI/hook-table version != this runtime.
  var MODULE_MAGIC = 0x50534331;       // "PSC1"
  var MODULE_ABI_VERSION = 1;
  function _fnv1a32(str) {              // FNV-1a/32 over UTF-8 bytes (hook names are ASCII)
    var h = 0x811C9DC5;
    for (var i = 0; i < str.length; i++) h = Math.imul(h ^ (str.charCodeAt(i) & 0xFF), 0x01000193) >>> 0;
    return h >>> 0;
  }
  function hookTableVersion() {
    var bc = PV_HOOKS.BY_CODE || {};
    var codes = Object.keys(bc).map(function (k) { return parseInt(k, 10); }).sort(function (a, b) { return a - b; });
    var lines = codes.map(function (c) { return c + ":" + bc[c]; });
    return _fnv1a32(lines.join("\n"));
  }
  function packModule(words) {
    var out = [MODULE_MAGIC >>> 0, MODULE_ABI_VERSION >>> 0, hookTableVersion(), words.length >>> 0];
    for (var i = 0; i < words.length; i++) out.push(words[i] >>> 0);
    return out;
  }
  function loadModule(container) {
    if (!container || container.length < 4) throw new Error("ModuleAbiError: truncated module header");
    var magic = container[0] >>> 0, abi = container[1] >>> 0, htv = container[2] >>> 0, count = container[3] >>> 0;
    if (magic !== (MODULE_MAGIC >>> 0)) throw new Error("ModuleAbiError: bad module magic 0x" + magic.toString(16));
    if (abi !== MODULE_ABI_VERSION) throw new Error("ModuleAbiError: ABI version mismatch module=" + abi + " runtime=" + MODULE_ABI_VERSION);
    var expect = hookTableVersion();
    if (htv !== expect) throw new Error("ModuleAbiError: host hook table version mismatch module=0x" + htv.toString(16) + " runtime=0x" + expect.toString(16));
    var words = container.slice(4);
    if (words.length !== count) throw new Error("ModuleAbiError: word count mismatch header=" + count + " actual=" + words.length);
    return words.map(function (w) { return w >>> 0; });
  }
  PicoVM.packModule = packModule;
  PicoVM.loadModule = loadModule;
  PicoVM.hookTableVersion = hookTableVersion;
  PicoVM.MODULE_MAGIC = MODULE_MAGIC;
  PicoVM.MODULE_ABI_VERSION = MODULE_ABI_VERSION;

  return PicoVM;
});
