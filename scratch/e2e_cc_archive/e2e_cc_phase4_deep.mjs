// Deep front-end render probe: does data reach the DOM? capture console + node/row counts.
import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}`));
  page.on('response', r => { if (r.status() >= 400) consoleAll.push(`[HTTP ${r.status()}] ${r.url()}`); });

  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(6000);

  const probe = await page.evaluate(async () => {
    const out = {};
    // 1. Is window.NX.api present and functional?
    out.nx_api_type = (window.NX && window.NX.api) ? typeof window.NX.api.get : 'MISSING';
    // 2. Manually invoke the same fetch the page uses and inspect result shape.
    let apiProbe = {};
    try {
      const r = await window.NX.api.get('/api/command-center/spatial', { component: 'scc', action: 'spatial' });
      apiProbe = { ok: r.ok, status: r.status, hasBody: !!r.body, nodes: r.body && Array.isArray(r.body.nodes) ? r.body.nodes.length : 'n/a', keys: r.body ? Object.keys(r.body) : [] };
    } catch (e) { apiProbe = { error: String(e) }; }
    out.api_spatial = apiProbe;
    // 3. fleet rows in DOM
    const tbody = document.getElementById('scc-fleet-tbody');
    out.fleetRowsInDom = tbody ? tbody.children.length : 'NO_TBODY';
    // 4. total/active/blocked header text
    out.header = {
      total: (document.getElementById('scc-total')||{}).textContent,
      active: (document.getElementById('scc-active')||{}).textContent,
      blocked: (document.getElementById('scc-blocked')||{}).textContent,
      valid: (document.getElementById('scc-valid')||{}).textContent,
      fleetNote: (document.getElementById('scc-fleet-note')||{}).textContent,
    };
    // 5. spatial canvas: did renderer place nodes? check canvas pixel non-empty + any overlay DOM
    const canvas = document.getElementById('scc-spatial-canvas');
    out.canvas = canvas ? { w: canvas.width, h: canvas.height } : 'NO_CANVAS';
    // 6. eval-metrics panel text
    out.evalMetricsText = (document.getElementById('scc-eval-metrics')||{}).textContent || '';
    out.bottleneckText = (document.getElementById('scc-research-bottleneck')||{}).textContent || '';
    // 7. console body rows
    const cbody = document.getElementById('scc-console-body');
    out.consoleRows = cbody ? cbody.children.length : 'NO_CONSOLE_BODY';
    // 8. did loadCommandCenter run? window.NX.scc exposure
    out.nx_scc = typeof window.NX.scc;
    return out;
  });

  fs.writeFileSync('tests/e2e_cc_phase4_deep.json', JSON.stringify({ probe, consoleAll }, null, 2));
  console.log(JSON.stringify({ probe, consoleAll: consoleAll.slice(0,40) }, null, 2));
  await browser.close();
})().catch(e => { console.error('DEEP FAIL', e); process.exit(1); });
