// Final validation: measure panel across viewports + toggle interaction test.
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
const widths = [1920, 1366, 1024, 768, 390];
for (const w of widths) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !m.text().includes('Failed to load resource')) errs.push('CONSOLE: ' + m.text()); });
  await page.goto('http://127.0.0.1:8080/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('.tab-btn')];
    const a = btns.find(b => b.textContent.includes('Account Center'));
    if (a) a.click();
  });
  await page.waitForTimeout(1600);
  const rep = await page.evaluate(() => {
    const hs = [...document.querySelectorAll('h3')];
    const h = hs.find(x => x.textContent.includes('Account Performance'));
    if (!h) return 'PANEL MISSING';
    const panel = h.closest('.bg-panelBg');
    const pb = panel.getBoundingClientRect();
    const hero = panel.querySelector('.grid');
    const hb = hero.getBoundingClientRect();
    const cs = getComputedStyle(hero);
    const deep = document.getElementById('acct-intel-deep');
    const deepHidden = deep ? deep.classList.contains('hidden') : 'MISSING';
    const grids = [...panel.querySelectorAll('.grid')].filter(g => getComputedStyle(g).display === 'grid' && g.getBoundingClientRect().height > 0).slice(1);
    const ginfo = grids.map(g => { const b = g.getBoundingClientRect(); const c = getComputedStyle(g); return Math.round(b.width) + 'x' + Math.round(b.height) + '/' + c.gridTemplateColumns.split(' ').length + 'c'; });
    const ov = document.documentElement.scrollWidth > window.innerWidth ? 'OVERFLOW' : 'ok';
    const intel = document.getElementById('acct-intel-texts').getBoundingClientRect();
    const cards = [...panel.querySelectorAll('.bg-darkBg\\/50, .rounded-xl')].filter(c => c.getBoundingClientRect().height > 0 && c.getBoundingClientRect().width > 50);
    const tall = cards.filter(c => c.getBoundingClientRect().height > 200);
    // the last advanced row (Avg Risk / Trade + R Coverage)
    const lastRow = [...panel.querySelectorAll('.grid')].find(g => g.textContent.includes('Avg Risk / Trade'));
    let lastRowInfo = 'MISSING';
    if (lastRow) {
      const csg = getComputedStyle(lastRow);
      lastRowInfo = [...lastRow.children].map(c => Math.round(c.getBoundingClientRect().width) + 'x' + Math.round(c.getBoundingClientRect().height)).join(' ') + ' / cols=' + csg.gridTemplateColumns.split(' ').length;
    }
    return 'overflow=' + ov + ' panel=' + Math.round(pb.width) + 'x' + Math.round(pb.height) +
      ' deepHidden=' + deepHidden +
      ' hero=' + Math.round(hb.width) + 'x' + Math.round(hb.height) + '/' + cs.gridTemplateColumns.split(' ').length + 'c' +
      ' kids=' + [...hero.children].map(c => Math.round(c.getBoundingClientRect().width) + 'x' + Math.round(c.getBoundingClientRect().height)).join(' ') +
      '\nadvGrids: ' + ginfo.join(' | ') +
      '\nlastRow: ' + lastRowInfo +
      '\nintel=' + Math.round(intel.width) + 'x' + Math.round(intel.height) +
      ' cards=' + cards.length + ' tallCards>200=' + tall.length;
  });
  console.log('== ' + w + 'px ==');
  console.log(rep);
  console.log('errors: ' + (errs.join('; ') || '(none)'));
  await page.close();
}

// toggle interaction test at 1366
{
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.goto('http://127.0.0.1:8080/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('.tab-btn')];
    const a = btns.find(b => b.textContent.includes('Account Center'));
    if (a) a.click();
  });
  await page.waitForTimeout(1500);
  const before = await page.evaluate(() => {
    const d = document.getElementById('acct-intel-deep');
    return { hidden: d.classList.contains('hidden'), h: Math.round(d.getBoundingClientRect().height) };
  });
  await page.click('#acct-intel-mode-btn');
  await page.waitForTimeout(300);
  const after = await page.evaluate(() => {
    const d = document.getElementById('acct-intel-deep');
    const btn = document.getElementById('acct-intel-mode-btn');
    return { hidden: d.classList.contains('hidden'), h: Math.round(d.getBoundingClientRect().height), btn: btn.textContent.trim() };
  });
  await page.click('#acct-intel-mode-btn');
  await page.waitForTimeout(300);
  const after2 = await page.evaluate(() => {
    const d = document.getElementById('acct-intel-deep');
    const btn = document.getElementById('acct-intel-mode-btn');
    return { hidden: d.classList.contains('hidden'), h: Math.round(d.getBoundingClientRect().height), btn: btn.textContent.trim() };
  });
  console.log('== toggle test ==');
  console.log('before:', JSON.stringify(before));
  console.log('after open:', JSON.stringify(after));
  console.log('after close:', JSON.stringify(after2));
  await page.close();
}
await browser.close();