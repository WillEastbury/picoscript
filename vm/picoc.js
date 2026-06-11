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
      var imm = (H.HOST_HOOK_BASE | hook);
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
      if (ins.op === "const") return 2;
      if (ins.op === "mov" && isImm(ins.a)) return 2;
      if (ins.op === "jmptab") return ins.targets.length + 1;
      return 1;
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
        words.push(enc(OP.SUB, rd, rd, ADDR_REG, rd));   // rd = rd - rd = 0
        words.push(enc(OP.ADD, rd, rd, 0, value & 0xFFFF));
        pc += 2; return;
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
  var C_KW = { int:1, var:1, void:1, if:1, else:1, while:1, for:1, return:1, break:1, continue:1, switch:1, case:1, default:1, do:1, goto:1, dispatch:1 };
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
      if (t.kind === "kw" && t.value === "void") { this.next(); var name = this.next().value; this.expect("("); this.expect(")"); return { t: "Func", name: name, body: this.parseBlock() }; }
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
      if (t.value === "{") { return { t: "If", cond: { t: "Num", value: 1 }, then: this.parseBlock(), els: null }; }
      if (t.kind === "id" && this.toks[this.i + 1].value === ":") { var lab = this.next().value; this.next(); return { t: "Label", name: lab }; }
      if (t.kind === "id" && this.toks[this.i + 1].value === "=") {
        var name = this.next().value; this.expect("="); var val = this.parseExpr(); this.expect(";"); return { t: "Assign", name: name, value: val };
      }
      if (t.kind === "id" && C_COMPOUND[this.toks[this.i + 1].value]) {
        var cn = this.next().value; var cop = C_COMPOUND[this.next().value]; var cv = this.parseExpr(); this.expect(";");
        return { t: "Assign", name: cn, value: { t: "Bin", op: cop, lhs: { t: "Var", name: cn }, rhs: cv } };
      }
      var e = this.parseExpr(); this.expect(";"); return { t: "ExprStmt", expr: e };
    },
    parseDecl: function () { this.next(); var name = this.next().value; var init = null; if (this.accept("=")) init = this.parseExpr(); this.expect(";"); return { t: "Decl", name: name, init: init }; },
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
        if (this.peek().value === ".") { this.next(); var m = this.next().value; return { t: "Call", ns: t.value, method: m, args: this.parseArgs() }; }
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

  function CLowerer() { this.b = new ILBuilder(); this.vars = {}; this.funcs = []; this.loop = []; this._strlitN = 0; }
  CLowerer.prototype = {
    varOf: function (name) { var k = name.toLowerCase(); if (!this.vars[k]) this.vars[k] = new VReg(name, true); return this.vars[k]; },
    lowerProgram: function (prog) {
      var self = this, body = [];
      prog.forEach(function (s) { if (s.t === "Func") self.funcs.push(s); else body.push(s); });
      body.forEach(function (s) { self.stmt(s); });
      this.b.ret();
      this.funcs.forEach(function (f) { self.b.label("fn_" + f.name.toLowerCase()); f.body.forEach(function (s) { self.stmt(s); }); self.b.ret(); });
      return this.b.insts;
    },
    stmt: function (s) {
      var self = this;
      if (typeof s.pos === "number" && s.pos >= 0) this.b.curPos = s.pos;   // INV-25
      if (s.t === "Decl") { var v = this.varOf(s.name); if (s.init != null) this.assignTo(v, s.init); else this.b.const_(v, 0); }
      else if (s.t === "Assign") this.assignTo(this.varOf(s.name), s.value);
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
      if (e.t === "Var") return this.varOf(e.name);
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
        this.b.call("fn_" + m.toLowerCase()); return null;
      }
      if (ns.toUpperCase() === "NET") {
        var M = m.toUpperCase();
        if (M === "STATUS") this.b.net("status", intlit(c.args[0]));
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
  var B_KW = {}; ["LET","DIM","IF","THEN","ELSEIF","ELSE","ENDIF","WHILE","ENDWHILE","FOR","TO","STEP","NEXT","FOREACH","IN","ENDFOREACH","SWITCH","CASE","DEFAULT","ENDSWITCH","DISPATCH","ENDDISPATCH","GOTO","GOSUB","SUB","ENDSUB","RETURN","PRINT","AND","OR","NOT","DO","LOOP","UNTIL","BREAK","SKIP","INC","DEC","IIF","EQ","NE","LT","GT","LE","GE","MOD"].forEach(function (k) { B_KW[k] = 1; });
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
    function push(k, v) { toks.push({ kind: k, value: v }); }
    while (i < n) {
      var c = src[i];
      if (c === "\n") { push("nl", "\\n"); i++; continue; }
      if (c === " " || c === "\t" || c === "\r") { i++; continue; }
      if (c === "'" || (c === "/" && src[i + 1] === "/")) { while (i < n && src[i] !== "\n") i++; continue; }
      if (c === '"') { var j = i + 1, b = ""; while (j < n && src[j] !== '"') { b += src[j]; j++; } push("str", b); i = j + 1; continue; }
      if (isDigit(c)) { var j2 = i; if (c === "0" && (src[j2 + 1] === "x" || src[j2 + 1] === "X")) { j2 += 2; while (j2 < n && /[0-9a-fA-F]/.test(src[j2])) j2++; } else { while (j2 < n && isDigit(src[j2])) j2++; } push("num", src.slice(i, j2)); i = j2; continue; }
      if (isAlpha(c)) { var j3 = i; while (j3 < n && isAlnum(src[j3])) j3++; if (src[j3] === "$") j3++; var w = src.slice(i, j3); var up = w.toUpperCase(); if (B_KW[up]) push("kw", up); else push("id", w); i = j3; continue; }
      var two = src.slice(i, i + 2);
      if (B_TWO[two]) { push("op", two); i += 2; continue; }
      if (B_ONE.indexOf(c) >= 0) { push("op", c); i++; continue; }
      throw new Error("BASIC: unexpected char " + JSON.stringify(c));
    }
    push("nl", "\\n"); push("eof", "");
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
      var t = this.peek();
      if (t.kind === "id" && this.peek2().kind === "op" && this.peek2().value === ":") { var name = this.next().value; this.next(); this.endLine(); return { t: "Label", name: name }; }
      if (t.kind === "kw") {
        var kw = t.value;
        if (kw === "LET") return this.parseLet(true);
        if (kw === "DIM") return this.parseDim();
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
        if (kw === "GOSUB") { this.next(); var nm2 = this.next().value; this.endLine(); return { t: "Gosub", name: nm2 }; }
        if (kw === "SUB") return this.parseSub();
        if (kw === "RETURN") { this.next(); this.endLine(); return { t: "Return" }; }
        if (kw === "BREAK") { this.next(); this.endLine(); return { t: "Break" }; }
        if (kw === "SKIP") { this.next(); this.endLine(); return { t: "Skip" }; }
        if (kw === "PRINT") { this.next(); var v = this.parseExpr(); this.endLine(); return { t: "Print", value: v }; }
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
      this.endLine(); return { t: "Dim", name: name, init: init };
    },
    parseLet: function (eat) { if (eat) this.eatKw("LET"); var name = this.next().value; this.eatOp("="); var v = this.parseExpr(); this.endLine(); return { t: "Let", name: name, value: v }; },
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
    parseSub: function () { this.eatKw("SUB"); var name = this.next().value; this.endLine(); var body = this.parseBlock("ENDSUB"); this.eatKw("ENDSUB"); this.endLine(); return { t: "Sub", name: name, body: body }; },
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

  function BLowerer() { this.b = new ILBuilder(); this.vars = {}; this.subs = []; this.scopes = []; this._strlitN = 0; }
  BLowerer.prototype = {
    varOf: function (name) { var k = name.toUpperCase(); if (!this.vars[k]) this.vars[k] = new VReg(name, true); return this.vars[k]; },
    lowerProgram: function (prog) {
      var self = this, body = [];
      prog.forEach(function (s) { if (s.t === "Sub") self.subs.push(s); else body.push(s); });
      body.forEach(function (s) { self.stmt(s); });
      this.b.ret();
      this.subs.forEach(function (sub) { self.b.label("sub_" + sub.name.toUpperCase()); sub.body.forEach(function (s) { self.stmt(s); }); self.b.ret(); });
      return this.b.insts;
    },
    stmt: function (s) {
      var self = this;
      if (s.t === "Let") this.assignTo(this.varOf(s.name), s.value);
      else if (s.t === "Dim") { var dv = this.varOf(s.name); if (s.init === null) this.b.const_(dv, 0); else this.assignTo(dv, s.init); }
      else if (s.t === "IncDec") { var iv = this.varOf(s.name); if (s.delta === 1) this.b.inc(iv); else this.b.arith("sub", iv, iv, new Imm(1)); }
      else if (s.t === "Label") this.b.label("lbl_" + s.name.toUpperCase());
      else if (s.t === "Goto") this.b.jmp("lbl_" + s.label.toUpperCase());
      else if (s.t === "Gosub") this.b.call("sub_" + s.name.toUpperCase());
      else if (s.t === "Return") this.b.ret();
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
      if (e.t === "Var") return this.varOf(e.name);
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
        if (M === "STATUS") this.b.net("status", intlit(c.args[0]));
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
  var PY_KW = {}; ["if","elif","else","while","for","in","range","def","return","break","continue","pass","and","or","not","print","true","false","match","case","do","until","goto","label","dispatch"].forEach(function (k) { PY_KW[k] = 1; });
  var PY_CMP = { "==":"EQ","!=":"NE","<":"LT",">":"GT","<=":"LE",">=":"GE" };
  var PY_AUG = { "+=":"+","-=":"-","*=":"*","/=":"/","%=":"MOD" };
  var PY_PREC = { or:1, and:2, "==":3, "!=":3, "<":3, ">":3, "<=":3, ">=":3, "+":5, "-":5, "*":6, "/":6, "%":6 };
  var PY_BINOP = { "+":"+","-":"-","*":"*","/":"/","%":"MOD", and:"AND", or:"OR" };
  var PY_TWO = { "==":1,"!=":1,"<=":1,">=":1,"+=":1,"-=":1,"*=":1,"/=":1,"%=":1 };
  var PY_ONE = "+-*/%()<>=,.:";

  function indentTokLine(text, out, kwset, two, one, who) {
    var i = 0, n = text.length;
    while (i < n) {
      var c = text[i];
      if (c === " " || c === "\t") { i++; continue; }
      if (c === "#") break;
      if (isDigit(c)) { var j = i; if (c === "0" && (text[j + 1] === "x" || text[j + 1] === "X")) { j += 2; while (j < n && /[0-9a-fA-F]/.test(text[j])) j++; } else { while (j < n && isDigit(text[j])) j++; } out.push({ kind: "num", value: text.slice(i, j) }); i = j; continue; }
      if (isAlpha(c)) { var j2 = i; while (j2 < n && isAlnum(text[j2])) j2++; var w = text.slice(i, j2); if (kwset === null) out.push({ kind: "word", value: w }); else out.push({ kind: kwset[w.toLowerCase()] ? "kw" : "id", value: w }); i = j2; continue; }
      if (c === '"' || c === "'") { var q = c, j3 = i + 1, b = ""; while (j3 < n && text[j3] !== q) { if (text[j3] === "\\" && j3 + 1 < n) { var nx = text[j3 + 1]; b += ({ n: "\n", t: "\t", "\\": "\\", '"': '"', "'": "'" }[nx] || nx); j3 += 2; } else { b += text[j3]; j3++; } } if (j3 >= n) throw new Error(who + ": unterminated string"); out.push({ kind: "str", value: b }); i = j3 + 1; continue; }
      var tw = text.slice(i, i + 2);
      if (two[tw]) { out.push({ kind: "op", value: tw }); i += 2; continue; }
      if (one.indexOf(c) >= 0) { out.push({ kind: "op", value: c }); i++; continue; }
      throw new Error(who + ": unexpected char " + JSON.stringify(c));
    }
  }

  function indentTokenize(src, kwset, two, one, who) {
    var out = [], indents = [0], lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    for (var li = 0; li < lines.length; li++) {
      var line = lines[li], stripped = line.replace(/^[ \t]+/, "");
      if (stripped === "" || stripped[0] === "#") continue;
      var indent = line.length - stripped.length;
      if (indent > indents[indents.length - 1]) { indents.push(indent); out.push({ kind: "indent", value: "" }); }
      else { while (indent < indents[indents.length - 1]) { indents.pop(); out.push({ kind: "dedent", value: "" }); } if (indent !== indents[indents.length - 1]) throw new Error(who + ": inconsistent indentation"); }
      var before = out.length;
      indentTokLine(line, out, kwset, two, one, who);
      if (out.length > before) out.push({ kind: "newline", value: "" });
    }
    while (indents.length > 1) { indents.pop(); out.push({ kind: "dedent", value: "" }); }
    out.push({ kind: "eof", value: "" });
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
      var t = this.peek();
      if (t.kind === "kw") {
        var kw = t.value.toLowerCase();
        if (kw === "if") return this.parseIf();
        if (kw === "while") { this.expectKw("while"); var c = this.parseExpr(); return { t: "While", cond: c, body: this.parseSuite() }; }
        if (kw === "for") return this.parseFor();
        if (kw === "match") return this.parseMatch();
      if (kw === "dispatch") return this.parseDispatch();
        if (kw === "do") return this.parseDo();
        if (kw === "goto") { this.next(); var gl = this.expect("id").value; this.expect("newline"); return { t: "Goto", label: gl }; }
        if (kw === "label") { this.next(); var ll = this.expect("id").value; this.expect("newline"); return { t: "Label", name: ll }; }
        if (kw === "def") return this.parseDef();
        if (kw === "return") { this.next(); this.expect("newline"); return { t: "Return" }; }
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
        if (nx.kind === "op" && nx.value === "(") { var gn = this.next().value; var gargs = this.parseArgs(); this.expect("newline"); if (gargs.length === 0) return { t: "Gosub", name: gn }; return { t: "CallStmt", call: { t: "Call", ns: null, method: gn, args: gargs } }; }
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
    parseDef: function () { this.expectKw("def"); var name = this.expect("id").value; this.expect("op", "("); this.expect("op", ")"); return { t: "Sub", name: name, body: this.parseSuite() }; },
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
          if (this.atWord("routine", "subroutine", "procedure", "function")) { this.next(); if (this.atWord("called", "named")) this.next(); }
          var dn = this.expect("word").value; return { t: "Sub", name: dn, body: this.parseSuite() };
        }
        if (w === "do" || w === "call") { this.next(); var cn = this.expect("word").value; this.endStmt(); return { t: "Gosub", name: cn }; }
        if (w === "return") { this.next(); this.endStmt(); return { t: "Return" }; }
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

  function compileIL(src, lang) {
    return (lang === "basic") ? compileBasic(src)
         : (lang === "python") ? compilePython(src)
         : (lang === "english") ? compileEnglish(src)
         : compileC(src);
  }

  return {
    compile: function (src, lang) {
      var il = compileIL(src, lang);
      return { words: lowerToBytecode(il, true), il: il };
    },
    compileDebug: function (src, lang) {
      var vars = {};
      var words = lowerToBytecode(compileIL(src, lang), true, vars);
      return { words: words, vars: vars };
    },
    compileC: function (src) { return { words: lowerToBytecode(compileC(src), true), il: compileC(src) }; },
    compileBasic: function (src) { return { words: lowerToBytecode(compileBasic(src), true), il: compileBasic(src) }; },
    compilePython: function (src) { return { words: lowerToBytecode(compilePython(src), true), il: compilePython(src) }; },
    compileEnglish: function (src) { return { words: lowerToBytecode(compileEnglish(src), true), il: compileEnglish(src) }; },
    compileWithDebug: function (src, lang) {
      var dbg = {};
      var words = lowerToBytecode(compileIL(src, lang), true, null, true, dbg);
      return { words: words, debug: dbg };
    },
    symbolize: symbolize,
    offsetToLineCol: offsetToLineCol,
    sourceLineText: sourceLineText,
    FAULT_NAMES: FAULT_NAMES,
    _lowerToBytecode: lowerToBytecode,
    _VReg: VReg, _Imm: Imm, _ILBuilder: ILBuilder, _canonHost: canonHost, _encodeCardAddr: encodeCardAddr,
    _COND: COND, _COND_NEGATE: COND_NEGATE
  };
});
