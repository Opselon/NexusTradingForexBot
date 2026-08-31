import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  const apiCalls = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}`));
  page.on('request', r => { if (r.url().includes('command-center')) apiCalls.push('REQ ' + r.url()); });
  page.on('response', async r => {
    if (r.url().includes('command-center')) {
      let body = '';
      try { body = (await r.text()).slice(0, 300); } catch(e) { body = '<<unreadable>>'; }
      apiCalls.push(`RES ${r.status()} ${r.url()} :: ${body}`);
    }
  });

  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(7000);
  // After boot, manually call load and capture the .ok/.body/status the page sees.
  const manual = await page.evaluate(async () => {
    const r1 = await window.NX.api.get('/api/command-center/overview', { component: 'scc', action: 'overview' });
    const r2 = await window.NX.api.get('/api/command-center/fleet', { component: 'scc', action: 'fleet' });
    const r3 = await window.NX.api.get('/api/command-center/spatial', { component: 'scc', action: 'spatial' });
    return {
      overview: { ok: r1.ok, status: r1.status, hasBody: !!r1.body, total: r1.body && r1.body.total_strategies },
      fleet: { ok: r2.ok, status: r2.status, hasBody: !!r2.body, rows: r2.body && (r2.body.rows||[]).length },
      spatial: { ok: r3.ok, status: r3.status, hasBody: !!r3.body, nodes: r3.body && Array.isArray(r3.body.nodes) ? r3.body.nodes.length : 'n/a' },
    };
  });

  fs.writeFileSync('tests/e2e_cc_phase4_net.json', JSON.stringify({ apiCalls, manual, consoleAll }, null, 2));
  console.log(JSON.stringify({ apiCalls: apiCalls.slice(0,20), manual, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
