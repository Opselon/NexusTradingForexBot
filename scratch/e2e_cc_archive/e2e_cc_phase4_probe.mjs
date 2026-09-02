// Targeted probe: capture 404 URL, render panels, screenshot, inspector breakdown.
import { chromium } from 'playwright';
import fs from 'fs';

const BASE = 'http://127.0.0.1:8082';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const all404 = [];
  const console404 = [];
  page.on('response', r => { if (r.status() === 404) all404.push(r.url()); });
  page.on('console', m => { if (m.type() === 'error' && /404/i.test(m.text())) console404.push(m.text()); });

  await page.goto(BASE + '/command_center.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);

  // DOM inventory of panels by id/class substring
  const panels = await page.evaluate(() => {
    const ids = [];
    document.querySelectorAll('[id]').forEach(e => ids.push(e.id));
    const classes = [];
    document.querySelectorAll('[class]').forEach(e => { String(e.className).split(/\s+/).forEach(c => { if (!classes.includes(c)) classes.push(c); }); });
    return { ids, classCount: classes.length, sampleClasses: classes.slice(0, 80) };
  });

  // Try to open inspector for a DISCOVERED node via the JS API the page exposes.
  const inspectorResult = await page.evaluate(async () => {
    // The page may expose window.NX or a command-center API. Probe.
    const w = window;
    const probes = {};
    for (const k of ['NX','CC','CommandCenter','cc','scc']) probes[k] = typeof w[k];
    // Try to find an inspector panel element and click-style select via fleet fetch + DOM.
    return { probes, hasInspectorEl: !!document.querySelector('#scc-inspector, #inspector, [id*=inspector]') };
  });

  await page.screenshot({ path: 'tests/cc_phase4_screenshot.png', fullPage: false });

  const out = {
    status404_resources: [...new Set(all404)],
    console404,
    panel_ids: panels.ids,
    inspector_probe: inspectorResult,
  };
  fs.writeFileSync('tests/e2e_cc_phase4_probe.json', JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})().catch(e => { console.error('PROBE FAIL', e); process.exit(1); });
