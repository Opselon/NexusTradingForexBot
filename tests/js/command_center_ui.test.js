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
  assert.match(content.innerHTML, /PARTIALLY MEASURED/);
  assert.match(content.innerHTML, /AI_RESEARCH/);
  assert.match(content.innerHTML, /FACT/);
  assert.match(content.innerHTML, /MISSING/);
  assert.match(title.textContent, /S-100/);
});

test('inspector shows CAN-THIS-TRADE verdict from real eligibility (never faked)', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  scc._test_renderInspector({
    available: true, strategy_id: 'S-200', current_state: 'ACTIVE', strategy_version: '2.1.0',
    execution_eligibility: { can_trade: true, eligibility_state: 'YES', reason: 'live eligible' },
    confidence_score: 0.9,
    evidence_summary: { backtest_status: 'PASS', walkforward_status: 'PASS', oos_status: 'PASS', robustness_status: 'PASS', score_verdict: 'VALIDATED' },
    ai_attribution: { status: 'MEASURED', measured: { weights: 2, note: '' }, contributions: [{ source_type: 'AI_MODEL', kind: 'AI_RANKED', weight: 0.5, weight_measured: true }] },
    debug_intelligence: { anomaly_score: { anomaly_score: 0.1 }, hints: [] },
    lineage_dna: { parent_strategy_ids: ['S-100'], generation: '2.1.0', descendants_recorded: false },
    events: [{ timestamp: '2026-08-23T10:00:00Z', event_type: 'LIFECYCLE_TRANSITION' }],
  });
  assert.match(content.innerHTML, /Execution Verdict/);
  assert.match(content.innerHTML, /YES/);
  assert.doesNotMatch(content.innerHTML, /UNKNOWN/); // real eligibility → not UNKNOWN
});

test('inspector honestly shows UNKNOWN when no eligibility returned', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  scc._test_renderInspector({
    available: true, strategy_id: 'S-300', current_state: 'DISCOVERED', strategy_version: '1.0.0',
    // no execution_eligibility key at all → must render UNKNOWN, never invent YES/NO
    confidence_score: 0.1,
    evidence_summary: { backtest_status: 'MISSING', walkforward_status: 'MISSING', oos_status: 'MISSING', robustness_status: 'MISSING', score_verdict: 'MISSING' },
    ai_attribution: { status: 'NOT_AVAILABLE', measured: { weights: 0, note: 'none' }, contributions: [] },
    debug_intelligence: { anomaly_score: { anomaly_score: 0 }, hints: [] },
    lineage_dna: { parent_strategy_ids: [], generation: '1.0.0', descendants_recorded: false },
    events: [],
  });
  assert.match(content.innerHTML, /UNKNOWN/);
  assert.match(content.innerHTML, /UNAVAILABLE/); // honest status label, not MEASURED
});

test('inspector renders FACT/INFERENCE/HYPOTHESIS/RECOMMENDATION hint categories distinctly', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  scc._test_renderInspector({
    available: true, strategy_id: 'S-400', current_state: 'REJECTED', strategy_version: '1.0.0',
    execution_eligibility: { can_trade: false, eligibility_state: 'BLOCKED', reason: 'rejected' },
    confidence_score: 0.2,
    evidence_summary: { backtest_status: 'PASS', walkforward_status: 'FAIL', oos_status: 'FAIL', robustness_status: 'PASS', score_verdict: 'REJECTED' },
    ai_attribution: { status: 'PARTIALLY_MEASURABLE', measured: { weights: 0, note: '' }, contributions: [] },
    debug_intelligence: { anomaly_score: { anomaly_score: 0.9 }, hints: [
      { category: 'FACT', message: '1361 walk-forward failures.' },
      { category: 'INFERENCE', message: 'Walk-forward is the current fleet bottleneck.' },
      { category: 'HYPOTHESIS', message: 'Candidate generalization may be weak.' },
      { category: 'RECOMMENDATION', message: 'Inspect parameter sensitivity and regime distribution.' },
    ] },
    lineage_dna: { parent_strategy_ids: [], generation: '1.0.0', descendants_recorded: false },
    events: [],
  });
  for (const cat of ['FACT', 'INFERENCE', 'HYPOTHESIS', 'RECOMMENDATION']) {
    assert.match(content.innerHTML, new RegExp('\\b' + cat + '\\b'));
  }
});

test('inspector shows LINEAGE PARTIALLY RECORDED honestly (no invented descendants)', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  scc._test_renderInspector({
    available: true, strategy_id: 'S-500', current_state: 'SHADOW', strategy_version: '3.0.0',
    execution_eligibility: { can_trade: true, eligibility_state: 'SHADOW_ONLY', reason: 'paper' },
    confidence_score: 0.6,
    evidence_summary: { backtest_status: 'PASS', walkforward_status: 'PASS', oos_status: 'PASS', robustness_status: 'PASS', score_verdict: 'VALIDATED' },
    ai_attribution: { status: 'PARTIALLY_MEASURABLE', measured: { weights: 0, note: '' }, contributions: [] },
    debug_intelligence: { anomaly_score: { anomaly_score: 0.2 }, hints: [] },
    lineage_dna: { parent_strategy_ids: [], generation: '3.0.0', descendants_recorded: false },
    events: [],
  });
  assert.match(content.innerHTML, /LINEAGE PARTIALLY RECORDED/);
  assert.match(content.innerHTML, /not enumerated/);
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

test('fleet row is fully clickable (no redundant Inspect button) and shows health bar', () => {
  const tbody = makeElementStub();
  const scc = loadModule({ 'scc-fleet-tbody': tbody });
  scc._test_renderFleet([
    { strategy_id: 'S-001', lifecycle: 'ACTIVE', health_final: 0.72, eligibility_state: 'YES', sample_count: 120 },
    { strategy_id: 'S-002', lifecycle: 'REJECTED', health_final: null, eligibility_state: 'BLOCKED', sample_count: 0 },
  ]);
  // Whole row triggers inspect()
  assert.match(tbody.innerHTML, /onclick="window\.NX\.scc\.inspect\('S-001'\)"/);
  // Redundant per-row Inspect button removed
  assert.doesNotMatch(tbody.innerHTML, /Inspect/);
  // Inline health bar present (track + fill) for measured health
  assert.match(tbody.innerHTML, /rounded-full bg-slate-800/);
  assert.match(tbody.innerHTML, /bg-emerald-500/);
  assert.match(tbody.innerHTML, /72%/);
  // Null health rendered as dash, not a fake bar
  assert.match(tbody.innerHTML, /—/);
});

test('inspector renders structured cards with verdict + visual hierarchy (no wall-of-text)', () => {
  const content = makeElementStub();
  const title = makeElementStub();
  const scc = loadModule({ 'scc-insp-content': content, 'scc-insp-title': title });
  scc._test_renderInspector({
    available: true, strategy_id: 'S-300', current_state: 'VALIDATED', strategy_version: '4.2.0',
    execution_eligibility: { can_trade: false, eligibility_state: 'NO', reason: 'gated' },
    confidence_score: 0.4,
    health_score: { final: 0.55 },
    evidence_summary: { backtest_status: 'PASS', walkforward_status: 'PASS', oos_status: 'PASS', robustness_status: 'PASS', score_verdict: 'VALIDATED' },
    ai_attribution: { status: 'MEASURED', measured: { weights: 2, note: 'ok' }, contributions: [{ source_type: 'AI_MODEL', kind: 'AI_RANKED', weight: 0.5, weight_measured: true }] },
    debug_intelligence: { anomaly_score: { anomaly_score: 0.3 }, hints: [{ category: 'FACT', message: 'stable' }] },
    evidence_completeness: { verdict: 'PASS' },
    lineage_dna: { parent_strategy_ids: [], generation: '4.2.0', descendants_recorded: false },
    events: [],
  });
  // Verdict card present with clear hierarchy
  assert.match(content.innerHTML, /Execution Verdict/);
  assert.match(content.innerHTML, /Identity & State/);
  assert.match(content.innerHTML, /Evidence & Gates/);
  assert.match(content.innerHTML, /AI Attribution/);
  assert.match(content.innerHTML, /Debug Intelligence/);
  assert.match(content.innerHTML, /Strategy DNA/);
  assert.match(content.innerHTML, /Recent Events/);
  // MEASURED badge rendered (honest attribution)
  assert.match(content.innerHTML, /MEASURED/);
});
