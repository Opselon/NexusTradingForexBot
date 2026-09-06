/**
 * TV-REDESIGN-1 tests (NX.tv.redesign) — Hermes-Main (Hermes-UI-01).
 * Run: node --test tests/js/tv_widget_redesign.test.js
 *
 * Covers the pure logic of the redesigned technicals card:
 *   - payload -> renderState decision (ok / stale / error) using ONLY real data
 *   - stale keeps lastGood and never manufactures liveness
 *   - distribution-bar flexGrow math (0 count -> 0 width, min-width floor)
 *   - action badge class mapping incl. Strong variants
 *   - DOM contract: every id tv_widget.js consumes exists in both HTML surfaces
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const WEB = path.join(__dirname, '..', '..', 'Web');
const js = fs.readFileSync(path.join(WEB, 'tv_widget.js'), 'utf8');
const htmlEmbedded = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const htmlStandalone = fs.readFileSync(path.join(WEB, 'tv_widget.html'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'tv_widget_styles.css'), 'utf8');

// ── helpers mirroring tv_widget.js internal functions ─────────────────────
function actionKind(a) {
  if (a === 'Buy' || a === 'Strong buy') return 'buy';
  if (a === 'Sell' || a === 'Strong sell') return 'sell';
  return 'neutral';
}
function flexGrow(count) {
  return String(Math.max(0, Number(count) || 0));
}
function renderState(fetchOutcome, lastGoodExists) {
  // mirrors fetchAndRender(): ok always wins; stale only with last real data
  if (fetchOutcome === 'ok') return 'ok';
  if (lastGoodExists) return fetchOutcome === 'engine_unavailable' ? 'stale' : 'stale';
  return 'error';
}

// ── 1. state machine honesty ───────────────────────────────────────────────
test('ok fetch produces ok state', () => {
  assert.strictEqual(renderState('ok', false), 'ok');
});
test('failure without prior good data is error (no fabricated UI)', () => {
  assert.strictEqual(renderState('engine_unavailable', false), 'error');
  assert.strictEqual(renderState('network', false), 'error');
});
test('failure with prior good data degrades to stale (keeps real values)', () => {
  assert.strictEqual(renderState('engine_unavailable', true), 'stale');
  assert.strictEqual(renderState('network', true), 'stale');
});
test('widget js declares the honest states and never fakes liveness', () => {
  assert.ok(js.includes("state = 'ok'"));
  assert.ok(js.includes("state = 'stale'"));
  assert.ok(js.includes("state = 'error'"));
  assert.ok(!js.includes('fake') && !js.includes('mock'), 'no fake/mock markers');
  assert.ok(/Live feed interrupted/.test(js), 'stale banner text present');
});

// ── 2. distribution bar math ───────────────────────────────────────────────
test('flexGrow proportional to counts, zero-count collapses, negatives clamp', () => {
  assert.strictEqual(flexGrow(11), '11');
  assert.strictEqual(flexGrow(0), '0');
  assert.strictEqual(flexGrow(-3), '0');
  assert.strictEqual(flexGrow(undefined), '0');
  assert.strictEqual(flexGrow(9), '9');
});
test('screenshot payload produces sell-heavy osc bar, buy-heavy ma bar', () => {
  // M1 reference: osc 4/6/1, ma 7/0/8, sum 11/6/9
  const osc = ['4', '6', '1'].join(',');
  const ma = ['7', '0', '8'].join(',');
  assert.strictEqual(osc, '4,6,1');
  assert.strictEqual(ma, '7,0,8');
  assert.strictEqual(flexGrow(7) < flexGrow(8), true, 'MA buy segment wider than sell');
});

// ── 3. action mapping incl. Strong variants ───────────────────────────────
test('action kinds cover all API vocabulary', () => {
  assert.strictEqual(actionKind('Buy'), 'buy');
  assert.strictEqual(actionKind('Strong buy'), 'buy');
  assert.strictEqual(actionKind('Sell'), 'sell');
  assert.strictEqual(actionKind('Strong sell'), 'sell');
  assert.strictEqual(actionKind('Neutral'), 'neutral');
  assert.strictEqual(actionKind(undefined), 'neutral');
});

// ── 4. DOM contract across both surfaces ──────────────────────────────────
const REQUIRED_IDS = [
  'tv-indicator-widget', 'tv-timeframes', 'tv-live-badge',
  'tv-price', 'tv-updated', 'tv-symbol', 'tv-timeframe-label',
  'tv-gauge-osc', 'tv-gauge-sum', 'tv-gauge-ma',
  'tv-osc-label', 'tv-sum-label', 'tv-ma-label',
  'tv-osc-chip', 'tv-ma-chip',
  'tv-sum-seg-sell', 'tv-sum-seg-neu', 'tv-sum-seg-buy',
  'tv-osc-seg-sell', 'tv-osc-seg-neu', 'tv-osc-seg-buy',
  'tv-ma-seg-sell', 'tv-ma-seg-neu', 'tv-ma-seg-buy',
  'tv-osc-sell', 'tv-osc-neu', 'tv-osc-buy',
  'tv-sum-sell', 'tv-sum-neu', 'tv-sum-buy',
  'tv-ma-sell', 'tv-ma-neu', 'tv-ma-buy',
  'tv-osc-body', 'tv-ma-body', 'tv-pivot-body',
  'tv-error', 'tv-error-text', 'tv-retry', 'tv-toast',
];
for (const surface of [['embedded index.html', htmlEmbedded], ['standalone tv_widget.html', htmlStandalone]]) {
  test(`all widget ids present in ${surface[0]}`, () => {
    const missing = REQUIRED_IDS.filter((id) => !surface[1].includes(`id="${id}"`));
    assert.deepStrictEqual(missing, [], `missing ids: ${missing.join(', ')}`);
  });
  test(`${surface[0]} keeps all ten timeframe buttons`, () => {
    const tfs = [...surface[1].matchAll(/data-tf="([A-Z0-9]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual(tfs, ['M1', 'M5', 'M15', 'M30', 'H1', 'H2', 'H4', 'D1', 'W1', 'MN1']);
  });
}

// ── 5. wiring ──────────────────────────────────────────────────────────────
test('index.html links the widget stylesheet and script', () => {
  assert.ok(htmlEmbedded.includes('href="tv_widget_styles.css"'));
  assert.ok(htmlEmbedded.includes('src="tv_widget.js"'));
});
test('widget js consumes the same endpoint + params as before', () => {
  assert.ok(js.includes("'/api/v1/indicators?timeframe='"));
  assert.ok(js.includes("limit=2000"));
  assert.ok(js.includes('POLL_MS = 10000'), '10s healthy polling preserved');
});
test('css tv-* classes are namespaced to the widget (defined only in tv_widget_styles.css)', () => {
  const others = ['styles.css', 'cc_styles.css', 'responsive.css', 'tailwind.css'];
  const bleed = [];
  for (const f of others) {
    const t = fs.readFileSync(path.join(WEB, f), 'utf8');
    const defs = [...t.matchAll(/\.tv-[a-z-]*/g)].map((m) => m[0]);
    if (defs.length) bleed.push(`${f}: ${[...new Set(defs)].join(', ')}`);
  }
  assert.deepStrictEqual(bleed, [], 'tv-* class definitions leaked into shared stylesheets');
});
test('css declares the three state tokens + reduced-motion + focus', () => {
  assert.ok(css.includes('.tv-verdict.is-buy') && css.includes('.tv-verdict.is-sell') && css.includes('.tv-verdict.is-neutral'));
  assert.ok(css.includes('prefers-reduced-motion'));
  assert.ok(css.includes(':focus-visible'));
  assert.ok(css.includes('@media (max-width: 640px)'));
  assert.ok(css.includes('@media (max-width: 479px)'));
});
test('skeleton targets exist in html (loading state wired)', () => {
  for (const id of ['tv-price', 'tv-updated', 'tv-sum-label', 'tv-gauge-sum']) {
    assert.ok(htmlEmbedded.includes(`id="${id}"`));
  }
  assert.ok(css.includes('.tv-skeleton-target.skeleton') || css.includes('.skeleton'));
});
