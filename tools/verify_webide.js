// Headless Edge verification of the WebIDE (docs/index.html) workflow + report surfaces.
const { chromium } = require(require('path').join(__dirname, '..', 'node_modules', 'playwright'));
const path = require('path');
function fileUrl(p) { return 'file:///' + path.resolve(p).replace(/\\/g, '/'); }

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const errors = [];
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1400, height: 900 }); // deterministic geometry for layout checks
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });

  await page.goto(fileUrl(path.join(__dirname, '..', 'docs', 'index.html')));
  await page.waitForTimeout(1200); // let monaco/init settle

  // Switch to the WebIDE view via the real tab button
  await page.click("button.tab:has-text('WebIDE')");
  await page.waitForTimeout(300);

  // ---- Layout / DOM-structure integrity (catches unbalanced <div> regressions) ----
  // A dropped/extra tag can close .ide-editor early, collapsing the editor column
  // and floating the debugger. Assert the editor column has real width, Monaco
  // rendered, the overlay modals are hidden and correctly nested, and the debugger
  // bar spans the width.
  const layout = await page.evaluate(() => {
    function r(sel) { var e = document.querySelector(sel); if (!e) return null; var b = e.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height) }; }
    var editor = document.querySelector('.ide-editor');
    var childIds = editor ? Array.from(editor.children).map(function (c) { return c.id || c.className; }) : [];
    return {
      ideEditor: r('.ide-editor'),
      monaco: r('#monaco'),
      controls: r('.controls'),
      dbgBar: r('#playDbgBar'),
      // overlay modals must be present, hidden by default, and nested inside .ide-editor
      pkgHidden: getComputedStyle(document.getElementById('pkgModal')).display === 'none',
      jsonHidden: getComputedStyle(document.getElementById('wfJsonModal')).display === 'none',
      pkgNested: !!(editor && editor.contains(document.getElementById('pkgModal'))),
      jsonNested: !!(editor && editor.contains(document.getElementById('wfJsonModal'))),
      hasWfDesigner: !!(editor && editor.contains(document.getElementById('wfDesigner'))),
      childIds: childIds
    };
  });
  const okLayout = layout.ideEditor && layout.ideEditor.w >= 500 &&
    layout.monaco && layout.monaco.w >= 400 && layout.monaco.h >= 80 &&
    layout.controls && layout.controls.w >= 400 &&
    layout.dbgBar && layout.dbgBar.w >= 1000 &&
    layout.pkgHidden && layout.jsonHidden &&
    layout.pkgNested && layout.jsonNested && layout.hasWfDesigner;
  console.log('WEBIDE layout integrity:', okLayout, '|', JSON.stringify(layout));

  // ---- Workflow surface (now the BareMetal.Workflow.Designer christmas-tree canvas) ----
  const wf = await page.evaluate(() => {
    setLang('workflow');
    compileSrc(true);
    var host = document.getElementById('wfFlow');
    return {
      out: DBG.vm.outputInts(),
      visible: document.getElementById('wfDesigner').style.display,
      nodes: host.querySelectorAll('.fc-node').length,
      ifBranches: host.querySelectorAll('.fc-node[data-type="IF"] .fc-branch').length,
      chips: host.querySelectorAll('.fc-palette .fc-chip').length,
      eng: document.getElementById('wfEng').textContent.indexOf('For each') >= 0
    };
  });
  const okWf = JSON.stringify(wf.out) === '[100]' && wf.visible === 'block' && wf.nodes >= 4 &&
    wf.ifBranches === 2 && wf.chips > 5 && wf.eng;
  console.log('WEBIDE workflow compile [100]:', okWf, '|', JSON.stringify(wf));

  // Designer edit via the Designer controller re-syncs & recompiles
  const wfAdd = await page.evaluate(() => {
    var before = wfParseSteps().length;
    FLOW.addNode('LOG', null, null, before);   // append a LOG box at root
    compileSrc(true);
    return { before: before, after: wfParseSteps().length, out: DBG.vm.outputInts() };
  });
  const okWfAdd = wfAdd.after === wfAdd.before + 1 && wfAdd.out.length === 2 && wfAdd.out[0] === 100;
  console.log('WEBIDE designer add-step re-syncs:', okWfAdd, '|', JSON.stringify(wfAdd));

  // WEB step lowers to a request Map + Http.Request and compiles cleanly
  const web = await page.evaluate(() => {
    var steps = [{ type: 'WEB', method: 'POST', url: '/api/orders', headers: { Accept: 'application/json' }, result: 'resp' }];
    setSrc(JSON.stringify(steps, null, 2));
    wfRenderDesigner();
    compileSrc(false);
    var eng = document.getElementById('wfEng').textContent;
    return {
      hasMap: eng.indexOf('Map.New()') >= 0 && eng.indexOf('Http.Request(') >= 0,
      hasHeader: eng.indexOf('Map.PutSS("Accept", "application/json")') >= 0,
      compiled: (document.getElementById('cerr').textContent || '').indexOf('compiled') >= 0
    };
  });
  const okWeb = web.hasMap && web.hasHeader && web.compiled;
  console.log('WEBIDE WEB -> Map + Http.Request:', okWeb, '|', JSON.stringify(web));

  // ---- Workflow first-class round-trip fidelity (any dialect -> workflow -> run) ----
  // Raise a control-flow program into workflow steps via the shared AST, lower it
  // back through WorkflowPico -> English, and assert identical VM output.
  const fidelity = await page.evaluate(() => {
    function runWords(src, lang) { var r = PicoCompile.compileDebug(src, lang); var vm = new PicoVM({}); vm.run(r.words.map(function (w) { return w >>> 0; })); return vm.outputInts(); }
    var cases = [
      ['c', 'int sum=0; for(int i=1;i<=5;i=i+1){ sum=sum+i; } if(sum>=10){print(sum);}else{print(0);}'],
      ['c', 'int n=3; while(n>0){ print(n); n=n-1; }'],
      ['basic', 'x=2\nSWITCH x\n CASE 1\n  PRINT 10\n CASE 2\n  PRINT 20\n DEFAULT\n  PRINT 99\nENDSWITCH'],
      ['c', 'int a=1; int b=0; if(a && !b){print(1);}else{print(0);}']
    ];
    return cases.map(function (c) {
      var direct = runWords(c[1], c[0]);
      var wf = PicoCompile.toWorkflow(c[1], c[0]);          // raise
      var eng = wfCompileSrc(wf).source;                    // lower (WorkflowPico)
      var via = runWords(eng, 'english');
      return { lang: c[0], ok: JSON.stringify(direct) === JSON.stringify(via), direct: direct, via: via };
    });
  });
  const okFidelity = fidelity.every(function (f) { return f.ok; });
  console.log('WEBIDE workflow round-trip fidelity:', okFidelity, '|', JSON.stringify(fidelity));

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

  const allOk = okLayout && okWf && okWfAdd && okWeb && okFidelity && okReport && okForm && pageErrs.length === 0;
  console.log(allOk ? '\nWEBIDE ALL VERIFIED OK' : '\nWEBIDE VERIFICATION FAILED');
  process.exit(allOk ? 0 : 1);
})();
