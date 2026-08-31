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
  // Sample DOM at increasing intervals to catch the auto-boot race.
  const samples = [];
  for (const ms of [500, 1500, 3000, 5000, 8000]) {
    await page.waitForTimeout(ms - (samples.length ? [500,1500,3000,5000,8000][samples.length-1] : 0));
    const s = await page.evaluate(() => ({
      total: (document.getElementById('scc-total')||{}).textContent,
      fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
      canvasNonBlank: (() => { const c=document.getElementById('scc-spatial-canvas'); if(!c) return 'no-canvas'; try{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for(let i=3;i<d.length;i+=4){if(d[i]!==0){n++; if(n>50)return true;}} return false;}catch(e){return 'err:'+e.message;} })(),
    }));
    samples.push({ t: ms, ...s });
  }
  await page.screenshot({ path: 'tests/cc_phase4_final.png' });
  fs.writeFileSync('tests/e2e_cc_phase4_timing.json', JSON.stringify({ samples, consoleAll }, null, 2));
  console.log(JSON.stringify({ samples, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
