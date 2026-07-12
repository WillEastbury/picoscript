// tests/test_translate_case.js -- round-trip identifier-case preservation across
// the cross-language translator (picoc.js). Run: node tests/test_translate_case.js
const path = require('path');
const P = require(path.join(__dirname, '..', 'vm', 'picoc.js'));

let fails = 0;
function check(name, cond, detail) {
  if (cond) { console.log('PASS ' + name); }
  else { fails++; console.log('FAIL ' + name + (detail ? '  -> ' + detail : '')); }
}
function t(src, a, b) { return P.translate(src, a, b); }

// 1. C -> BASIC keeps original identifier case (declarations were being uppercased)
var bas = t('int Total = 3;\nint itemCount = Total * 2;\nprint(itemCount);\n', 'c', 'basic');
check('c->basic preserves case', /DIM Total\b/.test(bas) && /DIM itemCount\b/.test(bas) && !/TOTAL|ITEMCOUNT/.test(bas), JSON.stringify(bas));

// 2. Multi-hop round-trip is case-stable (and internally consistent)
var s = 'int Total = 3;\nint itemCount = Total * 2;\nprint(itemCount);\n';
['c', 'english', 'python', 'basic', 'c'].reduce(function (a, b) { s = t(s, a, b); return b; });
check('multi-hop case stable', s.indexOf('Total') >= 0 && s.indexOf('itemCount') >= 0 && !/TOTAL|ITEMCOUNT/.test(s), JSON.stringify(s));

// 3. Labels / gotos preserve case in BASIC
var lab = t('int loopStart = 0;\nprint(loopStart);\n', 'c', 'basic');
check('c->basic assignment case', /loopStart/.test(lab) && !/LOOPSTART/.test(lab), JSON.stringify(lab));

// 4. Function/sub names preserve case
var subEn = t('int addTwo(int x){ return x + 2; }\nprint(addTwo(3));\n', 'c', 'basic');
check('c->basic sub name case', /addTwo/.test(subEn) && !/ADDTWO/.test(subEn), JSON.stringify(subEn));

if (fails) { console.log('\n' + fails + ' failed'); process.exit(1); }
console.log('\nall passed');
