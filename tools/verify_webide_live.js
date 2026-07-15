// Playwright end-to-end check: WebIDE "live server" mode against the real
// native host/picowal app_server (see docs/PICOSCRIPT_UNIFIED_RUNTIME.md /
// host/picowal/app_router.eng). Exercises Cards create/list/delete, Schema
// Designer push-to-live + validation round-trip, and Query in live mode
// (client-side PicoStore.compileQuery over records fetched from the real
// server), all through the real UI functions -- not direct HTTP calls.
//
// Usage: build+start host/picowal/app_server.exe first (see README in that
// dir), pass its base URL as argv[2] (default http://localhost:8110).
const { chromium } = require(require('path').join(__dirname, '..', 'node_modules', 'playwright'));
const path = require('path');
function fileUrl(p) { return 'file:///' + path.resolve(p).replace(/\\/g, '/'); }
const LIVE_URL = process.argv[2] || 'http://localhost:8110';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const errors = [];
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });

  await page.goto(fileUrl(path.join(__dirname, '..', 'docs', 'index.html')));
  await page.waitForTimeout(1000);
  await page.click("button.tab:has-text('WebIDE')");
  await page.waitForTimeout(300);

  const result = await page.evaluate(async (liveUrl) => {
    liveServerSet(liveUrl);
    var out = { liveUrlStored: liveServerUrl() };

    // Push a schema for a fresh, randomized pack name (avoid collisions with
    // any prior run's data sitting in the server's append-log file).
    var packName = 'e2epack' + Date.now();
    var schemaName = 'schemas/' + packName + '.schema.json';
    var files = filesRead();
    files[schemaName] = { kind: 'schema', lang: '', src: JSON.stringify({ fields: [{ name: 'qty', type: 'int' }, { name: 'note', type: 'str' }] }), updated: Date.now() };
    filesWrite(files);
    await psFilesOpen(schemaName);
    await schemaPushLive();
    await new Promise(function (r) { setTimeout(r, 150); }); // let the fetch settle

    // Cards: set the active pack, create a valid + an invalid record via the
    // real Cards UI (typed quick-add form, since the pack is schema-bound).
    document.getElementById('packname').value = packName;
    cardRender(); queryRenderChips();
    await new Promise(function (r) { setTimeout(r, 300); });
    out.formVisible = document.getElementById('cardTypedForm').style.display;

    document.getElementById('cf_qty').value = '7';
    document.getElementById('cf_note').value = 'widget';
    cardCreateTyped();
    await new Promise(function (r) { setTimeout(r, 400); });
    out.afterCreate1 = document.getElementById('cardmsg').textContent;

    document.getElementById('cf_qty').value = '9';
    document.getElementById('cf_note').value = 'gadget';
    cardCreateTyped();
    await new Promise(function (r) { setTimeout(r, 400); });
    out.afterCreate2 = document.getElementById('cardmsg').textContent;

    out.cardListHtmlHasTwoRows = (document.getElementById('cardlist').innerHTML.match(/<tr>/g) || []).length;

    // Invalid create: genuinely missing the required "note" field (an empty
    // string, unlike a missing key, is still a present+type-consistent value
    // per the schema validator -- see host/picowal/storage_file.c pwf_validate
    // -- so this uses the raw JSON path (cardCreate), which is always wired
    // to the fixed "Create" button regardless of schema binding).
    document.getElementById('cardjson').value = JSON.stringify({ qty: 1 });
    cardCreate();
    await new Promise(function (r) { setTimeout(r, 400); });
    out.afterInvalidCreate = document.getElementById('cardmsg').textContent;
    out.cardListHtmlAfterInvalid = (document.getElementById('cardlist').innerHTML.match(/<tr>/g) || []).length;

    // Query (live mode): richer DSL than the server's field=value filter,
    // applied client-side over the live-fetched list. Both valid records
    // (qty 7, 9) satisfy qty > 5.
    document.getElementById('querybox').value = 'qty > 5';
    cardQuery();
    await new Promise(function (r) { setTimeout(r, 400); });
    out.queryResultRows = (document.getElementById('qresults').innerHTML.match(/<tr>/g) || []).length;
    out.queryMsg = document.getElementById('cardmsg').textContent;

    // Delete via the real UI, then confirm the list shrinks.
    var firstRowMatch = /onclick="cardDelete\((\d+)\)"/.exec(document.getElementById('cardlist').innerHTML);
    out.deleteTargetFound = !!firstRowMatch;
    if (firstRowMatch) {
      cardDelete(parseInt(firstRowMatch[1], 10));
      await new Promise(function (r) { setTimeout(r, 500); });
    }
    out.cardListHtmlAfterDelete = (document.getElementById('cardlist').innerHTML.match(/<tr>/g) || []).length;

    // Turn live mode back off and confirm the simulator status text returns.
    liveServerSet('');
    out.statusAfterOff = document.getElementById('liveServerStatus').textContent;

    return out;
  }, LIVE_URL);

  console.log('WEBIDE live-server E2E result:', JSON.stringify(result, null, 2));

  const ok = result.liveUrlStored === LIVE_URL &&
    result.formVisible === 'flex' &&
    /created \(live\)/.test(result.afterCreate1) &&
    /created \(live\)/.test(result.afterCreate2) &&
    result.cardListHtmlHasTwoRows === 2 &&
    !/created/.test(result.afterInvalidCreate) &&         // rejected (missing "note"), not created
    result.cardListHtmlAfterInvalid === 2 &&               // rejection did not add a row
    result.queryResultRows === 2 &&                        // both qty=7 and qty=9 satisfy "qty > 5"
    result.cardListHtmlAfterDelete === 1 &&
    result.statusAfterOff === 'offline (localStorage simulator)';

  await browser.close();
  // A 400 (Bad Request) console.error is EXPECTED here: the invalid-create
  // test above deliberately triggers a real 400 response from the live
  // server's schema validation (Chromium logs every failed fetch() response
  // as a console error regardless of whether the app handles it, which this
  // one correctly does -- see afterInvalidCreate above).
  const pageErrs = errors.filter(e => !/favicon|monaco|cdn\.jsdelivr|400 \(Bad Request\)/i.test(e));
  console.log('WEBIDE live-server page errors:', pageErrs.length);
  if (pageErrs.length) console.log(pageErrs.slice(0, 8).join('\n'));

  const allOk = ok && pageErrs.length === 0;
  console.log(allOk ? '\nWEBIDE LIVE-SERVER E2E VERIFIED OK' : '\nWEBIDE LIVE-SERVER E2E FAILED');
  process.exit(allOk ? 0 : 1);
})();
