// Playwright end-to-end check: RBAC/RLS Designer + enforcement in Cards/Query
// (offline localStorage simulator mode -- see gen_site.py rbacGetModel/
// rbacRenderDesigner/rbacCanAct/rbacRowPredicate, wired into cardCreate/
// cardCreateTyped/cardDelete/cardSeed/cardClear/cardRender/cardQuery).
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
    var pack = 'rbacpack' + Date.now();

    // Build an RBAC/RLS file via the real Designer functions: a "viewer" role
    // that may only read+create in `pack`, plus a row policy restricting
    // viewer's visibility to status=1 rows.
    var name = rbacQuickNew();
    out.fileCreated = !!name;
    document.getElementById('rbacRoleName').value = 'viewer';
    rbacAddRole();
    document.getElementById('rbacPermRole').value = 'viewer';
    document.getElementById('rbacPermPack').value = pack;
    document.getElementById('rbacAct_read').checked = true;
    document.getElementById('rbacAct_create').checked = true;
    rbacAddPermission();
    document.getElementById('rbacPolPack').value = pack;
    document.getElementById('rbacPolRole').value = 'viewer';
    document.getElementById('rbacPolPred').value = 'status = 1';
    rbacAddRowPolicy();
    var model = rbacGetModel();
    out.modelRoles = model.roles;
    out.modelPerms = model.permissions;
    out.modelPolicies = model.rowPolicies;

    // Seed data directly (bypassing RBAC, as an unrestricted "admin" would)
    // then switch the active role to "viewer" and exercise Cards/Query.
    document.getElementById('packname').value = pack;
    document.getElementById('cardjson').value = JSON.stringify({ status: 1, note: 'visible' });
    cardCreate();
    document.getElementById('cardjson').value = JSON.stringify({ status: 0, note: 'hidden' });
    cardCreate();

    rbacSetCurrentRole('viewer');
    cardRender();
    out.cardListAsViewer = document.getElementById('cardlist').innerHTML;
    out.cardRowCountAsViewer = (out.cardListAsViewer.match(/<tr>/g) || []).length;

    document.getElementById('querybox').value = '';
    cardQuery();
    out.queryRowCountAsViewer = (document.getElementById('qresults').innerHTML.match(/<tr>/g) || []).length;

    // Viewer has no "delete" permission -> cardDelete should be blocked.
    var beforeDeleteMsg = document.getElementById('cardmsg').textContent;
    cardDelete(0);
    out.deleteBlockedMsg = document.getElementById('cardmsg').textContent;
    out.deleteMsgChanged = out.deleteBlockedMsg !== beforeDeleteMsg;

    // Switch back to unrestricted (blank role) and confirm both rows are visible.
    rbacSetCurrentRole('');
    document.getElementById('rbacRoleInput').value = '';
    cardRender();
    out.cardRowCountUnrestricted = (document.getElementById('cardlist').innerHTML.match(/<tr>/g) || []).length;

    return out;
  });

  console.log('WEBIDE RBAC/RLS E2E result:', JSON.stringify(result, null, 2));

  const ok = result.fileCreated &&
    result.modelRoles.indexOf('viewer') >= 0 &&
    result.modelPerms.length === 1 && result.modelPerms[0].actions.sort().join(',') === 'create,read' &&
    result.modelPolicies.length === 1 && result.modelPolicies[0].predicate === 'status = 1' &&
    result.cardRowCountAsViewer === 1 &&               // RLS hides the status=0 row
    /visible/.test(result.cardListAsViewer) && !/hidden/.test(result.cardListAsViewer) &&
    result.queryRowCountAsViewer === 1 &&               // Query also respects the row policy
    /cannot delete/.test(result.deleteBlockedMsg) &&    // permission check blocks delete
    result.cardRowCountUnrestricted === 2;              // no role = unrestricted, both rows visible

  await browser.close();
  const pageErrs = errors.filter(e => !/favicon|monaco|cdn\.jsdelivr/i.test(e));
  console.log('WEBIDE RBAC/RLS page errors:', pageErrs.length);
  if (pageErrs.length) console.log(pageErrs.slice(0, 8).join('\n'));

  const allOk = ok && pageErrs.length === 0;
  console.log(allOk ? '\nWEBIDE RBAC/RLS E2E VERIFIED OK' : '\nWEBIDE RBAC/RLS E2E FAILED');
  process.exit(allOk ? 0 : 1);
})();
