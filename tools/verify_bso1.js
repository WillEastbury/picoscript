// Verify the picovm.js BSO1 reader/writer is byte-compatible with the real
// BareMetalJsTools BareMetal.Binary serializer (incl. HMAC-SHA256 signing).
const PicoVM = require('C:/source/picoscript/vm/picovm.js');
const Bin = require('C:/source/baremetaljstools/src/BareMetal.Binary.js');

const KS = (a) => a.map((c) => String.fromCharCode(c)).join('');
const bytesOf = (s) => Array.from(Buffer.from(s, 'utf8'));

// wireType codes (must match docs/MAP.md)
const WT = { Bool: 1, Int32: 6, Int64: 8, String: 14 };

function sputInt(map, name, v) { const kb = bytesOf(name); map.set('s' + KS(kb), { kk: 's', ki: 0, kb, vk: 'i', vi: v | 0, vb: null }); }
function sputStr(map, name, vb) { const kb = bytesOf(name); map.set('s' + KS(kb), { kk: 's', ki: 0, kb, vk: 's', vi: 0, vb }); }

(async () => {
  const key = Array.from({ length: 32 }, (_, i) => (i * 7 + 3) & 0xFF);
  const schema = { version: 1, members: [
    { name: 'Qty', wireType: 'Int32' },
    { name: 'Sku', wireType: 'String' },
    { name: 'Big', wireType: 'Int64' },
    { name: 'Flag', wireType: 'Bool' }
  ] };
  const obj = { Qty: 42, Sku: 'ABC', Big: 5n, Flag: true };

  // reference blob from the real BareMetal.Binary (signed with the key)
  await Bin.setSigningKeyBytes(new Uint8Array(key));
  const blobRef = Array.from(new Uint8Array(await Bin.serialize(obj, schema)));

  // VM: build schema + data maps, set key, serialize
  const vm = new PicoVM();
  vm.maps = [null]; vm.activeMap = 0; vm.bso1Key = key;
  vm.maps.push(new Map()); const schH = vm.maps.length - 1;
  const sm = vm.maps[schH];
  sputInt(sm, ':version', 1);
  sputInt(sm, 'Qty', WT.Int32); sputInt(sm, 'Sku', WT.String); sputInt(sm, 'Big', WT.Int64); sputInt(sm, 'Flag', WT.Bool);
  vm.maps.push(new Map()); const dataH = vm.maps.length - 1;
  const dm = vm.maps[dataH];
  sputInt(dm, 'Qty', 42); sputStr(dm, 'Sku', bytesOf('ABC')); sputStr(dm, 'Big', [5, 0, 0, 0, 0, 0, 0, 0]); sputInt(dm, 'Flag', 1);

  vm.regs[1] = dataH; vm.regs[2] = schH;
  vm._parseHook('Binary.SerializeEntity', 0, 1, 2);
  const blobVm = vm._spanBytes(vm.regs[0]);

  const same = JSON.stringify(blobVm) === JSON.stringify(blobRef);
  console.log('SERIALIZE byte-identical to BareMetal.Binary:', same, '(' + blobVm.length + ' bytes)');
  if (!same) { console.log('vm :', blobVm.join(',')); console.log('ref:', blobRef.join(',')); }

  // VM: Verify the reference blob with the same key
  const blobRefSpan = vm._newSpanBytes(blobRef); vm.regs[3] = blobRefSpan;
  vm._parseHook('Binary.Verify', 0, 3, 0);
  const verified = vm.regs[0] === 1;
  console.log('VERIFY reference blob:', verified);

  // VM: ParseEntity the reference blob back into a Map
  vm.regs[4] = blobRefSpan; vm.regs[5] = schH;
  vm._parseHook('Binary.ParseEntity', 0, 4, 5);
  const res = vm.maps[vm.regs[0]];
  const qty = res.get('s' + KS(bytesOf('Qty')));
  const sku = res.get('s' + KS(bytesOf('Sku')));
  const big = res.get('s' + KS(bytesOf('Big')));
  const flag = res.get('s' + KS(bytesOf('Flag')));
  const okParse = qty.vi === 42 && KS(sku.vb) === 'ABC' && JSON.stringify(big.vb) === JSON.stringify([5, 0, 0, 0, 0, 0, 0, 0]) && flag.vi === 1;
  console.log('PARSE fields:', okParse, '| qty=' + qty.vi + ' sku=' + KS(sku.vb) + ' flag=' + flag.vi + ' big=' + big.vb.join(','));

  const allOk = same && verified && okParse;
  console.log(allOk ? '\nBSO1 VERIFIED OK (wire-compatible with BareMetal.Binary)' : '\nBSO1 FAILED');
  process.exit(allOk ? 0 : 1);
})();
