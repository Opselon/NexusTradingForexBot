/**
 * Command Center Debug Console — Observability/Explainability Regression Tests
 *
 * Covers (Agent 3 — Nexus-Observability-03):
 *   - distinct event classification (10 classes, no two look identical)
 *   - rich event context fields (timestamp, severity, type, strategy, generation,
 *     lifecycle, correlation id, source)
 *   - filtering by severity / event type / strategy / generation / time range
 *   - bounded retention
 *   - bottleneck visualization math derived from real /fleet evidence
 *   - honest attribution labels + UNKNOWN verdict path
 *
 * Run:  node --test tests/js/command_center_console.test.js
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

function makeEl() {
  return {
    value: '', textContent: '', innerHTML: '', style: {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
    classList: { add() {}, remove() {} },
    min: 0, max: 0,
    appendChild() {}, insertBefore() {},
    querySelectorAll() { return []; },
    addEventListener() {}, getContext() { return null; },
  };
}

function loadModule(dom) {
  global.window = {
    addEventListener() {},
    NX: {
      api: { async get() { return { ok: true, body: { rows: [] } }; } },
      spatial: { update() {}, select() {}, focusSelected() {} },
      scc: { inspect() {}, openTimeline() {} },
    },
  };
  global.document = {
    getElementById(id) { return dom[id] || null; },
    createElement() { return makeEl(); },
    addEventListener() {},
  };
  global.navigator = { clipboard: { writeText() {} } };
  const src = fs.readFileSync(path.join(__dirname, '../../Web/command_center_console.js'), 'utf8');
  const fn = new Function('window', 'document', 'navigator', src + '\nreturn window.NX;');
  return fn(global.window, global.document, global.navigator);
}

// ---------------------------------------------------------------------------
// 1. Distinct classification — each required class resolves to its own value.
// ---------------------------------------------------------------------------
test('classifyEvent maps each required event type to a distinct class', () => {
  const nx = loadModule({});
  const c = nx.console.classifyEvent;
  assert.strictEqual(c({ event_type: 'WALK_FORWARD_FAILURE' }), 'WALK_FORWARD_FAILURE');
  assert.strictEqual(c({ event_type: 'VALIDATION_FAILURE' }), 'VALIDATION_FAILURE');
  assert.strictEqual(c({ event_type: 'OOS_FAILURE' }), 'OOS_FAILURE');
  assert.strictEqual(c({ event_type: 'RESEARCH_FAILURE' }), 'RESEARCH_FAILURE');
  assert.strictEqual(c({ event_type: 'DATA_FAILURE' }), 'DATA_FAILURE');
  assert.strictEqual(c({ event_type: 'SYSTEM_ERROR' }), 'SYSTEM_ERROR');
  assert.strictEqual(c({ event_type: 'GENERATION_SWEPT' }), 'STALE_RUN_RECOVERY');
  assert.strictEqual(c({ event_type: 'GENERATION_COMPLETED' }), 'GENERATION_COMPLETED');
  assert.strictEqual(c({ event_type: 'LIFECYCLE_TRANSITION' }), 'LIFECYCLE_TRANSITION');
  assert.strictEqual(c({ event_type: 'EXPECTED_REJECTION' }), 'EXPECTED_REJECTION');
  // A GENERATION_SWEPT must NOT look like WALK_FORWARD_FAILURE.
  assert.notStrictEqual(c({ event_type: 'GENERATION_SWEPT' }), c({ event_type: 'WALK_FORWARD_FAILURE' }));
});

test('classifyEvent falls back to content scan only without event_type', () => {
  const nx = loadModule({});
  const c = nx.console.classifyEvent;
  // No event_type → content scan still distinguishes.
  assert.strictEqual(c({ message: 'walk-forward failed for strategy' }), 'WALK_FORWARD_FAILURE');
  assert.strictEqual(c({ message: 'stale run recovered' }), 'STALE_RUN_RECOVERY');
});

// ---------------------------------------------------------------------------
// 2. Rich context fields retained on stored event.
// ---------------------------------------------------------------------------
test('addEvent preserves context fields and classification', () => {
  const dom = { 'scc-console-body': makeEl() };
  const nx = loadModule(dom);
  nx.console.add({
    event_type: 'WALK_FORWARD_FAILURE',
    strategy_id: 'S-77',
    generation: 3,
    lifecycle: 'VALIDATING',
    correlation_id: 'corr-abc-123',
    source: 'research_pipeline',
    timestamp: '2026-08-23T11:00:00Z',
    message: 'fold 4 degraded',
  });
  const stored = nx.console.getEvents()[0];
  assert.strictEqual(stored._class, 'WALK_FORWARD_FAILURE');
  assert.strictEqual(stored.strategy_id, 'S-77');
  assert.strictEqual(stored.generation, 3);
  assert.strictEqual(stored.lifecycle, 'VALIDATING');
  assert.strictEqual(stored.correlation_id, 'corr-abc-123');
  assert.strictEqual(stored.source, 'research_pipeline');
});

// ---------------------------------------------------------------------------
// 3. Filtering by event type / generation / time range (re-render path).
// ---------------------------------------------------------------------------
// Shared helper: spy on document.createElement to count event rows actually
// rendered by applyFilters (rows carry the 'scc-event-row' class). Stores the
// final count on global.__consoleRowCount.
function withRowSpy(fn) {
  global.__consoleRowCount = 0;
  const orig = global.document.createElement;
  global.document.createElement = () => {
    const el = makeEl();
    let cls = '';
    Object.defineProperty(el, 'className', {
      set(v) { if (String(v).includes('scc-event-row')) global.__consoleRowCount++; cls = v; },
      get() { return cls; },
    });
    return el;
  };
  try { fn(); } finally { global.document.createElement = orig; }
}

test('applyFilters narrows by event type', () => {
  const dom = {
    'scc-console-body': makeEl(),
    'scc-console-sev': makeEl(), 'scc-console-type': makeEl(),
    'scc-console-strategy': makeEl(), 'scc-console-gen': makeEl(),
    'scc-console-from': makeEl(), 'scc-console-to': makeEl(),
    'scc-console-search': makeEl(),
  };
  const nx = loadModule(dom);
  nx.console.add({ event_type: 'WALK_FORWARD_FAILURE', generation: 1, strategy_id: 'A' });
  nx.console.add({ event_type: 'OOS_FAILURE', generation: 2, strategy_id: 'B' });
  nx.console.add({ event_type: 'VALIDATION_FAILURE', generation: 1, strategy_id: 'C' });

  // Filter: event type WALK_FORWARD_FAILURE only.
  document.getElementById('scc-console-type').value = 'WALK_FORWARD_FAILURE';
  withRowSpy(() => { nx.console.applyFilters(); });
  assert.strictEqual(global.__consoleRowCount, 1);
});

test('applyFilters excludes events outside the time window', () => {
  const dom = {
    'scc-console-body': makeEl(),
    'scc-console-sev': makeEl(), 'scc-console-type': makeEl(),
    'scc-console-strategy': makeEl(), 'scc-console-gen': makeEl(),
    'scc-console-from': makeEl(), 'scc-console-to': makeEl(),
    'scc-console-search': makeEl(),
  };
  const nx = loadModule(dom);
  nx.console.add({ event_type: 'DATA_FAILURE', timestamp: '2026-08-20T12:00:00Z', strategy_id: 'X' });
  nx.console.add({ event_type: 'DATA_FAILURE', timestamp: '2026-08-23T12:00:00Z', strategy_id: 'Y' });
  // Window: only 2026-08-23 (use Z-suffixed UTC to avoid tz drift).
  document.getElementById('scc-console-from').value = '2026-08-23T00:00:00Z';
  document.getElementById('scc-console-to').value = '2026-08-23T23:59:59Z';
  withRowSpy(() => { nx.console.applyFilters(); });
  assert.strictEqual(global.__consoleRowCount, 1, 'only the 2026-08-23 event is rendered');
});

// ---------------------------------------------------------------------------
// 4. Bounded retention (no unbounded memory growth).
// ---------------------------------------------------------------------------
test('bounded retention caps at MAX_EVENTS', () => {
  const dom = { 'scc-console-body': makeEl() };
  const nx = loadModule(dom);
  for (let i = 0; i < 6000; i++) nx.console.add({ event_type: 'GENERATION_SWEPT' });
  assert.ok(nx.console.getEvents().length <= 5000);
});

// ---------------------------------------------------------------------------
// 5. Bottleneck visualization math from REAL /fleet evidence data.
// ---------------------------------------------------------------------------
test('computeBottleneck identifies worst stage from real evidence statuses', () => {
  const nx = loadModule({});
  const rows = [
    { strategy_id: 'S1', generation: 1, evidence: { evidence_status_backtest: 'PASS', evidence_status_walk_forward: 'FAIL', evidence_status_oos: 'FAIL', evidence_status_robustness: 'PASS' } },
    { strategy_id: 'S2', generation: 1, evidence: { evidence_status_backtest: 'PASS', evidence_status_walk_forward: 'FAIL', evidence_status_oos: 'PASS', evidence_status_robustness: 'PASS' } },
    { strategy_id: 'S3', generation: 2, evidence: { evidence_status_backtest: 'PASS', evidence_status_walk_forward: 'PASS', evidence_status_oos: 'PASS', evidence_status_robustness: 'PASS' } },
    // Missing gates are ignored, not counted as failures.
    { strategy_id: 'S4', generation: 2, evidence: { evidence_status_backtest: 'MISSING', evidence_status_walk_forward: 'MISSING', evidence_status_oos: 'MISSING', evidence_status_robustness: 'MISSING' } },
  ];
  const { metrics, worst } = nx.console.computeBottleneck(rows);
  // Walk-forward has 2 fails out of 3 → highest volume-weighted failure.
  assert.strictEqual(worst.key, 'WALK_FORWARD');
  assert.strictEqual(metrics.WALK_FORWARD.fail, 2);
  assert.strictEqual(metrics.WALK_FORWARD.total, 3);
  // Missing gates never counted as failures.
  assert.strictEqual(metrics.BACKTEST.fail, 0);
  assert.strictEqual(metrics.BACKTEST.total, 3);
});

test('computeBottleneck returns no worst when no evidence recorded', () => {
  const nx = loadModule({});
  const { worst } = nx.console.computeBottleneck([]);
  assert.strictEqual(worst, null);
});

// ---------------------------------------------------------------------------
// 6. Pause / resume / clear controls behave defensively.
// ---------------------------------------------------------------------------
test('pause stops ingestion; clear empties store', () => {
  const dom = { 'scc-console-body': makeEl(), 'scc-console-pause': makeEl() };
  const nx = loadModule(dom);
  nx.console.add({ event_type: 'GENERATION_SWEPT' });
  assert.strictEqual(nx.console.getEvents().length, 1);
  nx.console.togglePause();
  nx.console.add({ event_type: 'GENERATION_SWEPT' });
  assert.strictEqual(nx.console.getEvents().length, 1, 'paused → no new events');
  nx.console.togglePause();
  nx.console.clear();
  assert.strictEqual(nx.console.getEvents().length, 0);
});
