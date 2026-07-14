// tests/test_map_hooks.js -- conformance for the Map.* primitive on the JS
// reference VM (picovm.js), driven through the English compiler (picoc.js).
// Run: node tests/test_map_hooks.js
const path = require('path');
const PicoCompile = require(path.join(__dirname, '..', 'vm', 'picoc.js'));
const PicoVM = require(path.join(__dirname, '..', 'vm', 'picovm.js'));

function runEnglish(src) {
  const r = PicoCompile.compileDebug(src, 'english');
  const vm = new PicoVM();
  vm.run(r.words.map((w) => w >>> 0));
  // Print writes each int as 4 big-endian bytes into vm.output.
  const out = vm.output, ints = [];
  for (let i = 0; i + 3 < out.length; i += 4) {
    ints.push(((out[i] << 24) | (out[i + 1] << 16) | (out[i + 2] << 8) | out[i + 3]) | 0);
  }
  return ints;
}

let pass = 0, fail = 0;
function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log('PASS ' + name); }
  else { fail++; console.log('FAIL ' + name + ' -> got ' + g + ' want ' + w); }
}

// int keys + int values + count
eq('int keys/values + count', runEnglish(
  'Set h to Map.New().\n' +
  'Map.PutII(1, 100).\n' +
  'Map.PutII(2, 200).\n' +
  'Map.PutII(1, 111).\n' +          // overwrite keeps one entry
  'Print Map.GetII(1).\n' +
  'Print Map.GetII(2).\n' +
  'Print Map.Count().\n'
), [111, 200, 2]);

// has / del
eq('has + del', runEnglish(
  'Set h to Map.New().\n' +
  'Map.PutII(5, 50).\n' +
  'Print Map.HasI(5).\n' +
  'Print Map.HasI(9).\n' +
  'Map.DelI(5).\n' +
  'Print Map.HasI(5).\n' +
  'Print Map.Count().\n'
), [1, 0, 0, 0]);

// string keys + int values
eq('string keys', runEnglish(
  'Set h to Map.New().\n' +
  'Map.PutSI("qty", 42).\n' +
  'Map.PutSI("age", 7).\n' +
  'Print Map.GetSI("qty").\n' +
  'Print Map.GetSI("age").\n' +
  'Print Map.HasS("qty").\n' +
  'Print Map.HasS("nope").\n'
), [42, 7, 1, 0]);

// insertion-order enumeration
eq('enumeration order', runEnglish(
  'Set h to Map.New().\n' +
  'Map.PutSI("a", 10).\n' +
  'Map.PutSI("b", 20).\n' +
  'Map.PutSI("c", 30).\n' +
  'Print Map.ValAt(0).\n' +
  'Print Map.ValAt(1).\n' +
  'Print Map.ValAt(2).\n' +
  'Print Map.Count().\n'
), [10, 20, 30, 3]);

// null values distinguished from absent
eq('null vs absent', runEnglish(
  'Set h to Map.New().\n' +
  'Map.PutNullI(3).\n' +
  'Print Map.HasI(3).\n' +
  'Print Map.IsNullI(3).\n' +
  'Print Map.IsNullI(4).\n'
), [1, 1, 0]);

// two independent maps via Use
eq('two maps + Use', runEnglish(
  'Set a to Map.New().\n' +
  'Map.PutII(1, 10).\n' +
  'Set b to Map.New().\n' +
  'Map.PutII(1, 99).\n' +
  'Map.Use(a).\n' +
  'Print Map.GetII(1).\n' +
  'Map.Use(b).\n' +
  'Print Map.GetII(1).\n'
), [10, 99]);

// FNV-1a hash is stable/deterministic (same span hashes equal)
eq('hash determinism', runEnglish(
  'Set x to Map.Hash("Content-Type").\n' +
  'Set y to Map.Hash("Content-Type").\n' +
  'Print x minus y.\n'
), [0]);

console.log('\n' + (fail === 0 ? 'ALL ' + pass + ' PASSED' : fail + ' FAILED, ' + pass + ' passed'));
process.exit(fail === 0 ? 0 : 1);
