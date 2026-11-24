/* Timer.Once/Every and host-delivered on Timer.On share the same event ABI
 * in the browser compiler and reference VM. Run with:
 * node --test tests/test_timer_callbacks.js */
const test = require('node:test');
const assert = require('node:assert/strict');
const PicoCompile = require('../vm/picoc.js');
const PicoVM = require('../vm/picovm.js');

function run(src, language = 'c') {
  const compiled = PicoCompile.compile(src, language);
  const vm = new PicoVM();
  vm.load(compiled.words);
  vm.run();
  return { compiled, vm };
}

test('Timer.Once delivers the declared event ID to on Timer.On', () => {
  const { vm } = run('int h = Timer.Once(10,42); Scheduler.Tick(10); on Timer.On { Io.WriteByte(Event.Target(__event__)); }');
  assert.deepEqual(vm.output, [42]);
});

test('Timer.Every coalesces delayed ticks for named events', () => {
  const { vm } = run('int h = Timer.Every(10,42); Scheduler.Tick(35); on Timer.On { Io.WriteByte(Event.Target(__event__)); }');
  assert.deepEqual(vm.output, [42]);
});

test('Timer.Cancel prevents delivery', () => {
  const { vm } = run('int h = Timer.Once(10,42); Timer.Cancel(h); Scheduler.Tick(10); on Timer.On { Io.WriteByte(77); }');
  assert.deepEqual(vm.output, []);
});

test('Timer.On cannot be called by guest bytecode', () => {
  assert.throws(() => PicoCompile.compile('Timer.On(42);', 'c'), /host-delivered only/);
});

test('Timer.Once and named handlers compile in BASIC and Python frontends', () => {
  assert.ok(PicoCompile.compile('DIM H = Timer.Once(10,42)\nON Timer.On\n PRINT 7\nEND ON\n', 'basic').words.length);
  assert.ok(PicoCompile.compile('h = Timer.Once(10,42)', 'python').words.length);
});
