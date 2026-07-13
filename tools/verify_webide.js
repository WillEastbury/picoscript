// Headless Edge verification of the WebIDE (docs/index.html) workflow + report surfaces.
const { chromium } = require(require('path').join(__dirname, '..', 'node_modules', 'playwright'));
const path = require('path');
function fileUrl(p) { return 'file:///' + path.resolve(p).replace(/\\/g, '/'); }

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const errors = [];
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });

  await page.goto(fileUrl(path.join(__dirname, '..', 'docs', 'index.html')));
  await page.waitForTimeout(1200); // let monaco/init settle

  // Switch to the WebIDE view via the real tab button
  await page.click("button.tab:has-text('WebIDE')");
  await page.waitForTimeout(300);

  // ---- Workflow surface ----
  const wf = await page.evaluate(() => {
    setLang('workflow');
    compileSrc(true);
    var host = document.getElementById('wfDesigner');
    return {
      out: DBG.vm.outputInts(),
      visible: host.style.display,
      hasRows: host.innerHTML.indexOf('wf-row') >= 0,
      hasEng: host.innerHTML.indexOf('wf-eng') >= 0
    };
  });
  const okWf = JSON.stringify(wf.out) === '[100]' && wf.visible === 'block' && wf.hasRows && wf.hasEng;
  console.log('WEBIDE workflow compile [100]:', okWf, '|', JSON.stringify(wf));

  // Designer edit -> add a step re-syncs & recompiles
  const wfAdd = await page.evaluate(() => {
    var steps = wfParseSteps();
    var before = steps.length;
    steps.push({ type: 'LOG', message: 'sum' });
    wfWriteSteps(steps);
    compileSrc(true);
    return { before: before, after: wfParseSteps().length, out: DBG.vm.outputInts() };
  });
  const okWfAdd = wfAdd.after === wfAdd.before + 1 && JSON.stringify(wfAdd.out) === '[100,100]';
  console.log('WEBIDE designer add-step re-syncs:', okWfAdd, '|', JSON.stringify(wfAdd));

  // ---- Report / Form ----
  const rep = await page.evaluate(() => {
    document.getElementById('lang').value = 'basic';
    CUR_LANG = 'basic';
    if (typeof wfToggle === 'function') wfToggle();
    setSrc('PRINT 2\nPRINT 10\nPRINT 3\nPRINT 20\nPRINT 1\nPRINT 50\n');
    compileSrc(true);
    openToolPanel('report');
    renderLayout();
    return { out: DBG.vm.outputInts(), text: document.getElementById('layoutText').textContent, html: document.getElementById('layoutPreview').innerHTML.indexOf('pico-report') >= 0 };
  });
  const okReport = JSON.stringify(rep.out) === '[2,10,3,20,1,50]' && /sum=6/.test(rep.text) && /sum=80/.test(rep.text) && rep.html;
  console.log('WEBIDE report render:', okReport, '|', rep.text.replace(/\n/g, ' '));

  // Form mode + save write-back
  const form = await page.evaluate(() => {
    document.querySelector('input[name="layoutMode"][value="form"]').checked = true;
    renderLayout();
    layoutSave();
    return { msg: document.getElementById('layoutSaveMsg').textContent };
  });
  const okForm = /data ABI/.test(form.msg) && /"0":2/.test(form.msg);
  console.log('WEBIDE form save write-back:', okForm, '|', form.msg);

  await browser.close();
  const pageErrs = errors.filter(e => !/favicon|monaco|cdn\.jsdelivr/i.test(e));
  console.log('WEBIDE page errors (excl. monaco/cdn):', pageErrs.length);
  if (pageErrs.length) console.log(pageErrs.slice(0, 8).join('\n'));

  const allOk = okWf && okWfAdd && okReport && okForm && pageErrs.length === 0;
  console.log(allOk ? '\nWEBIDE ALL VERIFIED OK' : '\nWEBIDE VERIFICATION FAILED');
  process.exit(allOk ? 0 : 1);
})();
