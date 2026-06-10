// picostore.js -- PicoStore (packs + CRUD) and the card query language, in JS.
// Mirrors picostore.py; result-identical queries and byte-identical card encoding
// (via picoserializer.js). A pluggable backend exposes get/set/remove/keys; the
// default is in-memory, but localStorage works directly (see the site).
(function (root, factory) {
  var SER = (typeof module !== "undefined" && module.exports) ? require("./picoserializer.js") : root.PicoSerializer;
  var P = factory(SER);
  if (typeof module !== "undefined" && module.exports) module.exports = P;
  else root.PicoStore = P;
})(typeof globalThis !== "undefined" ? globalThis : this, function (SER) {
  "use strict";

  function MemBackend() { this.d = {}; }
  MemBackend.prototype = {
    get: function (k) { return (k in this.d) ? this.d[k] : null; },
    set: function (k, v) { this.d[k] = v; },
    remove: function (k) { delete this.d[k]; },
    keys: function () { return Object.keys(this.d); }
  };

  // ---- query language ----
  var CMP2 = { "==": 1, "!=": 1, "<=": 1, ">=": 1, "<>": 1 };
  function qTokens(q) {
    var toks = [], i = 0, n = q.length;
    while (i < n) {
      var c = q[i];
      if (/\s/.test(c)) { i++; continue; }
      if (c === '"' || c === "'") { var j = i + 1, b = ""; while (j < n && q[j] !== c) { b += q[j]; j++; } toks.push(["str", b]); i = j + 1; continue; }
      var two = q.substr(i, 2);
      if (CMP2[two]) { toks.push(["op", two]); i += 2; continue; }
      if ("<>=~".indexOf(c) >= 0) { toks.push(["op", c]); i++; continue; }
      if (c === "(" || c === ")") { toks.push(["paren", c]); i++; continue; }
      var k = i; while (k < n && !/\s/.test(q[k]) && "<>=~()\"'".indexOf(q[k]) < 0) k++;
      var w = q.slice(i, k); i = k;
      var up = w.toUpperCase();
      if (up === "AND" || up === "OR") toks.push(["kw", up]); else toks.push(["word", w]);
    }
    return toks;
  }
  function coerce(tok) {
    if (tok[0] === "str") return tok[1];
    var t = tok[1];
    if (/^-?\d+$/.test(t)) return parseInt(t, 10);
    if (/^0[xX][0-9a-fA-F]+$/.test(t)) return parseInt(t, 16);
    return t;
  }
  function QParser(toks) { this.toks = toks; this.i = 0; }
  QParser.prototype = {
    peek: function () { return this.i < this.toks.length ? this.toks[this.i] : null; },
    nxt: function () { return this.toks[this.i++]; },
    parse: function () { return this.parseOr(); },
    parseOr: function () { var left = this.parseAnd(); var p; while ((p = this.peek()) && p[0] === "kw" && p[1] === "OR") { this.nxt(); left = ["or", left, this.parseAnd()]; } return left; },
    parseAnd: function () { var left = this.parseCmp(); var p; while ((p = this.peek()) && p[0] === "kw" && p[1] === "AND") { this.nxt(); left = ["and", left, this.parseCmp()]; } return left; },
    parseCmp: function () {
      var p = this.peek();
      if (p && p[0] === "paren" && p[1] === "(") { this.nxt(); var node = this.parseOr(); var q = this.peek(); if (q && q[0] === "paren" && q[1] === ")") this.nxt(); return node; }
      var field = this.nxt(); if (!field || field[0] !== "word") throw new Error("query: expected field");
      var op = this.nxt(); if (!op || op[0] !== "op") throw new Error("query: expected operator");
      var val = this.nxt(); if (!val) throw new Error("query: expected value");
      return ["cmp", field[1], op[1], coerce(val)];
    }
  };
  function evalCmp(field, op, value, rec) {
    if (!(field in rec)) return false;
    var fv = rec[field];
    if (op === "=" || op === "==") return fv === value;
    if (op === "!=" || op === "<>") return fv !== value;
    if (op === "~") return String(fv).indexOf(String(value)) >= 0;
    if (typeof fv !== typeof value) return false;
    if (op === "<") return fv < value;
    if (op === ">") return fv > value;
    if (op === "<=") return fv <= value;
    if (op === ">=") return fv >= value;
    throw new Error("query: unknown operator " + op);
  }
  function evalNode(node, rec) {
    if (node[0] === "and") return evalNode(node[1], rec) && evalNode(node[2], rec);
    if (node[0] === "or") return evalNode(node[1], rec) || evalNode(node[2], rec);
    return evalCmp(node[1], node[2], node[3], rec);
  }
  function compileQuery(q) {
    q = (q || "").trim();
    if (!q) return function () { return true; };
    var ast = new QParser(qTokens(q)).parse();
    return function (rec) { return evalNode(ast, rec); };
  }

  // ---- store ----
  function PicoStore(backend) { this.b = backend || new MemBackend(); }
  PicoStore.prototype = {
    _ids: function (pack) { var raw = this.b.get(pack + ":ids"); return raw ? raw.split(",").filter(Boolean).map(Number) : []; },
    _setIds: function (pack, ids) { this.b.set(pack + ":ids", ids.join(",")); },
    create: function (pack, record) {
      var nxt = parseInt(this.b.get(pack + ":next") || "1", 10);
      this.b.set(pack + ":card:" + nxt, SER.toHex(SER.serializeCard(record)));
      this._setIds(pack, this._ids(pack).concat([nxt]));
      this.b.set(pack + ":next", String(nxt + 1));
      return nxt;
    },
    read: function (pack, id) { var h = this.b.get(pack + ":card:" + id); return h ? SER.deserializeCard(SER.fromHex(h)) : null; },
    update: function (pack, id, record) { if (this._ids(pack).indexOf(id) < 0) return false; this.b.set(pack + ":card:" + id, SER.toHex(SER.serializeCard(record))); return true; },
    patch: function (pack, id, fields) { var r = this.read(pack, id); if (!r) return false; for (var k in fields) r[k] = fields[k]; return this.update(pack, id, r); },
    delete: function (pack, id) { var ids = this._ids(pack); var idx = ids.indexOf(id); if (idx < 0) return false; ids.splice(idx, 1); this._setIds(pack, ids); this.b.remove(pack + ":card:" + id); return true; },
    all: function (pack) { var self = this; return this._ids(pack).map(function (id) { return [id, self.read(pack, id)]; }).filter(function (e) { return e[1] !== null; }); },
    query: function (pack, q) { var pred = compileQuery(q); return this.all(pack).filter(function (e) { return pred(e[1]); }); },
    cardBytesHex: function (pack, id) { return this.b.get(pack + ":card:" + id); }
  };

  return { PicoStore: PicoStore, MemBackend: MemBackend, compileQuery: compileQuery };
});
