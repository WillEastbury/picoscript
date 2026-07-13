// Headless Edge verification of the consolidated (vendored) showcase + playground.
const { chromium } = require(require('path').join(__dirname, '..', 'node_modules', 'playwright'));
const path = require('path');

function fileUrl(p) { return 'file:///' + path.resolve(p).replace(/\\/g, '/'); }

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const errors = [];
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });

  // ---- Showcase ----
  await page.goto(fileUrl(path.join(__dirname, '..', 'docs', 'showcase.html')));
  await page.waitForTimeout(400);

  // Build tab: compile & run
  await page.locator('#compile').click();
  await page.waitForTimeout(300);
  const out = await page.locator('#out').innerText();
  const okCompile = /\[2, 10, 3, 20, 1, 50\]/.test(out);

  // Report tab
  await page.evaluate(() => { location.hash = '#/report'; });
  await page.waitForTimeout(300);
  const report = await page.locator('#view').innerText();
  const okReport = /sum/i.test(report);

  // Form tab: save
  await page.evaluate(() => { location.hash = '#/form'; });
  await page.waitForTimeout(300);
  await page.locator('#save').click();
  await page.waitForTimeout(200);
  const savemsg = await page.locator('#savemsg').innerText();
  const okForm = /data ABI/.test(savemsg);

  // Activity tab
  await page.evaluate(() => { location.hash = '#/activity'; });
  await page.waitForTimeout(300);
  const log = await page.locator('#log').innerText();
  const okActivity = /flow\.compiled|order\.saved|report\.rendered/.test(log);

  console.log('SHOWCASE compile[2,10,3,20,1,50]:', okCompile);
  console.log('SHOWCASE report footer:', okReport, '|', report.split('\n').slice(-2).join(' '));
  console.log('SHOWCASE form save:', okForm, '|', savemsg);
  console.log('SHOWCASE activity events:', okActivity);

  // ---- Playground ----
  const perrors = [];
  const pg = await browser.newPage();
  pg.on('pageerror', e => perrors.push('pageerror: ' + e.message));
  pg.on('console', m => { if (m.type() === 'error') perrors.push('console.error: ' + m.text()); });
  await pg.goto(fileUrl(path.join(__dirname, '..', 'docs', 'playground.html')));
  await pg.waitForTimeout(800);
  // Drive the playground workflow designer (same vendored compile path as WebIDE)
  const pgwf = await pg.evaluate(() => {
    setLang('workflow');
    compileSrc(true);
    var host = document.getElementById('wfDesigner');
    return { out: DBG.vm.outputInts(), eng: host.innerHTML.indexOf('wf-eng') >= 0 };
  });
  const okPgWf = JSON.stringify(pgwf.out) === '[100]' && pgwf.eng;
  console.log('PLAYGROUND workflow compile [100]:', okPgWf, '|', JSON.stringify(pgwf));
  const pgErrs = perrors.filter(e => !/favicon/i.test(e));
  console.log('PLAYGROUND loaded, errors:', pgErrs.length);
  if (pgErrs.length) console.log(pgErrs.slice(0, 6).join('\n'));

  await browser.close();

  const showErrs = errors.filter(e => !/favicon/i.test(e));
  console.log('SHOWCASE page errors:', showErrs.length);
  if (showErrs.length) console.log(showErrs.slice(0, 6).join('\n'));

  const allOk = okCompile && okReport && okForm && okActivity && okPgWf && showErrs.length === 0 && pgErrs.length === 0;
  console.log(allOk ? '\nALL VERIFIED OK' : '\nVERIFICATION FAILED');
  process.exit(allOk ? 0 : 1);
})();
