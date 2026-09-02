// NEXUS CLIENT EXPERIENCE AUDIT — real user journey, read-only
// Drives the live client at 127.0.0.1:8080 like a naive user.
// NO clicks on state-changing controls (mode selector, toggles, stop/start).
const { chromium } = require('C:/Users/Capsizer/source/repos/NexusTradingForexBot/node_modules/playwright');
const fs = require('fs');

const OUT = 'C:/Users/Capsizer/source/repos/NexusTradingForexBot/scratch/ux_audit_journey.json';
const BASE = 'http://127.0.0.1:8081';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const net = { requests: [], responses: [], errors: [] };
  page.on('request', r => { if (r.resourceType() === 'fetch' || r.resourceType() === 'xhr') net.requests.push(r.url().replace(BASE, '')); });
  page.on('response', r => { if (r.status() >= 400) net.errors.push(r.status() + ' ' + r.url().replace(BASE, '')); });
  page.on('console', m => { if (m.type() === 'error') net.errors.push('console: ' + m.text().slice(0, 200)); });
  page.on('pageerror', e => net.errors.push('pageerror: ' + String(e).slice(0, 200)));

  const journey = {};
  const t0 = Date.now();
  await page.goto(BASE, { waitUntil: 'load' });
  journey.load_ms = Date.now() - t0;
  await page.waitForTimeout(3500); // SSE + snapshot settle

  // 1. What does a first-time user see above the fold?
  journey.first_screen = {
    title: await page.title(),
    header_text: (await page.locator('header').innerText().catch(() => '')).replace(/\n+/g, ' | ').slice(0, 500),
    sidebar_items: await page.locator('.tab-btn').allInnerTexts(),
    active_tab: await page.locator('.tab-btn.active').innerText().catch(() => '?'),
    h1s: await page.locator('.tab-content.active h1, .tab-content.active h2, .tab-content.active h3').allInnerTexts(),
    status_badge: await page.locator('#system-status-badge').innerText().catch(() => '?'),
    mode_selector_value: await page.locator('#execution-mode-selector').inputValue().catch(() => '?'),
    runtime_badge: await page.locator('#runtime-mode-badge').innerText().catch(() => '?'),
  };

  // 2. Can a user find the current signal? Count elements mentioning decision/signal on the monitoring tab.
  const mon = page.locator('#tab-monitoring');
  journey.monitoring = {
    visible_metrics: (await mon.locator('text=/AI DECISION|DECISION|SIGNAL/i').count()),
    ai_decision_visible: await mon.getByText(/NO TRADE|BUY|SELL/).first().isVisible().catch(() => false),
    ai_reason_shown: (await mon.getByText(/BLOCKED|GUARDIAN|REASON/i).count()),
    has_plain_explanation: (await mon.getByText(/because|reason|conditions/i).count()),
  };

  // 3. Tab-by-tab reachable? Click each nav item, record what appears (no state changes).
  const navLabels = ['Monitoring', 'Account', 'AI Analysis', 'Research', 'Strategy Factory', 'News', 'Liquidity', 'Rules', 'Bot Settings', 'Debug Hub', 'Governance', 'Incidents', 'Command Center', 'Database'];
  journey.tabs = {};
  for (const label of navLabels) {
    try {
      const btn = page.locator(`.tab-btn:has-text("${label}")`).first();
      await btn.click({ timeout: 3000 });
      await page.waitForTimeout(700);
      const tabId = await page.evaluate(() => document.querySelector('.tab-content.active')?.id);
      const text = (await page.locator(`#${tabId}`).innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 220);
      journey.tabs[label] = { tabId, snippet: text };
    } catch (e) {
      journey.tabs[label] = { error: String(e).slice(0, 120) };
    }
  }

  // 4. Search for the "why no trade" answer path: is there any human-readable reason anywhere?
  journey.why_no_trade = {
    ai_reason_code: await page.getByText('BLOCKED_BY_GUARDIAN_UNSAFE_REGIME').count(),
    plain_words_count: await page.getByText(/unsafe regime|not favorable|Guardian/i).count(),
  };

  // 5. Network behavior summary while sitting idle on monitoring tab for 8s
  const before = net.requests.length;
  await page.locator('.tab-btn:has-text("Monitoring")').first().click().catch(() => {});
  await page.waitForTimeout(8000);
  journey.idle_8s_new_requests = net.requests.length - before;

  // 6. Responsive: tablet + mobile widths — is critical info reachable?
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.waitForTimeout(600);
  journey.tablet = {
    horizontal_overflow: await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2),
    header_visible: await page.locator('header').isVisible(),
  };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  journey.mobile = {
    horizontal_overflow: await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2),
    sidebar_visible: await page.locator('aside').first().isVisible(),
    first_clicks_to_market: 'sidebar visible => same nav',
  };

  journey.network = { total_fetches: net.requests.length, unique: [...new Set(net.requests)].length, errors: net.errors.slice(0, 15) };
  journey.screenshots = 'see scratch/ux_audit_*.png';

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: 'C:/Users/Capsizer/source/repos/NexusTradingForexBot/scratch/ux_audit_desktop.png' });

  fs.writeFileSync(OUT, JSON.stringify(journey, null, 2));
  console.log('WROTE', OUT);
  console.log('load_ms=', journey.load_ms, '| idle8s reqs=', journey.idle_8s_new_requests, '| errors=', journey.network.errors.length);
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
