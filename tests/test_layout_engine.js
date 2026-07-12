// tests/test_layout_engine.js -- JS layout engine smoke + form collect().
// Run: node tests/test_layout_engine.js
const path = require('path');
const L = require(path.join(__dirname, '..', 'vm', 'picolayout.js'));

let fails = 0;
function check(name, cond, detail) {
  if (cond) console.log('PASS ' + name);
  else { fails++; console.log('FAIL ' + name + (detail ? '  -> ' + detail : '')); }
}

var TMPL = {
  title: 'Orders',
  columns: [{ label: 'Qty', field: 0, width: 5 }, { label: 'Price', field: 1, width: 6 }],
  aggregates: [{ column: 0, fn: 'sum' }, { column: 1, fn: 'max' }]
};

var txt = L.renderText([2, 10, 3, 20, 1, 50], TMPL).split('\n');
check('text report title', txt[0] === 'Orders');
check('text report footer', /sum=6/.test(txt[txt.length - 2]) && /max=50/.test(txt[txt.length - 2]));

var html = L.renderHtml([1, 2, 3], { columns: [{ label: 'V', field: 0 }], aggregates: [{ column: 0, fn: 'sum' }] }, 'report');
check('html report table', html.indexOf('<table class="pico-report">') >= 0 && html.indexOf('<td>1</td>') >= 0 && html.indexOf('sum=6') >= 0);

var form = L.renderHtml([2, 10], { columns: [{ label: 'Qty', field: 0 }, { label: 'Price', field: 1, editable: false }] }, 'form');
check('form has input + output', form.indexOf('<input') >= 0 && form.indexOf('<output data-field="1"') >= 0);

// aggregate functions
function agg(fn) { return L.renderText([4, 2, 6, 8], { columns: [{ label: 'N', field: 0 }], aggregates: [{ column: 0, fn: fn }] }).split('\n').filter(Boolean).pop(); }
check('agg count', agg('count') === 'count=4');
check('agg avg', agg('avg') === 'avg=5');

// form write-back round-trip: collect() -> toWrites() -> data ABI map.
// Simulate a rendered form with a minimal fake element (no DOM needed).
function fakeInput(row, field, value) {
  return { getAttribute: function (a) { return a === 'data-row' ? String(row) : a === 'data-field' ? String(field) : null; }, value: String(value) };
}
var inputs = [fakeInput(0, 0, 2), fakeInput(0, 1, 99), fakeInput(1, 0, 3), fakeInput(1, 1, 20)];
var fakeForm = { querySelectorAll: function () { return inputs; } };
var rows = L.collect(fakeForm);
check('collect rows', JSON.stringify(rows) === JSON.stringify([[2, 99], [3, 20]]), JSON.stringify(rows));
check('flatten', JSON.stringify(L.flatten(rows)) === JSON.stringify([2, 99, 3, 20]));
var writes = L.toWrites(rows, { base: 8192 });
// row 0 field 1 (edited to 99) -> key 8192 + 0*2 + 1 = 8193
check('toWrites edited value', writes[8193] === 99, JSON.stringify(writes));

if (fails) { console.log('\n' + fails + ' failed'); process.exit(1); }
console.log('\nall passed');
