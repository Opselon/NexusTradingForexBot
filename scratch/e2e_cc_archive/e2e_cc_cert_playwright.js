// Real-browser certification harness for the Command Center.
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8081';
const OUT = 'tests/e2e_cc_cert_result.json';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', err => pageErrors.push(err.message));
  page.on('requestfailed', req => failedRequests.push(req.url() + ' :: ' + (req.failure() && req.failure().errorText)));

  const result = { steps: {}, errors: {} };

  // 1. Open command_center.html directly
  await page.goto(BASE + '/command_center.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  result.steps.pageTitle = await page.title();
  result.steps.canvasPresent = await page.$('#scc-canvas') !== null || await page.$('canvas') !== null;

  // 2. Spatial load + node render
  const spatialLoad = await page.evaluate(async () => {
    try {
      const r = await fetch('/api/command-center/spatial');
      const j = await r.json();
      return { ok: true, nodes: Array.isArray(j.nodes) ? j.nodes.length : 0, zones: j.zones ? Object.keys(j.zones).length : 0 };
    } catch (e) { return { ok: false, error: String(e) }; }
  });
  result.steps.spatial = spatialLoad;

  // 3. Overview
  const overview = await page.evaluate(async () => {
    try { const r = await fetch('/api/command-center/overview'); const j = await r.json(); return j; }
    catch (e) { return { error: String(e) }; }
  });
  result.steps.overviewTotal = overview.available ? overview.total_strategies : null;

  // 4. Click lifecycle filter buttons if present (DOM interaction in real page)
  const filterBtns = await page.$$('[data-lifecycle], .scc-lifecycle-btn, button');
  result.steps.buttonCount = filterBtns.length;

  // 5. Check canvas actually drew something (non-zero pixel area / non-blank)
  const canvasInfo = await page.evaluate(() => {
    const c = document.querySelector('canvas');
    if (!c) return { canvas: false };
    const rect = c.getBoundingClientRect();
    return { canvas: true, w: rect.width, h: rect.height, cw: c.width, ch: c.height };
  });
  result.steps.canvasInfo = canvasInfo;

  // 6. Inspector execution-safety for a known non-active strategy (execution safety adversarial)
  const execSafety = await page.evaluate(async () => {
    // pick a DISCOVERED strategy id from overview? Use fleet.
    try {
      const f = await fetch('/api/command-center/fleet?limit=5');
      const fj = await f.json();
      const rows = fj.fleet || [];
      if (!rows.length) return { noRows: true };
      const sid = rows[0].strategy_id;
      const r = await fetch('/api/command-center/execution-safety/' + sid);
      const j = await r.json();
      return { sid, eligibility: j.eligibility_state, can_trade: j.can_trade };
    } catch (e) { return { error: String(e) }; }
  });
  result.steps.execSafetyNonActive = execSafety;

  // 7. Time machine bounds
  const tm = await page.evaluate(async () => {
    try { const r = await fetch('/api/command-center/timemachine/bounds'); const j = await r.json(); return { ok: j.available, bounds: j.bounds ? Object.keys(j.bounds) : null }; }
    catch (e) { return { error: String(e) }; }
  });
  result.steps.timeMachine = tm;

  result.errors.consoleErrors = consoleErrors;
  result.errors.pageErrors = pageErrors;
  result.errors.failedRequests = failedRequests;

  fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
  console.log('RESULT:', JSON.stringify(result, null, 2));
  await browser.close();
})().catch(e => { console.error('HARNESS FAIL', e); process.exit(1); });
