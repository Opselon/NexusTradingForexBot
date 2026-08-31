import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}`));
  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(1500);
  const r = await page.evaluate(async () => {
    const out = {};
    const fres = await window.NX.api.get('/api/command-center/fleet', { component: 'scc', action: 'fleet' });
    out.fleetRowsRaw = (fres.body.rows||[]).length;
    try {
      window.NX.scc._test_renderFleet(fres.body.rows || []);
      out.afterRenderFleetRows = (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length;
    } catch (e) { out.renderFleetThrew = e.message + '\n' + (e.stack||''); }
    const ovRes = await window.NX.api.get('/api/command-center/overview', { component: 'scc', action: 'overview' });
    try {
      window.NX.scc._test_renderOverview(ovRes.body);
      out.afterRenderTotal = (document.getElementById('scc-total')||{}).textContent;
      out.afterRenderEvalMetrics = (document.getElementById('scc-eval-metrics')||{}).textContent.slice(0,60);
    } catch (e) { out.renderOverviewThrew = e.message + '\n' + (e.stack||''); }
    return out;
  });
  fs.writeFileSync('tests/e2e_cc_phase4_render.json', JSON.stringify({ r, consoleAll }, null, 2));
  console.log(JSON.stringify({ r, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
