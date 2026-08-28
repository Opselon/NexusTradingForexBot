import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}\n${e.stack||''}`));

  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2000);

  const res = await page.evaluate(async () => {
    const out = { steps: [] };
    try {
      out.steps.push('calling NX.scc.load()');
      const r = await window.NX.scc.load();
      out.steps.push('load() returned: ' + JSON.stringify(r));
    } catch (e) {
      out.steps.push('load() THREW: ' + e.message + '\n' + (e.stack||''));
    }
    await new Promise(res => setTimeout(res, 500));
    out.header = {
      total: (document.getElementById('scc-total')||{}).textContent,
      blocked: (document.getElementById('scc-blocked')||{}).textContent,
      fleetNote: (document.getElementById('scc-fleet-note')||{}).textContent,
    };
    const tbody = document.getElementById('scc-fleet-tbody');
    out.fleetRowsInDom = tbody ? tbody.children.length : 'NO_TBODY';
    out.evalMetricsText = (document.getElementById('scc-eval-metrics')||{}).textContent || '';
    out.lastFleet = (window.NX.scc._test_getLastFleet ? window.NX.scc._test_getLastFleet().length : 'n/a');
    out.spatialData = (window.NX.scc._test_getSpatialData ? (window.NX.scc._test_getSpatialData()?.nodes?.length) : 'n/a');
    // canvas pixel check
    const c = document.getElementById('scc-spatial-canvas');
    out.canvas = c ? { w:c.width, h:c.height } : 'none';
    return out;
  });

  fs.writeFileSync('tests/e2e_cc_phase4_invoke.json', JSON.stringify({ res, consoleAll }, null, 2));
  console.log(JSON.stringify({ res, consoleAll: consoleAll.slice(0,30) }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
