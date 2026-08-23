/**
 * Command Center Time Machine + Debug Console Frontend Tests
 *
 * Run:  node --test tests/js/command_center_timemachine.test.js
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

function makeEl() { return { value: '', textContent: '', innerHTML: '', style: {}, min: 0, max: 0, appendChild() {}, insertBefore() {}, querySelectorAll() { return []; }, addEventListener() {} }; }

function loadModule(srcFile, dom) {
  global.window = {
    addEventListener() {},
    NX: {
      api: {
        async get(url) {
          if (url.includes('bounds')) return { ok: true, body: { available: true, earliest: '2026-08-20T00:00:00Z', latest: '2026-08-23T00:00:00Z' } };
          if (url.includes('frame')) return { ok: true, body: { available: true, nodes: [{ strategy_id: 'A', zone: 'VALIDATED' }] } };
          return { ok: false };
        },
      },
      spatial: { update() {} },
    },
  };
  global.document = {
    getElementById(id) { return dom[id] || null; },
    createElement() {
      return makeEl();
    },
    addEventListener() {},
  };
  global.navigator = { clipboard: { writeText() {} } };
  const src = fs.readFileSync(path.join(__dirname, '../../Web/' + srcFile), 'utf8');
  const fn = new Function('window', 'document', 'navigator', src + '\nreturn window.NX;');
  return fn(global.window, global.document, global.navigator);
}

test('time machine initializes bounds and clamps slider', () => {
  const slider = makeEl();
  const label = makeEl();
  const nx = loadModule('command_center_timemachine.js', { 'scc-tm-slider': slider, 'scc-tm-label': label });
  return nx.tm.init().then(() => {
    assert.ok(slider.min && Number(slider.min) > 0);
    assert.ok(slider.max && Number(slider.max) > Number(slider.min));
    assert.ok(label.textContent.length > 0);
  });
});

test('time machine scrub fetches frame and updates spatial payload', async () => {
  let updatedPayload = null;
  const slider = makeEl();
  const label = makeEl();
  const nx = loadModule('command_center_timemachine.js', { 'scc-tm-slider': slider, 'scc-tm-label': label });
  // Patch AFTER module load — module captured window.NX reference at eval time,
  // but fetchFrame resolves window.NX.spatial.update dynamically at call time.
  global.window.NX.spatial.update = (p) => { updatedPayload = p; };
  await nx.tm.init();
  nx.tm.scrub(slider.max);
  await new Promise(r => setTimeout(r, 30)); // allow async frame fetch
  assert.ok(updatedPayload && updatedPayload.nodes && updatedPayload.nodes[0].strategy_id === 'A');
});

test('debug console classifies and stores events without DOM crash', () => {
  const dom = { 'scc-console-body': makeEl(), 'scc-console-search': makeEl(), 'scc-console-sev': makeEl() };
  const nx = loadModule('command_center_console.js', dom);
  const ev = { event_type: 'WALK_FORWARD_FAILURE', strategy_id: 'B48', correlation_id: 'corr-1', severity: 'WARN' };
  assert.doesNotThrow(() => nx.console.add(ev));
  assert.ok(Array.isArray(nx.console.getEvents()));
  assert.ok(nx.console.getEvents().length >= 1);
  // Classification must distinguish validation failure from stale recovery
  const stored = nx.console.getEvents()[nx.console.getEvents().length - 1];
  assert.strictEqual(stored._class, 'VALIDATION_FAILURE');
  nx.console.add({ event_type: 'GENERATION_SWEPT', severity: 'INFO' });
  const swept = nx.console.getEvents()[nx.console.getEvents().length - 1];
  assert.strictEqual(swept._class, 'STALE_RUN_RECOVERY');
});

test('debug console bounded retention (no unbounded memory growth)', () => {
  const dom = { 'scc-console-body': makeEl(), 'scc-console-search': makeEl(), 'scc-console-sev': makeEl() };
  const nx = loadModule('command_center_console.js', dom);
  for (let i = 0; i < 6000; i++) nx.console.add({ event_type: 'E' + i });
  assert.ok(nx.console.getEvents().length <= 5000);
});
