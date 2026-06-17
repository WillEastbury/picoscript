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
  var node = (typeof module !== "undefined" && module.exports);
  var hooks = node ? require("./pico_hooks.js") : root.PV_HOOKS;
  var pcz = node ? require("./picocompress.js") : root.PicoCompress;
  var pbz = node ? require("./picobrotli.js") : root.PicoBrotli;
  var PicoVM = factory(hooks, pcz, pbz);
  if (node) module.exports = PicoVM;
  else root.PicoVM = PicoVM;
})(typeof globalThis !== "undefined" ? globalThis : this, function (PV_HOOKS, PicoCompress, PicoBrotli) {
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
  var CAP = { KERNEL: 1, QUEUE: 2, RANDOM: 4, STORAGE: 8, TIME: 16, NET: 32, CONTEXT: 64, AUTH: 128, ENV: 256, CRYPTO: 512, GPIO: 1024, CAPSULE: 2048, DEVICE: 4096, DMA: 8192, EVENT: 16384, UI: 32768, PROCESS: 65536, TIMER: 131072, PRINCIPAL: 262144, CAPSULE_EXEC: 524288 };
  var CAP_ALL = 0xFFFFF;
  var CAP_BY_NS = { Kernel: CAP.KERNEL, Queue: CAP.QUEUE, Random: CAP.RANDOM,
    Req: CAP.NET, Resp: CAP.NET, Net: CAP.NET, Storage: CAP.STORAGE, DateTime: CAP.TIME,
    Context: CAP.CONTEXT, Auth: CAP.AUTH, X509: CAP.AUTH, Environment: CAP.ENV, Locale: CAP.ENV, Gpio: CAP.GPIO,
    Pack: CAP.CAPSULE, Card: CAP.CAPSULE, Fifo: CAP.CAPSULE, Device: CAP.DEVICE, Stream: CAP.DMA, Event: CAP.EVENT, Ui: CAP.UI,
    Process: CAP.PROCESS, Env: CAP.PROCESS, Timer: CAP.TIMER, Scheduler: CAP.TIMER,
    Principal: CAP.PRINCIPAL, Capability: CAP.PRINCIPAL, Sandbox: CAP.PRINCIPAL, Capsule: CAP.CAPSULE_EXEC };
  function hookCap(name) {   // "Ns.Method" -> required capability class (0 = pure)
    var dot = name.indexOf("."), ns = name.slice(0, dot), m = name.slice(dot + 1);
    if (ns === "Maths" && (m === "Random" || m === "RandomRange")) return CAP.RANDOM;
    if (ns === "Crypto" && m === "RandomBytes") return CAP.RANDOM;
    if (ns === "Crypto" && (m === "Encrypt" || m === "Decrypt")) return CAP.CRYPTO;
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
    // Pluggable provider layer (browser harness): the editor / PIOS can inject a
    // card store (PicoStore-compatible CRUD+query, e.g. PiosCapsuleStore) and/or a
    // GPIO provider ({ pins: {}, count: N }) to back Storage.*/Gpio.*. Defaults are
    // the built-in reference store + emulator, so VM parity is unchanged.
    this._cardStore = opts.cardStore || null;
    this._gpioProvider = opts.gpioProvider || null;
    this._streamProvider = opts.streamProvider || null;
    this.reset();
  }

  PicoVM.prototype.reset = function () {
    this.regs = new Int32Array(16);
    this.cards = this._extCards || new Map();   // PicoWAL store persists if external
    this.callStack = [];
    this.output = [];          // array of byte values (0..255)
    this.outputEvents = [];    // typed chunks for display only: int / bytes / byte
    this.httpStatus = -1;
    this.httpType = null;
    this.queues = {};
    this.rng = (this._seed !== null) ? this._seed : (0x4F6CDD1D >>> 0);
    this.hostStatus = 0;                      // INV-18: typed status of the last fallible hook
    this.constFloor = 0x8000;                 // INV-9: lowest literal const-pool address ([floor,0x8000) RO)
    this.mem = new Uint8Array(520 * 1024);   // process arena = RP2350 (Pico 2) 520 KB SRAM
    this.dotLen = 0;                          // active span length for Dot8.Of
    this.tensorRows = 0; this.tensorCols = 0;
    this.bitlinearRows = 0; this.bitlinearCols = 0;
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
      try {
        this.step();
      } catch (ex) {
        if (ex.fault !== undefined && this._errState && this._errState.handlerPc) {
          this._errState.code = ex.fault;
          this._errState.detail = ex.detail || 0;
          this._errState.resumePc = (ex.pc || 0) + 1;
          this.pc = this._errState.handlerPc;
        } else {
          throw ex;
        }
      }
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
    this.outputEvents.push({ kind: "int", value: v | 0 });
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
    if (name.indexOf("Tensor.") === 0) { if (this._tensor(name.slice(7), rd, rs1, rs2)) return; }
    if (name.indexOf("BitLinear.") === 0) { if (this._bitlinear(name.slice(10), rd, rs1, rs2)) return; }
    if (name.indexOf("Quant.") === 0) { if (this._quant(name.slice(6), rd, rs1, rs2)) return; }
    if (name.indexOf("Attention.") === 0) { if (this._attention(name.slice(10), rd, rs1, rs2)) return; }
    if (name.indexOf("Tokenizer.") === 0) { if (this._tokenizer(name.slice(10), rd, rs1, rs2)) return; }
    if (name.indexOf("Model.") === 0) { if (this._model(name.slice(6), rd, rs1, rs2)) return; }
    if (name.indexOf("Kv.") === 0) { if (this._kv(name.slice(3), rd, rs1, rs2)) return; }
    if (name.indexOf("Sampling.") === 0) { if (this._sampling(name.slice(9), rd, rs1, rs2)) return; }
    if (name.indexOf("Query.") === 0) { if (this._queryHelpers(name.slice(6), rd, rs1, rs2)) return; }
    if (name.indexOf("Search.") === 0) { if (this._search(name.slice(7), rd, rs1, rs2)) return; }
    // ---- program-level card store: Storage.* over a PicoStore --------------
    if (name.indexOf("Storage.") === 0) {
      if (this._storage(name.slice(8), rd, rs1, rs2)) return;
    }
    // ---- program-level GPIO emulator: Gpio.* (reference; PIOS injects real driver)
    if (name.indexOf("Gpio.") === 0) {
      if (this._gpio(name.slice(5), rd, rs1, rs2)) return;
    }
    // ---- Device.*/Stream.* reference DMA-ring emulator ---------------------
    if (name.indexOf("Device.") === 0) {
      if (this._device(name.slice(7), rd, rs1, rs2)) return;
    }
    if (name.indexOf("Stream.") === 0) {
      if (this._stream(name.slice(7), rd, rs1, rs2)) return;
    }
    // ---- Assert.* PSUnit assertion counters --------------------------------
    if (name.indexOf("Assert.") === 0) {
      if (this._assert(name.slice(7), rd, rs1, rs2)) return;
    }
    // ---- Event.* reactive event queue --------------------------------------
    if (name.indexOf("Event.") === 0) {
      if (this._event(name.slice(6), rd, rs1, rs2)) return;
    }
    // ---- Ui.* retained scene tree / remote windowing -----------------------
    if (name.indexOf("Ui.") === 0) {
      if (this._ui(name.slice(3), rd, rs1, rs2)) return;
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
      if (sw) {
        var bs = [];
        for (var iw = 0; iw < sw.len; iw++) { var bv = this.mem[sw.ptr + iw]; this.output.push(bv); bs.push(bv); }
        this.outputEvents.push({ kind: "bytes", bytes: bs });
      }
      return;
    }
    if (name === "Io.WriteByte") { var ob = this.regs[rs1] & 0xFF; this.output.push(ob); this.outputEvents.push({ kind: "byte", value: ob }); return; }
    // ---- text/binary primitives: Utf8Writer / Utf8Reader / Json / Xml -----
    if (name.indexOf("Utf8Writer.") === 0 || name.indexOf("Utf8Reader.") === 0 ||
        name.indexOf("Json.") === 0 || name.indexOf("Xml.") === 0) {
      var dot = name.indexOf(".");
      if (this._textio(name.slice(0, dot), name.slice(dot + 1), rd, rs1, rs2)) return;
    }
    if (name.indexOf("TextRender.") === 0) { if (this._textrender(name.slice(11), rd, rs1, rs2)) return; }
    // ---- OS-worker: Process/Env, Timer/Scheduler, Principal/Capability/Sandbox, Error, Capsule ----
    if (name.indexOf("Process.") === 0) { if (this._processEnv("Process", name.slice(8), rd, rs1, rs2)) return; }
    if (name.indexOf("Env.") === 0) { if (this._processEnv("Env", name.slice(4), rd, rs1, rs2)) return; }
    if (name.indexOf("Timer.") === 0) { if (this._timerScheduler("Timer", name.slice(6), rd, rs1, rs2)) return; }
    if (name.indexOf("Scheduler.") === 0) { if (this._timerScheduler("Scheduler", name.slice(10), rd, rs1, rs2)) return; }
    if (name.indexOf("Principal.") === 0) { if (this._principalCap("Principal", name.slice(10), rd, rs1, rs2)) return; }
    if (name.indexOf("Capability.") === 0) { if (this._principalCap("Capability", name.slice(11), rd, rs1, rs2)) return; }
    if (name.indexOf("Sandbox.") === 0) { if (this._principalCap("Sandbox", name.slice(8), rd, rs1, rs2)) return; }
    if (name.indexOf("Error.") === 0) { if (this._errorHook(name.slice(6), rd, rs1, rs2)) return; }
    if (name.indexOf("Capsule.") === 0) { if (this._capsuleExec(name.slice(8), rd, rs1, rs2)) return; }
    if (name.indexOf("Base64.") === 0) { if (this._base64(name.slice(7), rd, rs1, rs2)) return; }
    if (name === "DateTime.DiffDays" || name === "DateTime.Year" || name === "DateTime.Month" || name === "DateTime.Day") { if (this._datetimeExt(name.slice(9), rd, rs1, rs2)) return; }
    if (name === "Req.Param" || name === "Req.ParamCount") { if (this._reqParam(name.slice(4), rd, rs1, rs2)) return; }
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
  PicoVM.prototype._strToBytes = function (str) {
    var b = new TextEncoder().encode(str);
    return Array.from(b);
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
  // Q16.16 exp/log: fixmul/fixdiv via BigInt for exact 64-bit intermediates matching
  // the C int64 / Python big-int paths; series divides trunc-toward-zero.
  var Q16_LN2 = 45426, Q16_INV_LN2 = 94548, Q16_INV_LN10 = 28462, Q16_EXP_MAX_Z = 681300;
  function q16Fixmul(a, b) { return Number(BigInt.asIntN(32, (BigInt(a) * BigInt(b)) >> 16n)); }
  function q16Idiv(a, n) {
    var aa = a < 0 ? -a : a, nn = n < 0 ? -n : n, q = Math.floor(aa / nn);
    return ((a < 0) !== (n < 0)) ? -q : q;
  }
  function q16Fixdiv(a, b) {
    var num = BigInt(a) * 65536n, bb = BigInt(b);
    var an = num < 0n ? -num : num, ab = bb < 0n ? -bb : bb, q = an / ab;
    return Number(BigInt.asIntN(32, ((num < 0n) !== (bb < 0n)) ? -q : q));
  }
  function q16Exp(z) {
    if (z >= Q16_EXP_MAX_Z) return 0x7FFFFFFF;
    if (z <= -Q16_EXP_MAX_Z) return 0;
    var k = (q16Fixmul(z, Q16_INV_LN2) + (Q16_ONE >> 1)) >> 16;
    var r = (z - k * Q16_LN2) | 0, term = Q16_ONE, acc = Q16_ONE, n, i;
    for (n = 1; n < 8; n++) { term = q16Idiv(q16Fixmul(term, r), n); acc = (acc + term) | 0; }
    if (k >= 0) {
      var a = acc;
      for (i = 0; i < k; i++) { a = a * 2; if (a > 0x7FFFFFFF) return 0x7FFFFFFF; }
      return a | 0;
    }
    for (i = 0; i < -k; i++) acc = acc >> 1;
    return acc | 0;
  }
  function q16Log(x) {
    if (x <= 0) return -0x80000000;
    var e = 0, m = x;
    while (m >= 2 * Q16_ONE) { m = m >> 1; e++; }
    while (m < Q16_ONE) { m = m << 1; e--; }
    var u = q16Fixdiv((m - Q16_ONE) | 0, (m + Q16_ONE) | 0);
    var u2 = q16Fixmul(u, u), term = u, acc = 0, n;
    for (n = 0; n < 6; n++) { acc = (acc + q16Idiv(term, 2 * n + 1)) | 0; term = q16Fixmul(term, u2); }
    return ((2 * acc) + e * Q16_LN2) | 0;
  }

  PicoVM.prototype._mathslib = function (method, rd, rs1, rs2) {
    if (method === "Sin") { this.regs[rd] = q16Sincos(this.regs[rs1] | 0)[0] | 0; return true; }
    if (method === "Cos") { this.regs[rd] = q16Sincos(this.regs[rs1] | 0)[1] | 0; return true; }
    if (method === "Tan") { this.regs[rd] = q16Tan(this.regs[rs1] | 0) | 0; return true; }
    if (method === "Exp") { this.regs[rd] = q16Exp(this.regs[rs1] | 0) | 0; return true; }
    if (method === "Log") { this.regs[rd] = q16Log(this.regs[rs1] | 0) | 0; return true; }
    if (method === "Log10") { this.regs[rd] = q16Fixmul(q16Log(this.regs[rs1] | 0), Q16_INV_LN10) | 0; return true; }
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

  // ── DEFLATE (RFC 1951) + gzip (RFC 1952): byte-identical with picoscript_vm.py.
  // One final fixed-Huffman block, greedy LZ77 with a deterministic hash-chain
  // match finder. inflate is spec-deterministic (reads real zlib/gzip output).
  var Z_LEN_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
  var Z_LEN_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
  var Z_DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
  var Z_DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
  var Z_CLEN_ORDER = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
  var Z_CRC = (function () { var t = []; for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); t.push(c >>> 0); } return t; })();
  function zCrc32(data) { var crc = 0xFFFFFFFF; for (var i = 0; i < data.length; i++) crc = (Z_CRC[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8)) >>> 0; return (crc ^ 0xFFFFFFFF) >>> 0; }
  function zFixedLit() { var L = [], i; for (i = 0; i < 144; i++) L.push(8); for (i = 0; i < 112; i++) L.push(9); for (i = 0; i < 24; i++) L.push(7); for (i = 0; i < 8; i++) L.push(8); return L; }
  function zCodes(lengths) {
    var maxbits = 0, i; for (i = 0; i < lengths.length; i++) if (lengths[i] > maxbits) maxbits = lengths[i];
    var blc = []; for (i = 0; i <= maxbits; i++) blc.push(0);
    for (i = 0; i < lengths.length; i++) if (lengths[i]) blc[lengths[i]]++;
    var code = 0, nc = [0]; for (i = 1; i <= maxbits; i++) { code = (code + blc[i - 1]) << 1; nc[i] = code; }
    var out = {}; for (i = 0; i < lengths.length; i++) { var L = lengths[i]; if (L) { out[i] = [nc[L], L]; nc[L]++; } }
    return out;
  }
  function zTree(lengths) { var c = zCodes(lengths), t = {}; for (var s in c) t[c[s][0] + "_" + c[s][1]] = parseInt(s, 10); return t; }
  function zLenSym(length) { for (var i = Z_LEN_BASE.length - 1; i >= 0; i--) if (length >= Z_LEN_BASE[i]) return 257 + i; return 257; }
  function zDistSym(dist) { for (var i = Z_DIST_BASE.length - 1; i >= 0; i--) if (dist >= Z_DIST_BASE[i]) return i; return 0; }
  function zDeflate(data) {
    var lit = zCodes(zFixedLit()), out = [], bitbuf = 0, bitcnt = 0;
    function put(value, n) { bitbuf |= (value & ((1 << n) - 1)) << bitcnt; bitcnt += n; while (bitcnt >= 8) { out.push(bitbuf & 0xFF); bitbuf >>>= 8; bitcnt -= 8; } }
    function huff(code, n) { var r = 0; for (var k = 0; k < n; k++) { r = (r << 1) | (code & 1); code >>= 1; } put(r, n); }
    put(1, 1); put(1, 2);
    var n = data.length, head = {}, prev = new Array(n + 1), i; for (i = 0; i <= n; i++) prev[i] = 0;
    i = 0;
    while (i < n) {
      var matchLen = 0, matchDist = 0;
      if (i + 3 <= n) {
        var h = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        var j = (head[h] || 0) - 1, chain = 0, maxlen = Math.min(258, n - i);
        while (j >= 0 && i - j <= 32768 && chain < 256) {
          var length = 0;
          while (length < maxlen && data[j + length] === data[i + length]) length++;
          if (length > matchLen) { matchLen = length; matchDist = i - j; if (length >= maxlen) break; }
          j = prev[j] - 1; chain++;
        }
      }
      if (matchLen >= 3) {
        var ls = zLenSym(matchLen), lc = lit[ls];
        huff(lc[0], lc[1]); put(matchLen - Z_LEN_BASE[ls - 257], Z_LEN_EXTRA[ls - 257]);
        var ds = zDistSym(matchDist); huff(ds, 5); put(matchDist - Z_DIST_BASE[ds], Z_DIST_EXTRA[ds]);
        var end = i + matchLen;
        while (i < end) { if (i + 3 <= n) { var hh = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]; prev[i] = head[hh] || 0; head[hh] = i + 1; } i++; }
      } else {
        var c0 = lit[data[i]]; huff(c0[0], c0[1]);
        if (i + 3 <= n) { var hl = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]; prev[i] = head[hl] || 0; head[hl] = i + 1; }
        i++;
      }
    }
    var ce = lit[256]; huff(ce[0], ce[1]);
    if (bitcnt > 0) out.push(bitbuf & 0xFF);
    return out;
  }
  function zInflate(data) {
    var pos = 0, bitbuf = 0, bitcnt = 0, out = [];
    var fLit = zTree(zFixedLit()), fDist = zTree((function () { var a = [], i; for (i = 0; i < 30; i++) a.push(5); return a; })());
    function take(n) { while (bitcnt < n) { if (pos >= data.length) throw new Error("truncated compressed data"); var b = data[pos]; pos++; bitbuf |= b << bitcnt; bitcnt += 8; } var v = bitbuf & ((1 << n) - 1); bitbuf >>>= n; bitcnt -= n; return v; }
    function sym(tree) { var code = 0, length = 0; while (true) { code = (code << 1) | take(1); length++; var s = tree[code + "_" + length]; if (s !== undefined) return s; if (length > 15) throw new Error("bad compressed data"); } }
    while (true) {
      var bfinal = take(1), btype = take(2);
      if (btype === 0) { take(bitcnt & 7); var ln = take(16); take(16); for (var q = 0; q < ln; q++) out.push(take(8)); }
      else {
        var litTree, distTree;
        if (btype === 1) { litTree = fLit; distTree = fDist; }
        else { var dyn = zReadDynamic(take); litTree = dyn[0]; distTree = dyn[1]; }
        while (true) {
          var s = sym(litTree);
          if (s === 256) break;
          if (s < 256) out.push(s);
          else { var li = s - 257, length2 = Z_LEN_BASE[li] + take(Z_LEN_EXTRA[li]); var dsy = sym(distTree); var dist = Z_DIST_BASE[dsy] + take(Z_DIST_EXTRA[dsy]); var start = out.length - dist; for (var k = 0; k < length2; k++) out.push(out[start + k]); }
        }
      }
      if (bfinal) break;
    }
    return out;
  }
  function zReadDynamic(take) {
    var hlit = take(5) + 257, hdist = take(5) + 1, hclen = take(4) + 4;
    var cl = [], i; for (i = 0; i < 19; i++) cl.push(0);
    for (i = 0; i < hclen; i++) cl[Z_CLEN_ORDER[i]] = take(3);
    var ct = zTree(cl);
    function csym() { var code = 0, length = 0; while (true) { code = (code << 1) | take(1); length++; var s = ct[code + "_" + length]; if (s !== undefined) return s; if (length > 15) throw new Error("bad compressed data"); } }
    var lengths = [];
    while (lengths.length < hlit + hdist) {
      var s = csym();
      if (s < 16) lengths.push(s);
      else if (s === 16) { var r = take(2) + 3, last = lengths[lengths.length - 1], t; for (t = 0; t < r; t++) lengths.push(last); }
      else if (s === 17) { var r2 = take(3) + 3, t2; for (t2 = 0; t2 < r2; t2++) lengths.push(0); }
      else { var r3 = take(7) + 11, t3; for (t3 = 0; t3 < r3; t3++) lengths.push(0); }
    }
    return [zTree(lengths.slice(0, hlit)), zTree(lengths.slice(hlit, hlit + hdist))];
  }
  function zGzip(data) {
    var body = zDeflate(data), c = zCrc32(data), n = data.length >>> 0;
    var out = [0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 0xFF], i;
    for (i = 0; i < body.length; i++) out.push(body[i]);
    out.push(c & 0xFF, (c >>> 8) & 0xFF, (c >>> 16) & 0xFF, (c >>> 24) & 0xFF);
    out.push(n & 0xFF, (n >>> 8) & 0xFF, (n >>> 16) & 0xFF, (n >>> 24) & 0xFF);
    return out;
  }
  function zGunzip(data) {
    if (data.length < 18 || data[0] !== 0x1F || data[1] !== 0x8B) throw new Error("bad compressed data");
    var flg = data[3], pos = 10;
    if (flg & 4) { var xlen = data[pos] | (data[pos + 1] << 8); pos += 2 + xlen; }
    if (flg & 8) { while (data[pos] !== 0) pos++; pos++; }
    if (flg & 16) { while (data[pos] !== 0) pos++; pos++; }
    if (flg & 2) { pos += 2; }
    return zInflate(data.slice(pos, data.length - 8));
  }

  PicoVM.prototype._compresslib = function (method, rd, rs1, rs2) {
    var src = this._spanBytes(this.regs[rs1]);
    if (method === "PicoCompress") {
      this.regs[rd] = this._newSpanBytes(Array.from(PicoCompress.compress(Uint8Array.from(src)))); return true;
    }
    if (method === "PicoDecompress") {
      try { this.regs[rd] = this._newSpanBytes(Array.from(PicoCompress.decompress(Uint8Array.from(src)))); this.host_status = 0; }
      catch (e) { this.host_status = 2; this.regs[rd] = this._newSpanBytes([]); }
      return true;
    }
    if (method === "BrotliCompress") {
      this.regs[rd] = this._newSpanBytes(Array.from(PicoBrotli.encode(Uint8Array.from(src)))); return true;
    }
    if (method === "BrotliDecompress") {
      try { this.regs[rd] = this._newSpanBytes(Array.from(PicoBrotli.decode(Uint8Array.from(src)))); this.host_status = 0; }
      catch (e) { this.host_status = 2; this.regs[rd] = this._newSpanBytes([]); }
      return true;
    }
    if (method === "DeflateCompress" || method === "DeflateDecompress" || method === "GzipCompress" || method === "GzipDecompress") {
      var res;
      try {
        if (method === "DeflateCompress") res = zDeflate(src);
        else if (method === "DeflateDecompress") res = zInflate(src);
        else if (method === "GzipCompress") res = zGzip(src);
        else res = zGunzip(src);
        this.host_status = 0;
      } catch (e) { this.host_status = 2; res = []; }
      this.regs[rd] = this._newSpanBytes(res); return true;
    }
    return false;
  };

  // ── AES-256-CTR (Crypto.Encrypt/Decrypt). Tables + algorithm byte-identical with
  // picoscript_vm.py and vm/picovm.c; CTR is symmetric so encrypt == decrypt. ──
  var AES_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
  ];
  var AES_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d];
  function aesXtime(a) { return (a & 0x80) ? ((a << 1) ^ 0x1B) & 0xFF : (a << 1) & 0xFF; }
  function aesGmul(a, b) { var r = 0, i; for (i = 0; i < 8; i++) { if (b & 1) r ^= a; a = aesXtime(a); b >>= 1; } return r & 0xFF; }
  function aes256KeyExpand(key) {
    var rk = new Array(240), i, j, t = [0, 0, 0, 0], tmp;
    for (i = 0; i < 32; i++) rk[i] = key[i];
    for (i = 8; i < 60; i++) {
      for (j = 0; j < 4; j++) t[j] = rk[(i - 1) * 4 + j];
      if (i % 8 === 0) {
        tmp = t[0]; t[0] = t[1]; t[1] = t[2]; t[2] = t[3]; t[3] = tmp;
        for (j = 0; j < 4; j++) t[j] = AES_SBOX[t[j]];
        t[0] ^= AES_RCON[(i >> 3) - 1];
      } else if (i % 8 === 4) {
        for (j = 0; j < 4; j++) t[j] = AES_SBOX[t[j]];
      }
      for (j = 0; j < 4; j++) rk[i * 4 + j] = rk[(i - 8) * 4 + j] ^ t[j];
    }
    return rk;
  }
  function aes256EncryptBlock(inb, rk) {
    var s = new Array(16), t = new Array(16), out = new Array(16), i, c, r, rnd, a0, a1, a2, a3;
    for (i = 0; i < 16; i++) s[i] = inb[i] ^ rk[i];
    for (rnd = 1; rnd < 14; rnd++) {
      for (i = 0; i < 16; i++) s[i] = AES_SBOX[s[i]];
      for (r = 0; r < 4; r++) for (c = 0; c < 4; c++) t[r + 4 * c] = s[r + 4 * ((c + r) & 3)];
      for (c = 0; c < 4; c++) {
        a0 = t[4 * c]; a1 = t[4 * c + 1]; a2 = t[4 * c + 2]; a3 = t[4 * c + 3];
        s[4 * c]     = (aesGmul(a0, 2) ^ aesGmul(a1, 3) ^ a2 ^ a3) & 0xFF;
        s[4 * c + 1] = (a0 ^ aesGmul(a1, 2) ^ aesGmul(a2, 3) ^ a3) & 0xFF;
        s[4 * c + 2] = (a0 ^ a1 ^ aesGmul(a2, 2) ^ aesGmul(a3, 3)) & 0xFF;
        s[4 * c + 3] = (aesGmul(a0, 3) ^ a1 ^ a2 ^ aesGmul(a3, 2)) & 0xFF;
      }
      for (i = 0; i < 16; i++) s[i] ^= rk[rnd * 16 + i];
    }
    for (i = 0; i < 16; i++) s[i] = AES_SBOX[s[i]];
    for (r = 0; r < 4; r++) for (c = 0; c < 4; c++) t[r + 4 * c] = s[r + 4 * ((c + r) & 3)];
    for (i = 0; i < 16; i++) out[i] = t[i] ^ rk[14 * 16 + i];
    return out;
  }
  function aes256Ctr(key, iv, data) {
    var rk = aes256KeyExpand(key), out = [], ctr = iv.slice(0), off, j, ks;
    for (off = 0; off < data.length; off += 16) {
      ks = aes256EncryptBlock(ctr, rk);
      for (j = 0; j < 16 && off + j < data.length; j++) out.push(data[off + j] ^ ks[j]);
      for (j = 15; j >= 0; j--) { ctr[j] = (ctr[j] + 1) & 0xFF; if (ctr[j]) break; }
    }
    return out;
  }

  PicoVM.prototype._cryptolib = function (method, rd, rs1, rs2) {
    if (method === "Sha256") { this.regs[rd] = this._newSpanBytes(_sha256(this._spanBytes(this.regs[rs1]))); return true; }
    if (method === "HmacSha256") { this.regs[rd] = this._newSpanBytes(_hmacSha256(this._spanBytes(this.regs[rs1]), this._spanBytes(this.regs[rs2]))); return true; }
    if (method === "Encrypt" || method === "Decrypt") {
      var key = this._spanBytes(this.regs[rs1]), data = this._spanBytes(this.regs[rs2]);
      if (key.length !== 32 || data.length < 16) { this.hostStatus = 2; this.regs[rd] = 0; return true; }
      this.hostStatus = 0;
      var iv = data.slice(0, 16);
      this.regs[rd] = this._newSpanBytes(iv.concat(aes256Ctr(key, iv, data.slice(16))));
      return true;
    }
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
    function bodySpan(chunk) {
      if (chunk instanceof Uint8Array) return this._newSpanBytes(Array.from(chunk));
      if (Array.isArray(chunk)) return this._newSpanBytes(chunk.map(function (b) { return b & 0xFF; }));
      return this._strSpan(String(chunk));
    }
    this.requestContext = {
      seq: (ctx.seq || 0) >>> 0,
      principal: this._strSpan(String(ctx.principal || "")),
      method: this._strSpan(String(ctx.method || "GET")),
      path: this._strSpan(String(ctx.path || "/")),
      headers: hdr,
      bodyMode: (ctx.bodyMode || ctx.body_mode || 0) >>> 0,
      body: body.map(function (chunk) { return bodySpan.call(this, chunk); }, this),
      sliceOffset: 0,
      sliceLen: 0
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
    if (method === "SetSlice") { ctx.sliceOffset = Math.max(0, R[rs1] | 0); ctx.sliceLen = Math.max(0, R[rs2] | 0); R[rd] = 1; return true; }
    if (method === "BodyLen") { var li = R[rs1] | 0, lh = (li >= 0 && li < ctx.body.length) ? ctx.body[li] : 0; R[rd] = lh ? this._spanBytes(lh).length : 0; return true; }
    if (method === "BodySlice") { var si = R[rs1] | 0, sh = (si >= 0 && si < ctx.body.length) ? ctx.body[si] : 0, sd = sh ? this._spanBytes(sh) : []; var so = Math.min(ctx.sliceOffset, sd.length), se = Math.min(so + ctx.sliceLen, sd.length); R[rd] = this._newSpanBytes(sd.slice(so, se)); return true; }
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

  function modelLookup(model, key) {
    var lines = String(model).split(/\r?\n/), p = key + "=";
    for (var i = 0; i < lines.length; i++) if (lines[i].indexOf(p) === 0) return lines[i].slice(p.length);
    return "";
  }
  PicoVM.prototype._textrender = function (method, rd, rs1, rs2) {
    if (!this._tio) this._tio = { writers: {}, readers: {}, nextW: 1, nextR: 1 };
    var R = this.regs, T = this._tio;
    if (method === "Hole") {
      var hw = T.writers[1];
      if (!hw) { R[rd] = 0; return true; }
      this._wText(hw, xmlEsc(modelLookup(this._spanStr(R[rs1]), this._spanStr(R[rs2])))); R[rd] = 1; return true;
    }
    var w = T.writers[R[rs1]];
    if (!w) { R[rd] = 0; return true; }
    if (method === "Raw") { this._wSpan(w, R[rs2]); R[rd] = 1; return true; }
    if (method === "Text") { this._wText(w, xmlEsc(this._spanStr(R[rs2]))); R[rd] = 1; return true; }
    if (method === "Open") { this._wByte(w, 0x3C); this._wSpan(w, R[rs2]); R[rd] = 1; return true; }
    if (method === "Attr") { var sp = this._spanStr(R[rs2]).split("="); var name = sp.shift() || ""; var val = sp.join("="); this._wByte(w, 0x20); this._wText(w, name); this._wText(w, '="'); this._wText(w, xmlEsc(val)); this._wByte(w, 0x22); R[rd] = 1; return true; }
    if (method === "OpenEnd") { this._wByte(w, 0x3E); R[rd] = 1; return true; }
    if (method === "Close") { this._wText(w, "</"); this._wSpan(w, R[rs2]); this._wByte(w, 0x3E); R[rd] = 1; return true; }
    if (method === "Empty") { this._wText(w, "/>"); R[rd] = 1; return true; }
    if (method === "Br") { this._wText(w, "<br/>"); R[rd] = 1; return true; }
    return false;
  };

  // Mirrors picoscript_vm HostApi._storage. Context model (cur pack + card)
  // keeps every op within the 2-in/1-out host ABI; field names and queries are
  // UTF-8 byte-spans the program builds in arena memory.
  // ---- Tensor / BitLinear inference primitives ----------------------------
  function i8(b) { return b > 127 ? b - 256 : b; }
  function i32beAt(bytes, idx) {
    var o = idx * 4; if (o + 4 > bytes.length) return 0;
    return ((bytes[o] << 24) | (bytes[o + 1] << 16) | (bytes[o + 2] << 8) | bytes[o + 3]) | 0;
  }
  function packI32(vals) {
    var out = [];
    for (var i = 0; i < vals.length; i++) { var v = vals[i] | 0; out.push((v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255); }
    return out;
  }
  PicoVM.prototype._tensor = function (method, rd, rs1, rs2) {
    if (method === "SetShape") { this.tensorRows = Math.max(0, this.regs[rs1] | 0); this.tensorCols = Math.max(0, this.regs[rs2] | 0); this.regs[rd] = 1; return true; }
    if (method === "DotI8") { var a = this._spanBytes(this.regs[rs1]), b = this._spanBytes(this.regs[rs2]), n = this.tensorCols || Math.min(a.length, b.length), acc = 0; for (var i = 0; i < n && i < a.length && i < b.length; i++) acc = (acc + i8(a[i]) * i8(b[i])) | 0; this.regs[rd] = acc; return true; }
    if (method === "MatVecI8") { var mat = this._spanBytes(this.regs[rs1]), vec = this._spanBytes(this.regs[rs2]), rows = this.tensorRows, cols = this.tensorCols || vec.length, vals = []; for (var r = 0; r < rows; r++) { var sum = 0, base = r * cols; for (var c = 0; c < cols; c++) if (base + c < mat.length && c < vec.length) sum = (sum + i8(mat[base + c]) * i8(vec[c])) | 0; vals.push(sum); } this.regs[rd] = this._newSpanBytes(packI32(vals)); return true; }
    if (method === "ArgMaxI32") { var ai = this._spanBytes(this.regs[rs1]), bestI = 0, bestV = null, an = Math.floor(ai.length / 4); for (var ix = 0; ix < an; ix++) { var av = i32beAt(ai, ix); if (bestV === null || av > bestV) { bestV = av; bestI = ix; } } this.regs[rd] = bestI; return true; }
    if (["AddI32","MulI32","ScaleI32","ReluI32","RmsNormI32","RoPEI32","SoftmaxI32"].indexOf(method) >= 0) {
      var x = this._spanBytes(this.regs[rs1]), xn = Math.floor(x.length / 4), res = [];
      if (method === "AddI32" || method === "MulI32") {
        var y = this._spanBytes(this.regs[rs2]), yn = Math.min(xn, Math.floor(y.length / 4));
        for (var j = 0; j < yn; j++) {
          var av2 = i32beAt(x, j), bv2 = i32beAt(y, j);
          res.push(method === "AddI32" ? ((av2 + bv2) | 0) : ((Math.imul(av2, bv2) >> 8) | 0));
        }
      } else if (method === "ScaleI32") {
        var sc = this.regs[rs2] | 0; for (var k = 0; k < xn; k++) res.push(Math.imul(i32beAt(x, k), sc));
      } else if (method === "ReluI32") {
        for (var m = 0; m < xn; m++) { var rv = i32beAt(x, m); res.push(rv < 0 ? 0 : rv); }
      } else if (method === "RmsNormI32") {
        var g = this._spanBytes(this.regs[rs2]), ss = 0; for (var rn = 0; rn < xn; rn++) { var xv = i32beAt(x, rn); ss += xv * xv; }
        var rms = Math.max(1, Math.floor(Math.sqrt(Math.max(1, Math.floor(ss / Math.max(1, xn))))));
        for (var ri = 0; ri < xn; ri++) { var gg = (ri * 4 + 4 <= g.length) ? i32beAt(g, ri) : 256; res.push(((i32beAt(x, ri) * gg) / rms) | 0); }
      } else if (method === "RoPEI32") {
        var csb = this._spanBytes(this.regs[rs2]), pairs = Math.floor(xn / 2);
        for (var pi = 0; pi < pairs; pi++) {
          var xx = i32beAt(x, pi * 2), yy = i32beAt(x, pi * 2 + 1);
          var cc = (pi * 8 + 4 <= csb.length) ? i32beAt(csb, pi * 2) : 32768;
          var sn = (pi * 8 + 8 <= csb.length) ? i32beAt(csb, pi * 2 + 1) : 0;
          res.push(((xx * cc - yy * sn) >> 15) | 0, ((xx * sn + yy * cc) >> 15) | 0);
        }
      } else if (method === "SoftmaxI32") {
        var xs = [], mx = null; for (var si = 0; si < xn; si++) { var sv = i32beAt(x, si); xs.push(sv); if (mx === null || sv > mx) mx = sv; }
        var ws = [], sumw = 0; for (var wi = 0; wi < xs.length; wi++) { var bucket = Math.min(15, Math.max(0, (mx - xs[wi]) >> 8)); var ww = Math.max(1, 32768 >> bucket); ws.push(ww); sumw += ww; }
        for (var oi = 0; oi < ws.length; oi++) res.push(Math.floor(ws[oi] * 32767 / Math.max(1, sumw)));
      }
      this.regs[rd] = this._newSpanBytes(packI32(res)); return true;
    }
    return false;
  };
  function ternaryWeight(packed, idx) { if (((idx / 4) | 0) >= packed.length) return 0; var code = (packed[(idx / 4) | 0] >>> ((idx & 3) * 2)) & 3; return code === 1 ? 1 : (code === 2 ? -1 : 0); }
  PicoVM.prototype._bitlinear = function (method, rd, rs1, rs2) {
    if (method === "SetShape") { this.bitlinearRows = Math.max(0, this.regs[rs1] | 0); this.bitlinearCols = Math.max(0, this.regs[rs2] | 0); this.regs[rd] = 1; return true; }
    if (method === "MatVecTernary") { var w = this._spanBytes(this.regs[rs1]), v = this._spanBytes(this.regs[rs2]), rows = this.bitlinearRows, cols = this.bitlinearCols || v.length, vals = []; for (var r = 0; r < rows; r++) { var acc = 0, base = r * cols; for (var c = 0; c < cols; c++) if (c < v.length) acc = (acc + ternaryWeight(w, base + c) * i8(v[c])) | 0; vals.push(acc); } this.regs[rd] = this._newSpanBytes(packI32(vals)); return true; }
    if (method === "MatVecBitmap") { var bw = this._spanBytes(this.regs[rs1]), bv = this._spanBytes(this.regs[rs2]), brows = this.bitlinearRows, bcols = this.bitlinearCols || bv.length, mb = Math.ceil(bcols / 8), bvals = []; for (var br = 0; br < brows; br++) { var bacc = 0, row = br * mb * 2; for (var bc = 0; bc < bcols && bc < bv.length; bc++) { var bit = 1 << (bc & 7), z = (bw[row + ((bc / 8) | 0)] || 0) & bit, mn = (bw[row + mb + ((bc / 8) | 0)] || 0) & bit; bacc = (bacc + (z ? 0 : (mn ? -1 : 1)) * i8(bv[bc])) | 0; } bvals.push(bacc); } this.regs[rd] = this._newSpanBytes(packI32(bvals)); return true; }
    if (method === "MatVecBase3") { var pw = this._spanBytes(this.regs[rs1]), pv = this._spanBytes(this.regs[rs2]), prows = this.bitlinearRows, pcols = this.bitlinearCols || pv.length, rb = Math.ceil(pcols / 5), stride = (rb + 3) & ~3, pvals = []; for (var pr = 0; pr < prows; pr++) { var pacc = 0, col = 0; for (var pb = 0; pb < rb; pb++) { var code = pw[pr * stride + pb] || 0, tr = [0,0,0,0,0]; for (var ti = 4; ti >= 0; ti--) { var tt = code % 3; code = Math.floor(code / 3); tr[ti] = tt === 0 ? 0 : (tt === 1 ? 1 : -1); } for (var tj = 0; tj < 5 && col < pcols && col < pv.length; tj++, col++) pacc = (pacc + tr[tj] * i8(pv[col])) | 0; } pvals.push(pacc); } this.regs[rd] = this._newSpanBytes(packI32(pvals)); return true; }
    return false;
  };

  PicoVM.prototype._quant = function (method, rd, rs1, rs2) {
    var data = (method === "GroupScale") ? [] : this._spanBytes(this.regs[rs1]);
    if (method === "AbsMax") { var mx = 0; for (var i = 0; i < Math.floor(data.length / 4); i++) mx = Math.max(mx, Math.abs(i32beAt(data, i))); this.regs[rd] = mx; return true; }
    if (method === "QuantI8") { var scale = Math.max(1, this.regs[rs2] | 0), out = []; for (var q = 0; q < Math.floor(data.length / 4); q++) { var v = (i32beAt(data, q) / scale) | 0; out.push(v < -128 ? 128 : (v > 127 ? 255 : (v & 255))); } this.regs[rd] = this._newSpanBytes(out); return true; }
    if (method === "DequantI8") { var sc = this.regs[rs2] | 0, vals = data.map(function (b) { return i8(b) * sc; }); this.regs[rd] = this._newSpanBytes(packI32(vals)); return true; }
    if (method === "ApplyScale") { var as = this.regs[rs2] | 0, av = []; for (var a = 0; a < Math.floor(data.length / 4); a++) av.push(Math.imul(i32beAt(data, a), as)); this.regs[rd] = this._newSpanBytes(packI32(av)); return true; }
    if (method === "GroupScale") { var spec = this.regs[rs1] >>> 0, n = (spec >>> 16) & 0xFFFF, group = Math.max(1, spec & 0xFFFF), gv = []; for (var g = 0; g < n; g += group) gv.push(Math.min(group, n - g)); this.regs[rd] = this._newSpanBytes(packI32(gv)); return true; }
    return false;
  };

  PicoVM.prototype._attention = function (method, rd, rs1, rs2) {
    if (!this._attn) this._attn = { heads: 1, dim: 0 };
    if (method === "SetShape") { this._attn = { heads: Math.max(1, this.regs[rs1] | 0), dim: Math.max(0, this.regs[rs2] | 0) }; this.regs[rd] = 1; return true; }
    if (method === "Scores") { var q = this._spanBytes(this.regs[rs1]), k = this._spanBytes(this.regs[rs2]), dim = this._attn.dim || Math.min(q.length, k.length), rows = Math.floor(k.length / Math.max(1, dim)), vals = []; for (var r = 0; r < rows; r++) { var acc = 0, base = r * dim; for (var c = 0; c < dim; c++) if (c < q.length && base + c < k.length) acc = (acc + i8(q[c]) * i8(k[base + c])) | 0; vals.push(acc); } this.regs[rd] = this._newSpanBytes(packI32(vals)); return true; }
    if (method === "Mix") { var w = this._spanBytes(this.regs[rs1]), v = this._spanBytes(this.regs[rs2]), d = this._attn.dim || 1, nr = Math.min(Math.floor(w.length / 4), Math.floor(v.length / d)), out = []; for (var mc = 0; mc < d; mc++) { var sum = 0; for (var mr = 0; mr < nr; mr++) sum += i32beAt(w, mr) * i8(v[mr * d + mc]); out.push((sum >> 15) | 0); } this.regs[rd] = this._newSpanBytes(packI32(out)); return true; }
    if (method === "Attend") { if (!this._attention("Scores", rd, rs1, rs2)) return false; return this._tensor("SoftmaxI32", rd, rd, 0); }
    return false;
  };

  PicoVM.prototype._tokenizer = function (method, rd, rs1, rs2) {
    if (!this._tok) this._tok = [];
    if (!this._vocab) { this._vocab = []; this._vrev = {}; }
    if (method === "SetVocab") {
      var lines = this._spanStr(this.regs[rs1]).replace(/;/g, "\n").split(/\r?\n/), vocab = [], rev = {};
      lines.forEach(function (line) { var p = line.lastIndexOf("="); if (p > 0) { var piece = line.slice(0, p), id = parseInt(line.slice(p + 1), 10); if (!isNaN(id)) { var bytes = Array.from(new TextEncoder().encode(piece)); vocab.push([bytes, id]); rev[id] = bytes; } } });
      vocab.sort(function (a, b) { return (b[0].length - a[0].length) || (a[1] - b[1]); });
      this._vocab = vocab; this._vrev = rev; this.regs[rd] = vocab.length; return true;
    }
    if (method === "EncodeBytes") { var d = this._spanBytes(this.regs[rs1]); this._tok = d.map(function (b) { return b + 3; }); this.regs[rd] = this._tok.length; return true; }
    if (method === "EncodeTrie") { var data = this._spanBytes(this.regs[rs1]), out = [], i0 = 0; while (i0 < data.length) { var found = null; for (var vi = 0; vi < this._vocab.length; vi++) { var pc = this._vocab[vi][0], ok = pc.length && i0 + pc.length <= data.length; for (var pj = 0; ok && pj < pc.length; pj++) if (data[i0 + pj] !== pc[pj]) ok = false; if (ok) { found = this._vocab[vi]; break; } } if (found) { out.push(found[1]); i0 += found[0].length; } else { out.push(data[i0] + 3); i0++; } } this._tok = out; this.regs[rd] = out.length; return true; }
    if (method === "DecodeBytes") { this.regs[rd] = this._newSpanBytes(this._tok.filter(function(t){return t>=3&&t<=258;}).map(function(t){return (t-3)&255;})); return true; }
    if (method === "DecodeTrie") { var db = []; for (var di = 0; di < this._tok.length; di++) { var t = this._tok[di]; if (this._vrev[t]) db = db.concat(this._vrev[t]); else if (t >= 3 && t <= 258) db.push((t - 3) & 255); } this.regs[rd] = this._newSpanBytes(db); return true; }
    if (method === "Count") { this.regs[rd] = this._tok.length; return true; }
    if (method === "Token") { var i = this.regs[rs1] | 0; this.regs[rd] = (i >= 0 && i < this._tok.length) ? this._tok[i] : 0; return true; }
    return false;
  };
  PicoVM.prototype._model = function (method, rd, rs1, rs2) {
    if (!this._modelState) this._modelState = { cfg: {}, tensors: {} };
    var m = this._modelState;
    if (method === "SetConfig") { m.cfg[this.regs[rs1] | 0] = this.regs[rs2] | 0; this.regs[rd] = 1; return true; }
    if (method === "GetConfig") { this.regs[rd] = m.cfg[this.regs[rs1] | 0] || 0; return true; }
    if (method === "TensorView") { var spec = this._spanStr(this.regs[rs2]).split("|"), slen = spec.length; while (spec.length < 6) spec.push("0"); var tid = this.regs[rs1] | 0; var pack, card, off, rows, cols, fmt; if (slen >= 6) { pack=spec[0]; card=spec[1]; off=spec[2]; rows=spec[3]; cols=spec[4]; fmt=spec[5]; } else { pack="0"; card="0"; off=spec[0]; rows=spec[1]; cols=spec[2]; fmt=spec[3]; } m.tensors[tid] = { pack: parseInt(pack||"0",10)||0, card: parseInt(card||"0",10)||0, off: parseInt(off||"0",10)||0, rows: parseInt(rows||"0",10)||0, cols: parseInt(cols||"0",10)||0, fmt: parseInt(fmt||"0",10)||0 }; this.regs[rd] = tid; return true; }
    var t = m.tensors[this.regs[rs1] | 0] || {};
    if (method === "TensorOffset") { this.regs[rd] = t.off || 0; return true; }
    if (method === "TensorRows") { this.regs[rd] = t.rows || 0; return true; }
    if (method === "TensorCols") { this.regs[rd] = t.cols || 0; return true; }
    if (method === "TensorFormat") { this.regs[rd] = t.fmt || 0; return true; }
    if (method === "ReadTensor" || method === "ReadTensorRow") { var st = this._st || { blobs: {} }, blob = (st.blobs[String(t.pack||0)+":"+(t.card||0)] || []), elem = (t.fmt === 1 || t.fmt === 2 || t.fmt === 3 || t.fmt === 15) ? 1 : 4, rb = (t.cols || 0) * elem, start = t.off || 0, n = (t.rows || 0) * rb; if (method === "ReadTensorRow") { var row = Math.max(0, this.regs[rs2] | 0); start += row * rb; n = rb; } this.regs[rd] = this._newSpanBytes(blob.slice(start, start + n)); return true; }
    return false;
  };
  PicoVM.prototype._kv = function (method, rd, rs1, rs2) {
    if (!this._kvState) this._kvState = { k: {}, v: {}, shape: [0,0,0], head: 0 };
    var kv = this._kvState, key = (((this.regs[rs1] >>> 16) & 0xFFFF) + ":" + (this.regs[rs1] & 0xFFFF) + ":0"), hkey = (((this.regs[rs1] >>> 16) & 0xFFFF) + ":" + (this.regs[rs1] & 0xFFFF) + ":" + kv.head);
    if (method === "SetShape") { kv.shape = [this.regs[rs1] | 0, this.regs[rs2] | 0, this.regs[rs2] | 0]; this.regs[rd] = 1; return true; }
    if (method === "SetHead") { kv.head = Math.max(0, this.regs[rs1] | 0); this.regs[rd] = kv.head; return true; }
    if (method === "WriteK") { kv.k[key] = this._spanBytes(this.regs[rs2]); this.regs[rd] = 1; return true; }
    if (method === "WriteV") { kv.v[key] = this._spanBytes(this.regs[rs2]); this.regs[rd] = 1; return true; }
    if (method === "WriteKH") { kv.k[hkey] = this._spanBytes(this.regs[rs2]); this.regs[rd] = 1; return true; }
    if (method === "WriteVH") { kv.v[hkey] = this._spanBytes(this.regs[rs2]); this.regs[rd] = 1; return true; }
    if (method === "ReadK") { this.regs[rd] = this._newSpanBytes(kv.k[key] || []); return true; }
    if (method === "ReadV") { this.regs[rd] = this._newSpanBytes(kv.v[key] || []); return true; }
    if (method === "ReadKH") { this.regs[rd] = this._newSpanBytes(kv.k[hkey] || []); return true; }
    if (method === "ReadVH") { this.regs[rd] = this._newSpanBytes(kv.v[hkey] || []); return true; }
    if (method === "Len") { this.regs[rd] = Object.keys(kv.k).length + Object.keys(kv.v).length; return true; }
    if (method === "Clear") { kv.k = {}; kv.v = {}; this.regs[rd] = 1; return true; }
    return false;
  };
  PicoVM.prototype._sampling = function (method, rd, rs1, rs2) {
    if (!this._sampTemp) this._sampTemp = 256;
    if (method === "Temperature") { this._sampTemp = Math.max(1, this.regs[rs1] | 0); this.regs[rd] = this._sampTemp; return true; }
    if (method === "ArgMax") return this._tensor("ArgMaxI32", rd, rs1, rs2);
    if (method === "ArgMaxRows") { if (!this._tensor("MatVecI8", rd, rs1, rs2)) return false; return this._tensor("ArgMaxI32", rd, rd, 0); }
    if (method === "TopK") { var d = this._spanBytes(this.regs[rs1]), k = Math.max(1, this.regs[rs2] | 0), vals = []; for (var i = 0; i < Math.floor(d.length/4); i++) vals.push([i32beAt(d,i),i]); vals.sort(function(a,b){return (b[0]-a[0])||(a[1]-b[1]);}); this.regs[rd]=this._newSpanBytes(packI32(vals.slice(0,k).map(function(x){return x[1];}))); return true; }
    return false;
  };

  PicoVM.prototype._queryHelpers = function (method, rd, rs1, rs2) {
    if (method === "BuildLookupFilter") {
      var pack = this._spanStr(this.regs[rs1]), parts = this._spanStr(this.regs[rs2]).split("|");
      while (parts.length < 6) parts.push("");
      var lines = ["S:" + parts[0], "F:" + pack];
      if (parts[1] && parts[2]) lines.push("W:" + parts[1] + "|" + parts[2] + "|" + parts[3]);
      if (parts[4] && parts[5]) lines.push("W:" + parts[4] + "|!=|" + parts[5]);
      this.regs[rd] = this._strSpan(lines.join("\n")); return true;
    }
    if (method === "BuildManyToManyMap") {
      var p = this._spanStr(this.regs[rs1]), a = this._spanStr(this.regs[rs2]).split("|");
      while (a.length < 3) a.push("");
      this.regs[rd] = this._strSpan("S:" + a[2] + "\nF:" + p + "\nW:" + a[0] + "|==|" + a[1]); return true;
    }
    return false;
  };
  function searchTerms(text) { var m = String(text).toLowerCase().match(/[a-z0-9]+/g); return m || []; }
  PicoVM.prototype._search = function (method, rd, rs1, rs2) {
    if (!this._searchState) this._searchState = { docs: {}, results: [], plan: [0, 0, 0, 0], vector: 0, sem: 0, facets: {}, nums: {}, facetResults: [], saved: null, meta: { name: "", schema: 0 } };
    var s = this._searchState, pack = String((this._st && this._st.pack) || 0);
    function key(card) { return ((parseInt(pack, 10) || 0) << 22) | (card & 0x3FFFFF); }
    if (method === "Clear") { s.docs = {}; s.results = []; s.plan = [0, 0, 0, 0]; s.facets = {}; s.nums = {}; s.facetResults = []; this.regs[rd] = 1; return true; }
    if (method === "Configure") { s.meta = { name: this._spanStr(this.regs[rs1]), schema: this.regs[rs2] >>> 0 }; this.regs[rd] = 1; return true; }
    if (method === "Compatible") { this.regs[rd] = (s.meta.name === this._spanStr(this.regs[rs1]) && s.meta.schema === (this.regs[rs2] >>> 0)) ? 1 : 0; return true; }
    if (method === "Rebuild") { s.results = []; s.facetResults = []; this.regs[rd] = 1; return true; }
    if (method === "SetVector") { s.vector = this.regs[rs1] >>> 0; this.regs[rd] = 1; return true; }
    if (method === "SetSemanticWeight") { s.sem = Math.max(0, this.regs[rs1] | 0); this.regs[rd] = s.sem; return true; }
    if (method === "UpsertText") { var card = this.regs[rs1] >>> 0; s.docs[key(card)] = { card: card, text: this._spanStr(this.regs[rs2]), vector: s.vector }; this.regs[rd] = 1; return true; }
    if (method === "Delete") { var dk = key(this.regs[rs1] >>> 0), ok = s.docs[dk] ? 1 : 0; delete s.docs[dk]; Object.keys(s.facets).forEach(function(k){ if(k.indexOf(dk + "|")===0) delete s.facets[k]; }); Object.keys(s.nums).forEach(function(k){ if(k.indexOf(dk + "|")===0) delete s.nums[k]; }); this.regs[rd] = ok; return true; }
    if (method === "SetFacet") { var fc = this.regs[rs1] >>> 0, fp = this._spanStr(this.regs[rs2]).split("|"); s.facets[key(fc) + "|" + fp[0]] = fp[1] || ""; this.regs[rd] = 1; return true; }
    if (method === "SetNumber") { var nc = this.regs[rs1] >>> 0, np = this._spanStr(this.regs[rs2]).split("|"); s.nums[key(nc) + "|" + np[0]] = parseInt(np[1] || "0", 10) || 0; this.regs[rd] = 1; return true; }
    if (method === "ClearFields") { var ck = key(this.regs[rs1] >>> 0); Object.keys(s.facets).forEach(function(k){ if(k.indexOf(ck + "|")===0) delete s.facets[k]; }); Object.keys(s.nums).forEach(function(k){ if(k.indexOf(ck + "|")===0) delete s.nums[k]; }); this.regs[rd] = 1; return true; }
    if (method === "IndexPack") {
      var ST = storeLib(); if (!this._st) this._st = { store: new ST.PicoStore(), pack: 0, card: 0, results: [], schemas: {}, blobs: {}, sliceOffset: 0, sliceLen: 0 };
      var ipack = String(this.regs[rs1] >>> 0), rows = this._st.store.all(ipack), n = 0;
      rows.forEach(function (e) { var text = Object.keys(e[1]).sort().map(function (k) { return String(e[1][k]); }).join(" "); s.docs[((parseInt(ipack, 10) || 0) << 22) | (e[0] & 0x3FFFFF)] = { card: e[0], text: text, vector: 0 }; n++; });
      this.regs[rd] = n; return true;
    }
    if (method === "QueryText" || method === "QueryHybrid") {
      var q = this._spanStr(this.regs[rs1]), qt = searchTerms(q), res = [], lex = 0, vec = 0, sem = 0;
      Object.keys(s.docs).forEach(function (k) {
        var d = s.docs[k], dt = searchTerms(d.text), score = 0;
        qt.forEach(function (t) { dt.forEach(function (u) { if (u === t) score++; }); });
        if (score) lex++;
        if (method === "QueryHybrid" && s.vector && d.vector === s.vector) { score++; vec++; }
        if (s.sem && String(d.text).toLowerCase().indexOf(String(q).toLowerCase()) >= 0) { score += s.sem; sem++; }
        if (score) res.push([d.card, score]);
      });
      res.sort(function (a, b) { return (b[1] - a[1]) || (a[0] - b[0]); });
      s.results = res.slice(0, 128); s.plan = [lex, vec, s.results.length, sem]; this.regs[rd] = s.results.length; return true;
    }
    if (method === "Result") { var ri = this.regs[rs1] | 0; this.regs[rd] = (ri >= 0 && ri < s.results.length) ? s.results[ri][0] : 0; return true; }
    if (method === "Score") { var si = this.regs[rs1] | 0; this.regs[rd] = (si >= 0 && si < s.results.length) ? s.results[si][1] : 0; return true; }
    if (method === "Plan") { var pi = this.regs[rs1] | 0; this.regs[rd] = (pi >= 0 && pi < s.plan.length) ? s.plan[pi] : 0; return true; }
    if (method === "Facets") { var field = this._spanStr(this.regs[rs1]), counts = {}; Object.keys(s.facets).forEach(function(k){ var sp=k.split("|"); if(sp[1]===field) counts[s.facets[k]]=(counts[s.facets[k]]||0)+1; }); s.facetResults = Object.keys(counts).sort().map(function(v){ return [v, counts[v]]; }); this.regs[rd] = s.facetResults.length; return true; }
    if (method === "FacetValue") { var fvi = this.regs[rs1] | 0; this.regs[rd] = (fvi >= 0 && fvi < s.facetResults.length) ? this._strSpan(s.facetResults[fvi][0]) : 0; return true; }
    if (method === "FacetCount") { var fci = this.regs[rs1] | 0; this.regs[rd] = (fci >= 0 && fci < s.facetResults.length) ? s.facetResults[fci][1] : 0; return true; }
    if (method === "Range") { var spec=this._spanStr(this.regs[rs1]).split("|"), field=spec[0], lo=parseInt(spec[1]||"-2147483648",10), hi=parseInt(spec[2]||"2147483647",10), hits=[]; Object.keys(s.nums).forEach(function(k){ var sp=k.split("|"), v=s.nums[k]; if(sp[1]===field && v>=lo && v<=hi) hits.push(parseInt(sp[0],10)&0x3FFFFF); }); hits.sort(function(a,b){return a-b;}); s.results=hits.map(function(c){return [c,1];}); this.regs[rd]=hits.length; return true; }
    if (method === "Save") { s.saved = { docs: Object.assign({}, s.docs), facets: Object.assign({}, s.facets), nums: Object.assign({}, s.nums), meta: Object.assign({}, s.meta) }; this.regs[rd]=1; return true; }
    if (method === "Load") { if(s.saved){ s.docs=Object.assign({},s.saved.docs); s.facets=Object.assign({},s.saved.facets); s.nums=Object.assign({},s.saved.nums); s.meta=Object.assign({},s.saved.meta); this.regs[rd]=1; } else this.regs[rd]=0; return true; }
    if (method === "JournalUpsert") return this._search("UpsertText", rd, rs1, rs2);
    if (method === "JournalDelete") return this._search("Delete", rd, rs1, rs2);
    if (method === "JournalFacet") return this._search("SetFacet", rd, rs1, rs2);
    if (method === "JournalNumber") return this._search("SetNumber", rd, rs1, rs2);
    if (method === "JournalReplay") { this.regs[rd]=1; return true; }
    return false;
  };

  // ---- Storage.* card CRUD/query over PicoStore ---------------------------
  PicoVM.prototype._storage = function (method, rd, rs1, rs2) {
    if (!this._st) {
      var ST = storeLib();
      this._st = { store: this._cardStore || new ST.PicoStore(), pack: 0, card: 0, results: [], schemas: {}, blobs: {}, sliceOffset: 0, sliceLen: 0 };
    }
    var st = this._st;
    var pack = String(st.pack);
    if (method === "Ready") { this.hostStatus = 0; this.regs[rd] = 1; return true; }
    if (method === "IsUserPack") { var up = this.regs[rs1] >>> 0; this.regs[rd] = (up >= 2 && up <= 0x3FF) ? 1 : 0; return true; }
    if (method === "GetSchemaForPack") {
      this.regs[rd] = this._newSpanBytes(st.schemas[this.regs[rs1] | 0] || []); return true;
    }
    if (method === "SetSchemaForPack") {
      st.schemas[this.regs[rs1] | 0] = this._spanBytes(this.regs[rs2]); this.regs[rd] = 1; return true;
    }
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
    if (method === "SetSlice") {
      st.sliceOffset = Math.max(0, this.regs[rs1] | 0);
      st.sliceLen = Math.max(0, this.regs[rs2] | 0);
      this.regs[rd] = 1; return true;
    }
    if (method === "CardLen") {
      var clid = this.regs[rs1] | 0, ckey = pack + ":" + clid, cblob = st.blobs[ckey] || [];
      this.regs[rd] = cblob.length | 0; return true;
    }
    if (method === "ReadSlice") {
      var rid = this.regs[rs1] | 0, rkey = pack + ":" + rid, rblob = st.blobs[rkey] || [];
      var roff = Math.min(st.sliceOffset, rblob.length), rend = Math.min(roff + st.sliceLen, rblob.length);
      this.regs[rd] = this._newSpanBytes(rblob.slice(roff, rend)); return true;
    }
    if (method === "WriteSlice") {
      var wid = this.regs[rs1] | 0, wkey = pack + ":" + wid, wblob = st.blobs[wkey] || [];
      var wdata = this._spanBytes(this.regs[rs2]), woff = st.sliceOffset;
      while (wblob.length < woff) wblob.push(0);
      for (var wi = 0; wi < wdata.length; wi++) wblob[woff + wi] = wdata[wi] & 0xFF;
      st.blobs[wkey] = wblob; this.regs[rd] = 1; return true;
    }
    return false;
  };

  // Reference GPIO emulator (browser/sim). Mirrors picoscript_vm HostApi._gpio:
  // pins carry [0,1024]; dir 0=in/1=out; pull 0=none/1=up/2=down. Real pins are an
  // injected OS provider on PIOS; this keeps Python and JS byte-identical.
  PicoVM.prototype._gpio = function (method, rd, rs1, rs2) {
    var g = this._gpioProvider || this._gp || (this._gp = { pins: {}, count: 40 });
    if (method === "Count") { this.regs[rd] = g.count; return true; }
    var pin = this.regs[rs1] | 0;
    var st = g.pins[pin] || (g.pins[pin] = { dir: 0, pull: 0, value: 0 });
    if (method === "SetDir") { st.dir = (this.regs[rs2] | 0) ? 1 : 0; this.regs[rd] = 1; return true; }
    if (method === "GetDir") { this.regs[rd] = st.dir; return true; }
    if (method === "SetPull") { var p = this.regs[rs2] | 0; st.pull = (p === 1 || p === 2) ? p : 0; this.regs[rd] = 1; return true; }
    if (method === "GetPull") { this.regs[rd] = st.pull; return true; }
    if (method === "Write") { var v = this.regs[rs2] | 0; st.value = v < 0 ? 0 : (v > 1024 ? 1024 : v); this.regs[rd] = 1; return true; }
    if (method === "Read") { this.regs[rd] = st.value; return true; }
    return false;
  };

  // Reference DMA-ring emulator (Device.*/Stream.*). Mirrors picoscript_vm:
  // deterministic fake ring, RX frame n byte i = (n+i)&0xFF; ringCfg packs
  // dir(bit0:0=RX/1=TX) | bufSize<<1 | frames<<16. Python VM == JS VM.
  PicoVM.prototype._ringState = function () {
    if (this._streamProvider) return this._streamProvider;
    if (!this._dev) this._dev = { devices: {}, streams: {}, leases: {}, ds: 0, ss: 0, ls: 0, sliceOffset: 0, sliceLen: 0 };
    return this._dev;
  };
  PicoVM.prototype._ringFrame = function (idx, buf) {
    var a = []; for (var i = 0; i < buf; i++) a.push((idx + i) & 0xFF); return a;
  };
  PicoVM.prototype._device = function (method, rd, rs1, rs2) {
    var d = this._ringState();
    if (method === "Open") { d.ds++; d.devices[d.ds] = { id: this._spanStr(this.regs[rs1]), open: true }; this.regs[rd] = d.ds; return true; }
    var dev = d.devices[this.regs[rs1] | 0];
    if (method === "Caps") { this.regs[rd] = (dev && dev.open) ? 0x3 : 0; return true; }
    if (method === "Status") { this.regs[rd] = (dev && dev.open) ? 0 : 1; return true; }
    if (method === "Close") { if (dev) dev.open = false; this.regs[rd] = dev ? 1 : 0; return true; }
    return false;
  };
  PicoVM.prototype._stream = function (method, rd, rs1, rs2) {
    var d = this._ringState();
    if (method === "Open") {
      var dev = d.devices[this.regs[rs1] | 0];
      if (!dev || !dev.open) { this.hostStatus = 1; this.regs[rd] = 0; return true; }
      var cfg = this.regs[rs2] >>> 0;
      d.ss++; d.streams[d.ss] = { dir: cfg & 1, buf: (cfg >>> 1) & 0x7FFF, frames: (cfg >>> 16) & 0xFFFF, next: 0, tx: [] };
      this.regs[rd] = d.ss; return true;
    }
    if (method === "Next") {
      var st = d.streams[this.regs[rs1] | 0];
      if (!st || st.next >= st.frames) { this.hostStatus = 3; this.regs[rd] = 0; return true; }
      var idx = st.next++; d.ls++;
      var data = (st.dir === 0) ? this._ringFrame(idx, st.buf) : new Array(st.buf).fill(0);
      d.leases[d.ls] = { stream: this.regs[rs1] | 0, idx: idx, data: data, span: 0, released: false };
      this.regs[rd] = d.ls; return true;
    }
    if (method === "Span") {
      var le = d.leases[this.regs[rs1] | 0];
      if (!le || le.released) { this.hostStatus = 1; this.regs[rd] = 0; return true; }
      if (!le.span) le.span = this._newSpanBytes(le.data);
      this.regs[rd] = le.span; return true;
    }
    if (method === "SetSlice") { d.sliceOffset = Math.max(0, this.regs[rs1] | 0); d.sliceLen = Math.max(0, this.regs[rs2] | 0); this.regs[rd] = 1; return true; }
    if (method === "Slice") {
      var xle = d.leases[this.regs[rs1] | 0];
      if (!xle || xle.released) { this.hostStatus = 1; this.regs[rd] = 0; return true; }
      var xo = Math.min(d.sliceOffset, xle.data.length), xe = Math.min(xo + d.sliceLen, xle.data.length);
      this.regs[rd] = this._newSpanBytes(xle.data.slice(xo, xe)); return true;
    }
    if (method === "Submit") {
      var sst = d.streams[this.regs[rs1] | 0], sle = d.leases[this.regs[rs2] | 0];
      if (sst && sle && !sle.released) { sst.tx.push(sle.span ? this._spanBytes(sle.span) : sle.data); sle.released = true; this.regs[rd] = 1; }
      else this.regs[rd] = 0;
      return true;
    }
    if (method === "Release") {
      var rle = d.leases[this.regs[rs1] | 0];
      if (rle) { rle.released = true; this.regs[rd] = 1; } else this.regs[rd] = 0;
      return true;
    }
    if (method === "Close") { this.regs[rd] = d.streams[this.regs[rs1] | 0] ? 1 : 0; return true; }
    return false;
  };

  // -- Assert.* PSUnit assertion counters (mirrors picoscript_vm HostApi._assert)
  // A PicoScript-authored test harness: tests call Assert.Eq/True; the runner
  // reads Assert.Failed()/Count() after a run. Pure integer logic -> byte-identical
  // to the Python VM.
  PicoVM.prototype._assert = function (method, rd, rs1, rs2) {
    if (this._asTotal === undefined) { this._asTotal = 0; this._asFailed = 0; }
    if (method === "Eq") {
      var ok = ((this.regs[rs1] >>> 0) === (this.regs[rs2] >>> 0)) ? 1 : 0;
      this._asTotal++; if (!ok) this._asFailed++;
      this.regs[rd] = ok; return true;
    }
    if (method === "True") {
      var t = ((this.regs[rs1] >>> 0) !== 0) ? 1 : 0;
      this._asTotal++; if (!t) this._asFailed++;
      this.regs[rd] = t; return true;
    }
    if (method === "Count") { this.regs[rd] = this._asTotal >>> 0; return true; }
    if (method === "Failed") { this.regs[rd] = this._asFailed >>> 0; return true; }
    if (method === "Reset") { this._asTotal = 0; this._asFailed = 0; this.regs[rd] = 0; return true; }
    return false;
  };

  // -- Event.* reactive event queue (mirrors picoscript_vm HostApi._event) ----
  // Deterministic in-runtime FIFO of (type, target, data-span) records. Post
  // enqueues; Next dequeues the oldest (0 = empty). External UI/timer events are
  // injected through the same Post path -> byte-identical to the Python VM.
  PicoVM.prototype._event = function (method, rd, rs1, rs2) {
    if (!this._ev) this._ev = { recs: {}, queue: [], seq: 0, sliceOffset: 0, sliceLen: 0 };
    var e = this._ev;
    if (method === "Post") {
      e.seq++;
      e.recs[e.seq] = { type: this.regs[rs1] >>> 0, target: this.regs[rs2] >>> 0, data: null, span: 0 };
      e.queue.push(e.seq);
      this.regs[rd] = e.seq; return true;
    }
    if (method === "Next") { this.regs[rd] = e.queue.length ? e.queue.shift() : 0; return true; }
    if (method === "Count") { this.regs[rd] = e.queue.length; return true; }
    var rec = e.recs[this.regs[rs1] >>> 0];
    if (method === "Type") { this.regs[rd] = rec ? rec.type : 0; return true; }
    if (method === "Target") { this.regs[rd] = rec ? rec.target : 0; return true; }
    if (method === "Data") {
      if (!rec || rec.data === null) { this.regs[rd] = 0; return true; }
      if (!rec.span) rec.span = this._newSpanBytes(rec.data);
      this.regs[rd] = rec.span; return true;
    }
    if (method === "SetSlice") { e.sliceOffset = Math.max(0, this.regs[rs1] | 0); e.sliceLen = Math.max(0, this.regs[rs2] | 0); this.regs[rd] = 1; return true; }
    if (method === "DataLen") { this.regs[rd] = (rec && rec.data !== null) ? rec.data.length : 0; return true; }
    if (method === "DataSlice") { var ed = (rec && rec.data !== null) ? rec.data : [], eo = Math.min(e.sliceOffset, ed.length), ee = Math.min(eo + e.sliceLen, ed.length); this.regs[rd] = this._newSpanBytes(ed.slice(eo, ee)); return true; }
    if (method === "SetData") {
      if (rec) { rec.data = this._spanBytes(this.regs[rs2]); rec.span = 0; this.regs[rd] = 1; }
      else this.regs[rd] = 0;
      return true;
    }
    return false;
  };

  // -- Ui.* retained scene tree + PicoWire serialize (mirrors HostApi._ui) -----
  // Minimal remote windowing: build a retained tree, Ui.Serialize emits the
  // deterministic PicoWire binary. Byte-identical to the Python VM.
  var UI_KIND = { Window: 1, Panel: 2, Label: 3, Button: 4, TextBox: 5, Checkbox: 6 };
  PicoVM.prototype._ui = function (method, rd, rs1, rs2) {
    if (!this._uiState) this._uiState = { nodes: {}, seq: 0 };
    var u = this._uiState;
    if (UI_KIND[method] !== undefined) {
      u.seq++;
      var nid = u.seq, parent = 0, text = [];
      if (method === "Window") { text = this._spanBytes(this.regs[rs1]); }
      else { parent = this.regs[rs1] >>> 0; if (method !== "Panel") text = this._spanBytes(this.regs[rs2]); }
      u.nodes[nid] = { kind: UI_KIND[method], id: 0, x: 0, y: 0, w: 0, h: 0, value: 0, text: text, children: [] };
      var p = u.nodes[parent];
      if (p) p.children.push(nid);
      this.regs[rd] = nid; return true;
    }
    var nd = u.nodes[this.regs[rs1] >>> 0];
    if (method === "Pos") { var pv = this.regs[rs2] >>> 0; if (nd) { nd.x = (pv >>> 16) & 0xFFFF; nd.y = pv & 0xFFFF; } this.regs[rd] = nd ? 1 : 0; return true; }
    if (method === "Size") { var sv = this.regs[rs2] >>> 0; if (nd) { nd.w = (sv >>> 16) & 0xFFFF; nd.h = sv & 0xFFFF; } this.regs[rd] = nd ? 1 : 0; return true; }
    if (method === "SetText") { if (nd) nd.text = this._spanBytes(this.regs[rs2]); this.regs[rd] = nd ? 1 : 0; return true; }
    if (method === "SetId") { if (nd) nd.id = this.regs[rs2] & 0xFFFF; this.regs[rd] = nd ? 1 : 0; return true; }
    if (method === "SetValue") { if (nd) nd.value = this.regs[rs2] & 0xFFFF; this.regs[rd] = nd ? 1 : 0; return true; }
    if (method === "Serialize") { this.regs[rd] = this._newSpanBytes(this._uiWire(this.regs[rs1] >>> 0)); return true; }
    return false;
  };

  PicoVM.prototype._uiWire = function (root) {
    var u = this._uiState || { nodes: {} };
    var order = [];
    function walk(nid) {
      var nd = u.nodes[nid];
      if (!nd) return;
      order.push(nid);
      for (var i = 0; i < nd.children.length; i++) walk(nd.children[i]);
    }
    if (u.nodes[root]) walk(root);
    var out = [];
    function u16(v) { out.push((v >>> 8) & 0xFF); out.push(v & 0xFF); }
    function psInt(key, v) {            // PSC1 T_INT field
      out.push(key.length);
      for (var j = 0; j < key.length; j++) out.push(key.charCodeAt(j));
      out.push(1); out.push((v >>> 24) & 0xFF, (v >>> 16) & 0xFF, (v >>> 8) & 0xFF, v & 0xFF);
    }
    function psStr(key, vb) {           // PSC1 T_STR field (raw bytes)
      vb = vb.slice(0, 0xFFFF);
      out.push(key.length);
      for (var j = 0; j < key.length; j++) out.push(key.charCodeAt(j));
      out.push(2); out.push((vb.length >>> 8) & 0xFF, vb.length & 0xFF);
      for (var t = 0; t < vb.length; t++) out.push(vb[t] & 0xFF);
    }
    u16(order.length);
    for (var k = 0; k < order.length; k++) {
      var nd = u.nodes[order[k]];
      out.push(80, 83, 67, 49);        // 'PSC1' magic
      u16(9);                          // 9 fields per node record
      psInt("c", nd.kind);
      psInt("ch", nd.children.length);
      psInt("h", nd.h);
      psInt("id", nd.id);
      psStr("t", nd.text);
      psInt("v", nd.value);
      psInt("w", nd.w);
      psInt("x", nd.x);
      psInt("y", nd.y);
    }
    return out;
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

  // Human-readable output for mixed programs. Raw VM output remains `output`; this
  // only formats typed chunks for the browser UI (e.g. PRINT "<br/>"; PRINT 14).
  PicoVM.prototype.outputDisplayText = function () {
    if (!this.outputEvents || !this.outputEvents.length) return this.outputText();
    var dec = new TextDecoder("utf-8"), parts = [];
    for (var i = 0; i < this.outputEvents.length; i++) {
      var e = this.outputEvents[i];
      if (e.kind === "int") parts.push(String(e.value | 0));
      else if (e.kind === "byte") {
        var b = e.value & 0xFF;
        parts.push((b === 10 || b === 13 || b === 9 || (b >= 32 && b <= 126)) ? String.fromCharCode(b) : "[" + b + "]");
      } else if (e.kind === "bytes") {
        parts.push(dec.decode(Uint8Array.from(e.bytes || [])));
      }
    }
    return parts.join("");
  };

  // -- Process.*/Env.* OS-worker process lifecycle (mirrors HostApi._process_env) ----
  PicoVM.prototype._processEnv = function (ns, method, rd, rs1, rs2) {
    if (!this._proc) this._proc = { seq: 0, self: 1, parent: 0, table: {}, args: [], envVars: {} };
    var p = this._proc;
    if (ns === "Process") {
      if (method === "Self") { this.regs[rd] = p.self; return true; }
      if (method === "Parent") { this.regs[rd] = p.parent; return true; }
      if (method === "Spawn") {
        p.seq++;
        var pid = p.seq + 100;
        p.table[pid] = { status: 0, exitCode: 0, pack: this.regs[rs1] >>> 0, entry: this.regs[rs2] >>> 0 };
        this.log.push("Process.Spawn pack=" + (this.regs[rs1] >>> 0) + " entry=" + (this.regs[rs2] >>> 0) + " -> pid=" + pid);
        this.regs[rd] = pid; return true;
      }
      if (method === "Exit") {
        var code = this.regs[rs1] | 0;
        p.table[p.self] = { status: 1, exitCode: code, pack: 0, entry: 0 };
        this.log.push("Process.Exit code=" + code);
        this.halted = true; return true;
      }
      if (method === "Kill") {
        var kp = p.table[this.regs[rs1] >>> 0];
        if (kp && kp.status === 0) { kp.status = 2; kp.exitCode = -1; this.regs[rd] = 1; }
        else this.regs[rd] = 0;
        return true;
      }
      if (method === "Status") {
        var sp = p.table[this.regs[rs1] >>> 0];
        this.regs[rd] = sp ? sp.status : 1; return true;
      }
      if (method === "Wait") {
        var wp = p.table[this.regs[rs1] >>> 0];
        this.regs[rd] = wp ? (wp.exitCode | 0) : 0; return true;
      }
      if (method === "Args") { this.regs[rd] = this._newSpanBytes(p.args); return true; }
    }
    if (ns === "Env") {
      if (method === "Get") {
        var key = this._spanStr(this.regs[rs1]);
        var val = p.envVars[key];
        if (val !== undefined) { this.regs[rd] = this._newSpanBytes(this._strToBytes(val)); }
        else { this.regs[rd] = 0; this.hostStatus = 1; }
        return true;
      }
      if (method === "Set") {
        p.envVars[this._spanStr(this.regs[rs1])] = this._spanStr(this.regs[rs2]);
        this.regs[rd] = 1; return true;
      }
      if (method === "Count") {
        this.regs[rd] = Object.keys(p.envVars).length; return true;
      }
      if (method === "Key") {
        var keys = Object.keys(p.envVars).sort();
        var idx = this.regs[rs1] >>> 0;
        if (idx < keys.length) { this.regs[rd] = this._newSpanBytes(this._strToBytes(keys[idx])); }
        else { this.regs[rd] = 0; this.hostStatus = 1; }
        return true;
      }
    }
    return false;
  };

  // -- Timer.*/Scheduler.* timers and deterministic scheduler (mirrors HostApi._timer_scheduler) ----
  PicoVM.prototype._timerScheduler = function (ns, method, rd, rs1, rs2) {
    if (!this._tmr) this._tmr = { seq: 0, timers: {}, elapsedMs: 0 };
    var t = this._tmr;
    if (ns === "Timer") {
      if (method === "After") {
        t.seq++; var ms = this.regs[rs1] >>> 0;
        t.timers[t.seq] = { ms: ms, repeat: false, remaining: ms, active: true };
        this.regs[rd] = t.seq; return true;
      }
      if (method === "Every") {
        t.seq++; var ms2 = this.regs[rs1] >>> 0;
        t.timers[t.seq] = { ms: ms2, repeat: true, remaining: ms2, active: true };
        this.regs[rd] = t.seq; return true;
      }
      if (method === "Cancel") {
        var ti = t.timers[this.regs[rs1] >>> 0];
        if (ti) { ti.active = false; this.regs[rd] = 1; }
        else this.regs[rd] = 0;
        return true;
      }
      if (method === "Elapsed") { this.regs[rd] = t.elapsedMs >>> 0; return true; }
    }
    if (ns === "Scheduler") {
      if (method === "Tick") {
        var delta = this.regs[rs1] >>> 0;
        t.elapsedMs += delta;
        var fired = 0;
        if (!this._ev) this._ev = { recs: {}, queue: [], seq: 0, sliceOffset: 0, sliceLen: 0 };
        var e = this._ev;
        var handles = Object.keys(t.timers);
        for (var i = 0; i < handles.length; i++) {
          var h = handles[i] | 0, ti2 = t.timers[h];
          if (!ti2.active) continue;
          ti2.remaining -= delta;
          while (ti2.remaining <= 0 && ti2.active) {
            fired++;
            e.seq++;
            e.recs[e.seq] = { type: 100, target: h, data: null, span: 0 };
            e.queue.push(e.seq);
            if (ti2.repeat) ti2.remaining += ti2.ms;
            else { ti2.active = false; break; }
          }
        }
        this.regs[rd] = fired; return true;
      }
    }
    return false;
  };

  // -- Principal.*/Capability.*/Sandbox.* identity & authz harness (mirrors HostApi._principal_cap) ----
  PicoVM.prototype._principalCap = function (ns, method, rd, rs1, rs2) {
    if (!this._auth) this._auth = { name: "anonymous", roles: [], claims: {}, denied: 0 };
    var a = this._auth;
    if (ns === "Principal") {
      if (method === "Current") { this.regs[rd] = this._newSpanBytes(this._strToBytes(a.name)); return true; }
      if (method === "HasRole") {
        var role = this._spanStr(this.regs[rs1]);
        this.regs[rd] = a.roles.indexOf(role) >= 0 ? 1 : 0; return true;
      }
      if (method === "Claims") {
        var ks = Object.keys(a.claims).sort();
        var pairs = ks.map(function (k) { return k + "=" + a.claims[k]; }).join(";");
        this.regs[rd] = this._newSpanBytes(this._strToBytes(pairs)); return true;
      }
    }
    if (ns === "Capability") {
      if (method === "Has") {
        var cb = this.regs[rs1] >>> 0;
        this.regs[rd] = ((this.caps & cb) && !(a.denied & cb)) ? 1 : 0; return true;
      }
      if (method === "Request") {
        var cb2 = this.regs[rs1] >>> 0;
        if (a.denied & cb2) this.regs[rd] = 0;
        else { this.caps |= cb2; this.regs[rd] = 1; }
        return true;
      }
      if (method === "Drop") {
        this.caps &= ~(this.regs[rs1] >>> 0);
        this.regs[rd] = 1; return true;
      }
    }
    if (ns === "Sandbox") {
      if (method === "Deny") {
        var cb3 = this.regs[rs1] >>> 0;
        a.denied |= cb3; this.caps &= ~cb3;
        this.regs[rd] = 1; return true;
      }
    }
    return false;
  };

  // -- Error.* global error handler + fault inspection (mirrors HostApi._error_hook) ----
  PicoVM.prototype._errorHook = function (method, rd, rs1, rs2) {
    if (!this._errState) this._errState = { handlerPc: 0, code: 0, detail: 0, resumePc: 0 };
    var es = this._errState;
    if (method === "SetHandler") { es.handlerPc = this.regs[rs1] >>> 0; this.regs[rd] = 1; return true; }
    if (method === "HasHandler") { this.regs[rd] = es.handlerPc ? 1 : 0; return true; }
    if (method === "Code") { this.regs[rd] = es.code >>> 0; return true; }
    if (method === "Detail") { this.regs[rd] = es.detail >>> 0; return true; }
    if (method === "Resume") {
      es.code = 0; es.detail = 0;
      if (es.resumePc) { this.pc = es.resumePc; es.resumePc = 0; }
      this.regs[rd] = 1; return true;
    }
    if (method === "Clear") { es.code = 0; es.detail = 0; this.regs[rd] = 1; return true; }
    return false;
  };

  // -- Capsule.* inter-card module switching (mirrors HostApi._capsule_exec) ----
  PicoVM.prototype._capsuleExec = function (method, rd, rs1, rs2) {
    if (!this._capExec) this._capExec = { seq: 0, modules: {}, schedules: [] };
    var c = this._capExec;
    if (method === "Call") {
      this.log.push("Capsule.Call pack=" + (this.regs[rs1] >>> 0) + " card=" + (this.regs[rs2] >>> 0));
      this.regs[rd] = 0; return true;
    }
    if (method === "Schedule") {
      c.schedules.push({ pack: this.regs[rs1] >>> 0, card: this.regs[rs2] >>> 0 });
      this.log.push("Capsule.Schedule pack=" + (this.regs[rs1] >>> 0) + " card=" + (this.regs[rs2] >>> 0));
      this.regs[rd] = 1; return true;
    }
    if (method === "Jump") {
      this.log.push("Capsule.Jump pack=" + (this.regs[rs1] >>> 0) + " card=" + (this.regs[rs2] >>> 0));
      this.halted = true; return true;
    }
    if (method === "LoadModule") {
      c.seq++; var h = c.seq;
      c.modules[h] = { pack: this.regs[rs1] >>> 0, card: this.regs[rs2] >>> 0 };
      this.log.push("Capsule.LoadModule pack=" + (this.regs[rs1] >>> 0) + " card=" + (this.regs[rs2] >>> 0) + " -> handle=" + h);
      this.regs[rd] = h; return true;
    }
    if (method === "RunModule") {
      var m = c.modules[this.regs[rs1] >>> 0];
      if (m) this.log.push("Capsule.RunModule handle=" + (this.regs[rs1] >>> 0) + " pack=" + m.pack + " card=" + m.card);
      this.regs[rd] = 0; return true;
    }
    return false;
  };

  // -- Base64.* encode/decode (mirrors HostApi._base64) ----
  PicoVM.prototype._base64 = function (method, rd, rs1, rs2) {
    if (typeof btoa === "undefined" && typeof Buffer === "undefined") return false;
    if (method === "Encode") {
      var data = this._spanBytes(this.regs[rs1]);
      var str = "";
      for (var i = 0; i < data.length; i++) str += String.fromCharCode(data[i]);
      var enc = (typeof btoa !== "undefined") ? btoa(str) : Buffer.from(data).toString("base64");
      this.regs[rd] = this._newSpanBytes(this._strToBytes(enc));
      return true;
    }
    if (method === "Decode" || method === "UrlDecode") {
      var b64 = this._spanStr(this.regs[rs1]);
      if (method === "UrlDecode") {
        b64 = b64.replace(/-/g, "+").replace(/_/g, "/");
        var pad = (4 - b64.length % 4) % 4;
        for (var p = 0; p < pad; p++) b64 += "=";
      }
      try {
        var dec;
        if (typeof atob !== "undefined") {
          var raw = atob(b64); dec = [];
          for (var j = 0; j < raw.length; j++) dec.push(raw.charCodeAt(j));
        } else {
          var buf = Buffer.from(b64, "base64"); dec = Array.from(buf);
        }
        this.regs[rd] = this._newSpanBytes(dec);
      } catch (e) {
        this.regs[rd] = this._newSpanBytes([]);
        this.hostStatus = 2;
      }
      return true;
    }
    return false;
  };

  // -- DateTime extended (DiffDays/Year/Month/Day) (mirrors HostApi._datetime_ext) ----
  PicoVM.prototype._datetimeExt = function (method, rd, rs1, rs2) {
    if (method === "DiffDays") {
      var a = this.regs[rs1] | 0, b = this.regs[rs2] | 0;
      this.regs[rd] = ((a - b) / 86400000) | 0;
      return true;
    }
    var ms = this.regs[rs1] | 0;
    var dt = new Date(ms);
    if (method === "Year") { this.regs[rd] = dt.getUTCFullYear(); return true; }
    if (method === "Month") { this.regs[rd] = dt.getUTCMonth() + 1; return true; }
    if (method === "Day") { this.regs[rd] = dt.getUTCDate(); return true; }
    return false;
  };

  // -- Req.Param / Req.ParamCount (mirrors HostApi._req_param) ----
  PicoVM.prototype._reqParam = function (method, rd, rs1, rs2) {
    var ctx = this._reqCtx || {};
    var path = ctx.path || "";
    var segs = path.split("/").filter(function (s) { return s.length > 0; });
    if (method === "ParamCount") { this.regs[rd] = segs.length; return true; }
    if (method === "Param") {
      var idx = this.regs[rs1] >>> 0;
      if (idx < segs.length) { this.regs[rd] = this._newSpanBytes(this._strToBytes(segs[idx])); }
      else { this.regs[rd] = 0; this.hostStatus = 1; }
      return true;
    }
    return false;
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
