// Playwright end-to-end check: Program Exits (routes) designer + dispatch via
// the HTTP Simulator (see gen_site.py routesRender/routeAdd/matchRoute/
// sendRequest/sendRequestDataRoute). Exercises all three route types:
// 'code' (a workflow file, the pre-existing mechanism now surfaced in a real
// UI for the first time), 'query', and 'list' (both new, over PicoStore).
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
  await page.waitForTimeout(1000);
  await page.click("button.tab:has-text('WebIDE')");
  await page.waitForTimeout(300);

  const result = await page.evaluate(() => {
    var out = {};
    var pack = 'exitpack' + Date.now();

    // Seed two records directly via PicoStore (the same engine Cards uses).
    STORE.create(pack, { qty: 3, note: 'a' });
    STORE.create(pack, { qty: 9, note: 'b' });

    // Create a tiny workflow file to route to (type='code').
    var wfFile = 'routes/hello.psc';
    var files = filesRead();
    files[wfFile] = { kind: 'code', lang: 'workflow', src: JSON.stringify([
      { type: 'RESPOND', status: 200, contentType: 'text/plain', body: 'hello-from-route' }
    ]), updated: Date.now() };
    filesWrite(files);

    openToolPanel('routes');

    // Route 1: GET /hello -> the workflow file.
    document.getElementById('routeMethod').value = 'GET';
    document.getElementById('routePath').value = '/hello';
    document.getElementById('routeType').value = 'code';
    routesRenderTargetInputs();
    document.getElementById('routeFile').value = wfFile;
    routeAdd();

    // Route 2: GET /items -> List(pack).
    document.getElementById('routeMethod').value = 'GET';
    document.getElementById('routePath').value = '/items';
    document.getElementById('routeType').value = 'list';
    routesRenderTargetInputs();
    document.getElementById('routePack').value = pack;
    routeAdd();

    // Route 3: GET /items/big -> Query(pack, "qty > 5").
    document.getElementById('routeMethod').value = 'GET';
    document.getElementById('routePath').value = '/items/big';
    document.getElementById('routeType').value = 'query';
    routesRenderTargetInputs();
    document.getElementById('routePack').value = pack;
    document.getElementById('routeQuery').value = 'qty > 5';
    routeAdd();

    out.routesTableRows = (document.getElementById('routesBody').innerHTML.match(/<tr>/g) || []).length;
    out.routesModel = routesRead();

    // Dispatch each route through the real HTTP Simulator (Send action).
    document.getElementById('reqmode').value = 'text';
    document.getElementById('reqbox').value = 'GET /hello HTTP/1.1\r\nHost: x\r\n\r\n';
    sendRequest();
    out.helloResponse = document.getElementById('respout').textContent;

    document.getElementById('reqbox').value = 'GET /items HTTP/1.1\r\nHost: x\r\n\r\n';
    sendRequest();
    out.itemsResponse = document.getElementById('respout').textContent;

    document.getElementById('reqbox').value = 'GET /items/big HTTP/1.1\r\nHost: x\r\n\r\n';
    sendRequest();
    out.bigResponse = document.getElementById('respout').textContent;

    // Unmatched path falls back to the currently-loaded program (unchanged
    // pre-existing behaviour) -- just confirm no route was matched, not the
    // exact fallback body (that depends on whatever sample is loaded).
    document.getElementById('reqbox').value = 'GET /nope HTTP/1.1\r\nHost: x\r\n\r\n';
    var matchedNope = matchRoute(methodCodeName('GET'), '/nope');
    out.nopeMatched = !!matchedNope;

    // Remove one route and confirm the table shrinks + matchRoute stops firing.
    routeRemove(0);
    out.routesAfterRemove = routesRead().length;
    out.helloMatchedAfterRemove = !!matchRoute(methodCodeName('GET'), '/hello');

    return out;
  });

  console.log('WEBIDE Program Exits E2E result:', JSON.stringify(result, null, 2));

  const ok = result.routesTableRows === 3 &&
    result.routesModel.length === 3 &&
    /routed to routes\/hello\.psc/.test(result.helloResponse) &&
    /hello-from-route/.test(result.helloResponse) &&
    /routed to List/.test(result.itemsResponse) && /"count":2/.test(result.itemsResponse) &&
    /routed to Query/.test(result.bigResponse) && /"count":1/.test(result.bigResponse) &&
    !result.nopeMatched &&
    result.routesAfterRemove === 2 &&
    !result.helloMatchedAfterRemove;

  await browser.close();
  const pageErrs = errors.filter(e => !/favicon|monaco|cdn\.jsdelivr/i.test(e));
  console.log('WEBIDE Program Exits page errors:', pageErrs.length);
  if (pageErrs.length) console.log(pageErrs.slice(0, 8).join('\n'));

  const allOk = ok && pageErrs.length === 0;
  console.log(allOk ? '\nWEBIDE PROGRAM EXITS E2E VERIFIED OK' : '\nWEBIDE PROGRAM EXITS E2E FAILED');
  process.exit(allOk ? 0 : 1);
})();
