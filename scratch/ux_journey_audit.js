// TASK-UX-CLIENT / CHG-0046 — Real client journey audit (observation-only).
// Navigates the live Nexus client like a real user, records friction evidence.
// SAFETY: never clicks destructive controls (Stop Bot, mode selector, provider probes).
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8080';
const OUT = process.argv[2] || 'artifacts/forensics/ux_journey_audit_20260902.json';

(async () => {
  const report = {
    task: 'TASK-UX-CLIENT journey audit', base: BASE,
    started_at: new Date().toISOString(), steps: [],
    console_errors: [], page_errors: [], failed_requests: [],
    request_log: [],
  };
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') {
      report.console_errors.push({ type: m.type(), text: (m.text() || '').slice(0, 300) });
    }
  });
  page.on('pageerror', (e) => report.page_errors.push(String(e).slice(0, 300)));
  page.on('requestfailed', (r) => report.failed_requests.push({ url: r.url().slice(0, 160), err: String(r.failure() && r.failure().errorText).slice(0, 120) }));
  page.on('response', (r) => {
    const u = r.url();
    if (u.startsWith(BASE)) report.request_log.push({ s: r.status(), u: u.slice(BASE.length, 140) });
  });

  async function step(name, fn) {
    const t0 = Date.now();
    const s = { step: name, ms: 0, ok: true, findings: [] };
    try { await fn((f) => s.findings.push(f)); } catch (e) { s.ok = false; s.error = String(e).slice(0, 300); }
    s.ms = Date.now() - t0;
    report.steps.push(s);
    console.log(`[${s.ok ? 'OK' : 'ERR'}] ${name} (${s.ms}ms)${s.findings.length ? ' :: ' + s.findings.join(' | ') : ''}`);
  }

  await step('FIRST LAUNCH: load dashboard', async (f) => {
    const t0 = Date.now();
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(2500);
    const ms = Date.now() - t0;
    f(`initial render ${ms}ms`);
    const title = await page.title();
    const nav = await page.$$eval('.tab-btn', (els) => els.map((e) => e.textContent.trim().replace(/\s+/g, ' ')));
    const activeSection = await page.$eval('.tab-content.active', (e) => e.id);
    f(`title="${title}"`);
    f(`nav items (${nav.length}): ${nav.join(' · ')}`);
    f(`landing section: ${activeSection}`);
    report.nav = nav; report.landing = activeSection;
    if (nav.length > 12) f(`FRICTION: ${nav.length} flat sidebar entries, no grouping by user goal`);
    if (!nav.some((n) => /home|overview|dashboard/i.test(n))) f('FRICTION: no HOME/overview entry — app opens straight into a chart tab');
  });

  await step('HEADER STATE: mode/badges legibility', async (f) => {
    const badge = await page.$eval('#system-status-badge', (e) => e.textContent.trim()).catch(() => null);
    const modeSel = await page.$eval('#execution-mode-selector', (e) => ({ value: e.value, hasConfirm: e.hasAttribute('data-confirm-bound') })).catch(() => null);
    const health = await page.$eval('#header-health-badge', (e) => e.textContent.trim()).catch(() => null);
    f(`status badge="${badge}" health="${health}"`);
    f(`mode selector value=${modeSel && modeSel.value} confirm-bound=${modeSel && modeSel.hasConfirm}`);
    if (modeSel && !modeSel.hasConfirm) f('FRICTION: execution mode (PAPER/LIVE) changeable with a single stray select change — no confirmation dialog');
  });

  await step('SIGNAL READ: AI decision box on landing', async (f) => {
    const dec = await page.$eval('#ai-decision-badge', (e) => e.textContent.trim()).catch(() => null);
    const conf = await page.$eval('#ai-confidence', (e) => e.textContent.trim()).catch(() => null);
    const reason = await page.$eval('#ai-reason-text', (e) => e.textContent.trim()).catch(() => null);
    f(`decision="${dec}" ${conf} reason="${reason}"`);
    if (dec === 'NO_TRADE' && conf && /0\.0/.test(conf)) f('FRICTION: NO_TRADE shows "Conf: 0.0" — reads like a failure score (abstain semantics not surfaced)');
    if (reason && /^[A-Z_]+$/.test(reason.replace(/[ …]/g, ''))) f('FRICTION: raw machine reason code shown to user without human explanation');
    const whyBtn = await page.$('#btn-why-decision');
    if (!whyBtn) f('FRICTION: no "Why?" affordance to explain the current decision');
  });

  const tabs = ['tab-account', 'tab-ai-analysis', 'tab-research', 'tab-factory', 'tab-news', 'tab-liquidity', 'tab-rules', 'tab-config', 'tab-debug', 'tab-governance', 'tab-incidents', 'tab-command-center', 'tab-database'];
  for (const t of tabs) {
    await step(`NAVIGATE ${t}`, async (f) => {
      const btn = await page.$(`button:has-text("${t.replace('tab-', '')}")`).catch(() => null);
      // click by matching the onclick attribute instead — resilient to label text
      const ok = await page.evaluate((tid) => {
        const b = [...document.querySelectorAll('.tab-btn')].find((x) => (x.getAttribute('onclick') || '').includes(tid));
        if (!b) return false; b.click(); return true;
      }, t);
      if (!ok) { f('FRICTION: no sidebar button for this tab (orphaned section)'); return; }
      await page.waitForTimeout(700);
      const visible = await page.$eval(`#${t}`, (e) => !e.classList.contains('hidden'));
      if (!visible) f('section did not become visible');
      const txt = await page.$eval(`#${t}`, (e) => e.innerText.replace(/\s+/g, ' ').slice(0, 200));
      f(`content head: ${txt.slice(0, 140)}`);
    });
  }

  await step('ORPHAN CHECK: tab-health reachable?', async (f) => {
    const exists = await page.$('#tab-health');
    const reachable = await page.evaluate(() => [...document.querySelectorAll('.tab-btn')].some((x) => (x.getAttribute('onclick') || '').includes('tab-health')));
    f(`tab-health exists=${!!exists} reachableFromNav=${reachable}`);
    if (exists && !reachable) f('FRICTION: System Health section exists in DOM but has NO navigation entry — users cannot reach it');
  });

  await step('STOP CONTROL: safety affordances (observation only)', async (f) => {
    const hasTypeConfirm = await page.$('#stop-bot-confirm-input');
    f(`Stop Bot type-to-confirm present=${!!hasTypeConfirm} (not clicked)`);
  });

  await step('DISCONNECT UX: what does a dead backend look like?', async (f) => {
    // Simulate network loss at the page level (route abort) — non-invasive to server.
    await page.route('**/api/**', (route) => route.abort());
    await page.evaluate(() => { const es = window.eventSource; if (es) { try { es.close(); } catch (e) {} } });
    await page.waitForTimeout(6000);
    const badge = await page.$eval('#system-status-badge', (e) => e.textContent.trim()).catch(() => null);
    f(`badge after simulated drop="${badge}"`);
    const banner = await page.$('#nx-conn-banner');
    f(banner ? 'conn banner present' : 'FRICTION: no full-width CONNECTION LOST banner — stale numbers keep looking alive; only tiny header badge changes');
    await page.unroute('**/api/**');
  });

  await step('NETWORK HYGIENE: background request volume', async (f) => {
    const n0 = report.request_log.length;
    await page.waitForTimeout(5000);
    const n1 = report.request_log.length;
    f(`${n1 - n0} API requests in 5s while idle on last tab`);
    const counts = {};
    report.request_log.slice(-60).forEach((r) => { counts[r.u.split('?')[0]] = (counts[r.u.split('?')[0]] || 0) + 1; });
    report.recent_request_counts = counts;
  });

  await browser.close();
  report.finished_at = new Date().toISOString();
  fs.mkdirSync(require('path').dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log('WROTE ' + OUT);
})().catch((e) => { console.error('AUDIT FAILED', e); process.exit(1); });
