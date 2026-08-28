import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleAll = [];
  page.on('console', m => consoleAll.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', e => consoleAll.push(`[PAGEERROR] ${e.message}`));
  const samples = [];
  const t0 = Date.now();
  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  // Poll DOM every 3s up to 45s.
  for (let i=0;i<16;i++){
    await page.waitForTimeout(3000);
    const s = await page.evaluate(() => ({
      ms: 0,
      fleetRows: (document.getElementById('scc-fleet-tbody')||{children:[]}).children.length,
      total: (document.getElementById('scc-total')||{}).textContent,
      canvasNonBlank: (() => { const c=document.getElementById('scc-spatial-canvas'); if(!c) return 'none'; try{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for(let i=3;i<d.length;i+=4){if(d[i]!==0){n++; if(n>50)return true;}} return false;}catch(e){return 'err';} })(),
    }));
    s.ms = Date.now()-t0;
    samples.push(s);
    if (s.fleetRows > 0 && s.canvasNonBlank === true) break;
  }
  await page.screenshot({ path: 'tests/cc_phase4_populated.png' });
  fs.writeFileSync('tests/e2e_cc_phase4_populated.json', JSON.stringify({ samples, consoleAll }, null, 2));
  console.log(JSON.stringify({ samples, consoleAll }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
