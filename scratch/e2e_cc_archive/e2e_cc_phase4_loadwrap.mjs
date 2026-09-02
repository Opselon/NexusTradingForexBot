import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}\n${(e.stack||'').split('\n').slice(0,8).join('\n')}`));
  await page.addInitScript(() => {
    // Wrap loadCommandCenter once NX.scc exists.
    const iv = setInterval(() => {
      if (window.NX && window.NX.scc && window.NX.scc.load && !window.__loadWrapped) {
        window.__loadWrapped = true;
        clearInterval(iv);
        const orig = window.NX.scc.load;
        window.NX.scc.load = async function(...a) {
          try { window.__loadResult = 'entered'; const r = await orig.apply(this, a); window.__loadResult = 'completed:' + JSON.stringify(r); return r; }
          catch (e) { window.__loadResult = 'THREW: ' + e.message + '\n' + (e.stack||''); throw e; }
        };
        // Also wrap the THREE render fns used by loadCommandCenter.
        for (const [name, fn] of [['renderOverview','_test_renderOverview'],['renderFleetTable','_test_renderFleet']]) {
          // we can't easily wrap the internal fns; instead wrap spatial.update
        }
        if (window.NX.spatial && window.NX.spatial.update) {
          const so = window.NX.spatial.update;
          window.NX.spatial.update = function(d){ window.__spatialUpdateCalled = true; try { return so.call(this,d); } catch(e){ window.__spatialUpdateThrew = e.message; throw e; } };
        }
      }
    }, 5);
    setTimeout(() => clearInterval(iv), 6000);
  });
  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(8000);
  const out = await page.evaluate(() => ({
    loadResult: window.__loadResult,
    spatialUpdateCalled: window.__spatialUpdateCalled,
    spatialUpdateThrew: window.__spatialUpdateThrew,
    fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
    total: (document.getElementById('scc-total')||{}).textContent,
  }));
  fs.writeFileSync('tests/e2e_cc_phase4_loadwrap.json', JSON.stringify({ out, consoleAll }, null, 2));
  console.log(JSON.stringify({ out, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
