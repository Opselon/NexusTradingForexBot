// Market Radar UI render-binding test.
// Run with: node tests/js/test_market_radar_ui.test.js
//
// Verifies that renderMarketRadar() in Web/app.js renders the EXACT backend
// `radar` values into the DOM card (ids: radar-status, radar-regime,
// radar-best-type, radar-direction, radar-quality, radar-compatible,
// radar-count, radar-news, radar-decision, radar-updated) and that a null
// radar shows the explicit NO-RADAR waiting state — never fake numbers.
//
// app.js declares its functions in module/global scope (no exports), so we
// execute it inside a vm sandbox (mirroring the browser global scope) and pull
// renderMarketRadar off the sandbox's global.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ---- Minimal window/document shims (same approach as the other tests/js/*) ----
const elements = {};
function makeEl(id) {
  return { id, textContent: '', className: '' };
}

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  setInterval: () => {},
  clearInterval: () => {},
  MutationObserver: class { constructor() {} observe() {} disconnect() {} },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  addEventListener() {},
  document: {
    getElementById: (id) => (elements[id] = elements[id] || makeEl(id)),
    createElement: () => ({ classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {}, addEventListener() {} }),
    body: { appendChild() {} },
    addEventListener() {},
    querySelectorAll: () => [],
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// Load app.js into the sandbox (same global scope a browser gives it).
const appSrc = fs.readFileSync(path.join(__dirname, '..', '..', 'Web', 'app.js'), 'utf8');
vm.createContext(sandbox);
vm.runInContext(appSrc, sandbox, { filename: 'Web/app.js' });

const renderMarketRadar = sandbox.renderMarketRadar;
assert.strictEqual(typeof renderMarketRadar, 'function', 'renderMarketRadar not defined on load');

let passed = 0;
function test(name, fn) { fn(); passed++; console.log('  ok -', name); }

// ---- Test 1: a SETUP_READY radar populates EXACT backend values ----
test('SETUP_READY radar populates card with exact backend values', () => {
  renderMarketRadar({
    state: 'SETUP_READY',
    regime: 'BULLISH_TREND',
    candidate_count: 1,
    best_setup: {
      setup_id: 'su_001',
      setup_type: 'LIQUIDITY_SWEEP',
      quality: 0.85,
      factors: { direction: 1 },
      compatible_strategies: ['hunter_sweep_v1'],
    },
    news_state: 'HIGH_IMPACT',
    decision_reason: 'BLOCKED_BY_GUARDIAN_UNSAFE_REGIME',
    updated_at: '2026-08-26T03:15:00+00:00',
  });

  assert.strictEqual(elements['radar-status'].textContent, 'SETUP_READY', 'status badge');
  assert.strictEqual(elements['radar-regime'].textContent, 'BULLISH_TREND', 'regime');
  assert.strictEqual(elements['radar-count'].textContent, '1', 'candidate_count');
  assert.strictEqual(elements['radar-best-type'].textContent, 'LIQUIDITY_SWEEP', 'best setup type');
  assert.strictEqual(elements['radar-direction'].textContent, 'BUY', 'direction +1 => BUY');
  assert.strictEqual(elements['radar-quality'].textContent, '85.0%', 'quality formatted %');
  assert.strictEqual(elements['radar-compatible'].textContent, 'hunter_sweep_v1', 'compatible strategies joined');
  assert.strictEqual(elements['radar-news'].textContent, 'HIGH_IMPACT', 'news_state');
  assert.strictEqual(elements['radar-decision'].textContent, 'BLOCKED_BY_GUARDIAN_UNSAFE_REGIME', 'decision_reason (NOT shown as approved)');
  assert.strictEqual(elements['radar-updated'].textContent, '2026-08-26T03:15:00+00:00', 'updated_at');

  // The decision reason must NOT masquerade as an approval — SETUP_READY +
  // BLOCKED_BY_GUARDIAN_UNSAFE_REGIME stays visibly distinct from ENTRY_APPROVED.
  assert.ok(elements['radar-decision'].textContent.indexOf('BLOCKED') !== -1, 'blocked reason visible');
});

// ---- Test 2: direction SELL (-1) ----
test('direction -1 renders SELL', () => {
  renderMarketRadar({
    state: 'WATCHING',
    candidate_count: 0,
    best_setup: { setup_type: 'FVG_FILL', quality: 0.4, factors: { direction: -1 }, compatible_strategies: [] },
    decision_reason: 'NO_SETUP',
    updated_at: '2026-08-26T03:20:00+00:00',
  });
  assert.strictEqual(elements['radar-direction'].textContent, 'SELL', 'direction -1 => SELL');
  assert.strictEqual(elements['radar-status'].textContent, 'WATCHING', 'state WATCHING');
});

// ---- Test 3: null radar => explicit NO-RADAR waiting state, no fake numbers ----
test('null radar shows NO-RADAR waiting state', () => {
  renderMarketRadar(null);
  assert.strictEqual(elements['radar-status'].textContent, 'NO RADAR DATA', 'null => NO RADAR DATA');
  assert.strictEqual(elements['radar-best-type'].textContent, '-', 'no fake setup type');
  assert.strictEqual(elements['radar-quality'].textContent, '-', 'no fake quality');
  assert.strictEqual(elements['radar-count'].textContent, '-', 'no fake candidate count');
  assert.strictEqual(elements['radar-updated'].textContent, '-', 'no fake timestamp');
  assert.ok(
    elements['radar-decision'].textContent.indexOf('Awaiting radar snapshot') !== -1,
    'waiting message shown'
  );
});

// ---- Test 4: missing best_setup still renders state/count, no crash ----
test('radar with no best_setup does not crash and shows dashes', () => {
  renderMarketRadar({ state: 'NO_SETUP', candidate_count: 0, news_state: 'CALM', decision_reason: 'NO_SETUP', updated_at: '2026-08-26T03:25:00+00:00' });
  assert.strictEqual(elements['radar-status'].textContent, 'NO_SETUP');
  assert.strictEqual(elements['radar-best-type'].textContent, '-');
  assert.strictEqual(elements['radar-direction'].textContent, '-');
  assert.strictEqual(elements['radar-news'].textContent, 'CALM');
});

console.log('\nAll ' + passed + ' market-radar UI tests passed.');
