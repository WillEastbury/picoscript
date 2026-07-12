// picolayout.js -- the templated layout engine (PicoScript reports + forms), for
// the browser playground. Stage 2 of the 2-stage report/form model: stage 1 is an
// ordinary PicoScript data-producer program whose VM output is a flat list of
// ints; this renders that data with a layout template.
//
// One engine, two modes:
//   "report" -> read-only (text, or an HTML table + aggregate footer)
//   "form"   -> read-write (an HTML form of labelled inputs bound to the data)
//
// A report is a data program with a read-only layout; a form is the same with a
// read-write layout. Shape-aligned with picolayout.py. Exposes:
//   PicoLayout.renderText(data, template) -> string
//   PicoLayout.renderHtml(data, template, mode) -> string
//   PicoLayout.render(data, template, mode) -> string   (text if template.output==="text")
//   PicoLayout.collect(formEl) -> rows   (form write-back: read inputs back out)
(function (root) {
  'use strict';

  function chunk(data, ncols) {
    ncols = Math.max(1, ncols);
    var rows = [];
    for (var i = 0; i < data.length; i += ncols) rows.push(data.slice(i, i + ncols));
    return rows;
  }

  function fmt(value, f) {
    if (value == null) return '';
    if (f === 'hex') { var n = Number(value) >>> 0; return '0x' + n.toString(16); }
    if (f === 'raw') return String(value);
    var iv = parseInt(value, 10);
    return isNaN(iv) ? String(value) : String(iv);
  }

  function columns(template) {
    var cols = template.columns || template.fields || [];
    return cols.map(function (c) { return (c && typeof c === 'object') ? c : { label: String(c) }; });
  }

  function colField(col, idx) { return (typeof col.field === 'number') ? col.field : idx; }

  function agg(rows, col, fn) {
    var vals = [];
    for (var i = 0; i < rows.length; i++) { var v = rows[i][col]; if (typeof v === 'number') vals.push(v); }
    if (fn === 'count') return vals.length;
    if (!vals.length) return 0;
    if (fn === 'sum') return vals.reduce(function (a, b) { return a + b; }, 0);
    if (fn === 'min') return Math.min.apply(null, vals);
    if (fn === 'max') return Math.max.apply(null, vals);
    if (fn === 'avg') return Math.trunc(vals.reduce(function (a, b) { return a + b; }, 0) / vals.length);
    return 0;
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function padEnd(s, w) { s = String(s); while (s.length < w) s += ' '; return s; }
  function padStart(s, w) { s = String(s); while (s.length < w) s = ' ' + s; return s; }

  function renderText(data, template) {
    var cols = columns(template);
    if (!cols.length) return '';
    var widths = cols.map(function (c) { return Math.max(3, parseInt(c.width, 10) || 8); });
    var lines = [];
    if (template.title) lines.push(String(template.title));
    lines.push(cols.map(function (c, i) { return padEnd(c.label || '', widths[i]); }).join('  '));
    lines.push(widths.map(function (w) { return new Array(w + 1).join('-'); }).join('  '));
    var rows = chunk(data.slice(), cols.length);
    rows.forEach(function (row) {
      var cells = cols.map(function (c, i) {
        var fi = colField(c, i);
        var s = fmt(fi < row.length ? row[fi] : null, c.format || 'int');
        return c.align === 'right' ? padStart(s, widths[i]) : padEnd(s, widths[i]);
      });
      lines.push(cells.join('  '));
    });
    var aggs = template.aggregates || [];
    if (aggs.length) {
      lines.push(widths.map(function (w) { return new Array(w + 1).join('-'); }).join('  '));
      lines.push(aggs.map(function (a) {
        var fn = a.fn || 'sum';
        return fn + '=' + agg(rows, parseInt(a.column, 10) || 0, fn);
      }).join('  '));
    }
    return lines.join('\n') + '\n';
  }

  function renderHtml(data, template, mode) {
    var cols = columns(template);
    mode = (mode || template.mode || 'report').toLowerCase();
    var rows = chunk(data.slice(), cols.length || 1);

    if (mode === 'form') {
      var out = ['<form class="pico-form">'];
      if (template.title) out.push('<h3 class="pico-form-title">' + esc(template.title) + '</h3>');
      rows.forEach(function (row, ri) {
        out.push('<div class="pico-form-row" data-row="' + ri + '">');
        cols.forEach(function (c, i) {
          var fi = colField(c, i);
          var v = fi < row.length ? row[fi] : 0;
          var label = esc(c.label || '');
          var sval = esc(fmt(v, c.format || 'int'));
          var editable = c.editable !== false;
          if (editable) {
            out.push('<label class="pico-field"><span>' + label + '</span>' +
              '<input name="c' + i + '" data-field="' + fi + '" data-row="' + ri + '" value="' + sval + '"></label>');
          } else {
            out.push('<label class="pico-field"><span>' + label + '</span>' +
              '<output data-field="' + fi + '" data-row="' + ri + '">' + sval + '</output></label>');
          }
        });
        out.push('</div>');
      });
      out.push('</form>');
      return out.join('\n') + '\n';
    }

    var t = ['<table class="pico-report">'];
    if (template.title) t.push('<caption>' + esc(template.title) + '</caption>');
    t.push('<thead><tr>' + cols.map(function (c) { return '<th>' + esc(c.label || '') + '</th>'; }).join('') + '</tr></thead>');
    t.push('<tbody>');
    rows.forEach(function (row) {
      var cells = cols.map(function (c, i) {
        var fi = colField(c, i);
        return '<td>' + esc(fmt(fi < row.length ? row[fi] : null, c.format || 'int')) + '</td>';
      }).join('');
      t.push('<tr>' + cells + '</tr>');
    });
    t.push('</tbody>');
    var aggs = template.aggregates || [];
    if (aggs.length) {
      var cellmap = {};
      aggs.forEach(function (a) { var col = parseInt(a.column, 10) || 0; var fn = a.fn || 'sum'; cellmap[col] = fn + '=' + agg(rows, col, fn); });
      var tf = '';
      for (var i = 0; i < cols.length; i++) tf += '<td>' + esc(cellmap[i] || '') + '</td>';
      t.push('<tfoot><tr>' + tf + '</tr></tfoot>');
    }
    t.push('</table>');
    return t.join('\n') + '\n';
  }

  function render(data, template, mode) {
    if (typeof template === 'string') template = JSON.parse(template);
    if ((template.output || 'html').toLowerCase() === 'text') return renderText(data, template);
    return renderHtml(data, template, mode);
  }

  // Form write-back: read a rendered form's inputs back into rows of ints, so the
  // caller can persist them via the data ABI (Context/Memory scratch, Storage).
  function collect(formEl) {
    if (!formEl || !formEl.querySelectorAll) return [];
    var rows = {};
    formEl.querySelectorAll('input[data-row]').forEach(function (inp) {
      var r = parseInt(inp.getAttribute('data-row'), 10) || 0;
      var f = parseInt(inp.getAttribute('data-field'), 10) || 0;
      var val = parseInt(inp.value, 10); if (isNaN(val)) val = 0;
      (rows[r] = rows[r] || {})[f] = val;
    });
    return Object.keys(rows).sort(function (a, b) { return a - b; }).map(function (r) {
      var obj = rows[r], keys = Object.keys(obj).map(Number).sort(function (a, b) { return a - b; });
      var arr = []; keys.forEach(function (k) { arr[k] = obj[k]; });
      return arr;
    });
  }

  // Flatten rows (row-major) back to the flat int list the layout engine consumed.
  function flatten(rows) {
    var out = [];
    (rows || []).forEach(function (row) { (row || []).forEach(function (v) { out.push(v | 0); }); });
    return out;
  }

  // Write-back into the data ABI: rows -> { key: value } scratch/memory map keyed
  // by (base + rowIndex*stride + field). `stride` defaults to the widest row so a
  // stage-1 program can read each field back via Context.GetScratchValue/Memory.Get.
  function toWrites(rows, opts) {
    opts = opts || {};
    var base = opts.base | 0;
    var stride = opts.stride | 0;
    if (!stride) { stride = 0; (rows || []).forEach(function (r) { stride = Math.max(stride, (r || []).length); }); stride = Math.max(1, stride); }
    var map = {};
    (rows || []).forEach(function (row, ri) {
      (row || []).forEach(function (v, fi) { map[base + ri * stride + fi] = v | 0; });
    });
    return map;
  }

  var api = { renderText: renderText, renderHtml: renderHtml, render: render,
    collect: collect, flatten: flatten, toWrites: toWrites, VERSION: '1.0.0' };
  root.PicoLayout = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
