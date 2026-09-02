import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}\n${(e.stack||'').split('\n').slice(0,5).join('\n')}`));

  // Patch addEventListener to detect if/how DOMContentLoaded listener fires.
  await page.addInitScript(() => {
    window.__bootLog = [];
    const orig = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, fn, opts) {
      if (type === 'DOMContentLoaded') {
        window.__bootLog.push('addEventListener DOMContentLoaded registered on ' + (this === document ? 'document' : 'other'));
        const wrapped = function(ev) { try { window.__bootLog.push('DOMContentLoaded FIRED'); return fn(ev); } catch(e){ window.__bootLog.push('DOMContentLoaded HANDLER THREW: ' + e.message + '\n' + (e.stack||'')); throw e; } };
        return orig.call(this, type, wrapped, opts);
      }
      return orig.call(this, type, fn, opts);
    };
  });

  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(9000);

  const out = await page.evaluate(() => ({
    bootLog: window.__bootLog,
    fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
    total: (document.getElementById('scc-total')||{}).textContent,
    lastFleet: (window.NX.scc._test_getLastFleet ? window.NX.scc._test_getLastFleet().length : 'n/a'),
  }));

  fs.writeFileSync('tests/e2e_cc_phase4_boot.json', JSON.stringify({ out, consoleAll }, null, 2));
  console.log(JSON.stringify({ out, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
