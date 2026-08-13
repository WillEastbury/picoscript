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
      if (c === '"' || c === "'") { var j = i + 1, b = ""; while (j < n && q[j] !== c) { b += q[j]; j++; } if (j === n) throw new Error("query: unterminated string literal"); toks.push(["str", b]); i = j + 1; continue; }
      var two = q.substr(i, 2);
      if (CMP2[two]) { toks.push(["op", two]); i += 2; continue; }
      if ("<>=~".indexOf(c) >= 0) { toks.push(["op", c]); i++; continue; }
      if (c === "(" || c === ")") { toks.push(["paren", c]); i++; continue; }
      var k = i; while (k < n && !/\s/.test(q[k]) && "<>=~()\"'".indexOf(q[k]) < 0) k++;
      var w = q.slice(i, k); i = k;
      var up = w.toUpperCase();
      if (up === "AND" || up === "OR" || up === "NOT") toks.push(["kw", up]); else toks.push(["word", w]);
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
    parse: function () { var node = this.parseOr(); if (this.peek()) throw new Error("query: unexpected token"); return node; },
    parseOr: function () { var left = this.parseAnd(); var p; while ((p = this.peek()) && p[0] === "kw" && p[1] === "OR") { this.nxt(); left = ["or", left, this.parseAnd()]; } return left; },
    parseAnd: function () { var left = this.parseNot(); var p; while ((p = this.peek()) && p[0] === "kw" && p[1] === "AND") { this.nxt(); left = ["and", left, this.parseNot()]; } return left; },
    parseNot: function () { var p = this.peek(); if (p && p[0] === "kw" && p[1] === "NOT") { this.nxt(); return ["not", this.parseNot()]; } return this.parseCmp(); },
    parseCmp: function () {
      var p = this.peek();
      if (p && p[0] === "paren" && p[1] === "(") { this.nxt(); var node = this.parseOr(); var q = this.peek(); if (!q || q[0] !== "paren" || q[1] !== ")") throw new Error("query: expected ')'"); this.nxt(); return node; }
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
    if (node[0] === "not") return !evalNode(node[1], rec);
    return evalCmp(node[1], node[2], node[3], rec);
  }
  function compileQuery(q) {
    q = (q || "").trim();
    if (!q) return function () { return true; };
    var ast = new QParser(qTokens(q)).parse();
    return function (rec) { return evalNode(ast, rec); };
  }

  var STATUS = { OK: 0, NOT_FOUND: 1, INVALID: 2, DUPLICATE: 3, CONFLICT: 4, CORRUPT: 5 };
  var DEFAULT_MAX_CARD_BYTES = 4096;
  function packName(pack) {
    var value = String(pack);
    if (!value || value.indexOf(":") >= 0) throw new Error("pack identifiers must be non-empty and colon-free");
    return value;
  }
  function normalizeSchema(schema) {
    if (!schema || typeof schema !== "object") throw new Error("schema must be an object");
    if (!Array.isArray(schema.fields)) return Object.assign({}, schema);
    var names = {}, ids = {}, fields = schema.fields.map(function (field, i) {
      if (!field || !field.name) throw new Error("schema field must have a name");
      var id = field.id === undefined ? i + 1 : Number(field.id), name = String(field.name);
      if (id < 0 || names[name] || ids[id]) throw new Error("schema fields must have unique non-negative ids and names");
      names[name] = true; ids[id] = true;
      return { id: id, name: name, type: String(field.type || "ANY").toUpperCase(), required: !!field.required };
    });
    fields.sort(function (a, b) { return a.id - b.id; });
    return { fields: fields };
  }
  function stableStringify(value) {
    if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ":" + stableStringify(value[key]);
      }).join(",") + "}";
    }
    return JSON.stringify(value);
  }
  function schemaId(pack, version, schema) {
    var text = stableStringify([pack, Number(version), schema]);
    var h = 0xcbf29ce484222325n;
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      h ^= BigInt(code & 255); h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
    }
    return h.toString(16).padStart(16, "0");
  }

  // ---- store ----
  function PicoStore(backend) { this.b = backend || new MemBackend(); this.lastStatus = STATUS.OK; }
  PicoStore.prototype = {
    _packMetaKey: function (pack) { return "pack:" + packName(pack) + ":meta"; },
    _schemaKey: function (pack) { return "pack:" + packName(pack) + ":schema"; },
    _ensurePack: function (pack) {
      pack = packName(pack);
      if (this.b.get(this._packMetaKey(pack)) === null) this.b.set(this._packMetaKey(pack), JSON.stringify({ id: pack, name: pack, max_card_bytes: DEFAULT_MAX_CARD_BYTES }));
      return pack;
    },
    createPack: function (pack, name, maxCardBytes) {
      pack = packName(pack);
      if (this.b.get(this._packMetaKey(pack)) !== null) { this.lastStatus = STATUS.DUPLICATE; throw new Error("duplicate pack: " + pack); }
      var limit = maxCardBytes === undefined ? DEFAULT_MAX_CARD_BYTES : Number(maxCardBytes);
      if (limit <= 0) throw new Error("max_card_bytes must be positive");
      this.b.set(this._packMetaKey(pack), JSON.stringify({ id: pack, name: name === undefined ? pack : String(name), max_card_bytes: limit }));
      this.lastStatus = STATUS.OK; return pack;
    },
    packInfo: function (pack) {
      var raw = this.b.get(this._packMetaKey(pack));
      if (raw === null) { this.lastStatus = STATUS.NOT_FOUND; return null; }
      try { var info = JSON.parse(raw); this.lastStatus = STATUS.OK; return info; }
      catch (e) { this.lastStatus = STATUS.CORRUPT; throw new Error("pack metadata is corrupt"); }
    },
    packs: function () {
      var out = [], self = this;
      this.b.keys().forEach(function (key) {
        if (key.indexOf("pack:") === 0 && key.slice(-5) === ":meta") out.push(key.slice(5, -5));
      });
      return out.filter(function (v, i) { return out.indexOf(v) === i; }).sort();
    },
    registerSchema: function (pack, schema, opts) {
      pack = this._ensurePack(pack); opts = opts || {};
      var version = Number(opts.version || 1), normalized = normalizeSchema(schema);
      var raw = this.b.get(this._schemaKey(pack)), current = raw === null ? null : JSON.parse(raw);
      if (current && stableStringify(current.schema) !== stableStringify(normalized) &&
          (!opts.migrate || version <= Number(current.version))) {
        this.lastStatus = STATUS.CONFLICT; throw new Error("schema migration required");
      }
      var value = { id: schemaId(pack, version, normalized), version: version, schema: normalized };
      this.b.set(this._schemaKey(pack), JSON.stringify(value)); this.lastStatus = STATUS.OK; return value;
    },
    schema: function (pack) {
      var raw = this.b.get(this._schemaKey(pack));
      if (raw === null) { this.lastStatus = STATUS.NOT_FOUND; return null; }
      try { var value = JSON.parse(raw); this.lastStatus = STATUS.OK; return value; }
      catch (e) { this.lastStatus = STATUS.CORRUPT; throw new Error("schema is corrupt"); }
    },
    schemaCompatible: function (pack, schema, version) {
      var current = this.schema(pack);
      return !!current && stableStringify(current.schema) === stableStringify(normalizeSchema(schema)) &&
        (version === undefined || Number(current.version) === Number(version));
    },
    _validateRecord: function (pack, record) {
      var current = this.schema(pack), fields, allowed = {};
      var info = this.packInfo(pack);
      if (info && record.data instanceof Uint8Array &&
          record.data.length > Number(info.max_card_bytes || DEFAULT_MAX_CARD_BYTES)) {
        throw new Error("blobCard data exceeds pack max_card_bytes");
      }
      if (!current || !current.schema || !Array.isArray(current.schema.fields)) return;
      fields = current.schema.fields;
      fields.forEach(function (field) { allowed[field.name] = field; });
      Object.keys(record).forEach(function (name) { if (!allowed[name]) throw new Error("record contains unknown fields: " + name); });
      fields.forEach(function (field) {
        var value = record[field.name], type = field.type;
        if (field.required && value === undefined) throw new Error("record is missing required field: " + field.name);
        if (value === undefined || type === "ANY") return;
        if ((type === "INT" || type === "INT32" || type === "UINT32") &&
            (typeof value !== "number" || !Number.isInteger(value))) throw new Error("field must be an integer: " + field.name);
        if ((type === "STRING" || type === "TEXT" || type === "SPAN") && typeof value !== "string") throw new Error("field must be text: " + field.name);
      });
    },
    _ids: function (pack) { var raw = this.b.get(pack + ":ids"); return raw ? raw.split(",").filter(Boolean).map(Number) : []; },
    _setIds: function (pack, ids) { this.b.set(pack + ":ids", ids.join(",")); },
    create: function (pack, record, cardId) {
      pack = this._ensurePack(pack); this._validateRecord(pack, record);
      var nxt = parseInt(this.b.get(pack + ":next") || "1", 10);
      var id = cardId === undefined ? nxt : Number(cardId);
      if (id < 1 || this._ids(pack).indexOf(id) >= 0) { this.lastStatus = STATUS.DUPLICATE; throw new Error("duplicate or invalid card id"); }
      this.b.set(pack + ":card:" + id, SER.toHex(SER.serializeCard(record)));
      this._setIds(pack, this._ids(pack).concat([id]));
      this.b.set(pack + ":next", String(Math.max(nxt, id + 1))); this.lastStatus = STATUS.OK;
      return id;
    },
    insert: function (pack, record, cardId) { return this.create(pack, record, cardId); },
    createBlob: function (pack, payload, cardId) {
      pack = this._ensurePack(pack);
      var id = cardId === undefined ? parseInt(this.b.get(pack + ":next") || "1", 10) : Number(cardId);
      return this.create(pack, { id: id, data: payload instanceof Uint8Array ? payload : Uint8Array.from(payload) }, id);
    },
    readBlob: function (pack, id) {
      var record = this.read(pack, id);
      if (record === null) return null;
      if (!(record.data instanceof Uint8Array)) throw new Error("blobCard data field is not bytes");
      return { id: Number(id), data: record.data };
    },
    read: function (pack, id) { var h = this.b.get(pack + ":card:" + id); return h ? SER.deserializeCard(SER.fromHex(h)) : null; },
    update: function (pack, id, record) { pack = this._ensurePack(pack); this._validateRecord(pack, record); if (this._ids(pack).indexOf(id) < 0) { this.lastStatus = STATUS.NOT_FOUND; return false; } this.b.set(pack + ":card:" + id, SER.toHex(SER.serializeCard(record))); this.lastStatus = STATUS.OK; return true; },
    patch: function (pack, id, fields) { var r = this.read(pack, id); if (!r) return false; for (var k in fields) r[k] = fields[k]; return this.update(pack, id, r); },
    delete: function (pack, id) { var ids = this._ids(pack); var idx = ids.indexOf(id); if (idx < 0) { this.lastStatus = STATUS.NOT_FOUND; return false; } ids.splice(idx, 1); this._setIds(pack, ids); this.b.remove(pack + ":card:" + id); this.lastStatus = STATUS.OK; return true; },
    all: function (pack) { var self = this; return this._ids(pack).map(function (id) { return [id, self.read(pack, id)]; }).filter(function (e) { return e[1] !== null; }); },
    query: function (pack, q) { var pred = compileQuery(q); return this.all(pack).filter(function (e) { return pred(e[1]); }); },
    cardBytesHex: function (pack, id) { return this.b.get(pack + ":card:" + id); }
  };

  return { PicoStore: PicoStore, MemBackend: MemBackend, compileQuery: compileQuery, STATUS: STATUS };
});
