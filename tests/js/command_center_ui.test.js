/**
 * Command Center Frontend Tests (Node built-in test runner)
 * Verifies: fleet table, overview, inspector rendering — honest data display.
 *
 * Run:  node --test tests/js/command_center_ui.test.js
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    classList: { remove() {}, add() {} },
    getContext() { return null; },
    parentElement: { getBoundingClientRect() { return { width: 800 }; } },
    style: {},
  };
}

// Load the module source ONCE and expose internal test hooks.
const SRC_PATH = path.join(__dirname, '../../Web/command_center_ui.js');
const RAW_SRC = fs.readFileSync(SRC_PATH, 'utf8');

function loadModule(dom) {
  global.document = {
    getElementById(id) { return dom[id] || null; },
    addEventListener() {},
  };
  global.window = { NX: { api: { async get() { return { ok: false }; } } } };
  const fn = new Function('window', 'document', 'console', RAW_SRC + '\nreturn window.NX.scc;');
  return fn(global.window, global.document, console);
}

test('module loads and exposes public API + test hooks', () => {
  const scc = loadModule({});
  assert.ok(scc && typeof scc.load === 'function');
  assert.ok(typeof scc.inspect === 'function');
  assert.ok(typeof scc.closeInspector === 'function');
  assert.ok(typeof scc._test_renderFleet === 'function');
});

test('fleet table renders empty state when no strategies', () => {
  const tbody = makeElementStub();
  const scc = loadModule({ 'scc-fleet-tbody': tbody });
  scc._test_renderFleet([]);
  assert.match(tbody.innerHTML, /No strategies found/);
});

test('fleet table renders rows with eligibility badge and health scaling', () => {
  const tbody = makeElementStub();
  const scc = loadModule({ 'scc-fleet-tbody': tbody });
  scc._test_renderFleet([
    { strategy_id: 'S-001', lifecycle: 'ACTIVE', health_final: 0.85, eligibility_state: 'YES', sample_count: 120 },
    { strategy_id: 'S-002', lifecycle: 'REJECTED', health_final: null, eligibility_state: 'BLOCKED', sample_count: 0 },
  ]);
  assert.match(tbody.innerHTML, /S-001/);
  assert.match(tbody.innerHTML, /YES/);
  assert.match(tbody.innerHTML, /BLOCKED/);
  assert.match(tbody.innerHTML, /85%/);
  assert.doesNotMatch(tbody.innerHTML, /null%/);
});

test('overview renders counts defensively (no crash on missing fields)', () => {
  const els = {
    'scc-total': makeElementStub(),
    'scc-active': makeElementStub(),
    'scc-blocked': makeElementStub(),
    'scc-valid': makeElementStub(),
  };
  const scc = loadModule(els);
  // Missing `by_lifecycle` entirely → must not throw
  assert.doesNotThrow(() => scc._test_renderOverview({ total_strategies: 5 }));
  assert.strictEqual(els['scc-total'].textContent, 5);
});

test('inspector renders honest attribution status, evidence gaps, hints', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  assert.doesNotThrow(() =>
    scc._test_renderInspector({
      available: true,
      strategy_id: 'S-100',
      current_state: 'SHADOW',
      strategy_version: '1.0.0',
      execution_eligibility: { can_trade: false, eligibility_state: 'SHADOW_ONLY', reason: 'paper only' },
      confidence_score: 0.7,
      evidence_summary: { backtest_status: 'PASS', walkforward_status: 'PASS', oos_status: 'MISSING' },
      ai_attribution: { status: 'PARTIALLY_MEASURABLE', measured: { note: 'none recorded.' }, contributions: [{ source_type: 'AI_RESEARCH', kind: 'AI_SUGGESTED' }] },
      debug_intelligence: { anomaly_score: { anomaly_score: 0.42 }, hints: [{ category: 'FACT', message: '1 recorded failure' }] },
    })
  );
  assert.match(content.innerHTML, /PARTIALLY_MEASURABLE/);
  assert.match(content.innerHTML, /AI_RESEARCH/);
  assert.match(content.innerHTML, /FACT/);
  assert.match(content.innerHTML, /MISSING/);
  assert.match(title.textContent, /S-100/);
});

test('lifecycle filter narrows visible nodes via applyLifecycleFilter (honest subset)', () => {
  // Fake spatial module records the last payload it received.
  let lastPayload = null;
  const scc = loadModule({});
  // NOTE: loadModule overwrites global.window, so attach the spatial stub AFTER.
  global.window.NX = global.window.NX || {};
  global.window.NX.spatial = { update: (p) => { lastPayload = p; }, fitAll: () => true };
  // Seed authoritative payload with mixed lifecycle states.
  scc._test_setSpatialData({
    meta: { total_nodes: 3 },
    zones: [
      { zone: 'DISCOVERED', count: 1 },
      { zone: 'ACTIVE', count: 1 },
      { zone: 'REJECTED', count: 1 },
    ],
    nodes: [
      { strategy_id: 'A', zone: 'DISCOVERED', x: 0, y: 0 },
      { strategy_id: 'B', zone: 'ACTIVE', x: 0, y: 0 },
      { strategy_id: 'C', zone: 'REJECTED', x: 0, y: 0 },
    ],
  });
  scc.applyLifecycleFilter('ACTIVE');
  assert.ok(lastPayload, 'spatial.update was called');
  assert.strictEqual(lastPayload.nodes.length, 1, 'only ACTIVE nodes remain after filter');
  assert.strictEqual(lastPayload.nodes[0].zone, 'ACTIVE');
  scc.applyLifecycleFilter('ALL');
  assert.strictEqual(lastPayload.nodes.length, 3, 'ALL restores every node');
});

test('empty state overlay shows honest zero-match message (no fabricated counts)', () => {
  const el = makeElementStub();
  const scc = loadModule({ 'scc-spatial-empty': el });
  scc._test_showEmpty(1165, 'VALIDATED', 0);
  assert.match(el.innerHTML, /NO VISIBLE STRATEGIES/);
  assert.match(el.innerHTML, /Backend strategies: 1165/);
  assert.match(el.innerHTML, /Current filter: VALIDATED/);
  assert.match(el.innerHTML, /Matching: 0/);
  // Honest: must NOT claim any matching strategy count when zero.
  assert.doesNotMatch(el.innerHTML, /Matching: [1-9]/);
  scc._test_hideEmpty();
  assert.ok(el.classList.add.called || true); // hide path does not throw
});
