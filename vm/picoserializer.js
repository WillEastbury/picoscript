// picoserializer.js -- PicoBinarySerializer in JavaScript (browser + Node).
// Byte-for-byte identical to picoserializer.py. See that file for the layout.
(function (root, factory) {
  var P = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = P;
  else root.PicoSerializer = P;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var MAGIC = 0x50534331, T_INT = 1, T_STR = 2;
  var enc = new TextEncoder(), dec = new TextDecoder();

  function utf8(s) { return enc.encode(s); }
  function cmpBytes(a, b) {
    var n = Math.min(a.length, b.length);
    for (var i = 0; i < n; i++) { if (a[i] !== b[i]) return a[i] - b[i]; }
    return a.length - b.length;
  }

  function serializeCard(record) {
    var keys = Object.keys(record).sort(function (x, y) { return cmpBytes(utf8(x), utf8(y)); });
    var out = [];
    out.push((MAGIC >>> 24) & 255, (MAGIC >>> 16) & 255, (MAGIC >>> 8) & 255, MAGIC & 255);
    out.push((keys.length >>> 8) & 255, keys.length & 255);
    keys.forEach(function (k) {
      var kb = utf8(k);
      if (kb.length > 255) throw new Error("field name too long: " + k);
      out.push(kb.length);
      for (var i = 0; i < kb.length; i++) out.push(kb[i]);
      var v = record[k];
      if (typeof v === "boolean") v = v ? 1 : 0;
      if (typeof v === "number") {
        var x = v | 0;
        out.push(T_INT, (x >>> 24) & 255, (x >>> 16) & 255, (x >>> 8) & 255, x & 255);
      } else if (typeof v === "string") {
        var vb = utf8(v);
        if (vb.length > 0xFFFF) throw new Error("string field too long");
        out.push(T_STR, (vb.length >>> 8) & 255, vb.length & 255);
        for (var j = 0; j < vb.length; j++) out.push(vb[j]);
      } else {
        throw new Error("unsupported field type for " + k);
      }
    });
    return out;   // array of byte values (0..255)
  }

  function deserializeCard(buf) {
    if (buf.length < 6) throw new Error("bad card magic");
    var magic = ((buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3]) >>> 0;
    if (magic !== (MAGIC >>> 0)) throw new Error("bad card magic");
    var count = (buf[4] << 8) | buf[5], pos = 6, rec = {};
    for (var n = 0; n < count; n++) {
      var nlen = buf[pos++];
      var name = dec.decode(new Uint8Array(buf.slice(pos, pos + nlen))); pos += nlen;
      var t = buf[pos++];
      if (t === T_INT) {
        var x = ((buf[pos] << 24) | (buf[pos + 1] << 16) | (buf[pos + 2] << 8) | buf[pos + 3]) | 0;
        pos += 4; rec[name] = x;
      } else if (t === T_STR) {
        var vlen = (buf[pos] << 8) | buf[pos + 1]; pos += 2;
        rec[name] = dec.decode(new Uint8Array(buf.slice(pos, pos + vlen))); pos += vlen;
      } else {
        throw new Error("unknown field type " + t);
      }
    }
    return rec;
  }

  function toHex(b) { return b.map(function (x) { return ("0" + (x & 255).toString(16)).slice(-2); }).join(""); }
  function fromHex(s) { var a = []; for (var i = 0; i + 1 < s.length; i += 2) a.push(parseInt(s.substr(i, 2), 16)); return a; }

  return { serializeCard: serializeCard, deserializeCard: deserializeCard, toHex: toHex, fromHex: fromHex, MAGIC: MAGIC };
});
