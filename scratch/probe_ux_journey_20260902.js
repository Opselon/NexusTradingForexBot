// UX Journey Audit probe (2026-09-02) — Nexus-UX client experience agent.
// // Drives the LIVE web client at http://127.0.0.1:8080 like a real user:
//   - first launch: what is visible, console errors, failed requests
//   - visits every nav tab, screenshots it, samples visible text
//   - measures placeholder health ('—' counts), empty-state quality
//   - mobile viewport overflow probe (390x844)
// READ-ONLY: never clicks state-changing controls (no stop/mode/rule toggles).
// Evidence -> artifacts/forensics/ux_audit_20260902/
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.NEXUS_BASE || 'http://127.0.0.1:8080';
const OUT = path.join('artifacts', 'forensics', 'ux_audit_20260902');
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 300)));
  page.on('requestfailed', r => failedRequests.push(`${r.method()} ${r.url().slice(0, 120)} :: ${r.failure()?.errorText}`));

  const results = { base: BASE, steps: [] };

  // ---- STEP 1: first launch ----
  const t0 = Date.now();
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000); // allow initial snapshot + SSE to settle
  const firstLaunch = await page.evaluate(() => {
    const active = document.querySelector('.tab-content.active');
    const navBtns = [...document.querySelectorAll('.tab-btn')].map(b => b.textContent.trim().replace(/\s+/g, ' '));
    const header = document.querySelector('header')?.innerText.replace(/\n+/g, ' | ').slice(0, 800) || '';
    const bodyText = active ? active.innerText.slice(0, 1500) : '';
    const dashCount = (document.body.innerText.match(/—/g) || []).length;
    return {
      title: document.title,
      navCount: navBtns.length,
      navBtns,
      header,
      activeTabId: active?.id || null,
      activeTextSample: bodyText,
      placeholderDashCount: dashCount,
      lang: document.documentElement.lang,
      dir: document.documentElement.dir || 'unset',
      hasToastSystem: !!document.querySelector('.toast, [class*="toast"]'),
      hasCommandPalette: !!document.querySelector('[class*="palette"], [class*="cmdk"]'),
    };
  });
  results.firstLaunch = firstLaunch;
  results.firstLaunchLoadMs = Date.now() - t0;
  await page.screenshot({ path: path.join(OUT, '01_first_launch.png'), fullPage: false });

  // ---- STEP 2: visit every tab ----
  const tabs = await page.evaluate(() =>
    [...document.querySelectorAll('.tab-btn')].map(b => ({
      label: b.textContent.trim().replace(/\s+/g, ' '),
      onclick: b.getAttribute('onclick') || '',
    }))
  );
  for (let i = 0; i < tabs.length; i++) {
    const t = tabs[i];
    const m = t.onclick.match(/switchTab\('([^']+)'/);
    const tabId = m ? m[1] : `tab-${i}`;
    try {
      await page.evaluate(id => { const btns = [...document.querySelectorAll('.tab-btn')]; const b = btns.find(x => (x.getAttribute('onclick') || '').includes(id)); if (b) b.click(); }, tabId);
      await page.waitForTimeout(2200);
      const sample = await page.evaluate(id => {
        const el = document.getElementById(id);
        if (!el) return null;
        const txt = el.innerText.replace(/\n{2,}/g, '\n');
        const dashes = (txt.match(/—/g) || []).length;
        const buttons = el.querySelectorAll('button').length;
        const tables = el.querySelectorAll('table').length;
        return { chars: txt.length, sample: txt.slice(0, 700), dashes, buttons, tables };
      }, tabId);
      await page.screenshot({ path: path.join(OUT, `${String(i + 2).padStart(2, '0')}_${tabId}.png`), fullPage: false });
      results.steps.push({ tabId, label: t.label, sample });
    } catch (e) {
      results.steps.push({ tabId, label: t.label, error: String(e).slice(0, 200) });
    }
  }

  // ---- STEP 3: mobile viewport overflow probe ----
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { const b = [...document.querySelectorAll('.tab-btn')].find(x => (x.getAttribute('onclick') || '').includes('tab-monitoring')); if (b) b.click(); });
  await page.waitForTimeout(1500);
  const mobile = await page.evaluate(() => ({
    horizOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    sidebarTakesFullWidth: (() => { const a = document.querySelector('aside'); if (!a) return null; const r = a.getBoundingClientRect(); return r.width > 350; })(),
  }));
  results.mobile = mobile;
  await page.screenshot({ path: path.join(OUT, '20_mobile_390.png'), fullPage: false });

  // ---- STEP 4: keyboard accessibility probe ----
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.waitForTimeout(500);
  const kb = await page.evaluate(() => {
    const focusables = document.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])').length;
    const skipLink = !!document.querySelector('[class*="skip"]');
    const ariaLabels = document.querySelectorAll('[aria-label]').length;
    const roles = document.querySelectorAll('[role]').length;
    const ctrlK = !!window.__commandPalette;
    return { focusables, skipLink, ariaLabels, roles, ctrlK };
  });
  results.keyboard = kb;

  results.consoleErrors = consoleErrors.slice(0, 40);
  results.pageErrors = pageErrors.slice(0, 20);
  results.failedRequests = failedRequests.slice(0, 30);

  fs.writeFileSync(path.join(OUT, 'journey_results.json'), JSON.stringify(results, null, 2));
  console.log('WROTE', path.join(OUT, 'journey_results.json'));
  console.log('firstLaunch navCount:', firstLaunch.navCount, 'lang:', firstLaunch.lang, 'dir:', firstLaunch.dir);
  console.log('consoleErrors:', consoleErrors.length, 'pageErrors:', pageErrors.length, 'failedRequests:', failedRequests.length);
  await browser.close();
})().catch(e => { console.error('PROBE FAILED:', e); process.exit(1); });
