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

  // Wait until UI is populated (poll fleet rows; give up to 50s).
  let pop = 0;
  for (let i=0;i<20;i++){ await page.waitForTimeout(3000); pop = await page.evaluate(()=>(document.getElementById('scc-fleet-tbody')||{children:[]}).children.length); if (pop>0) break; }
  const out = { populatedFleetRows: pop };

  // TEST 4 + 6 (UI): click a DISCOVERED row -> inspector opens, shows CAN-THIS-TRADE verdict from backend.
  // Find a DISCOVERED row in the fleet table and click it.
  const clickRes = await page.evaluate(async () => {
    const tbody = document.getElementById('scc-fleet-tbody');
    const rows = Array.from(tbody ? tbody.children : []);
    const disc = rows.find(r => r.innerText.includes('DISCOVERED'));
    if (!disc) return { clicked: false };
    disc.click();
    await new Promise(r=>setTimeout(r,800));
    const drawer = document.getElementById('scc-inspector-drawer');
    const title = document.getElementById('scc-insp-title');
    const content = document.getElementById('scc-insp-content');
    return {
      clicked: true,
      drawerOpen: drawer ? !drawer.classList.contains('translate-x-full') : false,
      title: title ? title.textContent : null,
      contentHasVerdict: content ? /CAN THIS STRATEGY TRADE|eligibility|BLOCKED|UNKNOWN|YES/.test(content.textContent) : false,
      contentSample: content ? content.textContent.replace(/\s+/g,' ').slice(0,400) : null,
    };
  });
  out.TEST4_inspector = clickRes;

  // Lifecycle filter -> DISCOVERED should show 55, others 0 (no fake zone move; counts real).
  const filterRes = await page.evaluate(async () => {
    const f = document.getElementById('scc-lifecycle-filter');
    if (!f) return { noFilter: true };
    f.value = 'DISCOVERED';
    f.dispatchEvent(new Event('change'));
    await new Promise(r=>setTimeout(r,500));
    const tbody = document.getElementById('scc-fleet-tbody');
    return { discoveredRows: tbody ? tbody.children.length : 'n/a' };
  });
  out.TEST4_filter = filterRes;

  // TEST 8 restart: reload page, confirm reconstruction (fleet repopulates from authoritative).
  const reloadRes = await page.reload({ waitUntil: 'domcontentloaded' });
  let repop = 0;
  for (let i=0;i<20;i++){ await page.waitForTimeout(3000); repop = await page.evaluate(()=>(document.getElementById('scc-fleet-tbody')||{children:[]}).children.length); if (repop>0) break; }
  out.TEST8_restart = { reloaded: reloadRes!=null, repopulatedFleetRows: repop };

  // TEST 11 adversarial lifecycle: verify NO node ever shows ACTIVE/SHADOW/VALIDATED state with can_trade.
  const adv = await page.evaluate(async () => {
    const resp = await fetch('/api/command-center/fleet');
    const j = await resp.json();
    const rows = j.rows||[];
    const bad = rows.filter(r => ['VALIDATED','SHADOW','ACTIVE'].includes(r.lifecycle) && r.eligibility_state==='YES');
    return { total: rows.length, spuriousYes: bad.length };
  });
  out.TEST11_adversarial = adv;

  // TEST 12 re-confirm
  out.TEST12_noConsoleErrors = consoleAll.filter(c=>c.startsWith('[error]')).length;

  fs.writeFileSync('tests/e2e_cc_phase4_interactive.json', JSON.stringify({ out, consoleAll }, null, 2));
  console.log(JSON.stringify({ out, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
