import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}`));
  await page.addInitScript(() => {
    window.__apiLog = [];
    const patch = () => {
      if (window.NX && window.NX.api && !window.__apiPatched) {
        window.__apiPatched = true;
        const orig = window.NX.api.get;
        window.NX.api.get = async function(url, opts) {
          const r = await orig.apply(this, arguments);
          window.__apiLog.push({ url, ok: r.ok, status: r.status, bodyLen: r.body ? JSON.stringify(r.body).length : 0, rows: r.body && r.body.rows ? r.body.rows.length : (r.body && r.body.nodes ? r.body.nodes.length : undefined) });
          return r;
        };
      }
    };
    // patch as soon as NX.api exists; poll via interval started in page.
    const iv = setInterval(patch, 5);
    setTimeout(() => clearInterval(iv), 4000);
  });
  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(8000);
  const out = await page.evaluate(() => ({
    apiLog: window.__apiLog,
    fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
    total: (document.getElementById('scc-total')||{}).textContent,
  }));
  fs.writeFileSync('tests/e2e_cc_phase4_apilog.json', JSON.stringify({ out, consoleAll }, null, 2));
  console.log(JSON.stringify({ out, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
