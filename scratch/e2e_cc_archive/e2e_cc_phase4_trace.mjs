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
    window.__trace = [];
    // Poll until NX.scc.load exists, then install a wrapper that records entry/exit.
    const iv = setInterval(() => {
      try {
        if (window.NX && window.NX.scc && typeof window.NX.scc.load === 'function' && !window.__loadWrapped) {
          window.__loadWrapped = true;
          clearInterval(iv);
          const orig = window.NX.scc.load;
          window.NX.scc.load = async function() {
            window.__trace.push('LOADED_ENTER'); try { const r = await orig.apply(this, arguments); window.__trace.push('LOADED_OK'); return r; }
            catch (e) { window.__trace.push('LOADED_THREW: ' + e.message); throw e; }
          };
          const oshow = window.NX.scc.onShow;
          window.NX.scc.onShow = async function() { window.__trace.push('ONSHOW_ENTER'); try { return await oshow.apply(this, arguments); } catch(e){ window.__trace.push('ONSHOW_THREW: '+e.message); throw e; } };
        }
      } catch(e) {}
    }, 3);
    setTimeout(() => clearInterval(iv), 8000);
  });
  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(9000);
  const out = await page.evaluate(() => ({
    trace: window.__trace,
    fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
    total: (document.getElementById('scc-total')||{}).textContent,
  }));
  fs.writeFileSync('tests/e2e_cc_phase4_trace.json', JSON.stringify({ out, consoleAll }, null, 2));
  console.log(JSON.stringify({ out, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
