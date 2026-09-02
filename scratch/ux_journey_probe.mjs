// Nexus Client UX journey probe (read-only black-box).
// Drives the REAL client at 127.0.0.1:8080 like a naive operator and records
// every friction point with evidence. Nothing is clicked that mutates state.
import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const BASE = process.env.NEXUS_URL || 'http://127.0.0.1:8080';
const findings = [];
const consoleErrors = [];
const failedRequests = [];

function f(step, expect, actual, friction, severity, improvement) {
  findings.push({ step, expect, actual, friction, severity, improvement });
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 300)));
page.on('requestfailed', (r) => failedRequests.push(r.url() + ' :: ' + (r.failure()?.errorText || '')));
page.on('response', (r) => { if (r.status() >= 400) failedRequests.push(r.status() + ' ' + r.url()); });

// ---------- 1. FIRST LAUNCH ----------
const t0 = Date.now();
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(3500);
const loadMs = Date.now() - t0;

const title = await page.title();
const sidebar = await page.$$eval('.tab-btn', els => els.map(e => e.textContent.trim().replace(/\s+/g, ' ')));
const activeTab = await page.$eval('.tab-btn.active', e => e.textContent.trim()).catch(() => null);
const activeSectionId = await page.$eval('.tab-content.active', e => e.id).catch(() => null);
f('1. First launch', 'Purpose obvious within seconds; active view identifiable',
  `title="${title}"; default tab="${activeTab}" (#${activeSectionId}); ${sidebar.length} sidebar items; load ${loadMs}ms`,
  sidebar.length >= 12 ? '12+ undifferentiated nav items — no task grouping, no Home; technical terms (Debug Hub, Governance, Dependency) exposed to novices' : 'ok',
  sidebar.length >= 12 ? 'HIGH' : 'LOW',
  'Group nav by goal (Operate / Analyze / System); add a Home/Overview tab');

// ---------- 2. Header / status scan ----------
const headerTxt = (await page.$eval('header', e => e.innerText).catch(() => '')).replace(/\s+/g, ' ');
f('2. Header scan', 'Mode, symbol, price, freshness visible', headerTxt.slice(0, 220),
  /LAST UPDATE/.test(headerTxt) ? 'ok' : 'no explicit last-update',
  /LAST UPDATE/.test(headerTxt) ? 'LOW' : 'MED', 'n/a');

// ---------- 3. Signal / decision readability (default Monitoring tab) ----------
const decisionTxt = await page.evaluate(() => {
  const ids = ['ai-decision-text', 'ai-reason-text', 'ai-confidence-text', 'decision-reason'];
  const out = {};
  for (const id of ids) { const el = document.getElementById(id); if (el) out[id] = el.textContent.trim().slice(0, 120); }
  return out;
});
f('3. Signal readability', 'BUY/SELL/NO_TRADE + plain-language why',
  JSON.stringify(decisionTxt),
  /BLOCKED_BY_GUARDIAN|NO_TRADE/.test(JSON.stringify(decisionTxt)) ? 'raw enum jargon (e.g. BLOCKED_BY_GUARDIAN_UNSAFE_REGIME) shown verbatim; no plain-language layer' : 'ok',
  'HIGH', 'Human summary ("Market unsafe to trade right now") + expandable technical detail');

// ---------- 4. Tab walk: render every tab, time it, catch dead tabs ----------
const tabResults = [];
const tabButtons = await page.$$('.tab-btn');
let idx = 0;
for (const btn of tabButtons) {
  idx += 1;
  const name = (await btn.textContent()).trim().replace(/\s+/g, ' ');
  const t = Date.now();
  await btn.click();
  await page.waitForTimeout(900);
  const active = await page.$eval('.tab-content.active', e => e.id).catch(() => null);
  const visible = await page.$eval('.tab-content.active', e => e.innerText.length).catch(() => 0);
  const emptyish = visible < 40;
  const tMs = Date.now() - t;
  tabResults.push({ name, active, visible, tMs });
  if (emptyish) f(`4. Tab "${name}"`, 'content renders', `section=${active} visibleChars=${visible}`,
    'tab appears EMPTY (no empty-state explanation)', 'MED', 'EmptyState component: what/why/next-action');
  if (tMs > 2500) f(`4. Tab "${name}"`, 'switch < 2.5s', `${tMs}ms`, 'slow tab switch', 'LOW', 'lazy-load heavy panels');
}
console.log('TAB WALK:', JSON.stringify(tabResults));

// ---------- 5. Empty states ----------
const noTradeProbe = await page.evaluate(() => {
  const s = document.querySelector('.tab-content.active');
  return s ? s.innerText.slice(0, 400) : '';
});
f('5. Active tab content sample', 'n/a', noTradeProbe.replace(/\s+/g, ' ').slice(0, 300), 'sample', 'LOW', 'n/a');

// ---------- 6. Keyboard accessibility ----------
const focusables = await page.evaluate(() => document.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])').length);
await page.keyboard.press('Tab');
await page.keyboard.press('Tab');
const focused = await page.evaluate(() => document.activeElement ? document.activeElement.tagName + '.' + (document.activeElement.className || '').toString().slice(0, 40) : 'none');
f('6. Keyboard a11y', 'Tab reaches controls', `focusable=${focusables}; after 2xTab focus=${focused}`,
  /body|none/i.test(focused) ? 'Tab does not move focus into the app shell' : 'ok',
  /body|none/i.test(focused) ? 'MED' : 'LOW', 'roving tabindex / focus management');

// ---------- 7. Global search / command palette existence ----------
const hasPalette = await page.evaluate(() =>
  !!document.getElementById('nx-command-palette') || !!document.getElementById('global-search'));
f('7. Search / palette', 'Ctrl+K reachable', `palette=${hasPalette}`,
  !hasPalette ? 'no global search or command palette — every target must be found by reading 12+ nav labels' : 'ok',
  !hasPalette ? 'MED' : 'LOW', 'Ctrl+K command palette');

// ---------- 8. Disconnect UX (block SSE + API via route abort, reload) ----------
await page.route('**/api/ticks/stream**', r => r.abort());
await page.route('**/api/status**', r => r.abort());
try { await page.reload({ waitUntil: 'domcontentloaded', timeout: 15000 }); }
catch (e) { /* disconnect scenario: navigation may stall when APIs are aborted */ }
await page.waitForTimeout(4000);
const connLost = await page.evaluate(() => {
  const t = document.body.innerText;
  return { hasBanner: /CONNECTION LOST|RECONNECTING|disconnected/i.test(t), sample: (t.match(/[^\n]*(CONNECTION LOST|RECONNECTING|disconnected)[^\n]*/i) || [''])[0].slice(0, 160) };
});
f('8. Disconnect UX', 'obvious banner + last-updated + retry', JSON.stringify(connLost),
  !connLost.hasBanner ? 'NO visible disconnect banner — stale UI looks alive (console-only warn)' : 'banner present',
  !connLost.hasBanner ? 'HIGH' : 'LOW', 'Persistent CONNECTION LOST banner with last-update time + Retry');

// ---------- 9. Language / RTL support ----------
const lang = await page.evaluate(() => ({
  htmlLang: document.documentElement.lang,
  dir: document.documentElement.dir,
  hasSwitcher: !!document.getElementById('lang-switcher') || !!document.querySelector('[data-lang]'),
}));
f('9. Multilingual/RTL', 'EN/FA/DE/ES/AR + RTL', JSON.stringify(lang),
  lang.htmlLang !== 'fa' && !lang.hasSwitcher ? 'no language switcher; RTL unsupported (brief §45)' : 'ok',
  lang.htmlLang !== 'fa' && !lang.hasSwitcher ? 'HIGH' : 'LOW', 'i18n layer + dir=rtl flip');

// ---------- 10. Responsive smoke ----------
await page.setViewportSize({ width: 820, height: 900 });
await page.waitForTimeout(600);
const respOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
f('10. Tablet responsive', 'no horizontal overflow at 820px', `overflowX=${respOverflow}px`,
  respOverflow > 2 ? `horizontal overflow ${respOverflow}px on tablet` : 'ok', respOverflow > 2 ? 'MED' : 'LOW', 'fluid grid/breakpoints');

await browser.close();

const report = { generatedAt: new Date().toISOString(), base: BASE, loadMs, sidebarCount: sidebar.length, sidebar,
  findings: findings.filter(x => x.severity !== 'LOW'), allFindings: findings, consoleErrors: consoleErrors.slice(0, 40),
  failedRequests: failedRequests.slice(0, 40) };
writeFileSync('artifacts/forensics/ux_journey_probe_result.json', JSON.stringify(report, null, 2));
console.log('REPORT: artifacts/forensics/ux_journey_probe_result.json');
console.log('SUMMARY: findings=' + findings.length + ' consoleErrors=' + consoleErrors.length + ' failedReq=' + failedRequests.length);
