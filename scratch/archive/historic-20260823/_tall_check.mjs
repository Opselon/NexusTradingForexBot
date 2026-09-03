// Identify tall cards in the account panel at 1366px.
import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
await page.goto('http://127.0.0.1:8080/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
await page.evaluate(() => { const b = [...document.querySelectorAll('.tab-btn')]; const a = b.find(x => x.textContent.includes('Account Center')); if (a) a.click(); });
await page.waitForTimeout(1600);
const rep = await page.evaluate(() => {
  const hs = [...document.querySelectorAll('h3')];
  const h = hs.find(x => x.textContent.includes('Account Performance'));
  const panel = h.closest('.bg-panelBg');
  // find cards by class attribute contains darkBg/50 OR rounded-xl via attribute selector
  const cards = [...panel.querySelectorAll('[class*="bg-darkBg/50"], [class*="rounded-xl"]')].filter(c => {
    const b = c.getBoundingClientRect();
    return b.height > 200 && b.width > 50;
  });
  return cards.map(c => {
    const b = c.getBoundingClientRect();
    const label = c.querySelector('canvas') ? 'CANVAS' : (c.querySelector('span') ? c.querySelector('span').textContent.trim().slice(0, 30) : c.tagName);
    return label + ' ' + Math.round(b.width) + 'x' + Math.round(b.height);
  }).join('\n') || '(none > 200px)';
});
console.log(rep);
await page.evaluate(() => { const hs = [...document.querySelectorAll('h3')]; const h = hs.find(x => x.textContent.includes('Account Performance')); if (h) h.scrollIntoView({ block: 'start' }); });
await page.waitForTimeout(400);
await page.screenshot({ path: 'C:/Users/Capsizer/source/repos/NexusTradingForexBot/pics/_FINAL_1366.png' });
await browser.close();