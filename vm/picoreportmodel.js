// picoreportmodel.js -- self-contained report-model -> natural-English lowering
// for the browser playground (the report-designer pre-compile step). Mirrors
// picoscript_reportmodel.py. Exposes:
//   PicoReportModel.toEnglish(modelOrJson) -> { source, warnings }
(function (root) {
  'use strict';

  var INT_MAX = 2147483647, INT_MIN = -2147483648;
  var VALID_AGG = ['count', 'sum', 'min', 'max'];

  function sanitizeId(name) {
    var s = String(name == null ? '' : name).replace(/[^A-Za-z0-9_]/g, '_');
    if (!s) s = '_v';
    if (/^[0-9]/.test(s)) s = '_' + s;
    return s;
  }

  var BINARY_WORD = {
    '==': 'is', '===': 'is', '!=': 'is not', '!==': 'is not',
    '>=': 'is at least', '<=': 'is at most', '>': 'is greater than', '<': 'is less than',
    '&&': 'and', '||': 'or', '+': 'plus', '-': 'minus', '*': 'times', '/': 'divided by', '%': 'modulo'
  };

  function tokenizeExpr(s) {
    var toks = [], i = 0, n = s.length;
    while (i < n) {
      var c = s.charAt(i);
      if (/\s/.test(c)) { i++; continue; }
      if (c === '"' || c === "'") {
        var q = c, j = i + 1, str = c;
        while (j < n) { var d = s.charAt(j); str += d; if (d === '\\' && j + 1 < n) { str += s.charAt(j + 1); j += 2; continue; } if (d === q) { j++; break; } j++; }
        toks.push({ t: 'str', v: str }); i = j; continue;
      }
      if (/[0-9]/.test(c)) { var jn = i; while (jn < n && /[0-9.]/.test(s.charAt(jn))) jn++; toks.push({ t: 'num', v: s.slice(i, jn) }); i = jn; continue; }
      if (/[A-Za-z_$]/.test(c)) { var ji = i; while (ji < n && /[A-Za-z0-9_$]/.test(s.charAt(ji))) ji++; toks.push({ t: 'id', v: s.slice(i, ji) }); i = ji; continue; }
      var three = s.substr(i, 3); if (three === '===' || three === '!==') { toks.push({ t: 'op', v: three }); i += 3; continue; }
      var two = s.substr(i, 2); if (two === '==' || two === '!=' || two === '>=' || two === '<=' || two === '&&' || two === '||') { toks.push({ t: 'op', v: two }); i += 2; continue; }
      if ('+-*/%<>!'.indexOf(c) >= 0) { toks.push({ t: 'op', v: c }); i++; continue; }
      toks.push({ t: 'punct', v: c }); i++;
    }
    return toks;
  }

  function translateExpr(src) {
    var toks = tokenizeExpr(String(src == null ? '' : src)), parts = [], prev = false;
    for (var i = 0; i < toks.length; i++) {
      var tk = toks[i];
      if (tk.t === 'op') {
        if ((tk.v === '-' || tk.v === '+') && !prev) { if (tk.v === '-') { parts.push('0'); parts.push('minus'); } prev = false; }
        else if (tk.v === '!' && !prev) { parts.push('not'); prev = false; }
        else { parts.push(BINARY_WORD.hasOwnProperty(tk.v) ? BINARY_WORD[tk.v] : tk.v); prev = false; }
      } else if (tk.t === 'id') { parts.push(sanitizeId(tk.v)); prev = true; }
      else if (tk.t === 'num' || tk.t === 'str') { parts.push(tk.v); prev = true; }
      else { parts.push(tk.v); prev = (tk.v === ')'); }
    }
    return parts.join(' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').replace(/\s+,/g, ',').replace(/\s*\.\s*/g, '.').replace(/\s+/g, ' ').trim();
  }

  function numLit(n) {
    if (typeof n !== 'number' || !isFinite(n)) return '0';
    n = Math.trunc(n);
    return n < 0 ? '(0 minus ' + Math.abs(n) + ')' : String(n);
  }

  function emitScalar(v) {
    if (v == null) return '0';
    if (typeof v === 'number') return numLit(v);
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'string') { var t = v.trim(); if (/^-?\d+$/.test(t)) return numLit(parseInt(t, 10)); return '0'; }
    return '0';
  }

  function toEnglish(modelOrJson) {
    var model = (typeof modelOrJson === 'string') ? JSON.parse(modelOrJson) : modelOrJson;
    if (!model || typeof model !== 'object' || Object.prototype.toString.call(model) === '[object Array]') {
      throw new Error('report model must be a JSON object');
    }
    var out = [], warnings = [], memBase = 8192;

    if (model.title != null) out.push('Print ' + emitScalar(model.title) + '.');

    var source = model.source || {};
    var kind = String(source.kind || 'array').toLowerCase();
    var length = 0, getBase = String(memBase), upper = '';
    if (kind === 'array') {
      var arr = Object.prototype.toString.call(source.values) === '[object Array]' ? source.values : [];
      for (var k = 0; k < arr.length; k++) out.push('Memory.Set(' + (memBase + k) + ', ' + emitScalar(arr[k]) + ').');
      length = arr.length; getBase = String(memBase); upper = numLit(length - 1);
    } else if (kind === 'variable') {
      var nm = sanitizeId(source.name || 'data');
      getBase = nm; upper = nm + '_len minus 1';
      warnings.push('report: variable source "' + (source.name || '') + '" assumed pre-materialised in Memory (base=' + nm + ', ' + nm + '_len)');
    } else {
      warnings.push('report: unknown source kind "' + kind + '"; empty report');
      upper = numLit(-1);
    }

    var aggregates = (model.aggregates || []).filter(function (a) { return VALID_AGG.indexOf(a) >= 0; });
    var rowExpr = model.row || 'item';
    var where = model.where;

    if (aggregates.indexOf('count') >= 0) out.push('Set _count to 0.');
    if (aggregates.indexOf('sum') >= 0) out.push('Set _sum to 0.');
    if (aggregates.indexOf('min') >= 0) out.push('Set _min to ' + numLit(INT_MAX) + '.');
    if (aggregates.indexOf('max') >= 0) out.push('Set _max to ' + numLit(INT_MIN) + '.');

    if (kind === 'variable' || length > 0) {
      out.push('For each _r0 from 0 to ' + upper + ':');
      out.push('    Set item to Memory.Get(' + getBase + ' plus _r0).');
      var ind = '    ';
      if (where) { out.push('    If ' + translateExpr(where) + ':'); ind = '        '; }
      out.push(ind + 'Set _row to ' + translateExpr(rowExpr) + '.');
      out.push(ind + 'Print _row.');
      if (aggregates.indexOf('count') >= 0) out.push(ind + 'Set _count to _count plus 1.');
      if (aggregates.indexOf('sum') >= 0) out.push(ind + 'Set _sum to _sum plus _row.');
      if (aggregates.indexOf('min') >= 0) { out.push(ind + 'If _row is less than _min:'); out.push(ind + '    Set _min to _row.'); }
      if (aggregates.indexOf('max') >= 0) { out.push(ind + 'If _row is greater than _max:'); out.push(ind + '    Set _max to _row.'); }
    }

    for (var a = 0; a < aggregates.length; a++) out.push('Print _' + aggregates[a] + '.');

    return { source: out.join('\n') + '\n', warnings: warnings };
  }

  var api = { toEnglish: toEnglish, translateExpr: translateExpr, VERSION: '1.0.0' };
  root.PicoReportModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
