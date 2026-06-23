// picoc.js -- PicoScript compiler in JavaScript (browser + Node).
//
// Faithful port of picoscript_il.py + picoscript_cfront.py + picoscript_basic.py
// + picoscript_python.py + picoscript_english.py.
// Lets the browser compile C-syntax, BASIC-like, Python-style and natural-English
// source to the *identical* bytecode the Python toolchain produces, so you can
// compile AND debug in-browser.
//
//   PicoCompile.compile(src, "c" | "basic" | "python" | "english") -> { words, il }
//
// Verified against the Python compiler (byte-for-byte) by tests/test_jscompiler.js.
(function (root, factory) {
  var hooks = (typeof module !== "undefined" && module.exports)
    ? require("./pico_hooks.js") : root.PV_HOOKS;
  var P = factory(hooks);
  if (typeof module !== "undefined" && module.exports) module.exports = P;
  else root.PicoCompile = P;
})(typeof globalThis !== "undefined" ? globalThis : this, function (PV_HOOKS) {
  "use strict";

  // ── ISA constants ────────────────────────────────────────────────────────
  var OP = { NOOP:0, LOAD:1, SAVE:2, PIPE:3, ADD:4, SUB:5, MUL:6, DIV:7,
             INC:8, JUMP:9, BRANCH:10, CALL:11, RETURN:12, WAIT:13, RAISE:14, DSP:15 };
  var ADDR_REG = 1;
  var ADDR_REG_OFF = 3;
  var COND = { EQ:0, NE:1, LT:2, GT:3, LE:4, GE:5, Z:6, NZ:7, EOF:8, ERR:9 };
  var COND_NEGATE = { EQ:"NE", NE:"EQ", LT:"GE", GE:"LT", GT:"LE", LE:"GT" };
  var ARITH = { add:OP.ADD, sub:OP.SUB, mul:OP.MUL, div:OP.DIV };
  var H = PV_HOOKS;

  // host-hook name<->code tables from the generated map
  var HOOK_BY_NAME = {}, HOOK_CANON = {}, CT_BY_NAME = {};
  Object.keys(H.BY_CODE).forEach(function (code) {
    var name = H.BY_CODE[code];            // "Ns.Method"
    HOOK_BY_NAME[name] = parseInt(code, 10);
    var dot = name.indexOf(".");
    var ns = name.slice(0, dot), m = name.slice(dot + 1);
    HOOK_CANON[(ns + "." + m).toLowerCase()] = [ns, m];
  });
  Object.keys(H.CONTENT_TYPES).forEach(function (v) {
    CT_BY_NAME[H.CONTENT_TYPES[v]] = parseInt(v, 10);
  });
  var NAMED_CONSTANTS = {
    // HTTP methods (Req.Method)
    "HTTP_METHOD_GET": 1, "HTTP_METHOD_POST": 2, "HTTP_METHOD_PUT": 3,
    "HTTP_METHOD_DELETE": 4, "HTTP_METHOD_HEAD": 5, "HTTP_METHOD_PATCH": 6,
    "HTTP_METHOD_OPTIONS": 7, "HTTP_METHOD_CONNECT": 8, "HTTP_METHOD_TRACE": 9,
    "METHOD_GET": 1, "METHOD_POST": 2, "METHOD_PUT": 3,
    "METHOD_DELETE": 4, "METHOD_HEAD": 5, "METHOD_PATCH": 6,
    "METHOD_OPTIONS": 7, "METHOD_CONNECT": 8, "METHOD_TRACE": 9,
    "HTTPMETHOD.GET": 1, "HTTPMETHOD.POST": 2, "HTTPMETHOD.PUT": 3,
    "HTTPMETHOD.DELETE": 4, "HTTPMETHOD.HEAD": 5, "HTTPMETHOD.PATCH": 6,
    "HTTPMETHOD.OPTIONS": 7, "HTTPMETHOD.CONNECT": 8, "HTTPMETHOD.TRACE": 9,
    // HTTP statuses (Resp.Status)
    "HTTP_STATUS_OK": 200, "HTTP_STATUS_CREATED": 201, "HTTP_STATUS_ACCEPTED": 202,
    "HTTP_STATUS_NO_CONTENT": 204, "HTTP_STATUS_BAD_REQUEST": 400,
    "HTTP_STATUS_UNAUTHORIZED": 401, "HTTP_STATUS_FORBIDDEN": 403,
    "HTTP_STATUS_NOT_FOUND": 404, "HTTP_STATUS_CONFLICT": 409,
    "HTTP_STATUS_UNPROCESSABLE_ENTITY": 422, "HTTP_STATUS_TOO_MANY_REQUESTS": 429,
    "HTTP_STATUS_INTERNAL_SERVER_ERROR": 500, "HTTP_STATUS_NOT_IMPLEMENTED": 501,
    "HTTP_STATUS_BAD_GATEWAY": 502, "HTTP_STATUS_SERVICE_UNAVAILABLE": 503,
    "STATUS_OK": 200, "STATUS_CREATED": 201, "STATUS_ACCEPTED": 202,
    "STATUS_NO_CONTENT": 204, "STATUS_BAD_REQUEST": 400, "STATUS_UNAUTHORIZED": 401,
    "STATUS_FORBIDDEN": 403, "STATUS_NOT_FOUND": 404, "STATUS_CONFLICT": 409,
    "STATUS_UNPROCESSABLE_ENTITY": 422, "STATUS_TOO_MANY_REQUESTS": 429,
    "STATUS_INTERNAL_SERVER_ERROR": 500, "STATUS_NOT_IMPLEMENTED": 501,
    "STATUS_BAD_GATEWAY": 502, "STATUS_SERVICE_UNAVAILABLE": 503,
    "HTTPSTATUS.OK": 200, "HTTPSTATUS.CREATED": 201, "HTTPSTATUS.ACCEPTED": 202,
    "HTTPSTATUS.NO_CONTENT": 204, "HTTPSTATUS.BAD_REQUEST": 400,
    "HTTPSTATUS.UNAUTHORIZED": 401, "HTTPSTATUS.FORBIDDEN": 403,
    "HTTPSTATUS.NOT_FOUND": 404, "HTTPSTATUS.CONFLICT": 409,
    "HTTPSTATUS.UNPROCESSABLE_ENTITY": 422, "HTTPSTATUS.TOO_MANY_REQUESTS": 429,
    "HTTPSTATUS.INTERNAL_SERVER_ERROR": 500, "HTTPSTATUS.NOT_IMPLEMENTED": 501,
    "HTTPSTATUS.BAD_GATEWAY": 502, "HTTPSTATUS.SERVICE_UNAVAILABLE": 503
  };
  Object.keys(H.CONSTANTS || {}).forEach(function (k) { NAMED_CONSTANTS[k.toUpperCase()] = H.CONSTANTS[k] | 0; });
  function namedConstant(name) {
    if (name == null) return null;
    var key = String(name).toUpperCase();
    return Object.prototype.hasOwnProperty.call(NAMED_CONSTANTS, key) ? (NAMED_CONSTANTS[key] | 0) : null;
  }
  function canonHost(ns, m) {
    return HOOK_CANON[(ns + "." + m).toLowerCase()] || [ns, m];
  }

  function enc(op, rd, rs1, rs2, imm) {
    rd = rd || 0; rs1 = rs1 || 0; rs2 = rs2 || 0; imm = imm || 0;
    return (((op << 28) | (rd << 24) | (rs1 << 20) | (rs2 << 16) | (imm & 0xFFFF)) >>> 0);
  }
  function encodeCardAddr(tenant, pack, card) {
    return ((tenant << 11) | (pack << 5) | card) & 0xFFFF;
  }

  // ── operand + instruction model ─────────────────────────────────────────
  var _vid = 0;
  function VReg(name, pinned) { this.id = _vid++; this.name = name || ("v" + this.id); this.pinned = !!pinned; }
  function Imm(v) { this.value = v; }
  function isImm(x) { return x instanceof Imm; }
  function isVReg(x) { return x instanceof VReg; }

  // Inst is a plain object: { op, dst, a, b, cond, label, ns, method, args, imm, text }
  function Inst(op, f) { f = f || {}; f.op = op; if (!f.args) f.args = []; return f; }

  function ILBuilder() { this.insts = []; this._ln = 0; this.curPos = -1; }
  ILBuilder.prototype = {
    vreg: function (n) { return new VReg(n); },
    newLabel: function (h) { this._ln++; return (h || "L") + this._ln; },
    _push: function (ins) { ins.pos = this.curPos; this.insts.push(ins); return ins; },   // INV-25: stamp source offset
    const_: function (d, v) { this._push(Inst("const", { dst: d, imm: v })); },
    mov: function (d, s) { this._push(Inst("mov", { dst: d, a: s })); },
    arith: function (op, d, a, b) { this._push(Inst(op, { dst: d, a: a, b: b })); },
    inc: function (d) { this._push(Inst("inc", { dst: d })); },
    cmpbr: function (c, a, b, l) { this._push(Inst("cmpbr", { cond: c, a: a, b: b, label: l })); },
    jmp: function (l) { this._push(Inst("jmp", { label: l })); },
    jmptab: function (sel, targets, def) { this._push(Inst("jmptab", { a: sel, targets: targets, label: def })); },
    label: function (n) { this._push(Inst("label", { label: n })); },
    call: function (l) { this._push(Inst("call", { label: l })); },
    ret: function () { this._push(Inst("ret", {})); },
    host: function (ns, m, args, d) { this._push(Inst("host", { ns: ns, method: m, args: args || [], dst: d || null })); },
    load: function (d, a) { this._push(Inst("load", { dst: d, imm: a })); },
    save: function (s, a) { this._push(Inst("save", { a: s, imm: a })); },
    pipe: function (s, a) { this._push(Inst("pipe", { a: s, imm: a })); },
    net: function (k, v) { this._push(Inst("net", { method: k, imm: (typeof v === "number" ? v : 0), text: (typeof v === "string" ? v : "") })); }
  };

  function operandVRegs(ins) {
    var out = [];
    [ins.dst, ins.a, ins.b].forEach(function (x) { if (isVReg(x)) out.push(x); });
    (ins.args || []).forEach(function (x) { if (isVReg(x)) out.push(x); });
    return out;
  }

  // ── optimizer ────────────────────────────────────────────────────────────
  function optimize(insts) {
    var out = [];
    insts.forEach(function (ins) {
      if (ARITH[ins.op] !== undefined && isImm(ins.a) && isImm(ins.b)) {
        var av = ins.a.value, bv = ins.b.value, r;
        if (ins.op === "add") r = av + bv;
        else if (ins.op === "sub") r = av - bv;
        else if (ins.op === "mul") r = av * bv;
        else r = bv !== 0 ? ((av / bv) | 0) : 0;   // trunc toward zero + 2's-comp wrap (matches VM/C)
        out.push(Inst("const", { dst: ins.dst, imm: r, pos: ins.pos })); return;
      }
      if (ins.op === "add" && isVReg(ins.a) && ins.dst === ins.a && isImm(ins.b) && ins.b.value === 1) {
        out.push(Inst("inc", { dst: ins.dst, pos: ins.pos })); return;
      }
      if (ins.op === "mov" && isVReg(ins.a) && ins.dst === ins.a) return;
      out.push(ins);
    });
    return out;
  }

  // ── register allocation (loop-aware linear scan) ──────────────────────────
  // Mirrors picoscript_il.allocate. `spill` reserves 3 shuttle regs (usable=13) and
  // records overflow vregs in `spilled` instead of throwing -- the spill-decision pass.
  function allocate(insts, spill) {
    var first = {}, last = {}, order = [], vregs = {};
    insts.forEach(function (ins, i) {
      operandVRegs(ins).forEach(function (v) {
        if (!(v.id in first)) { first[v.id] = i; order.push(v.id); vregs[v.id] = v; }
        last[v.id] = i;
      });
    });
    var labelPos = {};
    insts.forEach(function (ins, i) { if (ins.op === "label") labelPos[ins.label] = i; });
    insts.forEach(function (ins, i) {
      if ((ins.op === "jmp" || ins.op === "cmpbr") && (ins.label in labelPos)) {
        var t = labelPos[ins.label];
        if (t <= i) {
          Object.keys(first).forEach(function (vid) {
            if (first[vid] < t && t <= last[vid] && last[vid] < i) last[vid] = i;
          });
        }
      }
    });
    var n = Math.max(1, insts.length);
    var callIdx = [];
    insts.forEach(function (ins, i) { if (ins.op === "call") callIdx.push(i); });
    Object.keys(vregs).forEach(function (vid) {
      var spansCall = callIdx.some(function (ci) { return first[vid] <= ci && ci <= last[vid]; });
      if (vregs[vid].pinned || spansCall) { first[vid] = 0; last[vid] = n; }
    });

    var usable = spill ? 13 : 16;   // NUM_REGS-3 shuttle headroom during spill decision
    var free = []; for (var k = 0; k < usable; k++) free.push(k);
    var active = [];           // [endIndex, vid]
    var mapping = {};
    var spilled = {}, nextSlot = 0;
    function expire(at) {
      var keep = [];
      active.forEach(function (e) {
        if (e[0] < at) free.push(mapping[e[1]]);
        else keep.push(e);
      });
      keep.sort(function (a, b) { return a[0] - b[0]; });
      active = keep;
    }
    var ord = order.slice().sort(function (a, b) { return first[a] - first[b]; });
    ord.forEach(function (vid) {
      expire(first[vid]);
      if (free.length) {
        free.sort(function (a, b) { return a - b; });
        var reg = free.shift();
        mapping[vid] = reg;
        active.push([last[vid], vid]);
        active.sort(function (a, b) { return a[0] - b[0]; });
      } else if (spill) {
        spilled[vid] = nextSlot++;
      } else {
        throw new Error("register pressure exceeds 16 live values; simplify the program");
      }
    });
    return { mapping: mapping, spilled: spilled };
  }

  // ── automatic register spilling (mirrors picoscript_il._legalize_spills) ───
  // Ops whose `dst` is a written destination, and ops that read `dst` as input.
  var DST_WRITTEN = { const: 1, mov: 1, add: 1, sub: 1, mul: 1, div: 1, host: 1, load: 1, inc: 1 };
  var DST_READ_OPS = { cmpbr: 1, inc: 1 };
  var SPILL_CARD_BASE = 0xF000;   // reserved scratch-card region: one card per spilled vreg

  function legalizeSpills(insts, spilledSet) {
    var vids = Object.keys(spilledSet);
    if (vids.length === 0) return insts;
    // Home scratch card per spilled vreg, assigned in ascending vreg-id order.
    var sorted = vids.map(Number).sort(function (a, b) { return a - b; });
    var slot = {};
    sorted.forEach(function (vid, i) { slot[vid] = SPILL_CARD_BASE + i; });
    function sp(x) { return isVReg(x) && (x.id in spilledSet); }
    function clone(ins, over) { var c = {}; for (var k in ins) c[k] = ins[k]; for (var k2 in over) c[k2] = over[k2]; return c; }
    var out = [];
    insts.forEach(function (ins) {
      if (!operandVRegs(ins).some(sp)) { out.push(ins); return; }
      var newA = ins.a, newB = ins.b, newArgs = (ins.args || []).slice();
      if (sp(ins.a)) { var sa = new VReg("spill"); out.push(Inst("load", { dst: sa, imm: slot[ins.a.id] })); newA = sa; }
      if (sp(ins.b)) { var sb = new VReg("spill"); out.push(Inst("load", { dst: sb, imm: slot[ins.b.id] })); newB = sb; }
      for (var i = 0; i < newArgs.length; i++) {
        if (sp(newArgs[i])) { var sg = new VReg("spill"); out.push(Inst("load", { dst: sg, imm: slot[newArgs[i].id] })); newArgs[i] = sg; }
      }
      var newDst = ins.dst, storeBack = null;
      if (sp(ins.dst)) {
        var sd = new VReg("spill");
        if (DST_READ_OPS[ins.op]) out.push(Inst("load", { dst: sd, imm: slot[ins.dst.id] }));   // dst read (cmpbr/inc)
        newDst = sd;
        if (DST_WRITTEN[ins.op]) storeBack = [sd, slot[ins.dst.id]];                            // dst written -> store back
      }
      out.push(clone(ins, { dst: newDst, a: newA, b: newB, args: newArgs }));
      if (storeBack) out.push(Inst("save", { a: storeBack[0], imm: storeBack[1] }));
    });
    return out;
  }

  // Allocate; on >16 live values, spill the overflow to scratch cards and re-allocate
  // (a working slow compile beats a hard RegisterPressureError on real code -- INV-13).
  function allocateOrSpill(insts) {
    try {
      return { insts: insts, mapping: allocate(insts, false).mapping };
    } catch (e) {
      if (!/register pressure/.test(String(e && e.message))) throw e;
      var meta = allocate(insts, true);
      var legal = legalizeSpills(insts, meta.spilled);
      return { insts: legal, mapping: allocate(legal, false).mapping };
    }
  }

  function phys(mapping, v) {
    var r = mapping[v.id];
    if (r === undefined) throw new Error("vreg " + v.name + " unallocated");
    return r;
  }

  // ── bytecode lowering (CONST/MOV-imm expand to 2 words) ───────────────────
  function emitWord(ins, mapping, labels, pc) {
    var op = ins.op;
    if (op === "mov") {
      var rd = phys(mapping, ins.dst);
      var rs1 = phys(mapping, ins.a);  // a is VReg here (imm handled by caller)
      return enc(OP.ADD, rd, rs1, 0, 0);
    }
    if (ARITH[op] !== undefined) {
      var d = phys(mapping, ins.dst), s1 = phys(mapping, ins.a);
      if (isImm(ins.b)) return enc(ARITH[op], d, s1, 0, ins.b.value & 0xFFFF);
      return enc(ARITH[op], d, s1, ADDR_REG, phys(mapping, ins.b));
    }
    if (op === "inc") return enc(OP.INC, phys(mapping, ins.dst));
    if (op === "cmpbr") {
      var ra = phys(mapping, ins.a), rb = phys(mapping, ins.b);
      var off = (labels[ins.label] - pc) & 0xFFFF;
      return enc(OP.BRANCH, ra, rb, COND[ins.cond], off);
    }
    if (op === "jmp") return enc(OP.JUMP, 0, 0, 0, labels[ins.label]);
    if (op === "call") return enc(OP.CALL, 0, 0, 0, labels[ins.label]);
    if (op === "ret") return enc(OP.RETURN);
    if (op === "load") return enc(OP.LOAD, phys(mapping, ins.dst), 0, 0, ins.imm);
    if (op === "save") return enc(OP.SAVE, 0, phys(mapping, ins.a), 0, ins.imm);
    if (op === "pipe") return enc(OP.PIPE, 0, phys(mapping, ins.a), 0, ins.imm);
    if (op === "net") {
      var k = ins.method;
      if (k === "status") return enc(OP.NOOP, 0, 0, 0, (H.NET_STATUS_BASE | (ins.imm & 0x0FFF)));
      if (k === "type") return enc(OP.NOOP, 0, 0, 0, (CT_BY_NAME[ins.text] || 0xA000));
      if (k === "header") return enc(OP.NOOP, 0, 0, 0, H.NET_HEADER_BASE);
      if (k === "body") return enc(OP.NOOP, 0, 0, 0, H.NET_BODY_MARKER);
      if (k === "close") return enc(OP.NOOP, 0, 0, 0, H.NET_CLOSE_MARKER);
      throw new Error("unknown net kind " + k);
    }
    if (op === "host") {
      var hook = HOOK_BY_NAME[ins.ns + "." + ins.method];
      if (hook === undefined) throw new Error("unknown host hook " + ins.ns + "." + ins.method);
      var imm = (hook <= 0xff) ? (H.HOST_HOOK_BASE | hook) : (H.EXT_HOST_HOOK_BASE | (hook & 0xfff));
      var hrd = isVReg(ins.dst) ? phys(mapping, ins.dst) : 0;
      var hrs1 = (ins.args[0] && isVReg(ins.args[0])) ? phys(mapping, ins.args[0]) : 0;
      var hrs2 = (ins.args[1] && isVReg(ins.args[1])) ? phys(mapping, ins.args[1]) : 0;
      return enc(OP.NOOP, hrd, hrs1, hrs2, imm);
    }
    throw new Error("cannot lower IL op " + op);
  }

  // INV-7 compile-time iso-lease: byte-identical to picoscript_il.verify_response_ownership.
  // Forward must-dataflow (AND-merge) over the IL CFG; a Resp.* op illegal on every
  // path to it is a compile error. State is a 4-bit mask SEALED=1/ENDED=2/BODY=4/
  // STREAM_CLOSED=8 (all monotonic); AND-merge is order-independent, the check loop
  // walks reachable points in ascending index order, so the first violation reported
  // matches the Python gate exactly.
  var RESP_GRAPH_METHODS = {
    Status: 1, Header: 1, Write: 1, Trailer: 1, Seal: 1, Respond: 1, End: 1, Abort: 1,
    Flush: 1, Continue: 1, EndStream: 1, Upgrade: 1, EarlyHints: 1
  };
  function verifyResponseOwnership(insts) {
    var n = insts.length, i, j, sc;
    var hasResp = false;
    for (i = 0; i < n; i++) { if (insts[i].op === "host" && insts[i].ns === "Resp") { hasResp = true; break; } }
    if (!hasResp) return;

    var labelAt = {};
    for (i = 0; i < n; i++) { if (insts[i].op === "label") labelAt[insts[i].label] = i; }

    function succs(i) {
      var ins = insts[i], op = ins.op, out = [], k, tg;
      if (op === "jmp") { return (ins.label in labelAt) ? [labelAt[ins.label]] : []; }
      if (op === "ret") { return []; }
      if (op === "cmpbr") {
        if (i + 1 < n) out.push(i + 1);
        if (ins.label in labelAt) out.push(labelAt[ins.label]);
        return out;
      }
      if (op === "jmptab") {
        tg = ins.targets || [];
        for (k = 0; k < tg.length; k++) { if (tg[k] in labelAt) out.push(labelAt[tg[k]]); }
        if (ins.label in labelAt) out.push(labelAt[ins.label]);
        return out;
      }
      if (op === "call") {
        if (i + 1 < n) out.push(i + 1);
        if (ins.label in labelAt) out.push(labelAt[ins.label]);
        return out;
      }
      return (i + 1 < n) ? [i + 1] : [];
    }

    var reachable = {}, stack = (n ? [0] : []), idx;
    while (stack.length) {
      idx = stack.pop();
      if (idx < 0 || idx >= n || reachable[idx]) continue;
      reachable[idx] = true;
      sc = succs(idx);
      for (j = 0; j < sc.length; j++) stack.push(sc[j]);
    }

    var preds = {};
    for (i in reachable) preds[i] = [];
    for (i in reachable) {
      sc = succs(+i);
      for (j = 0; j < sc.length; j++) { if (reachable[sc[j]]) preds[sc[j]].push(+i); }
    }

    var SEALED = 1, ENDED = 2, BODY = 4, STREAM_CLOSED = 8, TOP = 15, BOT = 0;
    function transfer(mask, ins) {
      if (ins.op === "host" && ins.ns === "Resp") {
        var m = ins.method;
        if (m === "Seal") mask |= SEALED;
        else if (m === "Respond") mask |= (SEALED | ENDED);
        else if (m === "End" || m === "Abort") mask |= ENDED;
        else if (m === "Write") mask |= BODY;
        else if (m === "EndStream") mask |= STREAM_CLOSED;
      }
      return mask;
    }

    var order = Object.keys(reachable).map(Number).sort(function (a, b) { return a - b; });
    var IN = {}, OUT = {}, p, inv, outv, key;
    for (i = 0; i < order.length; i++) { IN[order[i]] = TOP; OUT[order[i]] = TOP; }
    var changed = true;
    while (changed) {
      changed = false;
      for (i = 0; i < order.length; i++) {
        key = order[i];
        if (key === 0 || preds[key].length === 0) {
          inv = BOT;
        } else {
          inv = TOP;
          for (p = 0; p < preds[key].length; p++) inv &= OUT[preds[key][p]];
        }
        if (inv !== IN[key]) { IN[key] = inv; changed = true; }
        outv = transfer(IN[key], insts[key]);
        if (outv !== OUT[key]) { OUT[key] = outv; changed = true; }
      }
    }

    for (i = 0; i < order.length; i++) {
      var ins = insts[order[i]];
      if (ins.op !== "host" || ins.ns !== "Resp") continue;
      var mask = IN[order[i]], m = ins.method;
      if ((mask & ENDED) && RESP_GRAPH_METHODS[m])
        throw new Error("INV-7: Resp." + m + " after the response was finalized (use-after-end)");
      if ((m === "Status" || m === "Header") && (mask & SEALED))
        throw new Error("INV-7: Resp." + m + " after Seal (use-after-seal; preamble/headers are committed)");
      if (m === "Seal" && (mask & SEALED))
        throw new Error("INV-7: Resp.Seal after Seal (use-after-seal; double seal)");
      if (m === "Header" && (mask & BODY))
        throw new Error("INV-7: Resp.Header after a body write (header phase is over)");
      if (m === "Write" && (mask & STREAM_CLOSED))
        throw new Error("INV-7: Resp.Write after EndStream (stream phase closed)");
    }
  }

  function lowerToBytecode(insts, opt, outVars, checkOwnership, debug) {
    if (checkOwnership !== false) verifyResponseOwnership(insts);
    if (opt !== false) insts = optimize(insts);
    var alloc = allocateOrSpill(insts);   // auto-spills on >16 live values (INV-13)
    insts = alloc.insts;
    var mapping = alloc.mapping;
    if (outVars) {
      insts.forEach(function (ins) {
        operandVRegs(ins).forEach(function (v) {
          if (v.pinned && mapping[v.id] !== undefined) outVars[v.name] = mapping[v.id];
        });
      });
    }
    function width(ins) {
      if (ins.op === "label") return 0;
      if (ins.op === "const") return (ins.imm >= -32768 && ins.imm <= 32767) ? 2 : 8;
      if (ins.op === "mov" && isImm(ins.a)) return (ins.a.value >= -32768 && ins.a.value <= 32767) ? 2 : 8;
      if (ins.op === "jmptab") return ins.targets.length + 1;
      return 1;
    }
    // Load `value` into rd: 2-word SUB/ADD-imm for a 16-bit immediate (unchanged), else
    // an 8-word big-endian byte build (SUB; ADD b3; MUL 256; ...; ADD b0) using only
    // sign-safe positive immediates. Byte-identical to picoscript_il._emit_const.
    function emitConst(words, rd, value) {
      words.push(enc(OP.SUB, rd, rd, ADDR_REG, rd));   // rd = rd - rd = 0
      if (value >= -32768 && value <= 32767) {
        words.push(enc(OP.ADD, rd, rd, 0, value & 0xFFFF));
        return 2;
      }
      var u = value >>> 0, shs = [24, 16, 8, 0], i;
      for (i = 0; i < 4; i++) {
        words.push(enc(OP.ADD, rd, rd, 0, (u >>> shs[i]) & 0xFF));
        if (shs[i]) words.push(enc(OP.MUL, rd, rd, 0, 256));
      }
      return 8;
    }
    var labels = {}, pc = 0;
    insts.forEach(function (ins) {
      if (ins.op === "label") labels[ins.label] = pc; else pc += width(ins);
    });
    var words = []; pc = 0;
    insts.forEach(function (ins) {
      if (ins.op === "label") return;
      if (ins.op === "const" || (ins.op === "mov" && isImm(ins.a))) {
        var rd = phys(mapping, ins.dst);
        var value = (ins.op === "const") ? ins.imm : ins.a.value;
        pc += emitConst(words, rd, value); return;
      }
      if (ins.op === "jmptab") {
        var sel = phys(mapping, ins.a);
        words.push(enc(OP.JUMP, 0, sel, ADDR_REG_OFF, (pc + 1) & 0xFFFF));   // PC = sel + tablebase
        ins.targets.forEach(function (t) { words.push(enc(OP.JUMP, 0, 0, 0, labels[t])); });
        pc += ins.targets.length + 1; return;
      }
      words.push(emitWord(ins, mapping, labels, pc));
      pc += 1;
    });
    // INV-25: build pc -> [off, op, ns, method] from the SAME final insts/width the
    // words were emitted from (side-band; the word stream above is byte-identical).
    if (debug) {
      pc = 0;
      insts.forEach(function (ins) {
        var w = width(ins);
        if (w === 0) return;
        var rec = [ins.pos, ins.op, ins.ns != null ? ins.ns : null, ins.method != null ? ins.method : null];
        for (var p = pc; p < pc + w; p++) debug[p] = rec;
        pc += w;
      });
    }
    return words;
  }

  // ========================================================================
  // C-SYNTAX FRONTEND (port of picoscript_cfront.py)
  // ========================================================================
  var C_KW = { int:1, var:1, void:1, if:1, else:1, while:1, for:1, return:1, break:1, continue:1, switch:1, case:1, default:1, do:1, goto:1, dispatch:1, const:1, enum:1 };
  var C_TWO = { "==":1, "!=":1, "<=":1, ">=":1, "&&":1, "||":1, "++":1, "--":1, "+=":1, "-=":1, "*=":1, "/=":1, "%=":1 };
  var C_ONE = "+-*/%()<>=;,{}.!?:";
  var C_PREC = { "||":1, "&&":2, "==":3, "!=":3, "<":4, ">":4, "<=":4, ">=":4, "+":5, "-":5, "*":6, "/":6, "%":6 };
  var C_COMPOUND = { "+=":"+", "-=":"-", "*=":"*", "/=":"/", "%=":"%" };
  var CMP = { "<":"LT", ">":"GT", "<=":"LE", ">=":"GE", "==":"EQ", "!=":"NE" };
  var COP = { "+":"add", "-":"sub", "*":"mul", "/":"div" };

  function numval(s) { return /^0[xX]/.test(s) ? parseInt(s, 16) : parseInt(s, 10); }
  function isAlpha(c) { return /[A-Za-z_]/.test(c); }
  function isAlnum(c) { return /[A-Za-z0-9_]/.test(c); }
  function isDigit(c) { return c >= "0" && c <= "9"; }

  function ctokenize(src) {
    var toks = [], i = 0, n = src.length;
    function push(k, v, p) { toks.push({ kind: k, value: v, pos: p }); }   // INV-25: pos = token start offset
    while (i < n) {
      var c = src[i], start = i;
      if (c === " " || c === "\t" || c === "\r" || c === "\n") { i++; continue; }
      if (c === "/" && src[i + 1] === "/") { while (i < n && src[i] !== "\n") i++; continue; }
      if (c === "/" && src[i + 1] === "*") { i += 2; while (i + 1 < n && !(src[i] === "*" && src[i + 1] === "/")) i++; i += 2; continue; }
      if (c === '"') { var j = i + 1, b = ""; while (j < n && src[j] !== '"') { if (src[j] === "\\" && j + 1 < n) { b += src[j + 1]; j += 2; continue; } b += src[j]; j++; } push("str", b, start); i = j + 1; continue; }
      if (isDigit(c)) { var j2 = i; if (c === "0" && (src[j2 + 1] === "x" || src[j2 + 1] === "X")) { j2 += 2; while (j2 < n && /[0-9a-fA-F]/.test(src[j2])) j2++; } else { while (j2 < n && isDigit(src[j2])) j2++; } push("num", src.slice(i, j2), start); i = j2; continue; }
      if (isAlpha(c)) { var j3 = i; while (j3 < n && isAlnum(src[j3])) j3++; var w = src.slice(i, j3); var low = w.toLowerCase(); if (C_KW[low]) push("kw", low, start); else push("id", w, start); i = j3; continue; }
      var two = src.slice(i, i + 2);
      if (C_TWO[two]) { push("op", two, start); i += 2; continue; }
      if (C_ONE.indexOf(c) >= 0) { push("op", c, start); i++; continue; }
      throw new Error("C: unexpected char " + JSON.stringify(c));
    }
    push("eof", "", n);
    return toks;
  }

  function CParser(toks) { this.toks = toks; this.i = 0; }
  CParser.prototype = {
    peek: function () { return this.toks[this.i]; },
    next: function () { return this.toks[this.i++]; },
    accept: function (v) { var t = this.peek(); if (t.value === v && (t.kind === "op" || t.kind === "kw")) { this.i++; return true; } return false; },
    expect: function (v) { var t = this.peek(); if (t.value !== v) throw new Error("C: expected " + v + " got " + t.value); return this.next(); },
    parseProgram: function () { var s = []; while (this.peek().kind !== "eof") s.push(this.parseToplevel()); return s; },
    parseToplevel: function () {
      var t = this.peek();
      if (t.kind === "kw" && t.value === "void") {
        this.next(); var name = this.next().value; this.expect("(");
        var params = [];
        if (!this.accept(")")) {
          while (true) {
            var pt = this.peek();
            if (pt.kind === "kw" && pt.value === "int") this.next();
            params.push(this.next().value);
            if (!this.accept(",")) break;
          }
          this.expect(")");
        }
        return { t: "Func", name: name, body: this.parseBlock(), params: params.length ? params : null };
      }
      return this.parseStmt();
    },
    parseBlock: function () { this.expect("{"); var s = []; while (!this.accept("}")) { if (this.peek().kind === "eof") throw new Error("C: unterminated block"); s.push(this.parseStmt()); } return s; },
    parseStmt: function () {
      // INV-25: stamp every statement node with its first token's source offset.
      var start = this.peek().pos;
      var node = this._parseStmt();
      if (node != null) node.pos = start;
      return node;
    },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "kw") {
        if (t.value === "int" || t.value === "var") return this.parseDecl();
        if (t.value === "const") return this.parseConstDecl();
        if (t.value === "enum") return this.parseEnumDecl();
        if (t.value === "if") return this.parseIf();
        if (t.value === "while") return this.parseWhile();
        if (t.value === "for") return this.parseFor();
        if (t.value === "switch") return this.parseSwitch();
        if (t.value === "dispatch") return this.parseDispatch();
        if (t.value === "do") return this.parseDo();
        if (t.value === "goto") { this.next(); var gl = this.next().value; this.expect(";"); return { t: "Goto", label: gl }; }
        if (t.value === "return") { this.next(); if (this.accept(";")) return { t: "Return", value: null }; var v = this.parseExpr(); this.expect(";"); return { t: "Return", value: v }; }
        if (t.value === "break") { this.next(); this.expect(";"); return { t: "Break" }; }
        if (t.value === "continue") { this.next(); this.expect(";"); return { t: "Continue" }; }
      }
      if (t.kind === "id" && t.value === "Server" && this.toks[this.i + 1] && this.toks[this.i + 1].value === "." && this.toks[this.i + 2] && this.toks[this.i + 2].value === "Main" && this.toks[this.i + 3] && this.toks[this.i + 3].value === "{") {
        this.next(); this.next(); this.next(); return { t: "ServerMain", body: this.parseBlock() };
      }
      if (t.value === "{") { return { t: "If", cond: { t: "Num", value: 1 }, then: this.parseBlock(), els: null }; }
      if (t.kind === "id" && this.toks[this.i + 1].value === ":") { var lab = this.next().value; this.next(); return { t: "Label", name: lab }; }
      if (t.kind === "id" && this.toks[this.i + 1].kind === "id") { this.next(); return this.parseDeclAfterType(); }
      if (t.kind === "id" && this.toks[this.i + 1].value === "." && this.toks[this.i + 2].kind === "id" && ["=","++","--","+=","-=","*=","/=","%="].indexOf(this.toks[this.i + 3].value) >= 0) {
        var fo = this.next().value; this.expect("."); var ff = this.next().value; var fop = this.next().value, fv;
        if (fop === "=") fv = this.parseExpr();
        else if (fop === "++" || fop === "--") fv = { t: "Bin", op: fop === "++" ? "+" : "-", lhs: { t: "FieldRef", obj: fo, field: ff }, rhs: { t: "Num", value: 1 } };
        else fv = { t: "Bin", op: C_COMPOUND[fop], lhs: { t: "FieldRef", obj: fo, field: ff }, rhs: this.parseExpr() };
        this.expect(";"); return { t: "FieldAssign", obj: fo, field: ff, value: fv };
      }
      if (t.kind === "id" && this.toks[this.i + 1].value === "=") {
        var name = this.next().value; this.expect("="); var val = this.parseExpr(); this.expect(";"); return { t: "Assign", name: name, value: val };
      }
      if (t.kind === "id" && C_COMPOUND[this.toks[this.i + 1].value]) {
        var cn = this.next().value; var cop = C_COMPOUND[this.next().value]; var cv = this.parseExpr(); this.expect(";");
        return { t: "Assign", name: cn, value: { t: "Bin", op: cop, lhs: { t: "Var", name: cn }, rhs: cv } };
      }
      var e = this.parseExpr(); this.expect(";"); return { t: "ExprStmt", expr: e };
    },
    parseDecl: function () { this.next(); return this.parseDeclAfterType(); },
    parseConstDecl: function () {
      this.next(); // const
      if (this.peek().kind === "kw" && (this.peek().value === "int" || this.peek().value === "var")) this.next();
      var name = this.next().value;
      this.expect("=");
      var value = this.parseExpr();
      this.expect(";");
      return { t: "ConstDecl", name: name, value: value };
    },
    parseEnumDecl: function () {
      this.next(); // enum
      var enumName = this.next().value;
      this.expect("{");
      var members = [];
      while (!this.accept("}")) {
        if (this.peek().kind === "eof") throw new Error("C: unterminated enum declaration");
        var memberName = this.next().value;
        var memberValue = null;
        if (this.accept("=")) memberValue = this.parseExpr();
        members.push([memberName, memberValue]);
        this.accept(",");
      }
      this.expect(";");
      return { t: "EnumDecl", enum_name: enumName, members: members };
    },
    parseDeclAfterType: function () { var name = this.next().value; var init = null; if (this.accept("=")) init = this.parseExpr(); this.expect(";"); return { t: "Decl", name: name, init: init }; },
    parseDeclNoSemi: function () { this.next(); var name = this.next().value; var init = null; if (this.accept("=")) init = this.parseExpr(); this.expect(";"); return { t: "Decl", name: name, init: init }; },
    parseIf: function () {
      this.next(); this.expect("("); var cond = this.parseExpr(); this.expect(")"); var then = this.parseBlock(); var els = null;
      if (this.accept("else")) { els = (this.peek().value === "{") ? this.parseBlock() : [this.parseIf()]; }
      return { t: "If", cond: cond, then: then, els: els };
    },
    parseWhile: function () { this.next(); this.expect("("); var cond = this.parseExpr(); this.expect(")"); return { t: "While", cond: cond, body: this.parseBlock() }; },
    parseFor: function () {
      this.next(); this.expect("("); var init = null;
      if (!this.accept(";")) {
        if (this.peek().value === "int" || this.peek().value === "var") init = this.parseDeclNoSemi();
        else { var nm = this.next().value; this.expect("="); init = { t: "Assign", name: nm, value: this.parseExpr() }; this.expect(";"); }
      }
      var cond = null; if (!this.accept(";")) { cond = this.parseExpr(); this.expect(";"); }
      var step = null;
      if (this.peek().value !== ")") {
        if (this.peek().kind === "id" && this.toks[this.i + 1].value === "=") { var nm2 = this.next().value; this.expect("="); step = { t: "Assign", name: nm2, value: this.parseExpr() }; }
        else if (this.peek().kind === "id" && C_COMPOUND[this.toks[this.i + 1].value]) { var nm3 = this.next().value; var sop = C_COMPOUND[this.next().value]; step = { t: "Assign", name: nm3, value: { t: "Bin", op: sop, lhs: { t: "Var", name: nm3 }, rhs: this.parseExpr() } }; }
        else step = { t: "ExprStmt", expr: this.parseExpr() };
      }
      this.expect(")"); return { t: "For", init: init, cond: cond, step: step, body: this.parseBlock() };
    },
    parseSwitch: function () {
      this.next(); this.expect("("); var expr = this.parseExpr(); this.expect(")"); this.expect("{");
      var cases = [], def = null;
      while (!this.accept("}")) {
        var t = this.peek();
        if (t.kind === "kw" && t.value === "case") { this.next(); var val = this.parseExpr(); this.expect(":"); cases.push([val, this.parseCaseBody()]); }
        else if (t.kind === "kw" && t.value === "default") { this.next(); this.expect(":"); def = this.parseCaseBody(); }
        else throw new Error("C: expected case/default in switch");
      }
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseDispatch: function () {
      this.next(); this.expect("("); var expr = this.parseExpr(); this.expect(")"); this.expect("{");
      var cases = [], def = null;
      while (!this.accept("}")) {
        var t = this.peek();
        if (t.kind === "kw" && t.value === "case") { this.next(); var val = this.parseExpr(); this.expect(":"); cases.push([val, this.parseCaseBody()]); }
        else if (t.kind === "kw" && t.value === "default") { this.next(); this.expect(":"); def = this.parseCaseBody(); }
        else throw new Error("C: expected case/default in dispatch");
      }
      return { t: "Dispatch", expr: expr, cases: cases, def: def };
    },
    parseCaseBody: function () {
      var stmts = [];
      while (true) {
        var t = this.peek();
        if (t.value === "}") break;
        if (t.kind === "kw" && (t.value === "case" || t.value === "default")) break;
        if (t.kind === "kw" && t.value === "break") { this.next(); this.expect(";"); break; }
        stmts.push(this.parseStmt());
      }
      return stmts;
    },
    parseDo: function () {
      this.next(); var body = this.parseBlock();
      if (!(this.peek().kind === "kw" && this.peek().value === "while")) throw new Error("C: expected 'while' after do block");
      this.next(); this.expect("("); var cond = this.parseExpr(); this.expect(")"); this.expect(";");
      return { t: "DoWhile", cond: cond, until: false, body: body };
    },
    parseExpr: function (minp) { return this.parseTernary(); },
    parseTernary: function () {
      var cond = this.parseBinary(0);
      if (this.peek().kind === "op" && this.peek().value === "?") {
        this.next(); var then = this.parseExpr(); this.expect(":"); var els = this.parseTernary();
        return { t: "Ternary", cond: cond, then: then, els: els };
      }
      return cond;
    },
    parseBinary: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      while (true) { var t = this.peek(); if (t.kind !== "op" || C_PREC[t.value] === undefined || C_PREC[t.value] < minp) break; var op = this.next().value; var right = this.parseBinary(C_PREC[op] + 1); left = { t: "Bin", op: op, lhs: left, rhs: right }; }
      return left;
    },
    parseUnary: function () {
      var t = this.peek();
      if (t.kind === "op" && (t.value === "++" || t.value === "--")) { var po = this.next().value; return { t: "IncDec", op: po, target: this.parseUnary(), prefix: true }; }
      if ((t.value === "-" || t.value === "!") && t.kind === "op") { var op = this.next().value; return { t: "Unary", op: op, operand: this.parseUnary() }; }
      return this.parseAtom();
    },
    parseAtom: function () {
      var node = this.parsePrimary();
      while (this.peek().kind === "op" && (this.peek().value === "++" || this.peek().value === "--")) { var o = this.next().value; node = { t: "IncDec", op: o, target: node, prefix: false }; }
      return node;
    },
    parsePrimary: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.value === "(") { var e = this.parseExpr(); this.expect(")"); return e; }
      if (t.kind === "id") {
        if (this.peek().value === ".") { this.next(); var m = this.next().value; if (this.peek().value === "(") return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; return { t: "FieldRef", obj: t.value, field: m }; }
        if (this.peek().value === "(") return { t: "Call", ns: null, method: t.value, args: this.parseArgs() };
        return { t: "Var", name: t.value };
      }
      throw new Error("C: unexpected token " + t.value);
    },
    parseArgs: function () { this.expect("("); var a = []; if (!this.accept(")")) { a.push(this.parseExpr()); while (this.accept(",")) a.push(this.parseExpr()); this.expect(")"); } return a; }
  };

  // C-frontend aliases: libc spellings -> canonical (ns, method). Mirrors
  // picoscript_cfront.C_ALIASES so Python- and JS-compiled bytecode stay identical.
  var C_ALIASES = {
    strlen: ["String", "Length"], strcat: ["String", "Concat"], strstr: ["String", "IndexOf"],
    toupper: ["String", "ToUpper"], tolower: ["String", "ToLower"], substr: ["String", "Substring"],
    atoi: ["Number", "Parse"], itoa: ["Number", "ToString"], tohex: ["Number", "ToHex"],
    abs: ["Number", "Abs"], sqrt: ["Maths", "Sqrt"], pow: ["Maths", "Power"], sha256: ["Crypto", "Sha256"]
  };

  // Stage a string literal's UTF-8 bytes in a scratch arena region and return a
  // span over them (two alternating slots). Mirrors picoscript_*.emit_str_span.
  function emitStrSpan(self, text) {
    var data = Array.prototype.slice.call(new TextEncoder().encode(text));
    // String-literal constant pool (mirrors picoscript_cfront.emit_str_span): each
    // distinct literal interned to its own stable address growing down from 0x8000,
    // deduped by content, so any number can be live at once.
    if (!self._strpool) { self._strpool = {}; self._strpoolTop = 0x8000; }
    var key = data.join(",");
    var base = self._strpool[key];
    if (base === undefined) { self._strpoolTop -= data.length; base = self._strpoolTop; self._strpool[key] = base; }
    var b = self.b, areg = b.vreg(), vreg = b.vreg();
    for (var i = 0; i < data.length; i++) {
      b.const_(areg, base + i);
      b.const_(vreg, data[i]);
      b.host("Memory", "SetConst", [areg, vreg], null);
    }
    b.const_(areg, base);
    b.const_(vreg, data.length);
    var span = b.vreg();
    b.host("Span", "Make", [areg, vreg], span);
    return span;
  }

  function CLowerer() { this.b = new ILBuilder(); this.vars = {}; this.funcs = []; this.loop = []; this._strlitN = 0; this.userConstants = {}; }
  CLowerer.prototype = {
    varOf: function (name) { var k = name.toLowerCase(); if (!this.vars[k]) this.vars[k] = new VReg(name, true); return this.vars[k]; },
    resolveConstant: function (name) {
      var key = String(name).trim().toUpperCase();
      if (Object.prototype.hasOwnProperty.call(this.userConstants, key)) return this.userConstants[key] | 0;
      return namedConstant(name);
    },
    evalConstExpr: function (e) {
      if (e.t === "Num") return e.value | 0;
      if (e.t === "Var") {
        var cv = this.resolveConstant(e.name);
        if (cv === null) throw new Error("unknown constant " + e.name + " in constant expression");
        return cv | 0;
      }
      if (e.t === "FieldRef") {
        var fv = this.resolveConstant(e.obj + "." + e.field);
        if (fv === null) throw new Error("unknown constant " + e.obj + "." + e.field + " in constant expression");
        return fv | 0;
      }
      if (e.t === "Unary") {
        if (e.op === "-") return -this.evalConstExpr(e.operand);
        throw new Error("unsupported unary op " + e.op + " in constant expression");
      }
      if (e.t === "Bin") {
        var a = this.evalConstExpr(e.lhs), b = this.evalConstExpr(e.rhs);
        if (e.op === "+") return (a + b) | 0;
        if (e.op === "-") return (a - b) | 0;
        if (e.op === "*") return (a * b) | 0;
        if (e.op === "/") {
          if (b === 0) throw new Error("division by zero in constant expression");
          return (a / b) | 0;
        }
        if (e.op === "%") {
          if (b === 0) throw new Error("modulo by zero in constant expression");
          return (a - ((a / b) | 0) * b) | 0;
        }
      }
      throw new Error("unsupported constant expression " + e.t);
    },
    defineConstant: function (name, expr) {
      this.userConstants[String(name).trim().toUpperCase()] = this.evalConstExpr(expr) | 0;
    },
    defineEnum: function (enumName, members) {
      var ek = String(enumName).trim().toUpperCase();
      var cur = -1;
      for (var i = 0; i < members.length; i++) {
        var mname = members[i][0];
        var mexpr = members[i][1];
        cur = (mexpr == null) ? (cur + 1) : (this.evalConstExpr(mexpr) | 0);
        var mk = String(mname).trim().toUpperCase();
        this.userConstants[mk] = cur;
        this.userConstants[ek + "_" + mk] = cur;
        this.userConstants[ek + "." + mk] = cur;
      }
    },
    lowerProgram: function (prog) {
      var self = this, body = [];
      this._funcParams = {};
      prog.forEach(function (s) { if (s.t === "Func") { self.funcs.push(s); self._funcParams[s.name.toLowerCase()] = s.params || []; } else body.push(s); });
      body.forEach(function (s) { self.stmt(s); });
      this.b.ret();
      this.funcs.forEach(function (f) {
        self.b.label("fn_" + f.name.toLowerCase());
        (f.params || []).forEach(function (p, i) { var pv = self.varOf(p); var av = self.varOf("__arg" + i + "__"); self.b.mov(pv, av); });
        f.body.forEach(function (s) { self.stmt(s); });
        self.b.ret();
      });
      return this.b.insts;
    },
    stmt: function (s) {
      var self = this;
      if (typeof s.pos === "number" && s.pos >= 0) this.b.curPos = s.pos;   // INV-25
      if (s.t === "Decl") { var v = this.varOf(s.name); if (s.init != null) this.assignTo(v, s.init); else this.b.const_(v, 0); }
      else if (s.t === "ConstDecl") this.defineConstant(s.name, s.value);
      else if (s.t === "EnumDecl") this.defineEnum(s.enum_name, s.members || []);
      else if (s.t === "Assign") this.assignTo(this.varOf(s.name), s.value);
      else if (s.t === "FieldAssign") this.assignField(s.obj, s.field, s.value);
      else if (s.t === "If") this.lowerIf(s);
      else if (s.t === "While") this.lowerWhile(s);
      else if (s.t === "For") this.lowerFor(s);
      else if (s.t === "Switch") this.lowerSwitch(s);
      else if (s.t === "Dispatch") this.lowerDispatch(s);
      else if (s.t === "DoWhile") this.lowerDoWhile(s);
      else if (s.t === "Goto") this.b.jmp("lbl_" + s.label.toLowerCase());
      else if (s.t === "Label") this.b.label("lbl_" + s.name.toLowerCase());
      else if (s.t === "Return") { if (s.value != null) { var rv = this.eval(s.value); this.b.mov(this.varOf("__ret__"), rv); } this.b.ret(); }
      else if (s.t === "ExprStmt") { if (s.expr != null) this.eval(s.expr, false); }
      else if (s.t === "Break") { if (!this.loop.length) throw new Error("break outside loop"); this.b.jmp(this.loop[this.loop.length - 1][1]); }
      else if (s.t === "Continue") { if (!this.loop.length) throw new Error("continue outside loop"); this.b.jmp(this.loop[this.loop.length - 1][0]); }
      else if (s.t === "ServerMain") s.body.forEach(function (st) { self.stmt(st); });
      else throw new Error("C: cannot lower " + s.t);
    },
    assignTo: function (dst, e) {
      if (e.t === "Bin" && COP[e.op]) {
        var a = this.eval(e.lhs);
        if (e.rhs.t === "Num" && e.rhs.value >= -32768 && e.rhs.value <= 65535) { this.b.arith(COP[e.op], dst, a, new Imm(e.rhs.value)); return; }
        var bb = this.eval(e.rhs); this.b.arith(COP[e.op], dst, a, bb); return;
      }
      this.b.mov(dst, this.eval(e));
    },
    assignField: function (obj, field, expr) {
      var card = this.varOf(obj);
      this.b.host("Storage", "EditCard", [card], null);
      var name = emitStrSpan(this, field);
      if (expr.t === "Str") this.b.host("Storage", "SetFieldStr", [name, emitStrSpan(this, expr.value)], null);
      else this.b.host("Storage", "SetField", [name, this.eval(expr)], null);
    },
    lowerIf: function (s) {
      var elseL = this.b.newLabel("else"), endL = this.b.newLabel("endif");
      this.branchFalse(s.cond, elseL);
      var self = this; s.then.forEach(function (st) { self.stmt(st); });
      if (s.els) { this.b.jmp(endL); this.b.label(elseL); s.els.forEach(function (st) { self.stmt(st); }); this.b.label(endL); }
      else this.b.label(elseL);
    },
    lowerWhile: function (s) {
      var top = this.b.newLabel("while"), end = this.b.newLabel("endwhile");
      this.b.label(top); this.branchFalse(s.cond, end);
      this.loop.push([top, end]); var self = this; s.body.forEach(function (st) { self.stmt(st); }); this.loop.pop();
      this.b.jmp(top); this.b.label(end);
    },
    lowerFor: function (s) {
      if (s.init) this.stmt(s.init);
      var top = this.b.newLabel("for"), cont = this.b.newLabel("forcont"), end = this.b.newLabel("endfor");
      this.b.label(top); if (s.cond) this.branchFalse(s.cond, end);
      this.loop.push([cont, end]); var self = this; s.body.forEach(function (st) { self.stmt(st); }); this.loop.pop();
      this.b.label(cont); if (s.step) this.stmt(s.step); this.b.jmp(top); this.b.label(end);
    },
    lowerSwitch: function (s) {
      var val = this.eval(s.expr);
      var end = this.b.newLabel("endsw");
      var prevCont = this.loop.length ? this.loop[this.loop.length - 1][0] : end;
      this.loop.push([prevCont, end]);
      var self = this;
      s.cases.forEach(function (cb) {
        var nxt = self.b.newLabel("case");
        self.branchFalse({ t: "Bin", op: "==", lhs: { t: "Raw", v: val }, rhs: cb[0] }, nxt);
        cb[1].forEach(function (st) { self.stmt(st); });
        self.b.jmp(end); self.b.label(nxt);
      });
      if (s.def) s.def.forEach(function (st) { self.stmt(st); });
      this.loop.pop();
      this.b.label(end);
    },
    lowerDispatch: function (s) {
      var sel = this.eval(s.expr);
      var end = this.b.newLabel("enddisp"), defL = this.b.newLabel("dispdef");
      var prevCont = this.loop.length ? this.loop[this.loop.length - 1][0] : end;
      this.loop.push([prevCont, end]);
      var self = this, pairs = [];
      s.cases.forEach(function (cb) {
        if (cb[0].t !== "Num" || cb[0].value < 0) throw new Error("dispatch case must be a constant non-negative integer");
        pairs.push([cb[0].value, cb[1]]);
      });
      var n = 0; pairs.forEach(function (p) { if (p[0] + 1 > n) n = p[0] + 1; });
      var table = []; for (var i = 0; i < n; i++) table.push(defL);
      var bodies = [];
      pairs.forEach(function (p) { var lbl = self.b.newLabel("dcase"); table[p[0]] = lbl; bodies.push([lbl, p[1]]); });
      var nreg = this.b.vreg(); this.b.const_(nreg, n); this.b.cmpbr("GE", sel, nreg, defL);
      var zreg = this.b.vreg(); this.b.const_(zreg, 0); this.b.cmpbr("LT", sel, zreg, defL);
      this.b.jmptab(sel, table, defL);
      bodies.forEach(function (bd) { self.b.label(bd[0]); bd[1].forEach(function (st) { self.stmt(st); }); self.b.jmp(end); });
      this.b.label(defL);
      if (s.def) s.def.forEach(function (st) { self.stmt(st); });
      this.loop.pop();
      this.b.label(end);
    },
    lowerDoWhile: function (s) {
      var top = this.b.newLabel("do"), cont = this.b.newLabel("docont"), end = this.b.newLabel("enddo");
      this.b.label(top);
      this.loop.push([cont, end]); var self = this; s.body.forEach(function (st) { self.stmt(st); }); this.loop.pop();
      this.b.label(cont);
      if (s.until) { this.branchFalse(s.cond, top); }
      else { this.branchFalse(s.cond, end); this.b.jmp(top); }
      this.b.label(end);
    },
    branchFalse: function (cond, falseL) {
      if (cond.t === "Bin" && CMP[cond.op]) { var a = this.eval(cond.lhs), b = this.eval(cond.rhs); this.b.cmpbr(COND_NEGATE[CMP[cond.op]], a, b, falseL); return; }
      var v = this.eval(cond); this.b.cmpbr("Z", v, v, falseL);
    },
    eval: function (e, want) {
      if (want === undefined) want = true;
      if (e.t === "Num") { var v = this.b.vreg(); this.b.const_(v, e.value); return v; }
      if (e.t === "Var") {
        var cv = this.resolveConstant(e.name);
        if (cv !== null) { var vv = this.b.vreg(); this.b.const_(vv, cv); return vv; }
        return this.varOf(e.name);
      }
      if (e.t === "Raw") return e.v;
      if (e.t === "Bin") {
        if (CMP[e.op]) return this.evalBool(e);
        if (e.op === "&&" || e.op === "||") return this.evalLogical(e);
        if (e.op === "%") return this.evalMod(e.lhs, e.rhs);
        var a = this.eval(e.lhs), dst = this.b.vreg();
        if (e.rhs.t === "Num" && e.rhs.value >= -32768 && e.rhs.value <= 65535) this.b.arith(COP[e.op], dst, a, new Imm(e.rhs.value));
        else { var b = this.eval(e.rhs); this.b.arith(COP[e.op], dst, a, b); }
        return dst;
      }
      if (e.t === "IncDec") return this.evalIncDec(e);
      if (e.t === "Ternary") return this.evalTernary(e);
      if (e.t === "Unary") {
        if (e.op === "-") { var z = this.b.vreg(); this.b.const_(z, 0); var inner = this.eval(e.operand); var d = this.b.vreg(); this.b.arith("sub", d, z, inner); return d; }
        if (e.op === "!") { var iv = this.eval(e.operand); return this.evalBool({ t: "Bin", op: "==", lhs: { t: "Raw", v: iv }, rhs: { t: "Num", value: 0 } }); }
      }
      if (e.t === "Call") return this.lowerCall(e, want);
      if (e.t === "Str") return emitStrSpan(this, e.value);
      if (e.t === "FieldRef") {
        var fv = this.resolveConstant(e.obj + "." + e.field);
        if (fv !== null) { var fe = this.b.vreg(); this.b.const_(fe, fv); return fe; }
        var card = this.varOf(e.obj); this.b.host("Storage", "EditCard", [card], null); var name = emitStrSpan(this, e.field); var fd = this.b.vreg(); this.b.host("Storage", "GetField", [name], fd); return fd;
      }
      throw new Error("C: cannot evaluate " + e.t);
    },
    evalBool: function (e) {
      var a = this.eval(e.lhs), b = this.eval(e.rhs), dst = this.b.vreg();
      var tl = this.b.newLabel("bt"), el = this.b.newLabel("be");
      this.b.cmpbr(CMP[e.op], a, b, tl); this.b.const_(dst, 0); this.b.jmp(el);
      this.b.label(tl); this.b.const_(dst, 1); this.b.label(el); return dst;
    },
    evalMod: function (lhs, rhs) {
      var a = this.eval(lhs), b = this.eval(rhs);
      var q = this.b.vreg(); this.b.arith("div", q, a, b);
      var m = this.b.vreg(); this.b.arith("mul", m, q, b);
      var dst = this.b.vreg(); this.b.arith("sub", dst, a, m);
      return dst;
    },
    evalLogical: function (e) {
      var dst = this.b.vreg(); var a = this.eval(e.lhs); var endL = this.b.newLabel("lend");
      if (e.op === "&&") {
        var falseL = this.b.newLabel("land0");
        this.b.cmpbr("Z", a, a, falseL);
        var b = this.eval(e.rhs); this.b.cmpbr("Z", b, b, falseL);
        this.b.const_(dst, 1); this.b.jmp(endL);
        this.b.label(falseL); this.b.const_(dst, 0);
      } else {
        var trueL = this.b.newLabel("lor1");
        this.b.cmpbr("NZ", a, a, trueL);
        var b2 = this.eval(e.rhs); this.b.cmpbr("NZ", b2, b2, trueL);
        this.b.const_(dst, 0); this.b.jmp(endL);
        this.b.label(trueL); this.b.const_(dst, 1);
      }
      this.b.label(endL); return dst;
    },
    evalIncDec: function (e) {
      if (e.target.t !== "Var") throw new Error("++/-- requires a variable");
      var v = this.varOf(e.target.name);
      if (e.prefix) { if (e.op === "++") this.b.inc(v); else this.b.arith("sub", v, v, new Imm(1)); return v; }
      var old = this.b.vreg(); this.b.mov(old, v);
      if (e.op === "++") this.b.inc(v); else this.b.arith("sub", v, v, new Imm(1));
      return old;
    },
    evalTernary: function (e) {
      var dst = this.b.vreg(); var elseL = this.b.newLabel("telse"), endL = this.b.newLabel("tend");
      this.branchFalse(e.cond, elseL);
      var tv = this.eval(e.then); this.b.mov(dst, tv); this.b.jmp(endL);
      this.b.label(elseL); var ev = this.eval(e.els); this.b.mov(dst, ev);
      this.b.label(endL); return dst;
    },
    lowerCall: function (c, want) {
      var ns = c.ns, m = c.method;
      if (ns == null) {
        if (m.toLowerCase() === "print") { if (c.args[0].t === "Str") { this.b.host("Io", "Write", [emitStrSpan(this, c.args[0].value)], null); return null; } var v = this.eval(c.args[0]); this.b.save(v, 0xFFFE); this.b.pipe(v, 0xFFFE); return null; }
        var ak = m.toLowerCase();
        if (C_ALIASES[ak] && !this.funcs.some(function (f) { return f.name.toLowerCase() === ak; })) {
          return this.lowerCall({ t: "Call", ns: C_ALIASES[ak][0], method: C_ALIASES[ak][1], args: c.args }, want);
        }
        // pass args via arg-passing regs
        var params = (this._funcParams || {})[ak] || [];
        for (var ai = 0; ai < c.args.length; ai++) { var aav = this.varOf("__arg" + ai + "__"); this.assignTo(aav, c.args[ai]); }
        this.b.call("fn_" + m.toLowerCase());
        if (want) return this.varOf("__ret__");
        return null;
      }
      if (ns.toUpperCase() === "NET") {
        var M = m.toUpperCase();
        if (M === "STATUS") this.b.net("status", this.evalConstExpr(c.args[0]));
        else if (M === "TYPE") this.b.net("type", strlit(c.args[0]));
        else if (M === "BODY") this.b.net("body");
        else if (M === "CLOSE") this.b.net("close");
        else if (M === "HEADER") this.b.net("header");
        else throw new Error("unknown Net." + m);
        return null;
      }
      if (ns.toUpperCase() === "STORAGE" && ["LOAD", "SAVE", "PIPE"].indexOf(m.toUpperCase()) >= 0) {
        var addr = encodeCardAddr(intlit(c.args[0]), intlit(c.args[1]), intlit(c.args[2]));
        var reg = this.eval(c.args[3]); var MM = m.toUpperCase();
        if (MM === "LOAD") this.b.load(reg, addr); else if (MM === "SAVE") this.b.save(reg, addr); else this.b.pipe(reg, addr);
        return reg;
      }
      if (ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "GETCARD") { var gp = this.eval(c.args[0]), gc = this.eval(c.args[1]); this.b.host("Storage", "UsePack", [gp], null); var gd = want ? this.b.vreg() : null; this.b.host("Storage", "EditCard", [gc], gd); return gd; }
      if (ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "SAVECARD") { var sc = this.eval(c.args[0]); this.b.host("Storage", "EditCard", [sc], null); var sd = want ? this.b.vreg() : null; if (sd) this.b.const_(sd, 1); return sd; }
      if (ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "QUERYCARDS") { var qp = this.eval(c.args[0]), qq = this.eval(c.args[1]); this.b.host("Storage", "UsePack", [qp], null); var qd = want ? this.b.vreg() : null; this.b.host("Storage", "QueryCard", [qq], qd); return qd; }
      var cn = canonHost(ns, m); var self = this;
      var argregs = c.args.slice(0, 2).map(function (a) { return self.eval(a); });
      var dst = want ? this.b.vreg() : null;
      this.b.host(cn[0], cn[1], argregs, dst); return dst;
    }
  };
  function intlit(node) { if (node.t === "Num") return node.value; throw new Error("expected integer literal"); }
  function strlit(node) { if (node.t === "Str") return node.value; throw new Error("expected string literal"); }
  function compileC(src) { return new CLowerer().lowerProgram(new CParser(ctokenize(src)).parseProgram()); }

  // ========================================================================
  // BASIC-LIKE FRONTEND (port of picoscript_basic.py)
  // ========================================================================
  var B_KW = {}; ["LET","DIM","IF","THEN","ELSEIF","ELSE","ENDIF","WHILE","ENDWHILE","FOR","TO","STEP","NEXT","FOREACH","IN","ENDFOREACH","SWITCH","CASE","DEFAULT","ENDSWITCH","DISPATCH","ENDDISPATCH","GOTO","GOSUB","SUB","ENDSUB","RETURN","PRINT","AND","OR","NOT","DO","LOOP","UNTIL","BREAK","SKIP","INC","DEC","IIF","EQ","NE","LT","GT","LE","GE","MOD","STORE","GPIO","LOAD","SERVER","ENDSERVER","ASSERT","PACK","CARD","FIFO","DEVICE","STREAM","UI","EVENT","CONST","ENUM","ENDENUM"].forEach(function (k) { B_KW[k] = 1; });
  var B_CMPW = { EQ:"EQ", NE:"NE", LT:"LT", GT:"GT", LE:"LE", GE:"GE" };
  var B_CMPS = { "==":"EQ", "!=":"NE", "<>":"NE", "=":"EQ", "<":"LT", ">":"GT", "<=":"LE", ">=":"GE" };
  var B_COMPARATORS = {}; for (var _k in B_CMPW) B_COMPARATORS[_k] = B_CMPW[_k]; for (var _k2 in B_CMPS) B_COMPARATORS[_k2] = B_CMPS[_k2];
  var B_ASSIGN = { "+=":"+", "-=":"-", "*=":"*", "/=":"/" };
  var B_TWO = { "==":1, "!=":1, "<=":1, ">=":1, "<>":1, "+=":1, "-=":1, "*=":1, "/=":1 };
  var B_ONE = "+-*/()<>=,.:";
  var B_PREC = { OR:1, AND:2, "+":5, "-":5, "*":6, "/":6, MOD:6 };
  for (var _c in B_COMPARATORS) B_PREC[_c] = 3;
  var B_ARITH = { "+":"add", "-":"sub", "*":"mul", "/":"div" };
  var B_PRINT_CARD = 0xFFFE;

  function btokenize(src) {
    var toks = [], i = 0, n = src.length;
    function push(k, v, p) { toks.push({ kind: k, value: v, pos: p }); }
    while (i < n) {
      var c = src[i], start = i;
      if (c === "\n") { push("nl", "\\n", start); i++; continue; }
      if (c === " " || c === "\t" || c === "\r") { i++; continue; }
      if (c === "'" || (c === "/" && src[i + 1] === "/")) { while (i < n && src[i] !== "\n") i++; continue; }
      if (c === '"') { var j = i + 1, b = ""; while (j < n && src[j] !== '"') { b += src[j]; j++; } push("str", b, start); i = j + 1; continue; }
      if (isDigit(c)) { var j2 = i; if (c === "0" && (src[j2 + 1] === "x" || src[j2 + 1] === "X")) { j2 += 2; while (j2 < n && /[0-9a-fA-F]/.test(src[j2])) j2++; } else { while (j2 < n && isDigit(src[j2])) j2++; } push("num", src.slice(i, j2), start); i = j2; continue; }
      if (isAlpha(c)) { var j3 = i; while (j3 < n && isAlnum(src[j3])) j3++; if (src[j3] === "$") j3++; var w = src.slice(i, j3); var up = w.toUpperCase(); if (B_KW[up]) push("kw", up, start); else push("id", w, start); i = j3; continue; }
      var two = src.slice(i, i + 2);
      if (B_TWO[two]) { push("op", two, start); i += 2; continue; }
      if (B_ONE.indexOf(c) >= 0) { push("op", c, start); i++; continue; }
      throw new Error("BASIC: unexpected char " + JSON.stringify(c));
    }
    push("nl", "\\n", n); push("eof", "", n);
    return toks;
  }

  function BParser(toks) { this.toks = toks; this.i = 0; }
  BParser.prototype = {
    peek: function () { return this.toks[this.i]; },
    peek2: function () { return this.toks[this.i + 1]; },
    next: function () { return this.toks[this.i++]; },
    skipNl: function () { while (this.peek().kind === "nl") this.i++; },
    atKw: function () { var t = this.peek(); if (t.kind !== "kw") return false; for (var k = 0; k < arguments.length; k++) if (t.value === arguments[k]) return true; return false; },
    eatKw: function (name) { var t = this.next(); if (!(t.kind === "kw" && t.value === name)) throw new Error("BASIC: expected " + name + " got " + t.value); },
    eatOp: function (v) { var t = this.next(); if (!(t.kind === "op" && t.value === v)) throw new Error("BASIC: expected " + v + " got " + t.value); },
    endLine: function () { var t = this.peek(); if (t.kind === "nl" || t.kind === "eof") { this.skipNl(); return; } throw new Error("BASIC: expected EOL got " + t.value); },
    parseProgram: function () { var s = []; this.skipNl(); while (this.peek().kind !== "eof") { s.push(this.parseStmt()); this.skipNl(); } return s; },
    parseBlock: function () {
      var terms = Array.prototype.slice.call(arguments); var s = []; this.skipNl();
      while (!this.atKwArr(terms)) { if (this.peek().kind === "eof") throw new Error("BASIC: unexpected EOF expecting " + terms); s.push(this.parseStmt()); this.skipNl(); }
      return s;
    },
    atKwArr: function (arr) { var t = this.peek(); return t.kind === "kw" && arr.indexOf(t.value) >= 0; },
    parseStmt: function () {
      var start = this.peek().pos;
      var node = this._parseStmt();
      if (node != null) node.pos = start;
      return node;
    },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "id" && this.peek2().kind === "op" && this.peek2().value === ":") { var name = this.next().value; this.next(); this.endLine(); return { t: "Label", name: name }; }
      if (t.kind === "kw") {
        var kw = t.value;
        if (kw === "LET") return this.parseLet(true);
        if (kw === "DIM") return this.parseDim();
        if (kw === "CONST") return this.parseConst();
        if (kw === "ENUM") return this.parseEnum();
        if (kw === "INC") { this.next(); var ni = this.next().value; this.endLine(); return { t: "IncDec", name: ni, delta: 1 }; }
        if (kw === "DEC") { this.next(); var nd = this.next().value; this.endLine(); return { t: "IncDec", name: nd, delta: -1 }; }
        if (kw === "IF") return this.parseIf();
        if (kw === "WHILE") return this.parseWhile();
        if (kw === "DO") return this.parseDo();
        if (kw === "FOR") return this.parseFor();
        if (kw === "FOREACH") return this.parseForeach();
        if (kw === "SWITCH") return this.parseSwitch();
      if (kw === "DISPATCH") return this.parseDispatch();
        if (kw === "GOTO") { this.next(); var nm = this.next().value; this.endLine(); return { t: "Goto", label: nm }; }
        if (kw === "GOSUB") {
          this.next(); var nm2 = this.next().value;
          var gArgs = null;
          if (this.peek().kind === "op" && this.peek().value === "(") { gArgs = this.parseArgs(); }
          this.endLine(); return { t: "Gosub", name: nm2, args: gArgs };
        }
        if (kw === "SUB") return this.parseSub();
        if (kw === "SERVER") return this.parseServer();
        if (kw === "RETURN") {
          this.next();
          if (this.peek().kind === "nl" || this.peek().kind === "eof") { this.endLine(); return { t: "Return" }; }
          var rv = this.parseExpr(); this.endLine(); return { t: "Return", value: rv };
        }
        if (kw === "BREAK") { this.next(); this.endLine(); return { t: "Break" }; }
        if (kw === "SKIP") { this.next(); this.endLine(); return { t: "Skip" }; }
        if (kw === "PRINT") { this.next(); var v = this.parseExpr(); this.endLine(); return { t: "Print", value: v }; }
        if (kw === "STORE") { this.next(); var sc = this.parseStoreBody(false); this.endLine(); return { t: "CallStmt", call: sc }; }
        if (kw === "LOAD") { this.next(); var lc = this.parseLoadBody(false); this.endLine(); return { t: "CallStmt", call: lc }; }
        if (kw === "GPIO") { this.next(); var gc = this.parseGpioBody(false); this.endLine(); return { t: "CallStmt", call: gc }; }
        if (kw === "ASSERT") { this.next(); var ac = this.parseExpr(); this.endLine(); return { t: "CallStmt", call: { t: "Call", ns: "Assert", method: "True", args: [ac] } }; }
        if (kw === "PACK" || kw === "CARD" || kw === "FIFO" || kw === "DEVICE" || kw === "STREAM") { this.next(); var dc = this.parseCapsBody(kw, false); this.endLine(); return { t: "CallStmt", call: dc }; }
        if (kw === "UI" || kw === "EVENT") { this.next(); var uc = this.parseUiEvtBody(kw, false); this.endLine(); return { t: "CallStmt", call: uc }; }
        throw new Error("BASIC: unexpected keyword " + kw);
      }
      if (t.kind === "id") {
        var nx = this.peek2();
        if (t.value.toUpperCase() === "POKE" && !(nx.kind === "op" && nx.value === "(")) {
          this.next();                                  // classic no-parens form: POKE addr, value
          var pa = this.parseExpr(); this.eatOp(",");
          var pb = this.parseExpr(); this.endLine();
          return { t: "CallStmt", call: { t: "Call", ns: null, method: "poke", args: [pa, pb] } };
        }
        if (nx.kind === "op" && nx.value === "=") return this.parseLet(false);
        if (nx.kind === "op" && B_ASSIGN[nx.value]) {
          var an = this.next().value; var aop = B_ASSIGN[this.next().value];
          var arhs = this.parseExpr(); this.endLine();
          return { t: "Let", name: an, value: { t: "Bin", op: aop, lhs: { t: "Var", name: an }, rhs: arhs } };
        }
        if (nx.kind === "op" && nx.value === ".") { var call = this.parseCallFromId(); this.endLine(); return { t: "CallStmt", call: call }; }
        if (nx.kind === "op" && nx.value === "(") { var cn = this.next().value; var cargs = this.parseArgs(); this.endLine(); return { t: "CallStmt", call: { t: "Call", ns: null, method: cn, args: cargs } }; }
      }
      throw new Error("BASIC: cannot parse statement at " + t.value);
    },
    parseDim: function () {
      this.eatKw("DIM"); var name = this.next().value; var init = null;
      if (this.peek().kind === "op" && this.peek().value === "=") { this.next(); init = this.parseExpr(); }
      else if (this.peekWord() === "NEW") { this.eatWord(); this.expectWord("CARD"); init = { t: "Call", ns: "Storage", method: "AddCard", args: [] }; }
      this.endLine(); return { t: "Dim", name: name, init: init };
    },
    parseConst: function () {
      this.eatKw("CONST");
      var name = this.next().value;
      this.eatOp("=");
      var value = this.parseExpr();
      this.endLine();
      return { t: "ConstDecl", name: name, value: value };
    },
    parseEnum: function () {
      this.eatKw("ENUM");
      var enumName = this.next().value;
      this.endLine();
      var members = [];
      this.skipNl();
      while (!this.atKw("ENDENUM")) {
        if (this.peek().kind === "eof") throw new Error("BASIC: unexpected EOF expecting ENDENUM");
        var tok = this.next();
        if (tok.kind !== "id" && tok.kind !== "kw") throw new Error("BASIC: expected enum member name got " + tok.value);
        var memberName = tok.value, memberValue = null;
        if (this.peek().kind === "op" && this.peek().value === "=") { this.next(); memberValue = this.parseExpr(); }
        this.endLine();
        members.push([memberName, memberValue]);
        this.skipNl();
      }
      this.eatKw("ENDENUM");
      this.endLine();
      return { t: "EnumDecl", enum_name: enumName, members: members };
    },
    parseLet: function (eat) { if (eat) this.eatKw("LET"); var name = this.next().value; if (this.peekWord() === "NEW") { this.eatWord(); this.expectWord("CARD"); this.endLine(); return { t: "Let", name: name, value: { t: "Call", ns: "Storage", method: "AddCard", args: [] } }; } this.eatOp("="); var v = this.parseExpr(); this.endLine(); return { t: "Let", name: name, value: v }; },
    peekWord: function () { var t = this.peek(); if (t.kind === "id") return t.value.toUpperCase(); if (t.kind === "kw") return t.value; return null; },
    eatWord: function () { var t = this.next(); if (t.kind !== "id" && t.kind !== "kw") throw new Error("BASIC: expected a word got " + t.value); return t.value.toUpperCase(); },
    expectWord: function (e) { var w = this.eatWord(); if (w !== e) throw new Error("BASIC: expected " + e + " got " + w); },
    parseStoreBody: function (wantValue) {
      var verb = this.eatWord();
      if (wantValue && verb !== "NEW") throw new Error("BASIC: STORE " + verb + " is a statement, not a value");
      if (verb === "USE") { this.expectWord("PACK"); return { t: "Call", ns: "Storage", method: "UsePack", args: [this.parseAtom()] }; }
      if (verb === "SET") {
        if (this.peekWord() === "PACK") { this.eatWord(); return { t: "Call", ns: "Storage", method: "UsePack", args: [this.parseAtom()] }; }
        var field = this.parseAtom(); this.eatOp("="); var rhs = this.parseExpr();
        var method = (rhs.t === "Str") ? "SetFieldStr" : "SetField";
        return { t: "Call", ns: "Storage", method: method, args: [field, rhs] };
      }
      if (verb === "DELETE") { this.expectWord("CARD"); return { t: "Call", ns: "Storage", method: "DeleteCard", args: [this.parseAtom()] }; }
      if (verb === "NEW") { this.expectWord("CARD"); return { t: "Call", ns: "Storage", method: "AddCard", args: [] }; }
      throw new Error("BASIC: unknown STORE verb " + verb);
    },
    parseLoadBody: function (wantValue) {
      var w = this.peekWord();
      if (w === "CARD") { this.eatWord(); return { t: "Call", ns: "Storage", method: "EditCard", args: [this.parseAtom()] }; }
      if (w === "QUERY") { this.eatWord(); return { t: "Call", ns: "Storage", method: "QueryCard", args: [this.parseAtom()] }; }
      if (w === "RESULT") { this.eatWord(); return { t: "Call", ns: "Storage", method: "QueryResult", args: [this.parseAtom()] }; }
      var field = this.parseAtom();
      if (this.peekWord() === "AS") { this.eatWord(); this.expectWord("TEXT"); return { t: "Call", ns: "Storage", method: "GetFieldStr", args: [field] }; }
      return { t: "Call", ns: "Storage", method: "GetField", args: [field] };
    },
    parseGpioBody: function (wantValue) {
      var verb = this.eatWord();
      if (verb === "COUNT") return { t: "Call", ns: "Gpio", method: "Count", args: [] };
      if (verb === "READ") return { t: "Call", ns: "Gpio", method: "Read", args: [this.parseAtom()] };
      if (verb === "WRITE") {
        if (wantValue) throw new Error("BASIC: GPIO WRITE is a statement, not a value");
        var pin = this.parseAtom(); this.eatOp("="); var val = this.parseExpr();
        return { t: "Call", ns: "Gpio", method: "Write", args: [pin, val] };
      }
      if (verb === "DIR" || verb === "PULL") {
        var pin2 = this.parseAtom();
        if (!wantValue && this.peek().kind === "op" && this.peek().value === "=") {
          this.eatOp("=");
          var rhs2 = (verb === "DIR") ? this.parseDirValue() : this.parsePullValue();
          return { t: "Call", ns: "Gpio", method: (verb === "DIR" ? "SetDir" : "SetPull"), args: [pin2, rhs2] };
        }
        return { t: "Call", ns: "Gpio", method: (verb === "DIR" ? "GetDir" : "GetPull"), args: [pin2] };
      }
      throw new Error("BASIC: unknown GPIO verb " + verb);
    },
    needStmt: function (wantValue, what) { if (wantValue) throw new Error("BASIC: " + what + " is a statement, not a value"); },
    parseCapsBody: function (head, wantValue) {
      var verb;
      if (head === "PACK") { this.expectWord("USE"); return { t: "Call", ns: "Pack", method: "Use", args: [this.parseAtom()] }; }
      if (head === "CARD") {
        verb = this.eatWord();
        if (verb === "READ") return { t: "Call", ns: "Card", method: "Read", args: [this.parseAtom()] };
        if (verb === "ADDRESS") { var pk = this.parseAtom(), cd = this.parseAtom(); return { t: "Call", ns: "Card", method: "Address", args: [pk, cd] }; }
        if (verb === "WRITE") { this.needStmt(wantValue, "CARD WRITE"); var cw = this.parseAtom(); this.eatOp("="); var cv = this.parseExpr(); return { t: "Call", ns: "Card", method: "Write", args: [cw, cv] }; }
        throw new Error("BASIC: unknown CARD verb " + verb);
      }
      if (head === "FIFO") {
        verb = this.eatWord();
        if (verb === "OPEN") return { t: "Call", ns: "Fifo", method: "Open", args: [this.parseAtom()] };
        if (verb === "RECV") return { t: "Call", ns: "Fifo", method: "Recv", args: [this.parseAtom()] };
        if (verb === "POLL") return { t: "Call", ns: "Fifo", method: "Poll", args: [this.parseAtom()] };
        if (verb === "SEND") { this.needStmt(wantValue, "FIFO SEND"); var fh = this.parseAtom(); this.eatOp("="); var fv = this.parseExpr(); return { t: "Call", ns: "Fifo", method: "Send", args: [fh, fv] }; }
        throw new Error("BASIC: unknown FIFO verb " + verb);
      }
      if (head === "DEVICE") {
        verb = this.eatWord();
        if (verb === "OPEN") { var ident = this.parseAtom(); var cfg = { t: "Num", value: 0 }; if (this.peekWord() === "CONFIG") { this.eatWord(); cfg = this.parseAtom(); } return { t: "Call", ns: "Device", method: "Open", args: [ident, cfg] }; }
        if (verb === "CAPS") return { t: "Call", ns: "Device", method: "Caps", args: [this.parseAtom()] };
        if (verb === "STATUS") return { t: "Call", ns: "Device", method: "Status", args: [this.parseAtom()] };
        if (verb === "CLOSE") { this.needStmt(wantValue, "DEVICE CLOSE"); return { t: "Call", ns: "Device", method: "Close", args: [this.parseAtom()] }; }
        throw new Error("BASIC: unknown DEVICE verb " + verb);
      }
      if (head === "STREAM") {
        verb = this.eatWord();
        if (verb === "OPEN") { var dev = this.parseAtom(), scfg = this.parseAtom(); return { t: "Call", ns: "Stream", method: "Open", args: [dev, scfg] }; }
        if (verb === "NEXT") return { t: "Call", ns: "Stream", method: "Next", args: [this.parseAtom()] };
        if (verb === "SPAN") return { t: "Call", ns: "Stream", method: "Span", args: [this.parseAtom()] };
        if (verb === "SETSLICE") { this.needStmt(wantValue, "STREAM SETSLICE"); var so = this.parseAtom(); if (this.peek().kind === "op" && this.peek().value === ",") this.next(); var sl = this.parseAtom(); return { t: "Call", ns: "Stream", method: "SetSlice", args: [so, sl] }; }
        if (verb === "SLICE") return { t: "Call", ns: "Stream", method: "Slice", args: [this.parseAtom()] };
        if (verb === "SUBMIT") { this.needStmt(wantValue, "STREAM SUBMIT"); var sst = this.parseAtom(); this.eatOp("="); var sle = this.parseExpr(); return { t: "Call", ns: "Stream", method: "Submit", args: [sst, sle] }; }
        if (verb === "RELEASE") { this.needStmt(wantValue, "STREAM RELEASE"); return { t: "Call", ns: "Stream", method: "Release", args: [this.parseAtom()] }; }
        if (verb === "CLOSE") { this.needStmt(wantValue, "STREAM CLOSE"); return { t: "Call", ns: "Stream", method: "Close", args: [this.parseAtom()] }; }
        throw new Error("BASIC: unknown STREAM verb " + verb);
      }
      throw new Error("BASIC: unknown DSL head " + head);
    },
    parseUiEvtBody: function (head, wantValue) {
      var verb;
      if (head === "EVENT") {
        verb = this.eatWord();
        if (verb === "POST") { var ty = this.parseAtom(), tg = this.parseAtom(); return { t: "Call", ns: "Event", method: "Post", args: [ty, tg] }; }
        if (verb === "NEXT") return { t: "Call", ns: "Event", method: "Next", args: [] };
        if (verb === "TYPE") return { t: "Call", ns: "Event", method: "Type", args: [this.parseAtom()] };
        if (verb === "TARGET") return { t: "Call", ns: "Event", method: "Target", args: [this.parseAtom()] };
        if (verb === "DATA") return { t: "Call", ns: "Event", method: "Data", args: [this.parseAtom()] };
        if (verb === "DATALEN") return { t: "Call", ns: "Event", method: "DataLen", args: [this.parseAtom()] };
        if (verb === "DATASLICE") return { t: "Call", ns: "Event", method: "DataSlice", args: [this.parseAtom()] };
        if (verb === "COUNT") return { t: "Call", ns: "Event", method: "Count", args: [] };
        if (verb === "SETSLICE") { this.needStmt(wantValue, "EVENT SETSLICE"); var eo = this.parseAtom(); if (this.peek().kind === "op" && this.peek().value === ",") this.next(); var el = this.parseAtom(); return { t: "Call", ns: "Event", method: "SetSlice", args: [eo, el] }; }
        if (verb === "SETDATA") { this.needStmt(wantValue, "EVENT SETDATA"); var ev = this.parseAtom(); this.eatOp("="); var sp = this.parseExpr(); return { t: "Call", ns: "Event", method: "SetData", args: [ev, sp] }; }
        throw new Error("BASIC: unknown EVENT verb " + verb);
      }
      if (head === "UI") {
        verb = this.eatWord();
        if (verb === "WINDOW") return { t: "Call", ns: "Ui", method: "Window", args: [this.parseAtom()] };
        if (verb === "PANEL") return { t: "Call", ns: "Ui", method: "Panel", args: [this.parseAtom()] };
        if (verb === "LABEL" || verb === "BUTTON" || verb === "TEXTBOX" || verb === "CHECKBOX") {
          var parent = this.parseAtom(), text = this.parseAtom();
          var m = { LABEL: "Label", BUTTON: "Button", TEXTBOX: "TextBox", CHECKBOX: "Checkbox" }[verb];
          return { t: "Call", ns: "Ui", method: m, args: [parent, text] };
        }
        if (verb === "POS" || verb === "SIZE") {
          this.needStmt(wantValue, "UI " + verb);
          var node = this.parseAtom(); this.eatOp("="); var x = this.parseExpr(), val;
          if (this.peek().kind === "op" && this.peek().value === ",") {
            this.next(); var y = this.parseExpr();
            val = { t: "Bin", op: "+", lhs: { t: "Bin", op: "*", lhs: x, rhs: { t: "Num", value: 65536 } }, rhs: y };
          } else { val = x; }
          return { t: "Call", ns: "Ui", method: (verb === "POS" ? "Pos" : "Size"), args: [node, val] };
        }
        if (verb === "SETTEXT" || verb === "SETID" || verb === "SETVALUE") {
          this.needStmt(wantValue, "UI " + verb);
          var n2 = this.parseAtom(); this.eatOp("="); var v2 = this.parseExpr();
          var m2 = { SETTEXT: "SetText", SETID: "SetId", SETVALUE: "SetValue" }[verb];
          return { t: "Call", ns: "Ui", method: m2, args: [n2, v2] };
        }
        if (verb === "SERIALIZE") return { t: "Call", ns: "Ui", method: "Serialize", args: [this.parseAtom()] };
        throw new Error("BASIC: unknown UI verb " + verb);
      }
      throw new Error("BASIC: unknown DSL head " + head);
    },
    parseDirValue: function () {
      var t = this.peek();
      if (t.kind === "kw" && t.value === "IN") { this.next(); return { t: "Num", value: 0 }; }
      if (t.kind === "id" && (t.value.toUpperCase() === "OUT" || t.value.toUpperCase() === "OUTPUT")) { this.next(); return { t: "Num", value: 1 }; }
      if (t.kind === "id" && t.value.toUpperCase() === "INPUT") { this.next(); return { t: "Num", value: 0 }; }
      return this.parseExpr();
    },
    parsePullValue: function () {
      var t = this.peek();
      if (t.kind === "id") { var w = t.value.toUpperCase(); if (w === "NONE") { this.next(); return { t: "Num", value: 0 }; } if (w === "UP") { this.next(); return { t: "Num", value: 1 }; } if (w === "DOWN") { this.next(); return { t: "Num", value: 2 }; } }
      return this.parseExpr();
    },
    parseIf: function () {
      this.eatKw("IF"); var cond = this.parseCondition(); this.eatKw("THEN"); this.endLine();
      var body = this.parseBlock("ELSEIF", "ELSE", "ENDIF"); var arms = [[cond, body]]; var els = null;
      while (this.atKw("ELSEIF")) { this.eatKw("ELSEIF"); var c2 = this.parseCondition(); this.eatKw("THEN"); this.endLine(); arms.push([c2, this.parseBlock("ELSEIF", "ELSE", "ENDIF")]); }
      if (this.atKw("ELSE")) { this.eatKw("ELSE"); this.endLine(); els = this.parseBlock("ENDIF"); }
      this.eatKw("ENDIF"); this.endLine(); return { t: "If", arms: arms, els: els };
    },
    parseWhile: function () { this.eatKw("WHILE"); var cond = this.parseCondition(); this.endLine(); var body = this.parseBlock("ENDWHILE"); this.eatKw("ENDWHILE"); this.endLine(); return { t: "While", cond: cond, body: body }; },
    parseDo: function () {
      this.eatKw("DO");
      var topCond = null, topUntil = false;
      if (this.atKw("WHILE")) { this.eatKw("WHILE"); topCond = this.parseCondition(); }
      else if (this.atKw("UNTIL")) { this.eatKw("UNTIL"); topCond = this.parseCondition(); topUntil = true; }
      this.endLine();
      var body = this.parseBlock("LOOP");
      this.eatKw("LOOP");
      var botCond = null, botUntil = false;
      if (this.atKw("WHILE")) { this.eatKw("WHILE"); botCond = this.parseCondition(); }
      else if (this.atKw("UNTIL")) { this.eatKw("UNTIL"); botCond = this.parseCondition(); botUntil = true; }
      this.endLine();
      if ((topCond === null) === (botCond === null)) throw new Error("DO/LOOP needs a WHILE or UNTIL condition at exactly one of DO or LOOP");
      return { t: "DoLoop", topCond: topCond, topUntil: topUntil, botCond: botCond, botUntil: botUntil, body: body };
    },
    parseFor: function () {
      this.eatKw("FOR"); var v = this.next().value; this.eatOp("="); var start = this.parseExpr(); this.eatKw("TO"); var end = this.parseExpr();
      var step = null; if (this.atKw("STEP")) { this.eatKw("STEP"); step = this.parseExpr(); }
      this.endLine(); var body = this.parseBlock("NEXT"); this.eatKw("NEXT"); this.endLine(); return { t: "ForTo", v: v, start: start, end: end, step: step, body: body };
    },
    parseForeach: function () { this.eatKw("FOREACH"); var v = this.next().value; this.eatKw("IN"); var count = this.parseExpr(); this.endLine(); var body = this.parseBlock("ENDFOREACH"); this.eatKw("ENDFOREACH"); this.endLine(); return { t: "ForEach", v: v, count: count, body: body }; },
    parseSwitch: function () {
      this.eatKw("SWITCH"); var expr = this.parseExpr(); this.endLine(); this.skipNl(); var cases = [], def = null;
      while (!this.atKw("ENDSWITCH")) {
        if (this.atKw("CASE")) { this.eatKw("CASE"); var val = this.parseExpr(); this.endLine(); cases.push([val, this.parseBlock("CASE", "DEFAULT", "ENDSWITCH")]); }
        else if (this.atKw("DEFAULT")) { this.eatKw("DEFAULT"); this.endLine(); def = this.parseBlock("ENDSWITCH"); }
        else throw new Error("BASIC: expected CASE/DEFAULT/ENDSWITCH");
      }
      this.eatKw("ENDSWITCH"); this.endLine(); return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseDispatch: function () {
      this.eatKw("DISPATCH"); var expr = this.parseExpr(); this.endLine(); this.skipNl(); var cases = [], def = null;
      while (!this.atKw("ENDDISPATCH")) {
        if (this.atKw("CASE")) { this.eatKw("CASE"); var val = this.parseExpr(); this.endLine(); cases.push([val, this.parseBlock("CASE", "DEFAULT", "ENDDISPATCH")]); }
        else if (this.atKw("DEFAULT")) { this.eatKw("DEFAULT"); this.endLine(); def = this.parseBlock("ENDDISPATCH"); }
        else throw new Error("BASIC: expected CASE/DEFAULT/ENDDISPATCH");
      }
      this.eatKw("ENDDISPATCH"); this.endLine(); return { t: "Dispatch", expr: expr, cases: cases, def: def };
    },
    parseSub: function () {
      this.eatKw("SUB"); var name = this.next().value;
      var params = null;
      if (this.peek().kind === "op" && this.peek().value === "(") {
        this.next(); params = [];
        if (!(this.peek().kind === "op" && this.peek().value === ")")) {
          params.push(this.next().value);
          while (this.peek().kind === "op" && this.peek().value === ",") { this.next(); params.push(this.next().value); }
        }
        this.eatOp(")");
      }
      this.endLine(); var body = this.parseBlock("ENDSUB"); this.eatKw("ENDSUB"); this.endLine();
      return { t: "Sub", name: name, body: body, params: params };
    },
    parseServer: function () { this.eatKw("SERVER"); this.endLine(); var body = this.parseBlock("ENDSERVER"); this.eatKw("ENDSERVER"); this.endLine(); return { t: "ServerMain", body: body }; },
    parseCallFromId: function () { var ns = this.next().value; this.eatOp("."); var m = this.next().value; return { t: "Call", ns: ns, method: m, args: this.parseArgs() }; },
    parseArgs: function () { this.eatOp("("); var a = []; if (!(this.peek().kind === "op" && this.peek().value === ")")) { a.push(this.parseExpr()); while (this.peek().kind === "op" && this.peek().value === ",") { this.next(); a.push(this.parseExpr()); } } this.eatOp(")"); return a; },
    parseCondition: function () { return this.parseExpr(); },
    parseExpr: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      while (true) {
        var t = this.peek(); var ov = null;
        if (t.kind === "op" && B_PREC[t.value] !== undefined) ov = t.value;
        else if (t.kind === "kw" && B_PREC[t.value] !== undefined) ov = t.value;
        if (ov === null || B_PREC[ov] < minp) break;
        this.next();
        var right = this.parseExpr(B_PREC[ov] + 1);
        if (B_COMPARATORS[ov]) left = { t: "Cmp", cond: B_COMPARATORS[ov], lhs: left, rhs: right };
        else left = { t: "Bin", op: ov, lhs: left, rhs: right };
      }
      return left;
    },
    parseUnary: function () { var t = this.peek(); if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; } if (t.kind === "kw" && t.value === "NOT") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; } return this.parseAtom(); },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "kw" && t.value === "STORE") return this.parseStoreBody(true);
      if (t.kind === "kw" && t.value === "LOAD") return this.parseLoadBody(true);
      if (t.kind === "kw" && t.value === "GPIO") return this.parseGpioBody(true);
      if (t.kind === "kw" && (t.value === "PACK" || t.value === "CARD" || t.value === "FIFO" || t.value === "DEVICE" || t.value === "STREAM")) return this.parseCapsBody(t.value, true);
      if (t.kind === "kw" && (t.value === "UI" || t.value === "EVENT")) return this.parseUiEvtBody(t.value, true);
      if (t.kind === "kw" && t.value === "IIF") {
        this.eatOp("("); var c = this.parseExpr(); this.eatOp(",");
        var th = this.parseExpr(); this.eatOp(","); var el = this.parseExpr(); this.eatOp(")");
        return { t: "Ternary", cond: c, then: th, els: el };
      }
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.eatOp(")"); return e; }
      if (t.kind === "id") { if (this.peek().kind === "op" && this.peek().value === ".") { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; } if (this.peek().kind === "op" && this.peek().value === "(") { return { t: "Call", ns: null, method: t.value, args: this.parseArgs() }; } return { t: "Var", name: t.value }; }
      throw new Error("BASIC: unexpected token " + t.value);
    }
  };

  // Idiomatic aliases for the BASIC + (shared-lowerer) Python/English frontends ->
  // canonical (ns, method). Mirrors picoscript_basic.BP_ALIASES/BP_RADIX so Python-
  // and JS-compiled bytecode stay identical. Keys lowercase; a user SUB of the same
  // name takes precedence. Radix formatters follow each language's convention.
  var BP_ALIASES = {
    poke: ["Memory", "Set"], peek: ["Memory", "Get"],
    len: ["String", "Length"], "mid$": ["String", "Substring"],
    "ucase$": ["String", "ToUpper"], "lcase$": ["String", "ToLower"],
    instr: ["String", "IndexOf"], val: ["Number", "Parse"],
    "str$": ["Number", "ToString"], abs: ["Number", "Abs"],
    sqr: ["Maths", "Sqrt"], "oct$": ["Number", "ToOctal"], "bin$": ["Number", "ToBinary"],
    span: ["Span", "Make"], sha256: ["Crypto", "Sha256"],
    min: ["Number", "Min"], max: ["Number", "Max"],
    str: ["Number", "ToString"], int: ["Number", "Parse"],
    pow: ["Maths", "Power"], upper: ["String", "ToUpper"],
    lower: ["String", "ToLower"], find: ["String", "IndexOf"],
    substr: ["String", "Substring"]
  };
  var BP_RADIX = {
    hex: ["ToHex", "0x", false], oct: ["ToOctal", "0o", false],
    bin: ["ToBinary", "0b", false], "hex$": ["ToHex", null, true]
  };

  function BLowerer() { this.b = new ILBuilder(); this.vars = {}; this.subs = []; this.scopes = []; this._strlitN = 0; this.userConstants = {}; }
  BLowerer.prototype = {
    varOf: function (name) { var k = name.toUpperCase(); if (!this.vars[k]) this.vars[k] = new VReg(name, true); return this.vars[k]; },
    resolveConstant: function (name) {
      var key = String(name).trim().toUpperCase();
      if (Object.prototype.hasOwnProperty.call(this.userConstants, key)) return this.userConstants[key] | 0;
      return namedConstant(name);
    },
    evalConstExpr: function (e) {
      if (e.t === "Num") return e.value | 0;
      if (e.t === "Var") {
        var cv = this.resolveConstant(e.name);
        if (cv === null) throw new Error("unknown constant " + e.name + " in constant expression");
        return cv | 0;
      }
      if (e.t === "Bin") {
        var a = this.evalConstExpr(e.lhs), b = this.evalConstExpr(e.rhs);
        if (e.op === "+") return (a + b) | 0;
        if (e.op === "-") return (a - b) | 0;
        if (e.op === "*") return (a * b) | 0;
        if (e.op === "/") {
          if (b === 0) throw new Error("division by zero in constant expression");
          return (a / b) | 0;
        }
        if (e.op === "MOD") {
          if (b === 0) throw new Error("modulo by zero in constant expression");
          return (a - ((a / b) | 0) * b) | 0;
        }
      }
      throw new Error("unsupported constant expression " + e.t);
    },
    defineConstant: function (name, expr) {
      this.userConstants[String(name).trim().toUpperCase()] = this.evalConstExpr(expr) | 0;
    },
    defineEnum: function (enumName, members) {
      var ek = String(enumName).trim().toUpperCase();
      var cur = -1;
      for (var i = 0; i < members.length; i++) {
        var mname = members[i][0];
        var mexpr = members[i][1];
        cur = (mexpr == null) ? (cur + 1) : (this.evalConstExpr(mexpr) | 0);
        var mk = String(mname).trim().toUpperCase();
        this.userConstants[mk] = cur;
        this.userConstants[ek + "_" + mk] = cur;
        this.userConstants[ek + "." + mk] = cur;
      }
    },
    lowerProgram: function (prog) {
      var self = this, body = [];
      this._subParams = {};
      prog.forEach(function (s) { if (s.t === "Sub") { self.subs.push(s); self._subParams[s.name.toLowerCase()] = s.params || []; } else body.push(s); });
      body.forEach(function (s) { self.stmt(s); });
      this.b.ret();
      this.subs.forEach(function (sub) {
        self.b.label("sub_" + sub.name.toUpperCase());
        (sub.params || []).forEach(function (p, i) { var pv = self.varOf(p); var av = self.varOf("__arg" + i + "__"); self.b.mov(pv, av); });
        sub.body.forEach(function (s) { self.stmt(s); });
        self.b.ret();
      });
      return this.b.insts;
    },
    stmt: function (s) {
      var self = this;
      if (typeof s.pos === "number" && s.pos >= 0) this.b.curPos = s.pos;   // INV-25
      if (s.t === "Let" || s.t === "Assign") this.assignTo(this.varOf(s.name), s.value);
      else if (s.t === "Dim") { var dv = this.varOf(s.name); if (s.init === null) this.b.const_(dv, 0); else this.assignTo(dv, s.init); }
      else if (s.t === "ConstDecl") this.defineConstant(s.name, s.value);
      else if (s.t === "EnumDecl") this.defineEnum(s.enum_name, s.members || []);
      else if (s.t === "IncDec") { var iv = this.varOf(s.name); if (s.delta === 1) this.b.inc(iv); else this.b.arith("sub", iv, iv, new Imm(1)); }
      else if (s.t === "Label") this.b.label("lbl_" + s.name.toUpperCase());
      else if (s.t === "Goto") this.b.jmp("lbl_" + s.label.toUpperCase());
      else if (s.t === "Gosub") {
        if (s.args) { for (var gi = 0; gi < s.args.length; gi++) { this.assignTo(this.varOf("__arg" + gi + "__"), s.args[gi]); } }
        this.b.call("sub_" + s.name.toUpperCase());
      }
      else if (s.t === "Return") { if (s.value != null) { var rv = this.eval(s.value); this.b.mov(this.varOf("__ret__"), rv); } this.b.ret(); }
      else if (s.t === "Break") this.lowerBreak();
      else if (s.t === "Skip") this.lowerSkip();
      else if (s.t === "If") this.lowerIf(s);
      else if (s.t === "While") this.lowerWhile(s);
      else if (s.t === "DoLoop") this.lowerDo(s);
      else if (s.t === "ForTo") this.lowerFor(s);
      else if (s.t === "ForEach") this.lowerForeach(s);
      else if (s.t === "Switch") this.lowerSwitch(s);
      else if (s.t === "Dispatch") this.lowerDispatch(s);
      else if (s.t === "Print") { if (s.value.t === "Str") { this.b.host("Io", "Write", [emitStrSpan(this, s.value.value)], null); } else { var v = this.eval(s.value); this.b.save(v, B_PRINT_CARD); this.b.pipe(v, B_PRINT_CARD); } }
      else if (s.t === "CallStmt") this.lowerCall(s.call, false);
      else if (s.t === "ServerMain") s.body.forEach(function (st) { self.stmt(st); });
      else throw new Error("BASIC: cannot lower " + s.t);
    },
    assignTo: function (dst, e) {
      if (e.t === "Bin" && B_ARITH[e.op]) {
        var a = this.eval(e.lhs);
        if (e.rhs.t === "Num" && e.rhs.value >= -32768 && e.rhs.value <= 65535) { this.b.arith(B_ARITH[e.op], dst, a, new Imm(e.rhs.value)); return; }
        var bb = this.eval(e.rhs); this.b.arith(B_ARITH[e.op], dst, a, bb); return;
      }
      this.b.mov(dst, this.eval(e));
    },
    branchFalse: function (cond, falseL) {
      if (cond.t === "Cmp") { var a = this.eval(cond.lhs), b = this.eval(cond.rhs); this.b.cmpbr(COND_NEGATE[cond.cond], a, b, falseL); return; }
      var v = this.eval(cond); this.b.cmpbr("Z", v, v, falseL);
    },
    branchTrue: function (cond, trueL) {
      if (cond.t === "Cmp") { var a = this.eval(cond.lhs), b = this.eval(cond.rhs); this.b.cmpbr(cond.cond, a, b, trueL); return; }
      var v = this.eval(cond); this.b.cmpbr("NZ", v, v, trueL);
    },
    lowerBreak: function () {
      if (!this.scopes.length) throw new Error("BREAK outside a loop or SWITCH");
      this.b.jmp(this.scopes[this.scopes.length - 1][1]);
    },
    lowerSkip: function () {
      for (var i = this.scopes.length - 1; i >= 0; i--) {
        if (this.scopes[i][0] !== null) { this.b.jmp(this.scopes[i][0]); return; }
      }
      throw new Error("SKIP outside a loop");
    },
    lowerIf: function (s) {
      var end = this.b.newLabel("endif"), self = this;
      s.arms.forEach(function (arm) { var nxt = self.b.newLabel("arm"); self.branchFalse(arm[0], nxt); arm[1].forEach(function (st) { self.stmt(st); }); self.b.jmp(end); self.b.label(nxt); });
      if (s.els) s.els.forEach(function (st) { self.stmt(st); });
      this.b.label(end);
    },
    lowerWhile: function (s) { var top = this.b.newLabel("while"), end = this.b.newLabel("endwhile"), self = this; this.b.label(top); this.branchFalse(s.cond, end); this.scopes.push([top, end]); s.body.forEach(function (st) { self.stmt(st); }); this.scopes.pop(); this.b.jmp(top); this.b.label(end); },
    lowerDo: function (s) {
      var top = this.b.newLabel("do"), cont = this.b.newLabel("docont"), end = this.b.newLabel("enddo"), self = this;
      this.b.label(top);
      if (s.topCond !== null) {
        if (s.topUntil) this.branchTrue(s.topCond, end); else this.branchFalse(s.topCond, end);
      }
      this.scopes.push([cont, end]);
      s.body.forEach(function (st) { self.stmt(st); });
      this.scopes.pop();
      this.b.label(cont);
      if (s.botCond !== null) {
        if (s.botUntil) this.branchFalse(s.botCond, top); else this.branchTrue(s.botCond, top);
      } else {
        this.b.jmp(top);
      }
      this.b.label(end);
    },
    lowerFor: function (s) {
      var v = this.varOf(s.v); this.assignTo(v, s.start);
      var endv = this.b.vreg("__for_end__"); this.b.mov(endv, this.eval(s.end));
      var top = this.b.newLabel("for"), cont = this.b.newLabel("forcont"), end = this.b.newLabel("endfor"), self = this;
      this.b.label(top); this.b.cmpbr("GT", v, endv, end);
      this.scopes.push([cont, end]);
      s.body.forEach(function (st) { self.stmt(st); });
      this.scopes.pop();
      this.b.label(cont);
      if (s.step != null && s.step.t === "Num") this.b.arith("add", v, v, new Imm(s.step.value));
      else if (s.step != null) this.b.arith("add", v, v, this.eval(s.step));
      else this.b.inc(v);
      this.b.jmp(top); this.b.label(end);
    },
    lowerForeach: function (s) {
      var v = this.varOf(s.v); var cnt = this.b.vreg("__fe_count__"); this.b.mov(cnt, this.eval(s.count)); this.b.const_(v, 0);
      var top = this.b.newLabel("foreach"), cont = this.b.newLabel("fecont"), end = this.b.newLabel("endforeach"), self = this;
      this.b.label(top); this.b.cmpbr("GE", v, cnt, end);
      this.scopes.push([cont, end]);
      s.body.forEach(function (st) { self.stmt(st); });
      this.scopes.pop();
      this.b.label(cont);
      this.b.inc(v); this.b.jmp(top); this.b.label(end);
    },
    lowerSwitch: function (s) {
      var sel = this.eval(s.expr); var end = this.b.newLabel("endswitch"), self = this;
      var caseLabels = s.cases.map(function () { return self.b.newLabel("case"); });
      var defL = this.b.newLabel("default");
      s.cases.forEach(function (cse, idx) { var cv = self.eval(cse[0]); self.b.cmpbr("EQ", sel, cv, caseLabels[idx]); });
      this.b.jmp(defL);
      this.scopes.push([null, end]);
      s.cases.forEach(function (cse, idx) { self.b.label(caseLabels[idx]); cse[1].forEach(function (st) { self.stmt(st); }); self.b.jmp(end); });
      this.b.label(defL); if (s.def) s.def.forEach(function (st) { self.stmt(st); });
      this.scopes.pop();
      this.b.label(end);
    },
    lowerDispatch: function (s) {
      var sel = this.eval(s.expr); var end = this.b.newLabel("enddisp"), defL = this.b.newLabel("dispdef"), self = this;
      var pairs = [];
      s.cases.forEach(function (cb) {
        if (cb[0].t !== "Num" || cb[0].value < 0) throw new Error("DISPATCH case must be a constant non-negative integer");
        pairs.push([cb[0].value, cb[1]]);
      });
      var n = 0; pairs.forEach(function (p) { if (p[0] + 1 > n) n = p[0] + 1; });
      var table = []; for (var i = 0; i < n; i++) table.push(defL);
      var bodies = [];
      pairs.forEach(function (p) { var lbl = self.b.newLabel("dcase"); table[p[0]] = lbl; bodies.push([lbl, p[1]]); });
      var nreg = this.b.vreg(); this.b.const_(nreg, n); this.b.cmpbr("GE", sel, nreg, defL);
      var zreg = this.b.vreg(); this.b.const_(zreg, 0); this.b.cmpbr("LT", sel, zreg, defL);
      this.b.jmptab(sel, table, defL);
      this.scopes.push([null, end]);
      bodies.forEach(function (bd) { self.b.label(bd[0]); bd[1].forEach(function (st) { self.stmt(st); }); self.b.jmp(end); });
      this.b.label(defL); if (s.def) s.def.forEach(function (st) { self.stmt(st); });
      this.scopes.pop();
      this.b.label(end);
    },
    eval: function (e) {
      if (e.t === "Num") { var v = this.b.vreg(); this.b.const_(v, e.value); return v; }
      if (e.t === "Var") {
        var bv = this.resolveConstant(e.name);
        if (bv !== null) { var bc = this.b.vreg(); this.b.const_(bc, bv); return bc; }
        return this.varOf(e.name);
      }
      if (e.t === "Bin") {
        if (e.op === "AND" || e.op === "OR") return this.evalLogical(e);
        if (e.op === "MOD") return this.evalMod(e.lhs, e.rhs);
        var a = this.eval(e.lhs), dst = this.b.vreg();
        if (e.rhs.t === "Num" && e.rhs.value >= -32768 && e.rhs.value <= 65535) this.b.arith(B_ARITH[e.op], dst, a, new Imm(e.rhs.value));
        else { var b = this.eval(e.rhs); this.b.arith(B_ARITH[e.op], dst, a, b); }
        return dst;
      }
      if (e.t === "Cmp") return this.evalBool(e);
      if (e.t === "Ternary") return this.evalTernary(e);
      if (e.t === "Call") { var r = this.lowerCall(e, true); if (r == null) throw new Error(e.ns + "." + e.method + " has no value"); return r; }
      if (e.t === "Str") return emitStrSpan(this, e.value);
      throw new Error("BASIC: cannot evaluate " + e.t);
    },
    evalBool: function (e) {
      var a = this.eval(e.lhs), b = this.eval(e.rhs), dst = this.b.vreg();
      var tl = this.b.newLabel("bt"), el = this.b.newLabel("be");
      this.b.cmpbr(e.cond, a, b, tl); this.b.const_(dst, 0); this.b.jmp(el);
      this.b.label(tl); this.b.const_(dst, 1); this.b.label(el); return dst;
    },
    evalMod: function (lhs, rhs) {
      var a = this.eval(lhs), b = this.eval(rhs);
      var q = this.b.vreg(); this.b.arith("div", q, a, b);
      var m = this.b.vreg(); this.b.arith("mul", m, q, b);
      var dst = this.b.vreg(); this.b.arith("sub", dst, a, m);
      return dst;
    },
    evalLogical: function (e) {
      var dst = this.b.vreg(); var a = this.eval(e.lhs); var endL = this.b.newLabel("lend");
      if (e.op === "AND") {
        var falseL = this.b.newLabel("land0");
        this.b.cmpbr("Z", a, a, falseL);
        var b = this.eval(e.rhs); this.b.cmpbr("Z", b, b, falseL);
        this.b.const_(dst, 1); this.b.jmp(endL);
        this.b.label(falseL); this.b.const_(dst, 0);
      } else {
        var trueL = this.b.newLabel("lor1");
        this.b.cmpbr("NZ", a, a, trueL);
        var b2 = this.eval(e.rhs); this.b.cmpbr("NZ", b2, b2, trueL);
        this.b.const_(dst, 0); this.b.jmp(endL);
        this.b.label(trueL); this.b.const_(dst, 1);
      }
      this.b.label(endL); return dst;
    },
    evalTernary: function (e) {
      var dst = this.b.vreg(); var elseL = this.b.newLabel("telse"), endL = this.b.newLabel("tend");
      this.branchFalse(e.cond, elseL);
      var tv = this.eval(e.then); this.b.mov(dst, tv); this.b.jmp(endL);
      this.b.label(elseL); var ev = this.eval(e.els); this.b.mov(dst, ev);
      this.b.label(endL); return dst;
    },
    lowerCall: function (c, want) {
      var ns = c.ns, m = c.method;
      if (ns == null) {
        var key = m.toLowerCase();
        var isSub = this.subs.some(function (s) { return s.name.toLowerCase() === key; });
        if (isSub) {
          for (var si = 0; si < c.args.length; si++) { this.assignTo(this.varOf("__arg" + si + "__"), c.args[si]); }
          this.b.call("sub_" + m.toUpperCase());
          if (want) return this.varOf("__ret__");
          return null;
        }
        if (BP_RADIX[key] && !isSub) {
          var r = BP_RADIX[key], cm = r[0], prefix = r[1], upper = r[2];
          var val = this.eval(c.args[0]);
          var d = this.b.vreg(); this.b.host("Number", cm, [val], d);
          if (upper) { var outU = this.b.vreg(); this.b.host("String", "ToUpper", [d], outU); return outU; }
          if (prefix) { var pre = emitStrSpan(this, prefix); var outP = this.b.vreg(); this.b.host("String", "Concat", [pre, d], outP); return outP; }
          return d;
        }
        if (BP_ALIASES[key] && !isSub) {
          var al = BP_ALIASES[key];
          return this.lowerCall({ t: "Call", ns: al[0], method: al[1], args: c.args }, want);
        }
      }
      if (ns != null && ns.toUpperCase() === "NET") {
        var M = m.toUpperCase();
        if (M === "STATUS") this.b.net("status", this.evalConstExpr(c.args[0]));
        else if (M === "TYPE") this.b.net("type", strlit(c.args[0]));
        else if (M === "BODY") this.b.net("body");
        else if (M === "CLOSE") this.b.net("close");
        else if (M === "HEADER") this.b.net("header");
        else throw new Error("unknown Net." + m);
        return null;
      }
      if (ns != null && ns.toUpperCase() === "STORAGE" && ["LOAD", "SAVE", "PIPE"].indexOf(m.toUpperCase()) >= 0) {
        var addr = encodeCardAddr(intlit(c.args[0]), intlit(c.args[1]), intlit(c.args[2]));
        var reg = this.eval(c.args[3]); var MM = m.toUpperCase();
        if (MM === "LOAD") this.b.load(reg, addr); else if (MM === "SAVE") this.b.save(reg, addr); else this.b.pipe(reg, addr);
        return reg;
      }
      if (ns != null && ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "GETCARD") {
        var gp = this.eval(c.args[0]), gc = this.eval(c.args[1]);
        this.b.host("Storage", "UsePack", [gp], null);
        var gd = want ? this.b.vreg() : null;
        this.b.host("Storage", "EditCard", [gc], gd);
        return gd;
      }
      if (ns != null && ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "SAVECARD") {
        var sc = this.eval(c.args[0]);
        this.b.host("Storage", "EditCard", [sc], null);
        var sd = want ? this.b.vreg() : null;
        if (sd) this.b.const_(sd, 1);
        return sd;
      }
      if (ns != null && ns.toUpperCase() === "STORAGE" && m.toUpperCase() === "QUERYCARDS") {
        var qp = this.eval(c.args[0]), qq = this.eval(c.args[1]);
        this.b.host("Storage", "UsePack", [qp], null);
        var qd = want ? this.b.vreg() : null;
        this.b.host("Storage", "QueryCard", [qq], qd);
        return qd;
      }
      var self = this; var argregs = c.args.slice(0, 2).map(function (a) { return self.eval(a); });
      var dst = want ? this.b.vreg() : null;
      var cn = canonHost(ns, m); this.b.host(cn[0], cn[1], argregs, dst); return dst;
    }
  };
  function compileBasic(src) { return new BLowerer().lowerProgram(new BParser(btokenize(src)).parseProgram()); }


  // ========================================================================
  // EXTRA FRONTENDS (Python-style + natural-English) -- defined before the
  // factory's return so their var/prototype initializers actually execute.
  // ========================================================================

  // ---- Python-style frontend (port of picoscript_python.py) ---------------
  var PY_KW = {}; ["if","elif","else","while","for","in","range","def","return","break","continue","pass","and","or","not","print","true","false","match","case","do","until","goto","label","dispatch","const","enum"].forEach(function (k) { PY_KW[k] = 1; });
  var PY_CMP = { "==":"EQ","!=":"NE","<":"LT",">":"GT","<=":"LE",">=":"GE" };
  var PY_AUG = { "+=":"+","-=":"-","*=":"*","/=":"/","%=":"MOD" };
  var PY_PREC = { or:1, and:2, "==":3, "!=":3, "<":3, ">":3, "<=":3, ">=":3, "+":5, "-":5, "*":6, "/":6, "%":6 };
  var PY_BINOP = { "+":"+","-":"-","*":"*","/":"/","%":"MOD", and:"AND", or:"OR" };
  var PY_TWO = { "==":1,"!=":1,"<=":1,">=":1,"+=":1,"-=":1,"*=":1,"/=":1,"%=":1 };
  var PY_ONE = "+-*/%()<>=,.:";

  function indentTokLine(text, out, kwset, two, one, who, lineStart) {
    var i = 0, n = text.length;
    while (i < n) {
      var c = text[i], start = lineStart + i;
      if (c === " " || c === "\t") { i++; continue; }
      if (c === "#") break;
      if (isDigit(c)) { var j = i; if (c === "0" && (text[j + 1] === "x" || text[j + 1] === "X")) { j += 2; while (j < n && /[0-9a-fA-F]/.test(text[j])) j++; } else { while (j < n && isDigit(text[j])) j++; } out.push({ kind: "num", value: text.slice(i, j), pos: start }); i = j; continue; }
      if (isAlpha(c)) { var j2 = i; while (j2 < n && isAlnum(text[j2])) j2++; var w = text.slice(i, j2); if (kwset === null) out.push({ kind: "word", value: w, pos: start }); else out.push({ kind: kwset[w.toLowerCase()] ? "kw" : "id", value: w, pos: start }); i = j2; continue; }
      if (c === '"' || c === "'") { var q = c, j3 = i + 1, b = ""; while (j3 < n && text[j3] !== q) { if (text[j3] === "\\" && j3 + 1 < n) { var nx = text[j3 + 1]; b += ({ n: "\n", t: "\t", "\\": "\\", '"': '"', "'": "'" }[nx] || nx); j3 += 2; } else { b += text[j3]; j3++; } } if (j3 >= n) throw new Error(who + ": unterminated string"); out.push({ kind: "str", value: b, pos: start }); i = j3 + 1; continue; }
      var tw = text.slice(i, i + 2);
      if (two[tw]) { out.push({ kind: "op", value: tw, pos: start }); i += 2; continue; }
      if (one.indexOf(c) >= 0) { out.push({ kind: "op", value: c, pos: start }); i++; continue; }
      throw new Error(who + ": unexpected char " + JSON.stringify(c));
    }
  }

  function indentTokenize(src, kwset, two, one, who) {
    var out = [], indents = [0], lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    var offset = 0;
    for (var li = 0; li < lines.length; li++) {
      var line = lines[li], lineStart = offset; offset += line.length + 1;
      var stripped = line.replace(/^[ \t]+/, "");
      if (stripped === "" || stripped[0] === "#") continue;
      var indent = line.length - stripped.length;
      if (indent > indents[indents.length - 1]) { indents.push(indent); out.push({ kind: "indent", value: "", pos: lineStart }); }
      else { while (indent < indents[indents.length - 1]) { indents.pop(); out.push({ kind: "dedent", value: "", pos: lineStart }); } if (indent !== indents[indents.length - 1]) throw new Error(who + ": inconsistent indentation"); }
      var before = out.length;
      indentTokLine(line, out, kwset, two, one, who, lineStart);
      if (out.length > before) out.push({ kind: "newline", value: "", pos: lineStart });
    }
    while (indents.length > 1) { indents.pop(); out.push({ kind: "dedent", value: "", pos: offset }); }
    out.push({ kind: "eof", value: "", pos: offset });
    return out;
  }

  function pytokenize(src) { return indentTokenize(src, PY_KW, PY_TWO, PY_ONE, "Python"); }

  function PyParser(toks) { this.toks = toks; this.i = 0; }
  PyParser.prototype = {
    peek: function (k) { var j = this.i + (k || 0); return j < this.toks.length ? this.toks[j] : this.toks[this.toks.length - 1]; },
    next: function () { return this.toks[this.i++]; },
    at: function (kind, value) { var t = this.peek(); return t.kind === kind && (value === undefined || t.value === value); },
    atKw: function () { var t = this.peek(); if (t.kind !== "kw") return false; for (var k = 0; k < arguments.length; k++) if (t.value.toLowerCase() === arguments[k]) return true; return false; },
    expect: function (kind, value) { var t = this.next(); if (t.kind !== kind || (value !== undefined && t.value !== value)) throw new Error("Python: expected " + (value !== undefined ? value : kind) + " got " + t.value); return t; },
    expectKw: function (name) { var t = this.next(); if (!(t.kind === "kw" && t.value.toLowerCase() === name)) throw new Error("Python: expected " + name + " got " + t.value); },
    parseProgram: function () { var s = []; while (this.peek().kind !== "eof") { var st = this.parseStmt(); if (st !== null) s.push(st); } return s; },
    parseSuite: function () {
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var s = [];
      while (!this.at("dedent")) { if (this.peek().kind === "eof") throw new Error("Python: EOF in block"); var st = this.parseStmt(); if (st !== null) s.push(st); }
      this.expect("dedent"); return s;
    },
    parseStmt: function () {
      var start = this.peek().pos;
      var node = this._parseStmt();
      if (node != null) node.pos = start;
      return node;
    },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "kw") {
        var kw = t.value.toLowerCase();
        if (kw === "if") return this.parseIf();
        if (kw === "while") { this.expectKw("while"); var c = this.parseExpr(); return { t: "While", cond: c, body: this.parseSuite() }; }
        if (kw === "for") return this.parseFor();
        if (kw === "match") return this.parseMatch();
      if (kw === "dispatch") return this.parseDispatch();
        if (kw === "do") return this.parseDo();
        if (kw === "const") return this.parseConstDecl();
        if (kw === "enum") return this.parseEnumDecl();
        if (kw === "goto") { this.next(); var gl = this.expect("id").value; this.expect("newline"); return { t: "Goto", label: gl }; }
        if (kw === "label") { this.next(); var ll = this.expect("id").value; this.expect("newline"); return { t: "Label", name: ll }; }
        if (kw === "def") return this.parseDef();
        if (kw === "return") {
          this.next();
          if (this.at("newline")) { this.next(); return { t: "Return" }; }
          var rv = this.parseExpr(); this.expect("newline"); return { t: "Return", value: rv };
        }
        if (kw === "break") { this.next(); this.expect("newline"); return { t: "Break" }; }
        if (kw === "continue") { this.next(); this.expect("newline"); return { t: "Skip" }; }
        if (kw === "pass") { this.next(); this.expect("newline"); return null; }
        if (kw === "print") { this.next(); this.expect("op", "("); var v = this.parseExpr(); this.expect("op", ")"); this.expect("newline"); return { t: "Print", value: v }; }
        throw new Error("Python: unexpected keyword " + t.value);
      }
      if (t.kind === "id") {
        var nx = this.peek(1);
        if (nx.kind === "op" && nx.value === "=") { var nm = this.next().value; this.next(); var vv = this.parseExpr(); this.expect("newline"); return { t: "Let", name: nm, value: vv }; }
        if (nx.kind === "op" && PY_AUG[nx.value]) { var an = this.next().value; var op = PY_AUG[this.next().value]; var rhs = this.parseExpr(); this.expect("newline"); return { t: "Let", name: an, value: { t: "Bin", op: op, lhs: { t: "Var", name: an }, rhs: rhs } }; }
        if (nx.kind === "op" && nx.value === ".") { var call = this.parseCallFromId(); this.expect("newline"); return { t: "CallStmt", call: call }; }
        if (nx.kind === "op" && nx.value === "(") { var gn = this.next().value; var gargs = this.parseArgs(); this.expect("newline"); return { t: "Gosub", name: gn, args: gargs.length ? gargs : null }; }
      }
      throw new Error("Python: cannot parse statement at " + t.value);
    },
    parseIf: function () {
      this.expectKw("if"); var cond = this.parseExpr(); var body = this.parseSuite(); var arms = [[cond, body]], els = null;
      while (this.atKw("elif")) { this.expectKw("elif"); arms.push([this.parseExpr(), this.parseSuite()]); }
      if (this.atKw("else")) { this.expectKw("else"); els = this.parseSuite(); }
      return { t: "If", arms: arms, els: els };
    },
    parseFor: function () {
      this.expectKw("for"); var v = this.expect("id").value; this.expectKw("in"); this.expectKw("range"); this.expect("op", "(");
      var args = [this.parseExpr()];
      while (this.at("op", ",")) { this.next(); args.push(this.parseExpr()); }
      this.expect("op", ")"); var body = this.parseSuite();
      if (args.length === 1) return { t: "ForEach", v: v, count: args[0], body: body };
      var end = (args[1].t === "Num") ? { t: "Num", value: args[1].value - 1 } : { t: "Bin", op: "-", lhs: args[1], rhs: { t: "Num", value: 1 } };
      return { t: "ForTo", v: v, start: args[0], end: end, step: args.length >= 3 ? args[2] : null, body: body };
    },
    parseMatch: function () {
      this.expectKw("match"); var expr = this.parseExpr();
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var cases = [], def = null;
      while (!this.at("dedent")) {
        this.expectKw("case");
        if (this.peek().kind === "id" && this.peek().value === "_") { this.next(); def = this.parseSuite(); }
        else { var val = this.parseExpr(); cases.push([val, this.parseSuite()]); }
      }
      this.expect("dedent");
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseDispatch: function () {
      this.expectKw("dispatch"); var expr = this.parseExpr();
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var cases = [], def = null;
      while (!this.at("dedent")) {
        this.expectKw("case");
        if (this.peek().kind === "id" && this.peek().value === "_") { this.next(); def = this.parseSuite(); }
        else { var val = this.parseExpr(); cases.push([val, this.parseSuite()]); }
      }
      this.expect("dedent");
      return { t: "Dispatch", expr: expr, cases: cases, def: def };
    },
    parseDo: function () {
      this.expectKw("do"); var body = this.parseSuite();
      var cond, until;
      if (this.atKw("while")) { this.expectKw("while"); cond = this.parseExpr(); until = false; }
      else if (this.atKw("until")) { this.expectKw("until"); cond = this.parseExpr(); until = true; }
      else throw new Error("Python: 'do:' block must be followed by 'while' or 'until'");
      this.expect("newline");
      return { t: "DoLoop", topCond: null, topUntil: false, botCond: cond, botUntil: until, body: body };
    },
    parseDef: function () {
      this.expectKw("def"); var name = this.expect("id").value; this.expect("op", "(");
      var params = [];
      if (!this.at("op", ")")) { params.push(this.expect("id").value); while (this.at("op", ",")) { this.next(); params.push(this.expect("id").value); } }
      this.expect("op", ")");
      return { t: "Sub", name: name, body: this.parseSuite(), params: params.length ? params : null };
    },
    parseConstDecl: function () {
      this.expectKw("const");
      var name = this.expect("id").value;
      this.expect("op", "=");
      var value = this.parseExpr();
      this.expect("newline");
      return { t: "ConstDecl", name: name, value: value };
    },
    parseEnumDecl: function () {
      this.expectKw("enum");
      var enumName = this.expect("id").value;
      this.expect("op", ":");
      this.expect("newline");
      this.expect("indent");
      var members = [];
      while (!this.at("dedent")) {
        var memberName = this.expect("id").value;
        var memberValue = null;
        if (this.at("op", "=")) { this.next(); memberValue = this.parseExpr(); }
        this.expect("newline");
        members.push([memberName, memberValue]);
      }
      this.expect("dedent");
      return { t: "EnumDecl", enum_name: enumName, members: members };
    },
    parseCallFromId: function () { var ns = this.next().value; this.expect("op", "."); var m = this.next().value; return { t: "Call", ns: ns, method: m, args: this.parseArgs() }; },
    parseArgs: function () { this.expect("op", "("); var a = []; if (!this.at("op", ")")) { a.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); a.push(this.parseExpr()); } } this.expect("op", ")"); return a; },
    parseExpr: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      if (minp === 0 && this.atKw("if")) { this.expectKw("if"); var cond = this.parseExpr(); this.expectKw("else"); var els = this.parseExpr(); return { t: "Ternary", cond: cond, then: left, els: els }; }
      while (true) {
        var t = this.peek(), ov = null;
        if (t.kind === "op" && PY_PREC[t.value] !== undefined) ov = t.value;
        else if (t.kind === "kw" && PY_PREC[t.value.toLowerCase()] !== undefined) ov = t.value.toLowerCase();
        if (ov === null || PY_PREC[ov] < minp) break;
        this.next(); var right = this.parseExpr(PY_PREC[ov] + 1);
        if (PY_CMP[ov]) left = { t: "Cmp", cond: PY_CMP[ov], lhs: left, rhs: right };
        else left = { t: "Bin", op: PY_BINOP[ov], lhs: left, rhs: right };
      }
      return left;
    },
    parseUnary: function () {
      var t = this.peek();
      if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; }
      if (t.kind === "kw" && t.value.toLowerCase() === "not") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; }
      return this.parseAtom();
    },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "kw" && (t.value.toLowerCase() === "true" || t.value.toLowerCase() === "false")) return { t: "Num", value: t.value.toLowerCase() === "true" ? 1 : 0 };
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.expect("op", ")"); return e; }
      if (t.kind === "id") { if (this.at("op", ".")) { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; } if (this.at("op", "(")) { return { t: "Call", ns: null, method: t.value, args: this.parseArgs() }; } return { t: "Var", name: t.value }; }
      throw new Error("Python: unexpected token " + t.value);
    }
  };
  function compilePython(src) { return new BLowerer().lowerProgram(new PyParser(pytokenize(src)).parseProgram()); }

  // ---- English-style frontend (port of picoscript_english.py) -------------
  var EN_TWO = { "==":1,"!=":1,"<=":1,">=":1,"<>":1 };
  var EN_ONE = "+-*/%()<>=,.:";
  var EN_PREC_SYM = { "+":5,"-":5,"*":6,"/":6,"%":6,"<":3,">":3,"<=":3,">=":3,"==":3,"!=":3,"<>":3 };
  var EN_CMP_SYM = { "<":"LT",">":"GT","<=":"LE",">=":"GE","==":"EQ","!=":"NE","<>":"NE" };

  function entokenize(src) { return indentTokenize(src, null, EN_TWO, EN_ONE, "English"); }

  function EnParser(toks) { this.toks = toks; this.i = 0; }
  EnParser.prototype = {
    peek: function (k) { var j = this.i + (k || 0); return j < this.toks.length ? this.toks[j] : this.toks[this.toks.length - 1]; },
    next: function () { return this.toks[this.i++]; },
    at: function (kind, value) { var t = this.peek(); return t.kind === kind && (value === undefined || t.value === value); },
    atWord: function () { var t = this.peek(); if (t.kind !== "word") return false; for (var k = 0; k < arguments.length; k++) if (t.value.toLowerCase() === arguments[k]) return true; return false; },
    wordAt: function (k) { var t = this.peek(k); return t.kind === "word" ? t.value.toLowerCase() : null; },
    expect: function (kind, value) { var t = this.next(); if (t.kind !== kind || (value !== undefined && t.value !== value)) throw new Error("English: expected " + (value !== undefined ? value : kind) + " got " + t.value); return t; },
    eatWord: function () { var t = this.next(); if (t.kind !== "word") throw new Error("English: expected word got " + t.value); var lw = t.value.toLowerCase(); for (var k = 0; k < arguments.length; k++) if (lw === arguments[k]) return t.value; throw new Error("English: expected one of " + Array.prototype.slice.call(arguments) + " got " + t.value); },
    endStmt: function () { if (this.at("op", ".")) this.next(); this.expect("newline"); },
    parseProgram: function () { var s = []; while (this.peek().kind !== "eof") { var st = this.parseStmt(); if (st !== null) s.push(st); } return s; },
    parseSuite: function () {
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var s = [];
      while (!this.at("dedent")) { if (this.peek().kind === "eof") throw new Error("English: EOF in block"); var st = this.parseStmt(); if (st !== null) s.push(st); }
      this.expect("dedent"); return s;
    },
    parseStmt: function () {
      var start = this.peek().pos;
      var node = this._parseStmt();
      if (node != null) node.pos = start;
      return node;
    },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "word") {
        var w = t.value.toLowerCase();
        if (w === "set" || w === "let") { this.next(); var nm = this.expect("word").value; this.eatWord(w === "set" ? "to" : "be"); var v = this.parseExpr(); this.endStmt(); return { t: "Let", name: nm, value: v }; }
        if (w === "add") { this.next(); var e = this.parseExpr(); this.eatWord("to"); var an = this.expect("word").value; this.endStmt(); return { t: "Let", name: an, value: { t: "Bin", op: "+", lhs: { t: "Var", name: an }, rhs: e } }; }
        if (w === "subtract") { this.next(); var e2 = this.parseExpr(); this.eatWord("from"); var sn = this.expect("word").value; this.endStmt(); return { t: "Let", name: sn, value: { t: "Bin", op: "-", lhs: { t: "Var", name: sn }, rhs: e2 } }; }
        if (w === "increase" || w === "decrease" || w === "multiply" || w === "divide") { this.next(); var vn = this.expect("word").value; this.eatWord("by"); var ve = this.parseExpr(); this.endStmt(); var op = { increase: "+", decrease: "-", multiply: "*", divide: "/" }[w]; return { t: "Let", name: vn, value: { t: "Bin", op: op, lhs: { t: "Var", name: vn }, rhs: ve } }; }
        if (w === "print" || w === "show" || w === "display") { this.next(); var pe = this.parseExpr(); this.endStmt(); return { t: "Print", value: pe }; }
        if (w === "if") return this.parseIf();
        if (w === "while") { this.next(); var wc = this.parseExpr(); return { t: "While", cond: wc, body: this.parseSuite() }; }
        if (w === "as") { this.next(); this.eatWord("long"); this.eatWord("as"); var ac = this.parseExpr(); return { t: "While", cond: ac, body: this.parseSuite() }; }
        if (w === "choose") return this.parseChoose();
      if (w === "dispatch") return this.parseDispatch();
        if (w === "label") { this.next(); var lname = this.expect("word").value; this.endStmt(); return { t: "Label", name: lname }; }
        if (w === "go") { this.next(); this.eatWord("to"); var gname = this.expect("word").value; this.endStmt(); return { t: "Goto", label: gname }; }
        if (w === "repeat") return this.parseRepeat();
        if (w === "for") return this.parseFor();
        if (w === "define" || w === "to") {
          this.next();
          if (this.atWord("a", "an", "the")) this.next();
          if (this.atWord("constant", "const")) {
            this.next();
            var cname = this.expect("word").value;
            if (this.atWord("as", "to", "is", "equals", "be")) this.next();
            var cval = this.parseExpr();
            this.endStmt();
            return { t: "ConstDecl", name: cname, value: cval };
          }
          if (this.atWord("enum", "enumeration")) {
            this.next();
            var ename = this.expect("word").value;
            this.expect("op", ":"); this.expect("newline"); this.expect("indent");
            var emembers = [];
            while (!this.at("dedent")) {
              if (this.atWord("member")) this.next();
              var mn = this.expect("word").value;
              var mv = null;
              if (this.atWord("is", "equals", "as", "to", "be")) { this.next(); mv = this.parseExpr(); }
              this.endStmt();
              emembers.push([mn, mv]);
            }
            this.expect("dedent");
            return { t: "EnumDecl", enum_name: ename, members: emembers };
          }
          if (this.atWord("routine", "subroutine", "procedure", "function")) { this.next(); if (this.atWord("called", "named")) this.next(); }
          var dn = this.expect("word").value;
          var dparams = null;
          if (this.at("op", "(")) {
            this.next(); dparams = [];
            if (!this.at("op", ")")) { dparams.push(this.expect("word").value); while (this.at("op", ",")) { this.next(); dparams.push(this.expect("word").value); } }
            this.expect("op", ")");
          }
          return { t: "Sub", name: dn, body: this.parseSuite(), params: dparams };
        }
        if (w === "do" || w === "call") {
          this.next(); var cn = this.expect("word").value;
          var cargs = null;
          if (this.at("op", "(")) { this.next(); cargs = []; if (!this.at("op", ")")) { cargs.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); cargs.push(this.parseExpr()); } } this.expect("op", ")"); }
          this.endStmt(); return { t: "Gosub", name: cn, args: cargs };
        }
        if (w === "return") {
          this.next();
          if (this.at("newline") || this.at("eof") || this.at("op", ".")) { this.endStmt(); return { t: "Return" }; }
          var rv = this.parseExpr(); this.endStmt(); return { t: "Return", value: rv };
        }
        if (w === "stop" || w === "break") { this.next(); if (this.atWord("out")) this.next(); this.endStmt(); return { t: "Break" }; }
        if (w === "skip" || w === "continue") { this.next(); this.endStmt(); return { t: "Skip" }; }
        if (this.peek(1).kind === "op" && this.peek(1).value === "." && this.peek(2).kind === "word" && this.peek(3).kind === "op" && this.peek(3).value === "(") { var call = this.parseCallFromWord(); this.endStmt(); return { t: "CallStmt", call: call }; }
      }
      throw new Error("English: cannot parse statement at " + t.value);
    },
    parseIf: function () {
      this.eatWord("if"); var cond = this.parseExpr(); var body = this.parseSuite(); var arms = [[cond, body]], els = null;
      while (this.atWord("otherwise") && this.peek(1).kind === "word" && this.peek(1).value.toLowerCase() === "if") { this.eatWord("otherwise"); this.eatWord("if"); arms.push([this.parseExpr(), this.parseSuite()]); }
      if (this.atWord("otherwise")) { this.eatWord("otherwise"); els = this.parseSuite(); }
      return { t: "If", arms: arms, els: els };
    },
    parseChoose: function () {
      this.eatWord("choose"); var expr = this.parseExpr();
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var cases = [], def = null;
      while (!this.at("dedent")) {
        if (this.atWord("when")) { this.eatWord("when"); var val = this.parseExpr(); cases.push([val, this.parseSuite()]); }
        else if (this.atWord("otherwise")) { this.eatWord("otherwise"); def = this.parseSuite(); }
        else throw new Error("English: expected 'When' or 'Otherwise' in Choose");
      }
      this.expect("dedent");
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseDispatch: function () {
      this.eatWord("dispatch"); if (this.atWord("on")) this.eatWord("on"); var expr = this.parseExpr();
      this.expect("op", ":"); this.expect("newline"); this.expect("indent");
      var cases = [], def = null;
      while (!this.at("dedent")) {
        if (this.atWord("when")) { this.eatWord("when"); var val = this.parseExpr(); cases.push([val, this.parseSuite()]); }
        else if (this.atWord("otherwise")) { this.eatWord("otherwise"); def = this.parseSuite(); }
        else throw new Error("English: expected 'When' or 'Otherwise' in Dispatch");
      }
      this.expect("dedent");
      return { t: "Dispatch", expr: expr, cases: cases, def: def };
    },
    parseRepeat: function () {
      this.eatWord("repeat");
      if (this.at("op", ":")) {
        var body = this.parseSuite(); var cond, until;
        if (this.atWord("until")) { this.eatWord("until"); cond = this.parseExpr(); until = true; }
        else if (this.atWord("while")) { this.eatWord("while"); cond = this.parseExpr(); until = false; }
        else throw new Error("English: 'Repeat:' block must be followed by 'Until' or 'While'");
        this.endStmt();
        return { t: "DoLoop", topCond: null, topUntil: false, botCond: cond, botUntil: until, body: body };
      }
      if (this.atWord("while")) { this.eatWord("while"); var c = this.parseExpr(); return { t: "While", cond: c, body: this.parseSuite() }; }
      var count = this.parseUnary(); this.eatWord("times"); var v = "_i";
      if (this.atWord("with")) { this.eatWord("with"); v = this.expect("word").value; }
      return { t: "ForEach", v: v, count: count, body: this.parseSuite() };
    },
    parseFor: function () {
      this.eatWord("for"); this.eatWord("each"); var v = this.expect("word").value; this.eatWord("from"); var start = this.parseExpr(); this.eatWord("to"); var end = this.parseExpr();
      var step = null; if (this.atWord("by", "step")) { this.next(); step = this.parseExpr(); }
      return { t: "ForTo", v: v, start: start, end: end, step: step, body: this.parseSuite() };
    },
    parseCallFromWord: function () { var ns = this.next().value; this.expect("op", "."); var m = this.next().value; return { t: "Call", ns: ns, method: m, args: this.parseArgs() }; },
    parseArgs: function () { this.expect("op", "("); var a = []; if (!this.at("op", ")")) { a.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); a.push(this.parseExpr()); } } this.expect("op", ")"); return a; },
    matchBinop: function () {
      var t = this.peek();
      if (t.kind === "op" && EN_PREC_SYM[t.value] !== undefined) {
        if (EN_CMP_SYM[t.value]) return [EN_PREC_SYM[t.value], 1, "cmp", EN_CMP_SYM[t.value]];
        return [EN_PREC_SYM[t.value], 1, "bin", t.value === "%" ? "MOD" : t.value];
      }
      if (t.kind !== "word") return null;
      var w = t.value.toLowerCase(), w1 = this.wordAt(1), w2 = this.wordAt(2), w3 = this.wordAt(3), w4 = this.wordAt(4), w5 = this.wordAt(5);
      if (w === "and") return [2, 1, "bin", "AND"];
      if (w === "or") return [1, 1, "bin", "OR"];
      if (w === "plus") return [5, 1, "bin", "+"];
      if (w === "minus") return [5, 1, "bin", "-"];
      if (w === "times") return [6, 1, "bin", "*"];
      if (w === "modulo" || w === "mod") return [6, 1, "bin", "MOD"];
      if (w === "divided" && w1 === "by") return [6, 2, "bin", "/"];
      if (w === "over") return [6, 1, "bin", "/"];
      if (w === "is") {
        if (w1 === "greater" && w2 === "than") { if (w3 === "or" && w4 === "equal" && w5 === "to") return [3, 6, "cmp", "GE"]; return [3, 3, "cmp", "GT"]; }
        if (w1 === "less" && w2 === "than") { if (w3 === "or" && w4 === "equal" && w5 === "to") return [3, 6, "cmp", "LE"]; return [3, 3, "cmp", "LT"]; }
        if (w1 === "at" && w2 === "least") return [3, 3, "cmp", "GE"];
        if (w1 === "at" && w2 === "most") return [3, 3, "cmp", "LE"];
        if (w1 === "not") { if (w2 === "equal" && w3 === "to") return [3, 4, "cmp", "NE"]; return [3, 2, "cmp", "NE"]; }
        if (w1 === "equal" && w2 === "to") return [3, 3, "cmp", "EQ"];
        return [3, 1, "cmp", "EQ"];
      }
      if (w === "equals") return [3, 1, "cmp", "EQ"];
      if (w === "exceeds") return [3, 1, "cmp", "GT"];
      return null;
    },
    parseExpr: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      if (minp === 0 && this.atWord("if")) { this.eatWord("if"); var cond = this.parseExpr(); this.eatWord("otherwise"); var els = this.parseExpr(); return { t: "Ternary", cond: cond, then: left, els: els }; }
      while (true) {
        var m = this.matchBinop();
        if (m === null || m[0] < minp) break;
        for (var k = 0; k < m[1]; k++) this.next();
        var right = this.parseExpr(m[0] + 1);
        left = (m[2] === "cmp") ? { t: "Cmp", cond: m[3], lhs: left, rhs: right } : { t: "Bin", op: m[3], lhs: left, rhs: right };
      }
      return left;
    },
    parseUnary: function () {
      var t = this.peek();
      if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; }
      if (t.kind === "word" && t.value.toLowerCase() === "not") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; }
      return this.parseAtom();
    },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.expect("op", ")"); return e; }
      if (t.kind === "word") {
        var lw = t.value.toLowerCase();
        if (lw === "true") return { t: "Num", value: 1 };
        if (lw === "false") return { t: "Num", value: 0 };
        if (this.at("op", ".") && this.peek(1).kind === "word" && this.peek(2).kind === "op" && this.peek(2).value === "(") { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; }
        if (this.at("op", "(")) return { t: "Call", ns: null, method: t.value, args: this.parseArgs() };
        return { t: "Var", name: t.value };
      }
      throw new Error("English: unexpected token " + t.value);
    }
  };
  function compileEnglish(src) { return new BLowerer().lowerProgram(new EnParser(entokenize(src)).parseProgram()); }

  // ── INV-25: structured debug trace -- symbolication (mirrors picoscript_il) ──
  var FAULT_NAMES = {
    0: "none", 1: "step_budget", 2: "bad_opcode", 3: "bad_jump", 4: "call_overflow",
    5: "ret_underflow", 6: "bad_hook", 7: "template", 8: "capability", 9: "alloc", 10: "const_write"
  };
  function offsetToLineCol(source, off) {
    if (source == null || off == null || off < 0 || off > source.length) return [0, 0];
    var line = 1, lastNl = -1;
    for (var k = 0; k < off; k++) { if (source[k] === "\n") { line++; lastNl = k; } }
    return [line, off - lastNl];
  }
  function sourceLineText(source, off) {
    if (source == null || off == null || off < 0 || off > source.length) return "";
    var start = source.lastIndexOf("\n", off - 1) + 1;
    var end = source.indexOf("\n", off);
    if (end < 0) end = source.length;
    return source.slice(start, end);
  }
  function symbolize(code, pc, detail, debug, source) {
    source = source || "";
    var rec = (debug && Object.prototype.hasOwnProperty.call(debug, pc)) ? debug[pc] : null;
    var off = rec ? rec[0] : -1, op = rec ? rec[1] : "", ns = rec ? rec[2] : null, method = rec ? rec[3] : null;
    var lc = offsetToLineCol(source, off);
    var target = ns ? (ns + "." + method) : (op || "?");
    return {
      code: code, fault: (FAULT_NAMES[code] !== undefined ? FAULT_NAMES[code] : String(code)),
      pc: pc, detail: detail, op: op || "?", target: target,
      off: off, line: lc[0], col: lc[1], source_line: sourceLineText(source, off)
    };
  }

  function pushAst(out, node) {
    if (node == null) return;
    if (Array.isArray(node)) Array.prototype.push.apply(out, node);
    else out.push(node);
  }
  function markAst(node, pos) {
    var arr = Array.isArray(node) ? node : [node];
    arr.forEach(function (n) { if (n && typeof n === "object") n.pos = pos; });
    return node;
  }
  function periodTokenize(src, kwset, two, one, who, opt) {
    opt = opt || {};
    var out = [], i = 0, n = src.length, bol = true;
    function push(k, v, p) { out.push({ kind: k, value: v, pos: p }); bol = false; }
    while (i < n) {
      var c = src[i], start = i;
      if (c === "\n") { out.push({ kind: "nl", value: "", pos: start }); i++; bol = true; continue; }
      if (c === " " || c === "\t" || c === "\r") { i++; continue; }
      if (opt.commentInlineStarGt && c === "*" && src[i + 1] === ">") { while (i < n && src[i] !== "\n") i++; continue; }
      if (opt.commentSlashSlash && c === "/" && src[i + 1] === "/") { while (i < n && src[i] !== "\n") i++; continue; }
      if (opt.commentLineStar && c === "*" && bol) { while (i < n && src[i] !== "\n") i++; continue; }
      if (opt.commentInlineQuote && c === '"') { while (i < n && src[i] !== "\n") i++; continue; }
      if (c === '"' || c === "'") {
        var q = c, j = i + 1, b = "";
        while (j < n && src[j] !== q) {
          if (src[j] === "\\" && j + 1 < n) {
            var nx = src[j + 1];
            b += ({ n: "\n", t: "\t", "\\": "\\", '"': '"', "'": "'" }[nx] || nx);
            j += 2;
          } else {
            b += src[j++];
          }
        }
        if (j >= n) throw new Error(who + ": unterminated string");
        push("str", b, start); i = j + 1; continue;
      }
      if (isDigit(c)) {
        var j2 = i;
        if (c === "0" && (src[j2 + 1] === "x" || src[j2 + 1] === "X")) { j2 += 2; while (j2 < n && /[0-9a-fA-F]/.test(src[j2])) j2++; }
        else while (j2 < n && isDigit(src[j2])) j2++;
        push("num", src.slice(i, j2), start); i = j2; continue;
      }
      if (isAlpha(c) || c === "_") {
        var j3 = i;
        while (j3 < n) {
          var ch = src[j3];
          if (isAlnum(ch) || ch === "_" || (opt.allowHyphen && ch === "-")) j3++;
          else break;
        }
        var raw = src.slice(i, j3), word = opt.uppercase ? raw.toUpperCase() : raw, key = opt.uppercase ? word : raw.toUpperCase();
        push(kwset[key] ? "kw" : "id", word, start); i = j3; continue;
      }
      var tw = src.slice(i, i + 2);
      if (two[tw]) { push("op", tw, start); i += 2; continue; }
      if (one.indexOf(c) >= 0) { push("op", c, start); i++; continue; }
      throw new Error(who + ": unexpected char " + JSON.stringify(c));
    }
    out.push({ kind: "nl", value: "", pos: n });
    out.push({ kind: "eof", value: "", pos: n });
    return out;
  }

  // ---- COBOL-style frontend -------------------------------------------------
  var COB_KW = {};
  ["IDENTIFICATION","DIVISION","PROGRAM-ID","DATA","PROCEDURE","WORKING-STORAGE","SECTION","PIC","VALUE","MOVE","TO","COMPUTE","DISPLAY","IF","ELSE","END-IF","PERFORM","VARYING","FROM","BY","UNTIL","END-PERFORM","STOP","RUN","NOT","AND","OR","IS","GREATER","LESS","EQUAL","THAN","EVALUATE","WHEN","OTHER","END-EVALUATE","GO","MOD","EXIT","CYCLE"].forEach(function (k) { COB_KW[k] = 1; });
  var COB_TWO = { "==":1, "!=":1, "<=":1, ">=":1, "<>":1 };
  var COB_ONE = "+-*/()<>=,.:";
  var COB_CMP = { "=":"EQ", "==":"EQ", "!=":"NE", "<>":"NE", "<":"LT", ">":"GT", "<=":"LE", ">=":"GE" };
  var COB_PREC = { OR:1, AND:2, "+":5, "-":5, "*":6, "/":6, MOD:6 };
  Object.keys(COB_CMP).forEach(function (k) { COB_PREC[k] = 3; });
  function cobtokenize(src) { return periodTokenize(src, COB_KW, COB_TWO, COB_ONE, "COBOL", { commentLineStar: true, commentInlineStarGt: true, allowHyphen: true, uppercase: true }); }
  function CobParser(toks) { this.toks = toks; this.i = 0; }
  CobParser.prototype = {
    peek: function (k) { var j = this.i + (k || 0); return j < this.toks.length ? this.toks[j] : this.toks[this.toks.length - 1]; },
    next: function () { return this.toks[this.i++]; },
    at: function (kind, value) { var t = this.peek(); return t.kind === kind && (value === undefined || t.value === value); },
    atKw: function () { var t = this.peek(); if (t.kind !== "kw") return false; for (var k = 0; k < arguments.length; k++) if (t.value === arguments[k]) return true; return false; },
    wordAt: function (k) { var t = this.peek(k); return (t.kind === "kw" || t.kind === "id") ? t.value : null; },
    expect: function (kind, value) { var t = this.next(); if (t.kind !== kind || (value !== undefined && t.value !== value)) throw new Error("COBOL: expected " + (value !== undefined ? value : kind) + " got " + t.value); return t; },
    expectKw: function (name) { var t = this.next(); if (!(t.kind === "kw" && t.value === name)) throw new Error("COBOL: expected " + name + " got " + t.value); },
    expectName: function () { var t = this.next(); if (t.kind !== "id") throw new Error("COBOL: expected identifier got " + t.value); return t.value; },
    skipNl: function () { while (this.at("nl")) this.next(); },
    endHeader: function () { if (this.at("op", ".")) this.next(); if (this.at("nl") || this.at("eof")) { this.skipNl(); return; } throw new Error("COBOL: expected end of line got " + this.peek().value); },
    endSimple: function () { if (this.at("op", ".")) this.next(); if (this.at("nl") || this.at("eof")) { this.skipNl(); return; } throw new Error("COBOL: expected end of statement got " + this.peek().value); },
    skipLine: function () { while (!this.at("eof") && !this.at("nl")) this.next(); this.skipNl(); },
    atDivision: function (name) { return this.atKw(name) && this.peek(1).kind === "kw" && this.peek(1).value === "DIVISION"; },
    consumeDivision: function (name) { this.expectKw(name); this.expectKw("DIVISION"); if (this.at("op", ".")) this.next(); this.skipNl(); },
    atParagraph: function () { return this.peek().kind === "id" && this.peek(1).kind === "op" && this.peek(1).value === "." && (this.peek(2).kind === "nl" || this.peek(2).kind === "eof"); },
    parseProgram: function () {
      var decls = [], main = [], subs = [];
      this.skipNl();
      while (!this.at("eof")) {
        if (this.atDivision("IDENTIFICATION")) { this.consumeDivision("IDENTIFICATION"); continue; }
        if (this.atDivision("DATA")) { this.consumeDivision("DATA"); decls = decls.concat(this.parseDataDivision()); continue; }
        if (this.atDivision("PROCEDURE")) { this.consumeDivision("PROCEDURE"); var p = this.parseProcedureDivision(); main = main.concat(p.body); subs = subs.concat(p.subs); break; }
        this.skipLine();
      }
      return decls.concat(main, subs);
    },
    parseDataDivision: function () {
      var out = [];
      while (!this.at("eof") && !this.atDivision("PROCEDURE")) {
        this.skipNl();
        if (this.at("eof") || this.atDivision("PROCEDURE")) break;
        if (this.peek().kind === "num") out.push(this.parseDataItem());
        else this.skipLine();
      }
      return out;
    },
    parseDataItem: function () {
      this.expect("num");
      var name = this.expectName(), init = { t: "Num", value: 0 };
      while (!this.at("eof") && !this.at("op", ".") && !this.at("nl")) {
        if (this.atKw("VALUE")) { this.next(); init = this.parseExpr(); break; }
        this.next();
      }
      if (this.at("op", ".")) this.next();
      this.skipNl();
      return { t: "Let", name: name, value: init };
    },
    collectGotoTargets: function () {
      var labels = {};
      for (var j = this.i; j < this.toks.length; j++) {
        if (this.toks[j].kind === "kw" && this.toks[j].value === "GO" && this.toks[j + 1] && this.toks[j + 1].kind === "kw" && this.toks[j + 1].value === "TO" && this.toks[j + 2] && this.toks[j + 2].kind === "id") labels[this.toks[j + 2].value] = 1;
      }
      return labels;
    },
    parseProcedureDivision: function () {
      var body = [], subs = [];
      this.gotoTargets = this.collectGotoTargets();
      this.skipNl();
      while (!this.at("eof")) {
        if (this.atParagraph()) {
          if (this.gotoTargets && this.gotoTargets[this.peek().value]) body.push(this.parseLabelParagraph());
          else subs.push(this.parseParagraph());
        }
        else pushAst(body, this.parseStmt());
      }
      return { body: body, subs: subs };
    },
    parseLabelParagraph: function () { var start = this.peek().pos, name = this.expectName(); this.expect("op", "."); this.skipNl(); return markAst({ t: "Label", name: name }, start); },
    parseParagraph: function () { var name = this.expectName(); this.expect("op", "."); this.skipNl(); return { t: "Sub", name: name, params: null, body: this.parseBlock([], true) }; },
    parseBlock: function (stopWords, stopOnParagraph) {
      var out = [];
      this.skipNl();
      while (!this.at("eof")) {
        if (stopOnParagraph && this.atParagraph()) break;
        if (this.peek().kind === "kw" && stopWords.indexOf(this.peek().value) >= 0) break;
        pushAst(out, this.parseStmt());
        this.skipNl();
      }
      return out;
    },
    parseStmt: function () { this.skipNl(); var start = this.peek().pos, node = this._parseStmt(); return markAst(node, start); },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "kw") {
        if (t.value === "MOVE") return this.parseMove();
        if (t.value === "COMPUTE") return this.parseCompute();
        if (t.value === "DISPLAY") return this.parseDisplay();
        if (t.value === "IF") return this.parseIf();
        if (t.value === "PERFORM") return this.parsePerform();
        if (t.value === "EVALUATE") return this.parseEvaluate();
        if (t.value === "STOP") return this.parseStopRun();
        if (t.value === "GO") return this.parseGoTo();
        if (t.value === "EXIT") return this.parseExit();
      }
      if (t.kind === "id" && this.peek(1).kind === "op" && this.peek(1).value === "." && (this.peek(2).kind === "id" || this.peek(2).kind === "kw") && this.peek(3).kind === "op" && this.peek(3).value === "(") {
        var call = this.parseCallFromId(); this.endSimple(); return { t: "CallStmt", call: call };
      }
      throw new Error("COBOL: cannot parse statement at " + t.value);
    },
    parseMove: function () { this.expectKw("MOVE"); var value = this.parseExpr(); this.expectKw("TO"); var name = this.expectName(); this.endSimple(); return { t: "Let", name: name, value: value }; },
    parseCompute: function () { this.expectKw("COMPUTE"); var name = this.expectName(); this.expect("op", "="); var value = this.parseExpr(); this.endSimple(); return { t: "Let", name: name, value: value }; },
    parseDisplay: function () { this.expectKw("DISPLAY"); var value = this.parseExpr(); this.endSimple(); return { t: "Print", value: value }; },
    parseIf: function () {
      this.expectKw("IF");
      var cond = this.parseExpr(), arms = [], els = null;
      this.endHeader();
      arms.push([cond, this.parseBlock(["ELSE", "END-IF"], false)]);
      if (this.atKw("ELSE")) { this.next(); this.endHeader(); els = this.parseBlock(["END-IF"], false); }
      this.expectKw("END-IF"); this.endSimple();
      return { t: "If", arms: arms, els: els };
    },
    parsePerform: function () { this.expectKw("PERFORM"); if (this.atKw("VARYING")) return this.parsePerformVarying(); if (this.atKw("UNTIL")) return this.parsePerformUntil(); var name = this.expectName(); this.endSimple(); return { t: "Gosub", name: name, args: null }; },
    parseGoTo: function () { this.expectKw("GO"); if (this.atKw("TO")) this.next(); var name = this.expectName(); this.endSimple(); return { t: "Goto", label: name }; },
    parseExit: function () { this.expectKw("EXIT"); if (this.atKw("PERFORM")) this.next(); var isCycle = false; if (this.atKw("CYCLE")) { this.next(); isCycle = true; } this.endSimple(); return { t: isCycle ? "Skip" : "Break" }; },
    parsePerformUntil: function () {
      this.expectKw("UNTIL");
      var cond = this.parseExpr();
      this.endHeader();
      var body = this.parseBlock(["END-PERFORM"], false);
      this.expectKw("END-PERFORM"); this.endSimple();
      return { t: "While", cond: this.negateExpr(cond), body: body };
    },
    parsePerformVarying: function () {
      this.expectKw("VARYING");
      var v = this.expectName();
      this.expectKw("FROM");
      var start = this.parseExpr(), step = { t: "Num", value: 1 };
      if (this.atKw("BY")) { this.next(); step = this.parseExpr(); }
      this.expectKw("UNTIL");
      var cond = this.parseExpr();
      this.endHeader();
      var body = this.parseBlock(["END-PERFORM"], false);
      this.expectKw("END-PERFORM"); this.endSimple();
      return { t: "ForTo", v: v, start: start, end: this.forEndFromUntil(v, cond), step: step, body: body };
    },
    forEndFromUntil: function (v, cond) {
      if (cond.t === "Cmp" && cond.lhs && cond.lhs.t === "Var" && cond.lhs.name === v) {
        if (cond.cond === "GT") return cond.rhs;
        if (cond.cond === "GE") return { t: "Bin", op: "-", lhs: cond.rhs, rhs: { t: "Num", value: 1 } };
      }
      throw new Error("COBOL: unsupported PERFORM VARYING UNTIL condition");
    },
    parseEvaluate: function () {
      this.expectKw("EVALUATE");
      var expr = this.parseExpr(), cases = [], def = null;
      this.endHeader();
      while (!this.atKw("END-EVALUATE")) {
        this.expectKw("WHEN");
        if (this.atKw("OTHER")) {
          this.next();
          this.endHeader();
          def = this.parseBlock(["END-EVALUATE"], false);
          break;
        }
        var val = this.parseExpr();
        this.endHeader();
        cases.push([val, this.parseBlock(["WHEN", "END-EVALUATE"], false)]);
      }
      this.expectKw("END-EVALUATE"); this.endSimple();
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    negateExpr: function (expr) { return { t: "Cmp", cond: "EQ", lhs: expr, rhs: { t: "Num", value: 0 } }; },
    parseStopRun: function () { this.expectKw("STOP"); if (this.atKw("RUN")) this.next(); this.endSimple(); return { t: "Return" }; },
    parseCallFromId: function () { var ns = this.expectName(); this.expect("op", "."); var m = this.next(); if (m.kind !== "id" && m.kind !== "kw") throw new Error("COBOL: expected method after ."); return { t: "Call", ns: ns, method: m.value, args: this.parseArgs() }; },
    parseArgs: function () { this.expect("op", "("); var a = []; if (!this.at("op", ")")) { a.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); a.push(this.parseExpr()); } } this.expect("op", ")"); return a; },
    matchBinop: function () {
      var t = this.peek(), w = this.wordAt(0), w1 = this.wordAt(1), w2 = this.wordAt(2), w3 = this.wordAt(3), w4 = this.wordAt(4);
      if (t.kind === "op" && COB_PREC[t.value] !== undefined) return [COB_PREC[t.value], 1, COB_CMP[t.value] ? "cmp" : "bin", COB_CMP[t.value] || t.value];
      if (w === "AND") return [2, 1, "bin", "AND"];
      if (w === "OR") return [1, 1, "bin", "OR"];
      if (w === "MOD") return [6, 1, "bin", "MOD"];
      if (w === "GREATER" && w1 === "THAN") { if (w2 === "OR" && w3 === "EQUAL" && w4 === "TO") return [3, 5, "cmp", "GE"]; return [3, 2, "cmp", "GT"]; }
      if (w === "LESS" && w1 === "THAN") { if (w2 === "OR" && w3 === "EQUAL" && w4 === "TO") return [3, 5, "cmp", "LE"]; return [3, 2, "cmp", "LT"]; }
      if (w === "EQUAL" && w1 === "TO") return [3, 2, "cmp", "EQ"];
      if (w === "NOT" && w1 === "EQUAL" && w2 === "TO") return [3, 3, "cmp", "NE"];
      return null;
    },
    parseExpr: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      while (true) {
        var m = this.matchBinop();
        if (!m || m[0] < minp) break;
        for (var i = 0; i < m[1]; i++) this.next();
        var right = this.parseExpr(m[0] + 1);
        left = (m[2] === "cmp") ? { t: "Cmp", cond: m[3], lhs: left, rhs: right } : { t: "Bin", op: m[3], lhs: left, rhs: right };
      }
      return left;
    },
    parseUnary: function () { var t = this.peek(); if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; } if (t.kind === "kw" && t.value === "NOT") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; } return this.parseAtom(); },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.expect("op", ")"); return e; }
      if (t.kind === "id") {
        if (this.at("op", ".") && (this.peek(1).kind === "id" || this.peek(1).kind === "kw") && this.peek(2).kind === "op" && this.peek(2).value === "(") { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; }
        if (this.at("op", "(")) return { t: "Call", ns: null, method: t.value, args: this.parseArgs() };
        return { t: "Var", name: t.value };
      }
      throw new Error("COBOL: unexpected token " + t.value);
    }
  };
  function compileCobol(src) { return new BLowerer().lowerProgram(new CobParser(cobtokenize(src)).parseProgram()); }

  // ---- Report/4GL frontend --------------------------------------------------
  var REP_KW = {};
  ["DATA","TYPE","VALUE","IF","ELSE","ELSEIF","ENDIF","WRITE","FORM","ENDFORM","USING","PERFORM","CASE","WHEN","OTHERS","ENDCASE","LOOP","AT","INTO","WHERE","ENDLOOP","RETURN","EXIT","CONTINUE","AND","OR","NOT","WHILE","ENDWHILE","LABEL","GOTO","MOD"].forEach(function (k) { REP_KW[k] = 1; });
  var REP_TWO = { "==":1, "!=":1, "<=":1, ">=":1, "<>":1 };
  var REP_ONE = "+-*/%()<>=,.:";
  var REP_CMP = { "=":"EQ", "==":"EQ", "!=":"NE", "<>":"NE", "<":"LT", ">":"GT", "<=":"LE", ">=":"GE" };
  var REP_PREC = { OR:1, AND:2, "+":5, "-":5, "*":6, "/":6, "%":6 };
  Object.keys(REP_CMP).forEach(function (k) { REP_PREC[k] = 3; });
  function reptokenize(src) { return periodTokenize(src, REP_KW, REP_TWO, REP_ONE, "Report", { commentLineStar: true, commentInlineQuote: true, uppercase: true }); }
  function RepParser(toks) { this.toks = toks; this.i = 0; this.tmp = 0; }
  RepParser.prototype = {
    peek: function (k) { var j = this.i + (k || 0); return j < this.toks.length ? this.toks[j] : this.toks[this.toks.length - 1]; },
    next: function () { return this.toks[this.i++]; },
    at: function (kind, value) { var t = this.peek(); return t.kind === kind && (value === undefined || t.value === value); },
    atKw: function () { var t = this.peek(); if (t.kind !== "kw") return false; for (var k = 0; k < arguments.length; k++) if (t.value === arguments[k]) return true; return false; },
    expect: function (kind, value) { var t = this.next(); if (t.kind !== kind || (value !== undefined && t.value !== value)) throw new Error("Report: expected " + (value !== undefined ? value : kind) + " got " + t.value); return t; },
    expectKw: function (name) { var t = this.next(); if (!(t.kind === "kw" && t.value === name)) throw new Error("Report: expected " + name + " got " + t.value); },
    skipNl: function () { while (this.at("nl")) this.next(); },
    endStmt: function () { if (this.at("op", ".")) this.next(); this.skipNl(); },
    freshTemp: function () { this.tmp++; return "__loop" + this.tmp; },
    parseProgram: function () {
      var out = [];
      while (!this.at("eof")) {
        this.skipNl();
        if (this.at("eof")) break;
        if (this.at("op", ".")) { this.next(); continue; }
        pushAst(out, this.parseStmt());
      }
      return out;
    },
    parseBlockUntil: function (stops) {
      var out = [];
      while (!this.at("eof")) {
        this.skipNl();
        if (this.peek().kind === "kw" && stops.indexOf(this.peek().value) >= 0) break;
        if (this.at("op", ".")) { this.next(); continue; }
        pushAst(out, this.parseStmt());
      }
      return out;
    },
    parseStmt: function () { this.skipNl(); var start = this.peek().pos, node = this._parseStmt(); return markAst(node, start); },
    _parseStmt: function () {
      var t = this.peek();
      if (t.kind === "kw") {
        if (t.value === "DATA") return this.parseData();
        if (t.value === "IF") return this.parseIf();
        if (t.value === "WRITE") { this.next(); var pv = this.parseExpr(); this.endStmt(); return { t: "Print", value: pv }; }
        if (t.value === "FORM") return this.parseForm();
        if (t.value === "PERFORM") return this.parsePerform();
        if (t.value === "CASE") return this.parseCase();
        if (t.value === "LOOP") return this.parseLoop();
        if (t.value === "WHILE") return this.parseWhile();
        if (t.value === "LABEL") return this.parseLabel();
        if (t.value === "GOTO") return this.parseGoto();
        if (t.value === "RETURN") { this.next(); if (this.at("op", ".") || this.at("nl") || this.at("eof")) { this.endStmt(); return { t: "Return" }; } var rv = this.parseExpr(); this.endStmt(); return { t: "Return", value: rv }; }
        if (t.value === "EXIT") { this.next(); this.endStmt(); return { t: "Break" }; }
        if (t.value === "CONTINUE") { this.next(); this.endStmt(); return { t: "Skip" }; }
      }
      if (t.kind === "id") {
        if (this.peek(1).kind === "op" && this.peek(1).value === "=") { var name = this.next().value; this.next(); var vv = this.parseExpr(); this.endStmt(); return { t: "Let", name: name, value: vv }; }
        if (this.peek(1).kind === "op" && this.peek(1).value === "." && (this.peek(2).kind === "id" || this.peek(2).kind === "kw") && this.peek(3).kind === "op" && this.peek(3).value === "(") { var call = this.parseCallFromId(); this.endStmt(); return { t: "CallStmt", call: call }; }
      }
      throw new Error("Report: cannot parse statement at " + t.value);
    },
    parseData: function () {
      this.expectKw("DATA");
      if (this.at("op", ":")) this.next();
      var decls = [];
      while (true) {
        this.skipNl();
        var name = this.expect("id").value, init = { t: "Num", value: 0 };
        while (this.peek().kind === "kw" && (this.peek().value === "TYPE" || this.peek().value === "VALUE")) {
          if (this.peek().value === "TYPE") { this.next(); this.next(); }
          else { this.next(); init = this.parseExpr(); }
        }
        decls.push({ t: "Let", name: name, value: init });
        if (this.at("op", ",")) { this.next(); continue; }
        this.endStmt(); return decls;
      }
    },
    parseIf: function () {
      this.expectKw("IF");
      var cond = this.parseExpr(), arms = [], els = null;
      this.endStmt();
      arms.push([cond, this.parseBlockUntil(["ELSEIF", "ELSE", "ENDIF"])]);
      while (this.atKw("ELSEIF")) { this.next(); var ec = this.parseExpr(); this.endStmt(); arms.push([ec, this.parseBlockUntil(["ELSEIF", "ELSE", "ENDIF"])]); }
      if (this.atKw("ELSE")) { this.next(); this.endStmt(); els = this.parseBlockUntil(["ENDIF"]); }
      this.expectKw("ENDIF"); this.endStmt();
      return { t: "If", arms: arms, els: els };
    },
    parseForm: function () {
      this.expectKw("FORM");
      var name = this.expect("id").value, params = null;
      if (this.atKw("USING")) { this.next(); params = this.parseNameListUntilDot(); }
      this.endStmt();
      var body = this.parseBlockUntil(["ENDFORM"]);
      this.expectKw("ENDFORM"); this.endStmt();
      return { t: "Sub", name: name, body: body, params: params && params.length ? params : null };
    },
    parsePerform: function () {
      this.expectKw("PERFORM");
      var name = this.expect("id").value, args = null;
      if (this.atKw("USING")) { this.next(); args = this.parseExprListUntilDot(); }
      this.endStmt();
      return { t: "Gosub", name: name, args: args && args.length ? args : null };
    },
    parseCase: function () {
      this.expectKw("CASE");
      var expr = this.parseExpr(), cases = [], def = null;
      this.endStmt();
      while (!this.atKw("ENDCASE")) {
        this.expectKw("WHEN");
        if (this.atKw("OTHERS")) { this.next(); this.endStmt(); def = this.parseBlockUntil(["ENDCASE"]); break; }
        var val = this.parseExpr(); this.endStmt();
        cases.push([val, this.parseBlockUntil(["WHEN", "ENDCASE"])]);
      }
      this.expectKw("ENDCASE"); this.endStmt();
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseLoop: function () {
      this.expectKw("LOOP"); this.expectKw("AT");
      var count = this.parseExpr(); this.expectKw("INTO"); var v = this.expect("id").value; var where = null;
      if (this.atKw("WHERE")) { this.next(); where = this.parseExpr(); }
      this.endStmt();
      var body = this.parseBlockUntil(["ENDLOOP"]);
      this.expectKw("ENDLOOP"); this.endStmt();
      if (where) body = [{ t: "If", arms: [[where, body]], els: null }];
      return { t: "ForEach", v: v || this.freshTemp(), count: count, body: body };
    },
    parseWhile: function () {
      this.expectKw("WHILE");
      var cond = this.parseExpr();
      this.endStmt();
      var body = this.parseBlockUntil(["ENDWHILE"]);
      this.expectKw("ENDWHILE"); this.endStmt();
      return { t: "While", cond: cond, body: body };
    },
    parseLabel: function () { this.expectKw("LABEL"); var name = this.expect("id").value; this.endStmt(); return { t: "Label", name: name }; },
    parseGoto: function () { this.expectKw("GOTO"); var name = this.expect("id").value; this.endStmt(); return { t: "Goto", label: name }; },
    parseNameListUntilDot: function () { var a = []; while (!this.at("op", ".") && !this.at("eof")) { if (this.at("op", ",")) { this.next(); continue; } a.push(this.expect("id").value); } return a; },
    parseExprListUntilDot: function () { var a = []; while (!this.at("op", ".") && !this.at("eof")) { if (this.at("op", ",")) { this.next(); continue; } a.push(this.parseExpr()); } return a; },
    parseCallFromId: function () { var ns = this.expect("id").value; this.expect("op", "."); var m = this.next(); if (m.kind !== "id" && m.kind !== "kw") throw new Error("Report: expected method after ."); return { t: "Call", ns: ns, method: m.value, args: this.parseArgs() }; },
    parseArgs: function () { this.expect("op", "("); var a = []; if (!this.at("op", ")")) { a.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); a.push(this.parseExpr()); } } this.expect("op", ")"); return a; },
    matchBinop: function () {
      var t = this.peek();
      if (t.kind === "op" && REP_PREC[t.value] !== undefined) return [REP_PREC[t.value], 1, REP_CMP[t.value] ? "cmp" : "bin", REP_CMP[t.value] || (t.value === "%" ? "MOD" : t.value)];
      if (t.kind === "kw" && t.value === "AND") return [2, 1, "bin", "AND"];
      if (t.kind === "kw" && t.value === "OR") return [1, 1, "bin", "OR"];
      if (t.kind === "kw" && t.value === "MOD") return [6, 1, "bin", "MOD"];
      return null;
    },
    parseExpr: function (minp) {
      minp = minp || 0; var left = this.parseUnary();
      while (true) {
        var m = this.matchBinop();
        if (!m || m[0] < minp) break;
        this.next(); var right = this.parseExpr(m[0] + 1);
        left = (m[2] === "cmp") ? { t: "Cmp", cond: m[3], lhs: left, rhs: right } : { t: "Bin", op: m[3], lhs: left, rhs: right };
      }
      return left;
    },
    parseUnary: function () { var t = this.peek(); if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; } if (t.kind === "kw" && t.value === "NOT") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; } return this.parseAtom(); },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.expect("op", ")"); return e; }
      if (t.kind === "id") {
        if (this.at("op", ".") && (this.peek(1).kind === "id" || this.peek(1).kind === "kw") && this.peek(2).kind === "op" && this.peek(2).value === "(") { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; }
        if (this.at("op", "(")) return { t: "Call", ns: null, method: t.value, args: this.parseArgs() };
        return { t: "Var", name: t.value };
      }
      throw new Error("Report: unexpected token " + t.value);
    }
  };
  function compileReport(src) { return new BLowerer().lowerProgram(new RepParser(reptokenize(src)).parseProgram()); }

  // ---- Functional frontend --------------------------------------------------
  var FUN_KW = {};
  ["let","if","then","else","elif","match","with","for","in","do","while","printfn","printf","not","true","false","and","or","return","break","continue","skip","rec","mutable","goto","label"].forEach(function (k) { FUN_KW[k] = 1; });
  var FUN_TWO = { "==":1, "!=":1, "<>":1, "<=":1, ">=":1, "|>":1, "->":1, "..":1, "&&":1, "||":1 };
  var FUN_ONE = "+-*/%()<>=,.:|";
  var FUN_CMP = { "=":"EQ", "==":"EQ", "!=":"NE", "<>":"NE", "<":"LT", ">":"GT", "<=":"LE", ">=":"GE" };
  var FUN_PREC = { or:1, "||":1, and:2, "&&":2, "=":3, "==":3, "!=":3, "<>":3, "<":3, ">":3, "<=":3, ">=":3, "+":5, "-":5, "*":6, "/":6, "%":6 };
  var FUN_BIN = { "+":"+", "-":"-", "*":"*", "/":"/", "%":"MOD", and:"AND", or:"OR", "&&":"AND", "||":"OR" };
  function ftokenizeLine(text, out, kwset, two, one, who, lineStart) {
    var i = 0, n = text.length;
    while (i < n) {
      var c = text[i], start = lineStart + i;
      if (c === " " || c === "\t") { i++; continue; }
      if (c === "/" && text[i + 1] === "/") break;
      if (isDigit(c)) {
        var j = i;
        if (c === "0" && (text[j + 1] === "x" || text[j + 1] === "X")) { j += 2; while (j < n && /[0-9a-fA-F]/.test(text[j])) j++; }
        else while (j < n && isDigit(text[j])) j++;
        out.push({ kind: "num", value: text.slice(i, j), pos: start }); i = j; continue;
      }
      if (isAlpha(c) || c === "_") {
        var j2 = i; while (j2 < n && (isAlnum(text[j2]) || text[j2] === "_")) j2++;
        var w = text.slice(i, j2), lw = w.toLowerCase();
        out.push({ kind: kwset[lw] ? "kw" : "id", value: w, pos: start }); i = j2; continue;
      }
      if (c === '"') {
        var j3 = i + 1, b = "";
        while (j3 < n && text[j3] !== '"') {
          if (text[j3] === "\\" && j3 + 1 < n) { var nx = text[j3 + 1]; b += ({ n: "\n", t: "\t", "\\": "\\", '"': '"' }[nx] || nx); j3 += 2; }
          else b += text[j3++];
        }
        if (j3 >= n) throw new Error("Functional: unterminated string");
        out.push({ kind: "str", value: b, pos: start }); i = j3 + 1; continue;
      }
      var tw = text.slice(i, i + 2);
      if (two[tw]) { out.push({ kind: "op", value: tw, pos: start }); i += 2; continue; }
      if (one.indexOf(c) >= 0) { out.push({ kind: "op", value: c, pos: start }); i++; continue; }
      throw new Error("Functional: unexpected char " + JSON.stringify(c));
    }
  }
  function funtokenize(src) {
    var out = [], indents = [0], lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n"), offset = 0;
    for (var li = 0; li < lines.length; li++) {
      var line = lines[li], lineStart = offset; offset += line.length + 1;
      var stripped = line.replace(/^[ \t]+/, "");
      if (stripped === "" || stripped.indexOf("//") === 0) continue;
      var indent = line.length - stripped.length;
      if (indent > indents[indents.length - 1]) { indents.push(indent); out.push({ kind: "indent", value: "", pos: lineStart }); }
      else {
        while (indent < indents[indents.length - 1]) { indents.pop(); out.push({ kind: "dedent", value: "", pos: lineStart }); }
        if (indent !== indents[indents.length - 1]) throw new Error("Functional: inconsistent indentation");
      }
      var before = out.length;
      ftokenizeLine(line, out, FUN_KW, FUN_TWO, FUN_ONE, "Functional", lineStart);
      if (out.length > before) out.push({ kind: "newline", value: "", pos: lineStart });
    }
    while (indents.length > 1) { indents.pop(); out.push({ kind: "dedent", value: "", pos: offset }); }
    out.push({ kind: "eof", value: "", pos: offset });
    return out;
  }
  function FunCallTarget(ns, method) { this.ns = ns; this.method = method; }
  function FunParser(toks) { this.toks = toks; this.i = 0; }
  FunParser.prototype = {
    peek: function (k) { var j = this.i + (k || 0); return j < this.toks.length ? this.toks[j] : this.toks[this.toks.length - 1]; },
    next: function () { return this.toks[this.i++]; },
    at: function (kind, value) { var t = this.peek(); return t.kind === kind && (value === undefined || t.value === value); },
    atKw: function () { var t = this.peek(); if (t.kind !== "kw") return false; var w = t.value.toLowerCase(); for (var k = 0; k < arguments.length; k++) if (w === arguments[k]) return true; return false; },
    expect: function (kind, value) { var t = this.next(); if (t.kind !== kind || (value !== undefined && t.value !== value)) throw new Error("Functional: expected " + (value !== undefined ? value : kind) + " got " + t.value); return t; },
    expectKw: function (name) { var t = this.next(); if (!(t.kind === "kw" && t.value.toLowerCase() === name)) throw new Error("Functional: expected " + name + " got " + t.value); },
    parseProgram: function () { var out = []; while (!this.at("eof")) pushAst(out, this.parseStmt(true)); return out; },
    parseSuite: function (allowFunc) {
      this.expect("newline"); this.expect("indent");
      var out = [];
      while (!this.at("dedent")) { if (this.at("eof")) throw new Error("Functional: EOF in block"); pushAst(out, this.parseStmt(allowFunc)); }
      this.expect("dedent"); return out;
    },
    parseStmtBody: function () {
      if (this.at("newline")) return this.parseSuite(false);
      var st = this.parseStmt(false);
      if (st == null) return [];
      return Array.isArray(st) ? st : [st];
    },
    parseFunctionBody: function () {
      if (!this.at("newline")) { var expr = this.parseExpr(); this.expect("newline"); return [{ t: "Return", value: expr }]; }
      this.expect("newline"); this.expect("indent");
      var body = [];
      while (!this.at("dedent")) {
        if (this.lineStartsExpr()) {
          var ex = this.parseExpr(); this.expect("newline"); body.push({ t: "Return", value: ex });
          if (!this.at("dedent")) throw new Error("Functional: expression result must be final in function body");
          break;
        }
        pushAst(body, this.parseStmt(false));
      }
      this.expect("dedent");
      if (!body.length || body[body.length - 1].t !== "Return") body.push({ t: "Return" });
      return body;
    },
    parseStmt: function (allowFunc) {
      if (this.at("newline")) { this.next(); return null; }
      var start = this.peek().pos, node = this._parseStmt(allowFunc);
      return markAst(node, start);
    },
    _parseStmt: function (allowFunc) {
      var t = this.peek();
      if (t.kind === "kw") {
        var kw = t.value.toLowerCase();
        if (kw === "let") return this.parseLetStmt(allowFunc);
        if (kw === "printfn" || kw === "printf") { this.next(); var pv = this.parseExpr(); this.expect("newline"); return { t: "Print", value: pv }; }
        if (kw === "if") return this.parseIfStmt();
        if (kw === "while") return this.parseWhileStmt();
        if (kw === "for") return this.parseForStmt();
        if (kw === "match") return this.parseMatchStmt();
        if (kw === "label") { this.next(); var lname = this.expect("id").value; this.expect("newline"); return { t: "Label", name: lname }; }
        if (kw === "goto") { this.next(); var gname = this.expect("id").value; this.expect("newline"); return { t: "Goto", label: gname }; }
        if (kw === "return") { this.next(); if (this.at("newline")) { this.next(); return { t: "Return" }; } var rv = this.parseExpr(); this.expect("newline"); return { t: "Return", value: rv }; }
        if (kw === "break") { this.next(); this.expect("newline"); return { t: "Break" }; }
        if (kw === "continue" || kw === "skip") { this.next(); this.expect("newline"); return { t: "Skip" }; }
      }
      if (t.kind === "id" && this.peek(1).kind === "op" && this.peek(1).value === "=") {
        var name = this.next().value;
        this.next();
        var value = this.parseExpr();
        this.expect("newline");
        return { t: "Assign", name: name, value: value };
      }
      var expr = this.parseExpr();
      this.expect("newline");
      if (expr.t === "Call") return { t: "CallStmt", call: expr };
      throw new Error("Functional: expression statement must be a call");
    },
    parseLetStmt: function (allowFunc) {
      this.expectKw("let");
      if (this.atKw("rec")) this.next();
      if (this.atKw("mutable")) this.next();
      var name = this.expect("id").value, params = [];
      while (this.at("id")) params.push(this.next().value);
      var zeroParam = false;
      if (this.at("op", "(") && this.peek(1).kind === "op" && this.peek(1).value === ")") { this.next(); this.next(); zeroParam = true; }
      this.expect("op", "=");
      if (params.length || zeroParam) {
        if (!allowFunc) throw new Error("Functional: function definitions only allowed at top level");
        return { t: "Sub", name: name, params: params.length ? params : null, body: this.parseFunctionBody() };
      }
      var value = this.parseBindingExpr();
      this.expect("newline");
      return { t: "Let", name: name, value: value };
    },
    parseBindingExpr: function () {
      if (this.at("newline")) { this.expect("newline"); this.expect("indent"); var expr = this.parseExpr(); this.expect("newline"); this.expect("dedent"); return expr; }
      return this.parseExpr();
    },
    parseIfStmt: function () {
      this.expectKw("if"); var cond = this.parseExpr(); this.expectKw("then");
      var arms = [[cond, this.parseStmtBody()]], els = null;
      while (this.atKw("elif")) { this.next(); var c2 = this.parseExpr(); this.expectKw("then"); arms.push([c2, this.parseStmtBody()]); }
      if (this.atKw("else")) { this.next(); els = this.parseStmtBody(); }
      return { t: "If", arms: arms, els: els };
    },
    parseWhileStmt: function () { this.expectKw("while"); var cond = this.parseExpr(); this.expectKw("do"); return { t: "While", cond: cond, body: this.parseStmtBody() }; },
    parseForStmt: function () {
      this.expectKw("for"); var v = this.expect("id").value; this.expectKw("in");
      var start = this.parseExpr();
      if (this.at("op", "..")) { this.next(); var end = this.parseExpr(); this.expectKw("do"); return { t: "ForTo", v: v, start: start, end: end, step: null, body: this.parseStmtBody() }; }
      this.expectKw("do"); return { t: "ForEach", v: v, count: start, body: this.parseStmtBody() };
    },
    parseMatchStmt: function () {
      this.expectKw("match"); var expr = this.parseExpr(); this.expectKw("with"); this.expect("newline");
      var cases = [], def = null;
      while (this.at("op", "|")) {
        this.next();
        if (this.at("id", "_")) { this.next(); this.expect("op", "->"); def = this.parseStmtBody(); continue; }
        var val = this.parseExpr(); this.expect("op", "->"); cases.push([val, this.parseStmtBody()]);
      }
      if (!cases.length && !def) throw new Error("Functional: expected at least one match arm");
      return { t: "Switch", expr: expr, cases: cases, def: def };
    },
    parseExpr: function () { if (this.atKw("if")) return this.parseIfExpr(); return this.parsePipe(); },
    parseIfExpr: function () { this.expectKw("if"); var cond = this.parsePipe(); this.expectKw("then"); var th = this.parseExpr(); this.expectKw("else"); var el = this.parseExpr(); return { t: "Ternary", cond: cond, then: th, els: el }; },
    parsePipe: function () { var left = this.parseBinary(0); while (this.at("op", "|>")) { this.next(); left = this.applyPipe(left, this.parseApplication()); } return left; },
    applyPipe: function (lhs, rhs) {
      if (rhs.t === "Var") return { t: "Call", ns: null, method: rhs.name, args: [lhs] };
      if (rhs.t === "Call") return { t: "Call", ns: rhs.ns, method: rhs.method, args: [lhs].concat(rhs.args || []) };
      if (rhs instanceof FunCallTarget) return { t: "Call", ns: rhs.ns, method: rhs.method, args: [lhs] };
      throw new Error("Functional: invalid pipe target");
    },
    parseBinary: function (minp) {
      var left = this.parseApplication();
      while (true) {
        var t = this.peek(), op = null;
        if (t.kind === "op" && FUN_PREC[t.value] !== undefined) op = t.value;
        else if (t.kind === "kw" && FUN_PREC[t.value.toLowerCase()] !== undefined) op = t.value.toLowerCase();
        if (op == null || FUN_PREC[op] < minp) break;
        this.next(); var right = this.parseBinary(FUN_PREC[op] + 1);
        left = FUN_CMP[op] ? { t: "Cmp", cond: FUN_CMP[op], lhs: left, rhs: right } : { t: "Bin", op: FUN_BIN[op], lhs: left, rhs: right };
      }
      return left;
    },
    parseApplication: function () {
      var left = this.parseUnary();
      while (this.isAppArgStart(this.peek()) && this.callableExpr(left)) {
        var arg = this.parseUnary();
        if (left.t === "Var") left = { t: "Call", ns: null, method: left.name, args: [arg] };
        else if (left instanceof FunCallTarget) left = { t: "Call", ns: left.ns, method: left.method, args: [arg] };
        else if (left.t === "Call") left.args.push(arg);
      }
      if (left instanceof FunCallTarget) return { t: "Call", ns: left.ns, method: left.method, args: [] };
      return left;
    },
    parseUnary: function () { var t = this.peek(); if (t.kind === "op" && t.value === "-") { this.next(); return { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: this.parseUnary() }; } if (t.kind === "kw" && t.value.toLowerCase() === "not") { this.next(); return { t: "Cmp", cond: "EQ", lhs: this.parseUnary(), rhs: { t: "Num", value: 0 } }; } return this.parseAtom(); },
    parseAtom: function () {
      var t = this.next();
      if (t.kind === "num") return { t: "Num", value: numval(t.value) };
      if (t.kind === "str") return { t: "Str", value: t.value };
      if (t.kind === "kw" && (t.value.toLowerCase() === "true" || t.value.toLowerCase() === "false")) return { t: "Num", value: t.value.toLowerCase() === "true" ? 1 : 0 };
      if (t.kind === "op" && t.value === "(") { var e = this.parseExpr(); this.expect("op", ")"); return e; }
      if (t.kind === "id") {
        if (this.at("op", ".")) { this.next(); var m = this.next(); if (m.kind !== "id" && m.kind !== "kw") throw new Error("Functional: expected method after ."); if (this.at("op", "(")) return { t: "Call", ns: t.value, method: m.value, args: this.parseParenArgs() }; return new FunCallTarget(t.value, m.value); }
        if (this.at("op", "(")) return { t: "Call", ns: null, method: t.value, args: this.parseParenArgs() };
        return { t: "Var", name: t.value };
      }
      throw new Error("Functional: unexpected token " + t.value);
    },
    parseParenArgs: function () { this.expect("op", "("); var a = []; if (!this.at("op", ")")) { a.push(this.parseExpr()); while (this.at("op", ",")) { this.next(); a.push(this.parseExpr()); } } this.expect("op", ")"); return a; },
    callableExpr: function (node) { return !!node && (node.t === "Var" || node.t === "Call" || node instanceof FunCallTarget); },
    isAppArgStart: function (t) { if (t.kind === "num" || t.kind === "str" || t.kind === "id") return true; if (t.kind === "op" && t.value === "(") return true; return t.kind === "kw" && ["true","false","not"].indexOf(t.value.toLowerCase()) >= 0; },
    lineStartsExpr: function () { var t = this.peek(); if (t.kind === "id" && this.peek(1).kind === "op" && this.peek(1).value === "=") return false; if (t.kind === "num" || t.kind === "str" || t.kind === "id") return true; if (t.kind === "op" && (t.value === "(" || t.value === "-")) return true; return t.kind === "kw" && ["if","true","false","not"].indexOf(t.value.toLowerCase()) >= 0; }
  };
  function compileFunctional(src) { return new BLowerer().lowerProgram(new FunParser(funtokenize(src)).parseProgram()); }

  function compileIL(src, lang) {
    return (lang === "basic") ? compileBasic(src)
         : (lang === "python") ? compilePython(src)
         : (lang === "english") ? compileEnglish(src)
         : (lang === "cobol") ? compileCobol(src)
         : (lang === "report") ? compileReport(src)
         : (lang === "functional") ? compileFunctional(src)
         : compileC(src);
  }

  // ── Cross-language translator ──────────────────────────────────────────
  function translate(src, fromLang, toLang) {
    if (fromLang === toLang) return src;
    var ast;
    try {
      if (fromLang === "c") ast = new CParser(ctokenize(src)).parseProgram();
      else if (fromLang === "basic") ast = new BParser(btokenize(src)).parseProgram();
      else if (fromLang === "python") ast = new PyParser(pytokenize(src)).parseProgram();
      else if (fromLang === "english") ast = new EnParser(entokenize(src)).parseProgram();
      else if (fromLang === "cobol") ast = new CobParser(cobtokenize(src)).parseProgram();
      else if (fromLang === "report") ast = new RepParser(reptokenize(src)).parseProgram();
      else if (fromLang === "functional") ast = new FunParser(funtokenize(src)).parseProgram();
      else return src;
    } catch (e) { return src; }
    if (toLang === "c") return astToC(ast);
    if (toLang === "basic") return astToBasic(ast);
    if (toLang === "python") return astToPython(ast);
    if (toLang === "english") return astToEnglish(ast);
    if (toLang === "cobol") return astToCobol(ast);
    if (toLang === "report") return astToReport(ast);
    if (toLang === "functional") return astToFunctional(ast);
    return src;
  }
  function exprStr(e, L) {
    if (!e) return "0";
    if (e.t === "Num") return String(e.value);
    if (e.t === "Str") {
      var q = (L === "report" || L === "cobol") ? "'" : '"';
      var s = String(e.value).replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\t/g, "\\t");
      s = (q === "'") ? s.replace(/'/g, "\\'") : s.replace(/"/g, '\\"');
      return q + s + q;
    }
    if (e.t === "Var") return targetName(e.name, L);
    if (e.t === "Call") {
      var p = e.ns ? (e.ns + ".") : "";
      return p + e.method + "(" + (e.args || []).map(function (a) { return exprStr(a, L); }).join(", ") + ")";
    }
    if (e.t === "Bin") {
      var op = e.op;
      if (L === "basic" || L === "report" || L === "cobol") {
        if (op === "%" || op === "MOD") op = "MOD";
        if (op === "&&" || op === "AND") op = "AND";
        if (op === "||" || op === "OR") op = "OR";
      } else if (L === "python" || L === "functional" || L === "english") {
        if (op === "&&" || op === "AND") op = "and";
        else if (op === "||" || op === "OR") op = "or";
        else if (op === "MOD") op = (L === "english" ? "modulo" : "%");
      }
      return exprStr(e.lhs, L) + " " + op + " " + exprStr(e.rhs, L);
    }
    if (e.t === "Cmp") {
      var m = { EQ:"==", NE:"!=", LT:"<", GT:">", LE:"<=", GE:">=" };
      var bm = { EQ:"=", NE:"<>", LT:"<", GT:">", LE:"<=", GE:">=" };
      var map = (L === "basic" || L === "report" || L === "cobol") ? bm : m;
      return exprStr(e.lhs, L) + " " + (map[e.cond] || e.cond) + " " + exprStr(e.rhs, L);
    }
    if (e.t === "Ternary") {
      if (L === "basic") return "IIF(" + exprStr(e.cond, L) + ", " + exprStr(e.then, L) + ", " + exprStr(e.els, L) + ")";
      if (L === "python") return exprStr(e.then, L) + " if " + exprStr(e.cond, L) + " else " + exprStr(e.els, L);
      if (L === "functional") return "if " + exprStr(e.cond, L) + " then " + exprStr(e.then, L) + " else " + exprStr(e.els, L);
      if (L === "english") return exprStr(e.then, L) + " if " + exprStr(e.cond, L) + " otherwise " + exprStr(e.els, L);
      return "(" + exprStr(e.cond, L) + " ? " + exprStr(e.then, L) + " : " + exprStr(e.els, L) + ")";
    }
    if (e.t === "FieldRef") return "Storage.GetField(" + exprStr({ t: "Str", value: e.field }, L) + ")";
    if (e.t === "IncDec") {
      var name = incDecName(e), delta = incDecDelta(e);
      if (!name) return "?";
      name = targetName(name, L);
      if (L === "c") return e.prefix ? (delta >= 0 ? "++" : "--") + name : name + (delta >= 0 ? "++" : "--");
      return name + (delta >= 0 ? " + " : " - ") + Math.abs(delta);
    }
    return "?";
  }
  function splitProgDefs(prog) {
    var defs = [], body = [];
    prog.forEach(function (s) { if (s.t === "Func" || s.t === "Sub") defs.push(s); else body.push(s); });
    return { defs: defs, body: body };
  }
  function incDecName(s) {
    if (!s) return null;
    if (s.name) return s.name;
    if (s.target && s.target.t === "Var") return s.target.name;
    return null;
  }
  function incDecDelta(s) {
    if (!s) return 0;
    if (s.delta != null) return s.delta;
    if (s.op === "++") return 1;
    if (s.op === "--") return -1;
    return 0;
  }
  function targetName(name, L) {
    name = String(name || "");
    var up = name.toUpperCase();
    if (L === "cobol" && COB_KW[up]) return "v_" + name;
    if (L === "report" && REP_KW[up]) return "v_" + name;
    return name;
  }
  function targetLabel(name, L) { return targetName(name, L); }
  function collectFieldObjs(e, out) {
    if (!e) return;
    if (e.t === "FieldRef") { out.push(e.obj); return; }
    if (e.t === "Bin" || e.t === "Cmp") { collectFieldObjs(e.lhs, out); collectFieldObjs(e.rhs, out); return; }
    if (e.t === "Ternary") { collectFieldObjs(e.cond, out); collectFieldObjs(e.then, out); collectFieldObjs(e.els, out); return; }
    if (e.t === "Unary") { collectFieldObjs(e.operand, out); return; }
    if (e.t === "Call") { (e.args || []).forEach(function (a) { collectFieldObjs(a, out); }); return; }
    if (e.t === "IncDec") collectFieldObjs(e.target, out);
  }
  function singleFieldObj(e) {
    var out = [];
    collectFieldObjs(e, out);
    if (!out.length) return null;
    for (var i = 1; i < out.length; i++) if (out[i] !== out[0]) return null;
    return out[0];
  }
  function doLoopCondExpr(s) {
    var cond = s.botCond || s.cond || { t: "Num", value: 1 };
    return (s.until || s.botUntil) ? { t: "Cmp", cond: "EQ", lhs: cond, rhs: { t: "Num", value: 0 } } : cond;
  }
  function cForClause(s) {
    if (!s) return "";
    if (s.t === "ExprStmt") return cForClause(s.expr);
    if (s.t === "Decl" || s.t === "Let" || s.t === "Dim") return "int " + s.name + " = " + exprStr(s.init || s.value || { t: "Num", value: 0 }, "c");
    if (s.t === "Assign") return s.name + " = " + exprStr(s.value, "c");
    if (s.t === "IncDec") { var n = incDecName(s); return n ? (n + (incDecDelta(s) >= 0 ? "++" : "--")) : ""; }
    return exprStr(s, "c");
  }
  function forLoopInfo(s) {
    if (!s || s.t !== "For" || !s.init || !s.cond) return null;
    var cmpMap = { "<": "LT", "<=": "LE", ">": "GT", ">=": "GE", "==": "EQ" };
    var cmp = s.cond.cond || cmpMap[s.cond.op] || s.cond.op;
    var lhs = s.cond.lhs, rhs = s.cond.rhs;
    var v = (s.init && s.init.name) || (lhs && lhs.t === "Var" ? lhs.name : null) || incDecName(s.step);
    if (!v || !rhs) return null;
    var start = s.init.init || s.init.value || { t: "Num", value: 0 };
    var end = rhs;
    if (cmp === "LT") end = { t: "Bin", op: "-", lhs: rhs, rhs: { t: "Num", value: 1 } };
    else if (cmp !== "LE") return null;
    var stepNode = s.step && s.step.t === "ExprStmt" ? s.step.expr : s.step;
    var step = { t: "Num", value: 1 };
    if (stepNode && stepNode.t === "Assign" && stepNode.name === v && stepNode.value && stepNode.value.t === "Bin" && stepNode.value.lhs && stepNode.value.lhs.t === "Var" && stepNode.value.lhs.name === v) {
      if (stepNode.value.op === "+") step = stepNode.value.rhs;
      else if (stepNode.value.op === "-") step = { t: "Bin", op: "-", lhs: { t: "Num", value: 0 }, rhs: stepNode.value.rhs };
    } else if (stepNode && stepNode.t === "IncDec") {
      step = { t: "Num", value: incDecDelta(stepNode) || 1 };
    }
    return { v: v, start: start, end: end, step: step };
  }
  function stmtPlainCall(s) {
    var call = null;
    if (s && s.t === "CallStmt") call = s.call;
    else if (s && s.t === "ExprStmt") call = s.expr;
    return call && call.t === "Call" && !call.ns ? call : null;
  }
  function astToC(prog) {
    var parts = splitProgDefs(prog), l = [];
    parts.defs.forEach(function (fn) {
      l.push("void " + fn.name + "(" + (fn.params || []).map(function (p) { return "int " + p; }).join(", ") + ") {");
      (fn.body || []).forEach(function (s) { l.push(sC(s, 1)); });
      l.push("}");
    });
    parts.body.forEach(function (s) { l.push(sC(s, 0)); });
    return l.join("\n");
  }
  function sC(s,d){var p="    ".repeat(d||0);if(!s)return"";if(s.t==="Decl"||s.t==="Let"||s.t==="Dim")return p+"int "+s.name+" = "+exprStr(s.init||s.value||{t:"Num",value:0},"c")+";";if(s.t==="Assign")return p+s.name+" = "+exprStr(s.value,"c")+";";if(s.t==="IncDec")return p+s.name+(s.delta>0?"++":"--")+";";if(s.t==="If"){var arms=s.arms||[[s.cond,s.then]];var els=s.els;var o=p+"if ("+exprStr(arms[0][0],"c")+") {\n";arms[0][1].forEach(function(st){o+=sC(st,d+1)+"\n";});o+=p+"}";for(var i=1;i<arms.length;i++){o+=" else if ("+exprStr(arms[i][0],"c")+") {\n";arms[i][1].forEach(function(st){o+=sC(st,d+1)+"\n";});o+=p+"}";}if(els){o+=" else {\n";(Array.isArray(els)?els:[els]).forEach(function(st){o+=sC(st,d+1)+"\n";});o+=p+"}";}return o;}if(s.t==="While"){var o=p+"while ("+exprStr(s.cond,"c")+") {\n";s.body.forEach(function(st){o+=sC(st,d+1)+"\n";});return o+p+"}";}if(s.t==="ForTo")return p+"for ("+s.v+" = "+exprStr(s.start,"c")+"; "+s.v+" <= "+exprStr(s.end,"c")+"; "+s.v+"++) {\n"+s.body.map(function(st){return sC(st,d+1);}).join("\n")+"\n"+p+"}";if(s.t==="ForEach")return p+"for ("+s.v+" = 0; "+s.v+" < "+exprStr(s.count,"c")+"; "+s.v+"++) {\n"+s.body.map(function(st){return sC(st,d+1);}).join("\n")+"\n"+p+"}";if(s.t==="Return")return p+"return"+(s.value?" "+exprStr(s.value,"c"):"")+";";if(s.t==="Break")return p+"break;";if(s.t==="Skip"||s.t==="Continue")return p+"continue;";if(s.t==="Goto")return p+"goto "+s.label+";";if(s.t==="Label")return p+s.name+":";if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"c");});return p+s.name+"("+a.join(", ")+");";}if(s.t==="CallStmt")return p+exprStr(s.call,"c")+";";if(s.t==="ExprStmt"){if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print")return p+"print("+exprStr(s.expr.args[0],"c")+");";return s.expr?p+exprStr(s.expr,"c")+";":"";}if(s.t==="Print")return p+"print("+exprStr(s.value,"c")+");";if(s.t==="Switch"||s.t==="Dispatch"){var o=p+(s.t==="Dispatch"?"dispatch":"switch")+" ("+exprStr(s.expr,"c")+") {\n";(s.cases||[]).forEach(function(c){o+=p+"    case "+exprStr(c[0],"c")+": ";c[1].forEach(function(st){o+=sC(st,d+2)+"\n";});o+=p+"    break;\n";});if(s.def||s.els){o+=p+"    default: ";(s.def||s.els||[]).forEach(function(st){o+=sC(st,d+2)+"\n";});}return o+p+"}";}if(s.t==="DoLoop"||s.t==="DoWhile"){var cond=s.botCond||s.cond||{t:"Num",value:1};return p+"do {\n"+(s.body||[]).map(function(st){return sC(st,d+1);}).join("\n")+"\n"+p+"} while ("+exprStr(cond,"c")+");";}if(s.t==="For")return p+"for ("+cForClause(s.init)+"; "+(s.cond?exprStr(s.cond,"c"):"")+"; "+cForClause(s.step)+") {\n"+(s.body||[]).map(function(st){return sC(st,d+1);}).join("\n")+"\n"+p+"}";if(s.t==="ServerMain")return (s.body||[]).map(function(st){return sC(st,d);}).join("\n");return p+"// "+s.t;}
  function astToBasic(prog) {
    var parts = splitProgDefs(prog), l = [];
    parts.defs.forEach(function (f) {
      var pa = (f.params || []).length ? "(" + f.params.join(", ") + ")" : "";
      l.push("SUB " + (f.name || "").toUpperCase() + pa);
      (f.body || []).forEach(function (s) { l.push(sB(s, 1)); });
      l.push("ENDSUB");
    });
    parts.body.forEach(function (s) { l.push(sB(s, 0)); });
    return l.join("\n");
  }
  function sB(s,d){var p="    ".repeat(d||0);if(!s)return"";if(s.t==="Decl"||s.t==="Dim")return p+"DIM "+(s.name||"").toUpperCase()+(s.init!=null?" = "+exprStr(s.init,"basic"):"");if(s.t==="Let"||s.t==="Assign")return p+(s.name||"").toUpperCase()+" = "+exprStr(s.value,"basic");if(s.t==="IncDec")return p+(s.delta>0?"INC ":"DEC ")+(s.name||"").toUpperCase();if(s.t==="If"){var arms=s.arms||[[s.cond,s.then]];var els=s.els;var o=p+"IF "+exprStr(arms[0][0],"basic")+" THEN\n";arms[0][1].forEach(function(st){o+=sB(st,d+1)+"\n";});for(var i=1;i<arms.length;i++){o+=p+"ELSEIF "+exprStr(arms[i][0],"basic")+" THEN\n";arms[i][1].forEach(function(st){o+=sB(st,d+1)+"\n";});}if(els){o+=p+"ELSE\n";(Array.isArray(els)?els:[els]).forEach(function(st){o+=sB(st,d+1)+"\n";});}return o+p+"ENDIF";}if(s.t==="While"){var o=p+"WHILE "+exprStr(s.cond,"basic")+"\n";s.body.forEach(function(st){o+=sB(st,d+1)+"\n";});return o+p+"ENDWHILE";}if(s.t==="ForTo")return p+"FOR "+(s.v||"").toUpperCase()+" = "+exprStr(s.start,"basic")+" TO "+exprStr(s.end,"basic")+"\n"+s.body.map(function(st){return sB(st,d+1);}).join("\n")+"\n"+p+"NEXT";if(s.t==="ForEach")return p+"FOREACH "+(s.v||"").toUpperCase()+" IN "+exprStr(s.count,"basic")+"\n"+s.body.map(function(st){return sB(st,d+1);}).join("\n")+"\n"+p+"ENDFOREACH";if(s.t==="Return")return p+"RETURN"+(s.value?" "+exprStr(s.value,"basic"):"");if(s.t==="Break")return p+"BREAK";if(s.t==="Skip"||s.t==="Continue")return p+"SKIP";if(s.t==="Goto")return p+"GOTO "+(s.label||"").toUpperCase();if(s.t==="Label")return p+(s.name||"").toUpperCase()+":";if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"basic");});return p+"GOSUB "+(s.name||"").toUpperCase()+(a.length?"("+a.join(", ")+")":"");}if(s.t==="CallStmt")return p+exprStr(s.call,"basic");if(s.t==="ExprStmt"){if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print")return p+"PRINT "+exprStr(s.expr.args[0],"basic");return s.expr?p+exprStr(s.expr,"basic"):"";}if(s.t==="Print")return p+"PRINT "+exprStr(s.value,"basic");if(s.t==="Switch"||s.t==="Dispatch"){var o=p+(s.t==="Dispatch"?"DISPATCH ":"SWITCH ")+exprStr(s.expr,"basic")+"\n";(s.cases||[]).forEach(function(c){o+=p+"    CASE "+exprStr(c[0],"basic")+"\n";c[1].forEach(function(st){o+=sB(st,d+2)+"\n";});});if(s.def||s.els){o+=p+"    DEFAULT\n";(s.def||s.els||[]).forEach(function(st){o+=sB(st,d+2)+"\n";});}return o+p+"END"+(s.t==="Dispatch"?"DISPATCH":"SWITCH");}if(s.t==="DoLoop"||s.t==="DoWhile"){var cond=s.botCond||s.cond||{t:"Num",value:1};return p+"DO\n"+(s.body||[]).map(function(st){return sB(st,d+1);}).join("\n")+"\n"+p+"LOOP UNTIL "+exprStr(cond,"basic");}if(s.t==="For"){var f=forLoopInfo(s);if(f)return p+"FOR "+f.v.toUpperCase()+" = "+exprStr(f.start,"basic")+" TO "+exprStr(f.end,"basic")+"\n"+(s.body||[]).map(function(st){return sB(st,d+1);}).join("\n")+"\n"+p+"NEXT";}if(s.t==="ServerMain")return (s.body||[]).map(function(st){return sB(st,d);}).join("\n");return p+"' "+s.t;}
  function astToPython(prog){var parts=splitProgDefs(prog),l=[],body=[];parts.defs.forEach(function(f){l.push("def "+f.name+"("+(f.params||[]).join(", ")+"):");(f.body||[]).forEach(function(s){l.push(sP(s,1));});l.push("");});parts.body.forEach(function(s){body.push(sP(s,0));});return l.concat(body).join("\n");}
  function sP(s,d){
    var p="    ".repeat(d||0), v=s&&s.value||s&&s.init||{t:"Num",value:0}, obj;
    if(!s)return"";
    if(s.t==="Decl"||s.t==="Dim"||s.t==="Let"||s.t==="Assign"){obj=singleFieldObj(v);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"python")+")\n":"")+p+s.name+" = "+exprStr(v,"python");}
    if(s.t==="FieldAssign"){var pyMethod=s.value&&s.value.t==="Str"?"SetFieldStr":"SetField";return p+"Storage.EditCard("+exprStr({t:"Var",name:s.obj},"python")+")\n"+p+"Storage."+pyMethod+"("+exprStr({t:"Str",value:s.field},"python")+", "+exprStr(s.value,"python")+")";}
    if(s.t==="IncDec")return p+incDecName(s)+" "+(incDecDelta(s)>=0?"+=":"-=")+" "+Math.abs(incDecDelta(s));
    if(s.t==="If"){var arms=s.arms||[[s.cond,s.then]],els=s.els;var o=p+"if "+exprStr(arms[0][0],"python")+":\n";arms[0][1].forEach(function(st){o+=sP(st,d+1)+"\n";});for(var i=1;i<arms.length;i++){o+=p+"elif "+exprStr(arms[i][0],"python")+":\n";arms[i][1].forEach(function(st){o+=sP(st,d+1)+"\n";});}if(els){o+=p+"else:\n";(Array.isArray(els)?els:[els]).forEach(function(st){o+=sP(st,d+1)+"\n";});}return o.trimEnd();}
    if(s.t==="While"){var o2=p+"while "+exprStr(s.cond,"python")+":\n";s.body.forEach(function(st){o2+=sP(st,d+1)+"\n";});return o2.trimEnd();}
    if(s.t==="ForTo")return p+"for "+s.v+" in range("+exprStr(s.start,"python")+", "+exprStr(s.end,"python")+" + 1):\n"+s.body.map(function(st){return sP(st,d+1);}).join("\n");
    if(s.t==="ForEach")return p+"for "+s.v+" in range("+exprStr(s.count,"python")+"):\n"+s.body.map(function(st){return sP(st,d+1);}).join("\n");
    if(s.t==="Return")return p+"return"+(s.value?" "+exprStr(s.value,"python"):"");
    if(s.t==="Break")return p+"break";
    if(s.t==="Skip"||s.t==="Continue")return p+"continue";
    if(s.t==="Goto")return p+"goto "+targetLabel(s.label,"python");
    if(s.t==="Label")return p+"label "+targetLabel(s.name,"python");
    if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"python");});return p+s.name+"("+a.join(", ")+")";}
    if(s.t==="CallStmt")return p+exprStr(s.call,"python");
    if(s.t==="ExprStmt"){if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print"){obj=singleFieldObj(s.expr.args[0]);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"python")+")\n":"")+p+"print("+exprStr(s.expr.args[0],"python")+")";}if(s.expr&&s.expr.t==="IncDec")return sP(s.expr,d);return s.expr?p+exprStr(s.expr,"python"):"";}
    if(s.t==="Print"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"python")+")\n":"")+p+"print("+exprStr(s.value,"python")+")";}
    if(s.t==="Switch"||s.t==="Dispatch"){var o3=p+"match "+exprStr(s.expr,"python")+":\n";(s.cases||[]).forEach(function(c){o3+=p+"    case "+exprStr(c[0],"python")+":\n";c[1].forEach(function(st){o3+=sP(st,d+2)+"\n";});});if(s.def||s.els){o3+=p+"    case _:\n";(s.def||s.els||[]).forEach(function(st){o3+=sP(st,d+2)+"\n";});}return o3.trimEnd();}
    if(s.t==="DoLoop"||s.t==="DoWhile"){var cond=doLoopCondExpr(s), kw=(s.until||s.botUntil)?"until":"while";return p+"do:\n"+(s.body||[]).map(function(st){return sP(st,d+1);}).join("\n")+"\n"+kw+" "+exprStr((s.botCond||s.cond||{t:"Num",value:1}),"python");}
    if(s.t==="For"){var f=forLoopInfo(s);if(f)return p+"for "+f.v+" in range("+exprStr(f.start,"python")+", "+exprStr(f.end,"python")+" + 1):\n"+(s.body||[]).map(function(st){return sP(st,d+1);}).join("\n");}
    if(s.t==="ServerMain")return (s.body||[]).map(function(st){return sP(st,d);}).join("\n");
    return p+"# "+s.t;
  }
  function astToEnglish(prog) {
    var parts = splitProgDefs(prog), l = [];
    parts.defs.forEach(function (f) {
      var pa = (f.params || []).length ? "(" + f.params.join(", ") + ")" : "";
      l.push("Define " + f.name + pa + ":");
      (f.body || []).forEach(function (s) { l.push(sE(s, 1)); });
    });
    parts.body.forEach(function (s) { l.push(sE(s, 0)); });
    return l.join("\n");
  }
  function sE(s,d){
    var p="    ".repeat(d||0), v=s&&s.value||s&&s.init||{t:"Num",value:0}, obj;
    if(!s)return"";
    if(s.t==="Decl"||s.t==="Dim"||s.t==="Let"||s.t==="Assign"){obj=singleFieldObj(v);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"english")+").\n":"")+p+"Set "+s.name+" to "+exprStr(v,"english")+".";}
    if(s.t==="FieldAssign"){var enMethod=s.value&&s.value.t==="Str"?"SetFieldStr":"SetField";return p+"Storage.EditCard("+exprStr({t:"Var",name:s.obj},"english")+").\n"+p+"Storage."+enMethod+"("+exprStr({t:"Str",value:s.field},"english")+", "+exprStr(s.value,"english")+").";}
    if(s.t==="IncDec")return p+(incDecDelta(s)>=0?"Increase ":"Decrease ")+incDecName(s)+" by "+Math.abs(incDecDelta(s))+".";
    if(s.t==="If"){var arms=s.arms||[[s.cond,s.then]],els=s.els;var o=p+"If "+exprStr(arms[0][0],"english")+":\n";arms[0][1].forEach(function(st){o+=sE(st,d+1)+"\n";});if(els){o+=p+"Otherwise:\n";(Array.isArray(els)?els:[els]).forEach(function(st){o+=sE(st,d+1)+"\n";});}return o.trimEnd();}
    if(s.t==="While"){var o2=p+"While "+exprStr(s.cond,"english")+":\n";s.body.forEach(function(st){o2+=sE(st,d+1)+"\n";});return o2.trimEnd();}
    if(s.t==="ForTo"||s.t==="ForEach"){var n=s.count||s.end,start=s.count?{t:"Num",value:0}:s.start;return p+"For each "+(s.v||"i")+" from "+exprStr(start,"english")+" to "+exprStr(n,"english")+":\n"+(s.body||[]).map(function(st){return sE(st,d+1);}).join("\n");}
    if(s.t==="Return")return p+"Return"+(s.value?" "+exprStr(s.value,"english"):"")+".";
    if(s.t==="Break")return p+"Stop.";
    if(s.t==="Skip"||s.t==="Continue")return p+"Skip.";
    if(s.t==="Goto")return p+"Go to "+targetLabel(s.label,"english")+".";
    if(s.t==="Label")return p+"Label "+targetLabel(s.name,"english")+".";
    if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"english");});return p+"Call "+s.name+(a.length?"("+a.join(", ")+")":"")+".";}
    if(s.t==="CallStmt"){var c=stmtPlainCall(s);if(c)return p+"Call "+c.method+((c.args||[]).length?"("+c.args.map(function(x){return exprStr(x,"english");}).join(", ")+")":"")+".";return p+exprStr(s.call,"english")+".";}
    if(s.t==="ExprStmt"){if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print"){obj=singleFieldObj(s.expr.args[0]);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"english")+").\n":"")+p+"Print "+exprStr(s.expr.args[0],"english")+".";}if(s.expr&&s.expr.t==="IncDec")return sE(s.expr,d);var c2=stmtPlainCall(s);if(c2)return p+"Call "+c2.method+((c2.args||[]).length?"("+c2.args.map(function(x){return exprStr(x,"english");}).join(", ")+")":"")+".";return s.expr?p+exprStr(s.expr,"english")+".":"";}
    if(s.t==="Print"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"english")+").\n":"")+p+"Print "+exprStr(s.value,"english")+".";}
    if(s.t==="Switch"||s.t==="Dispatch"){var o3=p+"Choose "+exprStr(s.expr,"english")+":\n";(s.cases||[]).forEach(function(c){o3+=p+"    When "+exprStr(c[0],"english")+":\n";c[1].forEach(function(st){o3+=sE(st,d+2)+"\n";});});if(s.def||s.els){o3+=p+"    Otherwise:\n";(s.def||s.els||[]).forEach(function(st){o3+=sE(st,d+2)+"\n";});}return o3.trimEnd();}
    if(s.t==="DoLoop"||s.t==="DoWhile"){var kw=(s.until||s.botUntil)?"Until ":"While ";return p+"Repeat:\n"+(s.body||[]).map(function(st){return sE(st,d+1);}).join("\n")+"\n"+p+kw+exprStr((s.botCond||s.cond||{t:"Num",value:1}),"english")+".";}
    if(s.t==="For"){var f=forLoopInfo(s);if(f)return p+"For each "+f.v+" from "+exprStr(f.start,"english")+" to "+exprStr(f.end,"english")+":\n"+(s.body||[]).map(function(st){return sE(st,d+1);}).join("\n");}
    if(s.t==="ServerMain")return (s.body||[]).map(function(st){return sE(st,d);}).join("\n");
    return p+"' "+s.t;
  }

  function astToCobol(prog){
    var main=[],subs=[];
    prog.forEach(function(s){if(s.t==="Func"||s.t==="Sub")subs.push(s);else main.push(s);});
    var l=["IDENTIFICATION DIVISION.","PROGRAM-ID. PICO.","PROCEDURE DIVISION."];
    main.forEach(function(s){l.push(sCob(s,1));});
    subs.forEach(function(f){
      l.push((f.name||"SUB")+".");
      (f.params||[]).forEach(function(p,i){l.push(sCob({t:"Assign",name:p,value:{t:"Var",name:"__arg"+i+"__"}},1));});
      (f.body||[]).forEach(function(s){l.push(sCob(s,1));});
    });
    return l.join("\n");
  }
  function sCob(s,d){
    var p="    ".repeat(d||0), value=s&&(s.value||s.init||{t:"Num",value:0}), obj, arms, els, o, f, once, loop, cond;
    if(!s)return"";
    if(s.t==="Decl"||s.t==="Dim"||s.t==="Let"||s.t==="Assign"){
      var cobName=targetName(s.name,"cobol");
      if(value&&value.t==="Ternary")return p+"IF "+exprStr(value.cond,"cobol")+"\n"+p+"    COMPUTE "+cobName+" = "+exprStr(value.then,"cobol")+".\n"+p+"ELSE\n"+p+"    COMPUTE "+cobName+" = "+exprStr(value.els,"cobol")+".\n"+p+"END-IF.";
      obj=singleFieldObj(value);
      return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"cobol")+").\n":"")+p+"COMPUTE "+cobName+" = "+exprStr(value,"cobol")+".";
    }
    if(s.t==="FieldAssign"){var cobMethod=s.value&&s.value.t==="Str"?"SetFieldStr":"SetField";return p+"Storage.EditCard("+exprStr({t:"Var",name:s.obj},"cobol")+").\n"+p+"Storage."+cobMethod+"("+exprStr({t:"Str",value:s.field},"cobol")+", "+exprStr(s.value,"cobol")+").";}
    if(s.t==="Print"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"cobol")+").\n":"")+p+"DISPLAY "+exprStr(s.value,"cobol")+".";}
    if(s.t==="ExprStmt"){
      if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print"){obj=singleFieldObj(s.expr.args[0]);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"cobol")+").\n":"")+p+"DISPLAY "+exprStr(s.expr.args[0],"cobol")+".";}
      if(s.expr&&s.expr.t==="IncDec")return sCob(s.expr,d);
      var ec=stmtPlainCall(s);
      if(ec&&!(ec.args||[]).length)return p+"PERFORM "+ec.method+".";
      return s.expr?p+exprStr(s.expr,"cobol")+".":"";
    }
    if(s.t==="Gosub")return p+"PERFORM "+s.name+".";
    if(s.t==="CallStmt"){var cc=stmtPlainCall(s);if(cc&&!(cc.args||[]).length)return p+"PERFORM "+cc.method+".";return p+exprStr(s.call,"cobol")+".";}
    if(s.t==="Return")return s.value?p+"COMPUTE __ret__ = "+exprStr(s.value,"cobol")+".\n"+p+"STOP RUN.":p+"STOP RUN.";
    if(s.t==="IncDec"){var cobInc=targetName(incDecName(s),"cobol"),cobDelta=incDecDelta(s);return p+"COMPUTE "+cobInc+" = "+cobInc+(cobDelta>=0?" + ":" - ")+Math.abs(cobDelta)+".";}
    if(s.t==="If"){arms=s.arms||[[s.cond,s.then]];els=s.els;o=p+"IF "+exprStr(arms[0][0],"cobol");arms[0][1].forEach(function(st){o+="\n"+sCob(st,d+1);});for(var i=1;i<arms.length;i++){o+="\n"+p+"ELSE\n"+p+"    IF "+exprStr(arms[i][0],"cobol");arms[i][1].forEach(function(st){o+="\n"+sCob(st,d+2);});o+="\n"+p+"    END-IF.";}if(els){o+="\n"+p+"ELSE";(Array.isArray(els)?els:[els]).forEach(function(st){o+="\n"+sCob(st,d+1);});}return o+"\n"+p+"END-IF.";}
    if(s.t==="While")return p+"PERFORM UNTIL NOT ("+exprStr(s.cond,"cobol")+")\n"+(s.body||[]).map(function(st){return sCob(st,d+1);}).join("\n")+"\n"+p+"END-PERFORM.";
    if(s.t==="ForTo"){var cobV=targetName(s.v,"cobol"),step=s.step?exprStr(s.step,"cobol"):"1";return p+"PERFORM VARYING "+cobV+" FROM "+exprStr(s.start,"cobol")+" BY "+step+" UNTIL "+cobV+" > "+exprStr(s.end,"cobol")+"\n"+(s.body||[]).map(function(st){return sCob(st,d+1);}).join("\n")+"\n"+p+"END-PERFORM.";}
    if(s.t==="ForEach"){var cobEach=targetName(s.v,"cobol");return p+"PERFORM VARYING "+cobEach+" FROM 0 BY 1 UNTIL "+cobEach+" >= "+exprStr(s.count,"cobol")+"\n"+(s.body||[]).map(function(st){return sCob(st,d+1);}).join("\n")+"\n"+p+"END-PERFORM.";}
    if(s.t==="Break")return p+"EXIT PERFORM.";
    if(s.t==="Skip"||s.t==="Continue")return p+"EXIT PERFORM CYCLE.";
    if(s.t==="Goto")return p+"GO TO "+targetLabel(s.label,"cobol")+".";
    if(s.t==="Label")return p+targetLabel(s.name,"cobol")+".";
    if(s.t==="Switch"||s.t==="Dispatch"){o=p+"EVALUATE "+exprStr(s.expr,"cobol");(s.cases||[]).forEach(function(c){o+="\n"+p+"WHEN "+exprStr(c[0],"cobol");(c[1]||[]).forEach(function(st){o+="\n"+sCob(st,d+1);});});if(s.def){o+="\n"+p+"WHEN OTHER";s.def.forEach(function(st){o+="\n"+sCob(st,d+1);});}return o+"\n"+p+"END-EVALUATE.";}
    if(s.t==="DoLoop"||s.t==="DoWhile"){cond=doLoopCondExpr(s);once=(s.body||[]).map(function(st){return sCob(st,d);}).join("\n");loop=p+"PERFORM UNTIL NOT ("+exprStr(cond,"cobol")+")\n"+(s.body||[]).map(function(st){return sCob(st,d+1);}).join("\n")+"\n"+p+"END-PERFORM.";return once+(once&&loop?"\n":"")+loop;}
    if(s.t==="For"){f=forLoopInfo(s);if(f){var cobFor=targetName(f.v,"cobol");return p+"PERFORM VARYING "+cobFor+" FROM "+exprStr(f.start,"cobol")+" BY "+exprStr(f.step,"cobol")+" UNTIL "+cobFor+" > "+exprStr(f.end,"cobol")+"\n"+(s.body||[]).map(function(st){return sCob(st,d+1);}).join("\n")+"\n"+p+"END-PERFORM.";}}
    if(s.t==="ServerMain")return (s.body||[]).map(function(st){return sCob(st,d);}).join("\n");
    return p+"*> "+s.t;
  }
  function astToReport(prog){
    var l=[],subs=[];
    prog.forEach(function(s){if(s.t==="Func"||s.t==="Sub")subs.push(s);else l.push(sRep(s,0));});
    subs.forEach(function(f){
      var pa=(f.params&&f.params.length)?" USING "+f.params.map(function(p){return targetName(p,"report");}).join(" "):"";
      l.push("FORM "+f.name+pa+".");
      (f.body||[]).forEach(function(s){l.push(sRep(s,1));});
      l.push("ENDFORM.");
    });
    return l.join("\n");
  }
  function sRep(s,d){
    var p="  ".repeat(d||0), value=s&&(s.value||s.init||{t:"Num",value:0}), obj, arms, els, o, f, once, loop, cond;
    if(!s)return"";
    if(s.t==="Decl"||s.t==="Dim"||s.t==="Let"||s.t==="Assign"){
      var repName=targetName(s.name,"report");
      if(value&&value.t==="Ternary")return p+"IF "+exprStr(value.cond,"report")+".\n"+p+"  "+repName+" = "+exprStr(value.then,"report")+".\n"+p+"ELSE.\n"+p+"  "+repName+" = "+exprStr(value.els,"report")+".\n"+p+"ENDIF.";
      obj=singleFieldObj(value);
      return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"report")+").\n":"")+p+repName+" = "+exprStr(value,"report")+".";
    }
    if(s.t==="FieldAssign"){var repMethod=s.value&&s.value.t==="Str"?"SetFieldStr":"SetField";return p+"Storage.EditCard("+exprStr({t:"Var",name:s.obj},"report")+").\n"+p+"Storage."+repMethod+"("+exprStr({t:"Str",value:s.field},"report")+", "+exprStr(s.value,"report")+").";}
    if(s.t==="Print"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"report")+").\n":"")+p+"WRITE "+exprStr(s.value,"report")+".";}
    if(s.t==="ExprStmt"){
      if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print"){obj=singleFieldObj(s.expr.args[0]);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"report")+").\n":"")+p+"WRITE "+exprStr(s.expr.args[0],"report")+".";}
      if(s.expr&&s.expr.t==="IncDec")return sRep(s.expr,d);
      var ec=stmtPlainCall(s);if(ec)return p+"PERFORM "+ec.method+((ec.args||[]).length?" USING "+ec.args.map(function(x){return exprStr(x,"report");}).join(" "):"")+".";
      return s.expr?p+exprStr(s.expr,"report")+".":"";
    }
    if(s.t==="IncDec"){var repInc=targetName(incDecName(s),"report"),repDelta=incDecDelta(s);return p+repInc+" = "+repInc+(repDelta>=0?" + ":" - ")+Math.abs(repDelta)+".";}
    if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"report");});return p+"PERFORM "+s.name+(a.length?" USING "+a.join(" "):"")+".";}
    if(s.t==="CallStmt"){var cc=stmtPlainCall(s);if(cc)return p+"PERFORM "+cc.method+((cc.args||[]).length?" USING "+cc.args.map(function(x){return exprStr(x,"report");}).join(" "):"")+".";return p+exprStr(s.call,"report")+".";}
    if(s.t==="Return")return p+"RETURN"+(s.value?" "+exprStr(s.value,"report"):"")+".";
    if(s.t==="Break")return p+"EXIT.";
    if(s.t==="Skip"||s.t==="Continue")return p+"CONTINUE.";
    if(s.t==="If"){arms=s.arms||[[s.cond,s.then]];els=s.els;o=p+"IF "+exprStr(arms[0][0],"report")+".";arms[0][1].forEach(function(st){o+="\n"+sRep(st,d+1);});for(var i=1;i<arms.length;i++){o+="\n"+p+"ELSEIF "+exprStr(arms[i][0],"report")+".";arms[i][1].forEach(function(st){o+="\n"+sRep(st,d+1);});}if(els){o+="\n"+p+"ELSE.";(Array.isArray(els)?els:[els]).forEach(function(st){o+="\n"+sRep(st,d+1);});}return o+"\n"+p+"ENDIF.";}
    if(s.t==="While")return p+"WHILE "+exprStr(s.cond,"report")+".\n"+(s.body||[]).map(function(st){return sRep(st,d+1);}).join("\n")+"\n"+p+"ENDWHILE.";
    if(s.t==="Switch"||s.t==="Dispatch"){o=p+"CASE "+exprStr(s.expr,"report")+".";(s.cases||[]).forEach(function(c){o+="\n"+p+"WHEN "+exprStr(c[0],"report")+"."; (c[1]||[]).forEach(function(st){o+="\n"+sRep(st,d+1);});});if(s.def){o+="\n"+p+"WHEN OTHERS.";s.def.forEach(function(st){o+="\n"+sRep(st,d+1);});}return o+"\n"+p+"ENDCASE.";}
    if(s.t==="ForEach")return p+"LOOP AT "+exprStr(s.count,"report")+" INTO "+targetName(s.v||"I","report")+".\n"+(s.body||[]).map(function(st){return sRep(st,d+1);}).join("\n")+"\n"+p+"ENDLOOP.";
    if(s.t==="ForTo"){var repLoop=targetName(s.v||"I","report"),repTmp="__"+repLoop+"_loop",repCnt={t:"Bin",op:"+",lhs:{t:"Bin",op:"-",lhs:s.end,rhs:s.start},rhs:{t:"Num",value:1}},repVal={t:"Bin",op:"+",lhs:s.start,rhs:{t:"Var",name:repTmp}};return p+"LOOP AT "+exprStr(repCnt,"report")+" INTO "+repTmp+".\n"+p+"  "+repLoop+" = "+exprStr(repVal,"report")+".\n"+(s.body||[]).map(function(st){return sRep(st,d+1);}).join("\n")+"\n"+p+"ENDLOOP.";}
    if(s.t==="Goto")return p+"GOTO "+targetLabel(s.label,"report")+".";
    if(s.t==="Label")return p+"LABEL "+targetLabel(s.name,"report")+".";
    if(s.t==="DoLoop"||s.t==="DoWhile"){cond=doLoopCondExpr(s);once=(s.body||[]).map(function(st){return sRep(st,d);}).join("\n");loop=p+"WHILE "+exprStr(cond,"report")+".\n"+(s.body||[]).map(function(st){return sRep(st,d+1);}).join("\n")+"\n"+p+"ENDWHILE.";return once+(once&&loop?"\n":"")+loop;}
    if(s.t==="For"){f=forLoopInfo(s);if(f){var repFor=targetName(f.v,"report"),cnt2={t:"Bin",op:"+",lhs:{t:"Bin",op:"-",lhs:f.end,rhs:f.start},rhs:{t:"Num",value:1}},tmp="__"+repFor+"_loop",loopVal={t:"Bin",op:"+",lhs:f.start,rhs:{t:"Var",name:tmp}},body=(s.body||[]).map(function(st){return sRep(st,d+1);}).join("\n");return p+"LOOP AT "+exprStr(cnt2,"report")+" INTO "+tmp+".\n"+p+"  "+repFor+" = "+exprStr(loopVal,"report")+".\n"+body+"\n"+p+"ENDLOOP.";}}
    return p+"* "+s.t;
  }
  function astToFunctional(prog){
    var defs=[],body=[];
    prog.forEach(function(s){if(s.t==="Func"||s.t==="Sub")defs.push(s);else body.push(s);});
    var l=[];
    defs.forEach(function(f){
      var pa=(f.params||[]).join(" ");
      if((f.body||[]).length===1&&f.body[0].t==="Return"&&f.body[0].value)l.push("let "+f.name+(pa?" "+pa:" ()")+" = "+exprStr(f.body[0].value,"functional"));
      else{l.push("let "+f.name+(pa?" "+pa:" ()")+" =");(f.body||[]).forEach(function(s){l.push(sFun(s,1));});}
      l.push("");
    });
    body.forEach(function(s){l.push(sFun(s,0));});
    return l.join("\n").replace(/\n+$/,"");
  }
  function sFun(s,d){
    var p="    ".repeat(d||0), value=s&&(s.value||s.init||{t:"Num",value:0}), obj, arms, els, o, f, once, loop, cond;
    if(!s)return"";
    if(s.t==="Decl"||s.t==="Dim"||s.t==="Let"){obj=singleFieldObj(value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"functional")+")\n":"")+p+"let "+s.name+" = "+exprStr(value,"functional");}
    if(s.t==="Assign"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"functional")+")\n":"")+p+s.name+" = "+exprStr(s.value,"functional");}
    if(s.t==="FieldAssign"){var funMethod=s.value&&s.value.t==="Str"?"SetFieldStr":"SetField";return p+"Storage.EditCard("+exprStr({t:"Var",name:s.obj},"functional")+")\n"+p+"Storage."+funMethod+"("+exprStr({t:"Str",value:s.field},"functional")+", "+exprStr(s.value,"functional")+")";}
    if(s.t==="Print"){obj=singleFieldObj(s.value);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"functional")+")\n":"")+p+"printfn "+exprStr(s.value,"functional");}
    if(s.t==="ExprStmt"){if(s.expr&&s.expr.t==="Call"&&!s.expr.ns&&s.expr.method==="print"){obj=singleFieldObj(s.expr.args[0]);return(obj?p+"Storage.EditCard("+exprStr({t:"Var",name:obj},"functional")+")\n":"")+p+"printfn "+exprStr(s.expr.args[0],"functional");}if(s.expr&&s.expr.t==="IncDec")return sFun(s.expr,d);return s.expr?p+exprStr(s.expr,"functional"):"";}
    if(s.t==="IncDec"){var funInc=incDecName(s),funDelta=incDecDelta(s);return p+funInc+" = "+funInc+(funDelta>=0?" + ":" - ")+Math.abs(funDelta);}
    if(s.t==="Return")return p+(s.value?"return "+exprStr(s.value,"functional"):"return");
    if(s.t==="Break")return p+"break";
    if(s.t==="Skip"||s.t==="Continue")return p+"continue";
    if(s.t==="CallStmt")return p+exprStr(s.call,"functional");
    if(s.t==="Gosub"){var a=(s.args||[]).map(function(x){return exprStr(x,"functional");});return p+s.name+"("+a.join(", ")+")";}
    if(s.t==="Goto")return p+"goto "+targetLabel(s.label,"functional");
    if(s.t==="Label")return p+"label "+targetLabel(s.name,"functional");
    if(s.t==="If"){arms=s.arms||[[s.cond,s.then]];els=s.els;o=p+"if "+exprStr(arms[0][0],"functional")+" then\n"+(arms[0][1]||[]).map(function(st){return sFun(st,d+1);}).join("\n");for(var i=1;i<arms.length;i++){o+="\n"+p+"else\n"+sFun({t:"If",arms:[arms[i]],els:null},d+1);}if(els){o+="\n"+p+"else\n"+els.map(function(st){return sFun(st,d+1);}).join("\n");}return o;}
    if(s.t==="While")return p+"while "+exprStr(s.cond,"functional")+" do\n"+(s.body||[]).map(function(st){return sFun(st,d+1);}).join("\n");
    if(s.t==="ForTo")return p+"for "+s.v+" in "+exprStr(s.start,"functional")+".."+exprStr(s.end,"functional")+" do\n"+(s.body||[]).map(function(st){return sFun(st,d+1);}).join("\n");
    if(s.t==="ForEach")return p+"for "+s.v+" in "+exprStr(s.count,"functional")+" do\n"+(s.body||[]).map(function(st){return sFun(st,d+1);}).join("\n");
    if(s.t==="Switch"||s.t==="Dispatch"){o=p+"match "+exprStr(s.expr,"functional")+" with";(s.cases||[]).forEach(function(c){o+="\n"+p+"| "+exprStr(c[0],"functional")+" ->";if((c[1]||[]).length===1)o+=" "+sFun(c[1][0],0).replace(/^\s+/,"");else o+="\n"+(c[1]||[]).map(function(st){return sFun(st,d+1);}).join("\n");});if(s.def){o+="\n"+p+"| _ ->";if(s.def.length===1)o+=" "+sFun(s.def[0],0).replace(/^\s+/,"");else o+="\n"+s.def.map(function(st){return sFun(st,d+1);}).join("\n");}return o;}
    if(s.t==="DoLoop"||s.t==="DoWhile"){cond=doLoopCondExpr(s);once=(s.body||[]).map(function(st){return sFun(st,d);}).join("\n");loop=p+"while "+exprStr(cond,"functional")+" do\n"+(s.body||[]).map(function(st){return sFun(st,d+1);}).join("\n");return once+(once&&loop?"\n":"")+loop;}
    if(s.t==="For"){f=forLoopInfo(s);if(f)return p+"for "+f.v+" in "+exprStr(f.start,"functional")+".."+exprStr(f.end,"functional")+" do\n"+(s.body||[]).map(function(st){return sFun(st,d+1);}).join("\n");}
    return p+"// "+s.t;
  }

  return {
    compile: function (src, lang) {
      var il = compileIL(src, lang);
      return { words: lowerToBytecode(il, true), il: il };
    },
    compileDebug: function (src, lang) {
      var vars = {};
      var dbg = {};
      var words = lowerToBytecode(compileIL(src, lang), true, vars, true, dbg);
      return { words: words, vars: vars, debug: dbg };
    },
    compileC: function (src) { return { words: lowerToBytecode(compileC(src), true), il: compileC(src) }; },
    compileBasic: function (src) { return { words: lowerToBytecode(compileBasic(src), true), il: compileBasic(src) }; },
    compilePython: function (src) { return { words: lowerToBytecode(compilePython(src), true), il: compilePython(src) }; },
    compileEnglish: function (src) { return { words: lowerToBytecode(compileEnglish(src), true), il: compileEnglish(src) }; },
    compileCobol: function(src) { return { words: lowerToBytecode(compileCobol(src), true) }; },
    compileReport: function(src) { return { words: lowerToBytecode(compileReport(src), true) }; },
    compileFunctional: function(src) { return { words: lowerToBytecode(compileFunctional(src), true) }; },
    compileWithDebug: function (src, lang) {
      var dbg = {};
      var words = lowerToBytecode(compileIL(src, lang), true, null, true, dbg);
      return { words: words, debug: dbg };
    },
    translate: translate,
    symbolize: symbolize,
    offsetToLineCol: offsetToLineCol,
    sourceLineText: sourceLineText,
    FAULT_NAMES: FAULT_NAMES,
    _lowerToBytecode: lowerToBytecode,
    _VReg: VReg, _Imm: Imm, _ILBuilder: ILBuilder, _canonHost: canonHost, _encodeCardAddr: encodeCardAddr,
    _COND: COND, _COND_NEGATE: COND_NEGATE
  };
});
