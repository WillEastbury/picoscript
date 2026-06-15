// picobrotli.js -- minimal RFC 7932 (Brotli) encoder + decoder.
//
// Byte-identical JS port of vm/picobrotli.c / picobrotli.py (vendored from the
// picoweb codec). Produces valid Brotli streams decodable by any browser / zlib
// / Node, using LZ77 + canonical Huffman, a single meta-block (WBITS=16, no
// static dictionary, no context modeling), with an uncompressed meta-block
// fallback. The decoder reads the subset this encoder emits.
//
// Kept byte-for-byte in lockstep with vm/picobrotli.c and picobrotli.py so
// Compress.Brotli* is identical on the Python, JS and C VMs. UMD-wrapped so the
// browser (inlined) and Node (require) both expose `PicoBrotli`.
(function (root, factory) {
  var P = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = P;
  else root.PicoBrotli = P;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var MAX_HUFF_BITS = 15;
  var HASH_BITS = 15;
  var HASH_SIZE = 1 << HASH_BITS;
  var WIN_SIZE = 1 << 16;
  var MIN_MATCH = 4;
  var MAX_MATCH = 258;
  var MAX_CHAIN = 32;

  var kCLOrder = [1, 2, 3, 4, 0, 5, 17, 6, 16, 7, 8, 9, 10, 11, 12, 13, 14, 15];
  var kCLCL_val = [0, 7, 3, 2, 1, 15];
  var kCLCL_len = [2, 4, 3, 2, 2, 4];

  var kInsLen = [
    [0, 0], [1, 0], [2, 0], [3, 0], [4, 0], [5, 0], [6, 1], [8, 1],
    [10, 2], [14, 2], [18, 3], [26, 3], [34, 4], [50, 4], [66, 5], [98, 5],
    [130, 6], [194, 7], [322, 8], [578, 9], [1090, 10], [2114, 12],
    [6210, 14], [22594, 24]
  ];
  var kCopyLen = [
    [2, 0], [3, 0], [4, 0], [5, 0], [6, 0], [7, 0], [8, 0], [9, 0],
    [10, 1], [12, 1], [14, 2], [18, 2], [22, 3], [30, 3], [38, 4], [54, 4],
    [70, 5], [102, 5], [134, 6], [198, 7], [326, 8], [582, 9],
    [1094, 10], [2118, 24]
  ];

  var POW2 = [];
  for (var _i = 0; _i <= 32; _i++) POW2.push(Math.pow(2, _i));

  // ---- Bit writer (LSB-first, 32-bit safe) ----
  function BitW() {
    this.out = [];
    this.accum = 0; // < 2^31, always
    this.nbits = 0; // < 8 between puts
  }
  BitW.prototype.put = function (val, n) {
    this.accum += val * POW2[this.nbits];
    this.nbits += n;
    while (this.nbits >= 8) {
      this.out.push(this.accum & 0xFF);
      this.accum = Math.floor(this.accum / 256);
      this.nbits -= 8;
    }
  };
  BitW.prototype.align = function () {
    if (this.nbits > 0) {
      var pad = 8 - (this.nbits % 8);
      if (pad < 8) this.put(0, pad);
    }
  };
  BitW.prototype.finish = function () {
    if (this.nbits > 0) {
      this.out.push(this.accum & 0xFF);
      this.accum = 0;
      this.nbits = 0;
    }
  };

  function countUsed(freq, n) {
    var c = 0;
    for (var i = 0; i < n; i++) if (freq[i]) c++;
    return c;
  }

  function buildLengthsEx(freq, nsym, maxBits) {
    var lens = new Array(nsym).fill(0);
    var sorted = [];
    for (var i = 0; i < nsym; i++) if (freq[i]) sorted.push(i);
    var nused = sorted.length;
    if (nused === 0) return lens;
    if (nused === 1) { lens[sorted[0]] = 1; return lens; }

    // Insertion sort by frequency ascending (stable)
    for (i = 1; i < nused; i++) {
      var key = sorted[i];
      var kf = freq[key];
      var j = i - 1;
      while (j >= 0 && freq[sorted[j]] > kf) { sorted[j + 1] = sorted[j]; j--; }
      sorted[j + 1] = key;
    }

    var cap = 2 * nused;
    var nf = new Array(cap).fill(0);
    var par = new Array(cap).fill(-1);
    for (i = 0; i < nused; i++) { nf[i] = freq[sorted[i]]; par[i] = -1; }

    var nn = nused, q1 = 0;
    var q2buf = new Array(cap).fill(0);
    var q2h = 0, q2t = 0;

    for (var m = 0; m < nused - 1; m++) {
      var pick = [0, 0];
      for (var p = 0; p < 2; p++) {
        var h1 = q1 < nused, h2 = q2h < q2t;
        if (h1 && h2) {
          if (nf[q1] <= nf[q2buf[q2h]]) { pick[p] = q1; q1++; }
          else { pick[p] = q2buf[q2h]; q2h++; }
        } else if (h1) { pick[p] = q1; q1++; }
        else { pick[p] = q2buf[q2h]; q2h++; }
      }
      nf[nn] = nf[pick[0]] + nf[pick[1]];
      par[nn] = -1;
      par[pick[0]] = nn;
      par[pick[1]] = nn;
      q2buf[q2t] = nn; q2t++;
      nn++;
    }

    for (i = 0; i < nused; i++) {
      var d = 0, cur = i;
      while (par[cur] !== -1) { cur = par[cur]; d++; }
      if (d > maxBits) d = maxBits;
      lens[sorted[i]] = d;
    }

    for (var it = 0; it < 50; it++) {
      var kraft = 0;
      for (i = 0; i < nsym; i++) if (lens[i]) kraft += (1 << (maxBits - lens[i]));
      var target = (1 << maxBits);
      if (kraft === target) break;
      if (kraft > target) {
        for (var l = 1; l < maxBits && kraft > target; l++)
          for (i = 0; i < nsym && kraft > target; i++)
            if (lens[i] === l) {
              lens[i]++;
              kraft -= (1 << (maxBits - l));
              kraft += (1 << (maxBits - l - 1));
            }
      } else {
        for (var l2 = maxBits; l2 > 1 && kraft < target; l2--)
          for (i = nsym - 1; i >= 0 && kraft < target; i--)
            if (lens[i] === l2) {
              lens[i]--;
              kraft -= (1 << (maxBits - l2));
              kraft += (1 << (maxBits - l2 + 1));
            }
      }
    }
    return lens;
  }

  function buildLengths(freq, nsym) { return buildLengthsEx(freq, nsym, MAX_HUFF_BITS); }

  function assignCodes(lens, nsym) {
    var blCount = new Array(MAX_HUFF_BITS + 1).fill(0);
    for (var i = 0; i < nsym; i++) if (lens[i]) blCount[lens[i]]++;
    var next = new Array(MAX_HUFF_BITS + 1).fill(0);
    var c = 0;
    for (var b = 1; b <= MAX_HUFF_BITS; b++) { c = (c + blCount[b - 1]) << 1; next[b] = c & 0xFFFF; }
    var codes = [];
    for (i = 0; i < nsym; i++) {
      if (lens[i]) { codes.push([next[lens[i]], lens[i]]); next[lens[i]] = (next[lens[i]] + 1) & 0xFFFF; }
      else codes.push([0, 0]);
    }
    return codes;
  }

  function bwHuff(w, code) {
    var cCode = code[0], cLen = code[1];
    var rev = 0;
    for (var i = 0; i < cLen; i++) rev |= (((cCode >> i) & 1) << (cLen - 1 - i));
    w.put(rev & 0xFFFF, cLen);
  }

  function writeSimpleCode(w, lens, nsym, alphaBits) {
    var used = [];
    for (var i = 0; i < nsym && used.length < 4; i++) if (lens[i]) used.push(i);
    if (used.length === 0) used = [0];
    used.sort(function (a, b) { return a - b; });
    var nu = used.length;
    w.put(1, 2);
    w.put(nu - 1, 2);
    for (i = 0; i < nu; i++) w.put(used[i], alphaBits);
    if (nu === 4) w.put(lens[used[0]] === 1 ? 1 : 0, 1);
  }

  function writeComplexCode(w, lens, nsym) {
    var clSyms = [], clExtra = [];
    var lastNz = nsym - 1;
    while (lastNz > 0 && lens[lastNz] === 0) lastNz--;
    var clEnd = lastNz + 1;

    var i = 0;
    while (i < clEnd) {
      if (lens[i] === 0) {
        var run = 0;
        while (i + run < clEnd && lens[i + run] === 0) run++;
        var prevWas17 = false;
        while (run > 0) {
          if (run >= 3 && !prevWas17) {
            var r = run > 10 ? 10 : run;
            clSyms.push(17); clExtra.push(r - 3);
            run -= r; i += r; prevWas17 = true;
          } else {
            clSyms.push(0); clExtra.push(0);
            run--; i++; prevWas17 = false;
          }
        }
      } else {
        clSyms.push(lens[i]); clExtra.push(0); i++;
      }
    }
    var clN = clSyms.length;

    var clFreq = new Array(18).fill(0);
    for (i = 0; i < clN; i++) clFreq[clSyms[i]]++;

    var clUsed = 0;
    for (i = 0; i < 18; i++) if (clFreq[i]) clUsed++;
    if (clUsed === 1) {
      var dummy = (clFreq[0] === 0) ? 0 : ((clFreq[1] === 0) ? 1 : 2);
      clFreq[dummy] = 1;
    }

    var clLens = buildLengthsEx(clFreq, 18, 5);
    var clCodes = assignCodes(clLens, 18);

    var numCl = 18;
    while (numCl > 4 && clLens[kCLOrder[numCl - 1]] === 0) numCl--;

    var hskip = 0;
    if (numCl > 3 && clLens[kCLOrder[0]] === 0 && clLens[kCLOrder[1]] === 0) {
      if (clLens[kCLOrder[2]] === 0) hskip = 3; else hskip = 2;
    }
    if (hskip === 1) hskip = 0;

    w.put(hskip, 2);
    for (i = hskip; i < numCl; i++) {
      var v = clLens[kCLOrder[i]];
      w.put(kCLCL_val[v], kCLCL_len[v]);
    }
    for (i = 0; i < clN; i++) {
      bwHuff(w, clCodes[clSyms[i]]);
      if (clSyms[i] === 17) w.put(clExtra[i], 3);
    }
  }

  function writePrefix(w, freq, lens, nsym, alphaBits, codes) {
    var nu = countUsed(freq, nsym);
    if (nu <= 4) {
      writeSimpleCode(w, lens, nsym, alphaBits);
      var used = [];
      for (var i = 0; i < nsym && used.length < 4; i++) if (freq[i]) used.push(i);
      used.sort(function (a, b) { return a - b; });
      var n = used.length;

      for (i = 0; i < nsym; i++) { codes[i][0] = 0; codes[i][1] = 0; lens[i] = 0; }

      if (n === 1) {
        lens[used[0]] = 0; codes[used[0]] = [0, 0];
      } else if (n === 2) {
        lens[used[0]] = 1; codes[used[0]] = [0, 1];
        lens[used[1]] = 1; codes[used[1]] = [1, 1];
      } else if (n === 3) {
        lens[used[0]] = 1; codes[used[0]] = [0, 1];
        lens[used[1]] = 2; codes[used[1]] = [2, 2];
        lens[used[2]] = 2; codes[used[2]] = [3, 2];
      } else if (n === 4) {
        var treeSel = (lens[used[0]] === 1);
        if (treeSel) {
          lens[used[0]] = 1; codes[used[0]] = [0, 1];
          lens[used[1]] = 2; codes[used[1]] = [2, 2];
          lens[used[2]] = 3; codes[used[2]] = [6, 3];
          lens[used[3]] = 3; codes[used[3]] = [7, 3];
        } else {
          lens[used[0]] = 2; codes[used[0]] = [0, 2];
          lens[used[1]] = 2; codes[used[1]] = [1, 2];
          lens[used[2]] = 2; codes[used[2]] = [2, 2];
          lens[used[3]] = 2; codes[used[3]] = [3, 2];
        }
      }
    } else {
      writeComplexCode(w, lens, nsym);
    }
  }

  // ---- LZ77 ----
  function hash4(b, p) {
    var v = (b[p] | (b[p + 1] << 8) | (b[p + 2] << 16) | (b[p + 3] << 24));
    return (Math.imul(v, 0x1E35A7BD) >>> (32 - HASH_BITS));
  }

  function lzParse(data) {
    var n = data.length;
    if (n === 0) return [];
    var head = new Int32Array(HASH_SIZE).fill(-1);
    var prev = new Int32Array(n);
    var cmds = [];

    var ip = 0, litStart = 0;
    while (ip < n) {
      var bestLen = 0, bestDist = 0;
      if (ip + MIN_MATCH <= n) {
        var h = hash4(data, ip);
        var chain = head[h];
        var cc = 0;
        while (chain >= 0 && cc < MAX_CHAIN) {
          var dist = ip - chain;
          if (dist > WIN_SIZE) break;
          var maxl = n - ip;
          if (maxl > MAX_MATCH) maxl = MAX_MATCH;
          var ml = 0;
          while (ml < maxl && data[chain + ml] === data[ip + ml]) ml++;
          if (ml > bestLen && ml >= MIN_MATCH) {
            bestLen = ml; bestDist = dist;
            if (bestLen >= MAX_MATCH) break;
          }
          chain = prev[chain]; cc++;
        }
        prev[ip] = head[h];
        head[h] = ip;
      }
      if (bestLen >= MIN_MATCH) {
        cmds.push([ip - litStart, bestLen, bestDist]);
        var k = 1;
        while (k < bestLen && ip + k + MIN_MATCH <= n) {
          var hk = hash4(data, ip + k);
          prev[ip + k] = head[hk];
          head[hk] = ip + k;
          k++;
        }
        ip += bestLen;
        litStart = ip;
      } else {
        ip++;
      }
    }
    if (litStart < n) cmds.push([n - litStart, 0, 0]);
    return cmds;
  }

  function findInsCode(v) {
    for (var i = 23; i >= 0; i--) if (v >= kInsLen[i][0]) return [i, v - kInsLen[i][0], kInsLen[i][1]];
    return [0, 0, 0];
  }
  function findCopyCode(v) {
    for (var i = 23; i >= 0; i--) if (v >= kCopyLen[i][0]) return [i, v - kCopyLen[i][0], kCopyLen[i][1]];
    return [0, 0, 0];
  }

  function icSymbol(ic, cc, useDist) {
    var icOff = ic % 8, ccOff = cc % 8;
    var val = icOff * 8 + ccOff;
    if (!useDist) { if (cc < 8) return 0 + val; return 64 + val; }
    if (ic < 8) { if (cc < 8) return 128 + val; if (cc < 16) return 192 + val; return 384 + val; }
    if (ic < 16) { if (cc < 8) return 256 + val; if (cc < 16) return 320 + val; return 512 + val; }
    if (cc < 8) return 448 + val; if (cc < 16) return 576 + val; return 640 + val;
  }

  function findDistCode(dist) {
    if (dist === 0) return [0, 0, 0];
    var d = dist - 1;
    for (var hcode = 0; hcode < 48; hcode++) {
      var nb = 1 + (hcode >> 1);
      var off = ((2 + (hcode & 1)) << nb) - 4;
      if (d >= off && d - off < (1 << nb)) return [16 + hcode, d - off, nb];
    }
    return [16, 0, 0];
  }

  function encodeStored(data) {
    var n = data.length;
    if (n > 0xFFFFFF) throw new Error("too large for stored");
    var w = new BitW();
    w.put(0, 1);
    var remaining = n, ptr = 0;
    while (remaining > 0) {
      var chunk = remaining;
      if (chunk > (1 << 24) - 1) chunk = (1 << 24) - 1;
      w.put(0, 1);
      var mlen = chunk - 1;
      var mn = (mlen < (1 << 16)) ? 4 : (mlen < (1 << 20)) ? 5 : 6;
      w.put(mn - 4, 2);
      w.put(mlen, mn * 4);
      w.put(1, 1);
      w.align();
      w.finish();
      for (var i = 0; i < chunk; i++) w.out.push(data[ptr + i]);
      ptr += chunk;
      remaining -= chunk;
    }
    w.put(1, 1);
    w.put(1, 1);
    w.finish();
    return Uint8Array.from(w.out);
  }

  function encode(input) {
    var data = (input instanceof Uint8Array) ? input : Uint8Array.from(input);
    var n = data.length;
    if (n === 0) return Uint8Array.from([0x06]);
    if (n > 16 * 1024 * 1024) throw new Error("input too large");

    var cmds = lzParse(data);

    var litFreq = new Array(256).fill(0);
    var icFreq = new Array(704).fill(0);
    var distFreq = new Array(64).fill(0);

    var lp = 0, i, cmd, insLen, copyLen, distance, icode, ccode, hasDist, sym, dc, j;
    for (i = 0; i < cmds.length; i++) {
      cmd = cmds[i]; insLen = cmd[0]; copyLen = cmd[1]; distance = cmd[2];
      icode = findInsCode(insLen)[0];
      ccode = 0;
      if (copyLen) ccode = findCopyCode(copyLen)[0];
      hasDist = copyLen > 0;
      sym = icSymbol(icode, ccode, hasDist);
      if (sym >= 0 && sym < 704) icFreq[sym]++;
      for (j = 0; j < insLen && lp < n; j++) litFreq[data[lp++]]++;
      if (copyLen) {
        dc = findDistCode(distance)[0];
        if (dc < 64) distFreq[dc]++;
        lp += copyLen;
      }
    }

    var litLens = buildLengths(litFreq, 256);
    var icLens = buildLengths(icFreq, 704);
    var distLens = buildLengths(distFreq, 64);

    var litCodes = assignCodes(litLens, 256);
    var icCodes = assignCodes(icLens, 704);
    var distCodes = assignCodes(distLens, 64);

    var w = new BitW();
    w.put(0, 1); w.put(1, 1); w.put(0, 1);

    var mlen = n - 1;
    var mn = (mlen < (1 << 16)) ? 4 : (mlen < (1 << 20)) ? 5 : 6;
    w.put(mn - 4, 2);
    w.put(mlen, mn * 4);

    w.put(0, 1); w.put(0, 1); w.put(0, 1);
    w.put(0, 2); w.put(0, 4);
    w.put(0, 2);
    w.put(0, 1); w.put(0, 1);

    writePrefix(w, litFreq, litLens, 256, 8, litCodes);
    writePrefix(w, icFreq, icLens, 704, 10, icCodes);
    writePrefix(w, distFreq, distLens, 64, 6, distCodes);

    lp = 0;
    for (i = 0; i < cmds.length; i++) {
      cmd = cmds[i]; insLen = cmd[0]; copyLen = cmd[1]; distance = cmd[2];
      var ins = findInsCode(insLen);
      icode = ins[0];
      var ie = ins[1], ieb = ins[2];
      ccode = 0; var ce = 0, ceb = 0;
      if (copyLen) { var cp = findCopyCode(copyLen); ccode = cp[0]; ce = cp[1]; ceb = cp[2]; }
      hasDist = copyLen > 0;
      sym = icSymbol(icode, ccode, hasDist);

      bwHuff(w, icCodes[sym]);
      if (ieb > 0) w.put(ie, ieb);
      if (hasDist && ceb > 0) w.put(ce, ceb);

      for (j = 0; j < insLen && lp < n; j++) bwHuff(w, litCodes[data[lp++]]);

      if (hasDist) {
        var dd = findDistCode(distance);
        dc = dd[0]; var de = dd[1], deb = dd[2];
        bwHuff(w, distCodes[dc]);
        if (deb > 0) w.put(de, deb);
        lp += copyLen;
      }
    }

    w.finish();

    if (w.out.length >= n) return encodeStored(data);
    return Uint8Array.from(w.out);
  }

  function bound(inputLen) { return inputLen + ((inputLen / 64) | 0) + 64; }

  // ---- Decoder ----
  function BitR(data) { this.p = data; this.len = data.length; this.bit = 0; }
  BitR.prototype.read = function (n) {
    if (n < 0 || n > 24 || this.bit + n > this.len * 8) return -1;
    var v = 0;
    for (var i = 0; i < n; i++) {
      var bi = this.bit++;
      v |= (((this.p[bi >> 3] >> (bi & 7)) & 1) << i);
    }
    return v >>> 0;
  };
  BitR.prototype.alignByte = function () { this.bit = (this.bit + 7) & ~7; };

  function bitReverse(v, n) {
    var r = 0;
    for (var i = 0; i < n; i++) { r = ((r << 1) | (v & 1)) & 0xFFFF; v >>= 1; }
    return r;
  }

  function hdecBuild(lens, nsym) {
    var h = { len: new Array(nsym).fill(0), code: new Array(nsym).fill(0), nsym: nsym, single: -1 };
    var blCount = new Array(16).fill(0);
    for (var i = 0; i < nsym; i++) {
      if (lens[i] > 15) return null;
      h.len[i] = lens[i];
      if (lens[i]) blCount[lens[i]]++;
    }
    var next = new Array(16).fill(0);
    var c = 0;
    for (var b = 1; b < 16; b++) { c = ((c + blCount[b - 1]) << 1) & 0xFFFF; next[b] = c; }
    for (i = 0; i < nsym; i++) {
      if (h.len[i]) { var canon = next[h.len[i]]; next[h.len[i]] = (next[h.len[i]] + 1) & 0xFFFF; h.code[i] = bitReverse(canon, h.len[i]); }
    }
    return h;
  }

  function hdecSymbol(r, h) {
    if (h.single >= 0) return h.single;
    var code = 0;
    for (var len = 1; len <= 15; len++) {
      var bit = r.read(1);
      if (bit < 0) return -1;
      code |= (bit << (len - 1));
      for (var s = 0; s < h.nsym; s++) if (h.len[s] === len && h.code[s] === code) return s;
    }
    return -1;
  }

  function readClclSymbol(r) {
    var code = 0;
    for (var len = 1; len <= 4; len++) {
      var bit = r.read(1);
      if (bit < 0) return -1;
      code |= (bit << (len - 1));
      for (var v = 0; v <= 5; v++) if (kCLCL_len[v] === len && kCLCL_val[v] === code) return v;
    }
    return -1;
  }

  function readPrefixCode(r, nsym, alphaBits) {
    var lens = new Array(nsym).fill(0);
    var hskip = r.read(2);
    if (hskip < 0) return null;

    if (hskip === 1) {
      var nsymM1 = r.read(2);
      if (nsymM1 < 0) return null;
      var nn = nsymM1 + 1;
      var used = [0, 0, 0, 0];
      for (var i = 0; i < nn; i++) {
        var sym = r.read(alphaBits);
        if (sym < 0 || sym >= nsym) return null;
        used[i] = sym;
      }
      if (nn === 1) { var h1 = hdecBuild(lens, nsym); if (!h1) return null; h1.single = used[0]; return h1; }
      else if (nn === 2) { lens[used[0]] = 1; lens[used[1]] = 1; }
      else if (nn === 3) { lens[used[0]] = 1; lens[used[1]] = 2; lens[used[2]] = 2; }
      else {
        var treeSel = r.read(1);
        if (treeSel < 0) return null;
        if (treeSel) { lens[used[0]] = 1; lens[used[1]] = 2; lens[used[2]] = 3; lens[used[3]] = 3; }
        else { lens[used[0]] = 2; lens[used[1]] = 2; lens[used[2]] = 2; lens[used[3]] = 2; }
      }
      return hdecBuild(lens, nsym);
    }

    if (hskip > 3) return null;
    var clLens = new Array(18).fill(0);
    var space = 0;
    for (var ii = hskip; ii < 18; ii++) {
      var vv = readClclSymbol(r);
      if (vv < 0) return null;
      clLens[kCLOrder[ii]] = vv;
      if (vv) space += 1 << (5 - vv);
      if (ii + 1 >= 4 && space === 32) break;
      if (space > 32) return null;
    }

    var clh = hdecBuild(clLens, 18);
    if (!clh) return null;

    var pos = 0, codeSpace = 0;
    while (pos < nsym && codeSpace < (1 << 15)) {
      var sym2 = hdecSymbol(r, clh);
      if (sym2 < 0) return null;
      if (sym2 === 17) {
        var extra = r.read(3);
        if (extra < 0) return null;
        var run = 3 + extra;
        if (pos + run > nsym) return null;
        pos += run;
      } else if (sym2 >= 0 && sym2 <= 15) {
        lens[pos++] = sym2;
        if (sym2) { codeSpace += 1 << (15 - sym2); if (codeSpace > (1 << 15)) return null; }
      } else return null;
    }
    if (codeSpace !== (1 << 15)) return null;
    return hdecBuild(lens, nsym);
  }

  function decodeIcSymbol(sym) {
    if (sym < 0 || sym >= 704) return null;
    var base;
    if (sym < 64) { base = sym; return [base >> 3, base & 7, false]; }
    if (sym < 128) { base = sym - 64; return [base >> 3, 8 + (base & 7), false]; }
    if (sym < 192) { base = sym - 128; return [base >> 3, base & 7, true]; }
    if (sym < 256) { base = sym - 192; return [base >> 3, 8 + (base & 7), true]; }
    if (sym < 320) { base = sym - 256; return [8 + (base >> 3), base & 7, true]; }
    if (sym < 384) { base = sym - 320; return [8 + (base >> 3), 8 + (base & 7), true]; }
    if (sym < 448) { base = sym - 384; return [base >> 3, 16 + (base & 7), true]; }
    if (sym < 512) { base = sym - 448; return [16 + (base >> 3), base & 7, true]; }
    if (sym < 576) { base = sym - 512; return [8 + (base >> 3), 16 + (base & 7), true]; }
    if (sym < 640) { base = sym - 576; return [16 + (base >> 3), 8 + (base & 7), true]; }
    base = sym - 640; return [16 + (base >> 3), 16 + (base & 7), true];
  }

  function decodeDistance(r, dc) {
    if (dc < 16 || dc >= 64) return -1;
    var hcode = dc - 16;
    var nb = 1 + (hcode >> 1);
    var extra = r.read(nb);
    if (extra < 0) return -1;
    var off = ((2 + (hcode & 1)) << nb) - 4;
    return off + extra + 1;
  }

  function decode(input) {
    var data = (input instanceof Uint8Array) ? input : Uint8Array.from(input);
    var r = new BitR(data);
    var out = [];

    var v = r.read(1);
    if (v !== 0) throw new Error("bad WBITS");

    for (;;) {
      var islast = r.read(1);
      if (islast < 0) throw new Error("truncated");
      if (islast) {
        var islastempty = r.read(1);
        if (islastempty < 0) throw new Error("truncated");
        if (islastempty) return Uint8Array.from(out);
      }
      var mn = r.read(2);
      if (mn < 0 || mn > 2) throw new Error("bad MNIBBLES");
      var nibbles = 4 + mn;
      var mlenM1 = r.read(nibbles * 4);
      if (mlenM1 < 0) throw new Error("truncated");
      var mlen = mlenM1 + 1;

      if (!islast) {
        var isuncompressed = r.read(1);
        if (isuncompressed < 0) throw new Error("truncated");
        if (isuncompressed) {
          r.alignByte();
          var start = r.bit >> 3;
          if (start + mlen > r.len) throw new Error("truncated stored");
          for (var s = 0; s < mlen; s++) out.push(data[start + s]);
          r.bit += mlen * 8;
          continue;
        }
      }

      decodeCompressedMeta(r, mlen, out);
      if (islast) return Uint8Array.from(out);
    }
  }

  function decodeCompressedMeta(r, mlen, out) {
    var widths = [1, 1, 1, 2, 4, 2, 1, 1];
    for (var k = 0; k < 8; k++) {
      var v = r.read(widths[k]);
      if (v !== 0) throw new Error("unsupported meta-block");
    }
    var litH = readPrefixCode(r, 256, 8);
    if (!litH) throw new Error("bad literal code");
    var icH = readPrefixCode(r, 704, 10);
    if (!icH) throw new Error("bad ic code");
    var distH = readPrefixCode(r, 64, 6);
    if (!distH) throw new Error("bad dist code");

    var end = out.length + mlen;
    while (out.length < end) {
      var sym = hdecSymbol(r, icH);
      var dec = decodeIcSymbol(sym);
      if (!dec) throw new Error("bad ic symbol");
      var ic = dec[0], cc = dec[1], explicitDist = dec[2];
      var extra = r.read(kInsLen[ic][1]);
      if (extra < 0) throw new Error("truncated ins");
      var insLen = kInsLen[ic][0] + extra;
      var copyLen = 0;
      if (explicitDist) {
        extra = r.read(kCopyLen[cc][1]);
        if (extra < 0) throw new Error("truncated copy");
        copyLen = kCopyLen[cc][0] + extra;
      }
      if (out.length + insLen > end) throw new Error("ins overflow");
      for (var i = 0; i < insLen; i++) {
        var lit = hdecSymbol(r, litH);
        if (lit < 0 || lit > 255) throw new Error("bad literal");
        out.push(lit);
      }
      if (explicitDist) {
        var dc = hdecSymbol(r, distH);
        var dist = decodeDistance(r, dc);
        if (dist < 0) throw new Error("bad distance");
        if (dist === 0 || dist > out.length) throw new Error("bad back-distance");
        var src = out.length - dist;
        for (i = 0; i < copyLen; i++) out.push(out[src + i]);
        if (out.length > end) throw new Error("copy overflow");
      }
    }
  }

  return {
    encode: encode,
    decode: decode,
    compress: encode,
    decompress: decode,
    bound: bound
  };
});
