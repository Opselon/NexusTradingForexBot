/**
 * Control Center design system tests (NX.cc.design)
 * CHG-0043 / TASK-CONTROL-CENTER — Hermes-UI
 *
 * Covers: state vocabulary (never green-only), badge/mode rendering,
 * freshness STALE promotion, NOT-RECORDED honesty, structured confirm
 * dialog (no generic "Are you sure?"), bar breakdown math, empty states.
 *
 * Run:  node --test tests/js/cc_design.test.js
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

function makeEl(tag) {
  return {
    tag, value: '', textContent: '', innerHTML: '', style: {},
    children: [], attributes: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    addEventListener() {}, focus() {},
    querySelector() { return makeEl('button'); },
    querySelectorAll() { return []; },
    remove() {},
    appendChild(c) { this.children.push(c); },
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
  };
}

function loadDesign() {
  global.window = { NX: {} };
  global.document = {
    getElementById() { return null; },
    createElement(tag) { return makeEl(tag); },
    addEventListener() {},
    body: makeEl('body'),
  };
  global.navigator = {};
  const src = fs.readFileSync(
    path.join(__dirname, '../../Web/cc_components.js'), 'utf8'
  );
  new Function('window', 'document', 'navigator', src)(
    global.window, global.document, global.navigator
  );
  return global.window.NX.cc.design;
}

test('state vocabulary distinguishes non-healthy states', () => {
  const D = loadDesign();
  const required = ['HEALTHY', 'DEGRADED', 'BLOCKED', 'UNAVAILABLE', 'DISABLED',
    'NOT_CONFIGURED', 'RECOVERING', 'UNKNOWN'];
  required.forEach((s) => assert.ok(D.STATES[s], 'missing state ' + s));
  // Every state must have a glyph (color is never the only signal)
  Object.values(D.STATES).forEach((s) => {
    assert.ok(s.glyph && s.glyph.length, 'glyph required for ' + s.label);
    assert.ok(s.label, 'label required');
  });
});

test('statusBadge embeds glyph + label text (not color alone)', () => {
  const D = loadDesign();
  const html = D.statusBadge('DEGRADED');
  assert.ok(html.includes('\u26A0'), 'glyph present');
  assert.ok(html.includes('DEGRADED'), 'label present');
  assert.ok(html.includes('ccb-warn'), 'state class present');
});

test('modeBadge separates LIVE from non-live modes', () => {
  const D = loadDesign();
  assert.ok(D.modeBadge('LIVE').includes('ccb-mode-live'));
  assert.ok(D.modeBadge('PAPER').includes('ccb-mode-paper'));
  assert.ok(D.modeBadge('SHADOW').includes('ccb-mode-shadow'));
  assert.ok(D.modeBadge('REPLAY').includes('ccb-mode-replay'));
  // Unknown modes must not render as LIVE
  assert.ok(!D.modeBadge('WEIRD').includes('ccb-mode-live'));
});

test('freshAge promotes STALE honestly', () => {
  const D = loadDesign();
  const fresh = D.freshAge(new Date(Date.now() - 2000).toISOString());
  assert.strictEqual(fresh.state, 'FRESH');
  const stale = D.freshAge(new Date(Date.now() - 10 * 60 * 1000).toISOString());
  assert.strictEqual(stale.state, 'STALE');
  const noTs = D.freshAge(null);
  assert.strictEqual(noTs.state, 'UNKNOWN');
});

test('metricCard renders NOT RECORDED instead of fabricating zeros', () => {
  const D = loadDesign();
  const html = D.metricCard({ label: 'X', value: null });
  assert.ok(html.includes('NOT RECORDED'));
  assert.ok(!/>0</.test(html));
});

test('emptyState / errorState render explicit role + content', () => {
  const D = loadDesign();
  const e = D.emptyState('No shadow runs yet', 'Start a shadow session first.');
  assert.ok(e.includes('No shadow runs yet'));
  assert.ok(e.includes('role="status"'));
  const r = D.errorState('Request failed', 'timeout', 'req_123', 'retryFn');
  assert.ok(r.includes('role="alert"'));
  assert.ok(r.includes('req_123'));
  assert.ok(r.includes('retryFn'));
});

test('probCell never fabricates a missing probability', () => {
  const D = loadDesign();
  assert.ok(D.probCell(null).includes('EVIDENCE NOT RECORDED'));
  assert.ok(D.probCell(undefined).includes('EVIDENCE NOT RECORDED'));
  assert.strictEqual(D.probCell(0.5), '50.0%');
});

test('barBreakdown computes share against the passed total only', () => {
  const D = loadDesign();
  const html = D.barBreakdown(
    [{ label: 'A', count: 50 }, { label: 'B', count: 50 }], 200
  );
  assert.ok(html.includes('(25%)'), 'share uses explicit total, not row sum');
});

test('confirmDialog requires structured rows, not a generic prompt', () => {
  const D = loadDesign();
  const overlay = makeEl('div');
  const origCreate = global.document.createElement;
  global.document.createElement = function (tag) {
    const el = makeEl(tag);
    if (tag === 'div') { el.id = ''; el.querySelector = () => makeEl('button'); }
    return el;
  };
  global.document.getElementById = () => null;
  let confirmed = false;
  D.confirmDialog({
    action: 'Restart Runtime',
    current: 'Running',
    impact: 'In-memory state may reset.',
    recovery: 'Reconstructed from persistent storage.',
    confirmVerb: 'RESTART',
    onConfirm: () => { confirmed = true; },
  });
  global.document.createElement = origCreate;
  // The overlay must have been appended to body with the dialog content.
  const appended = global.document.body.children.pop();
  const html = String(appended.innerHTML || '');
  assert.ok(html.includes('ACTION'), 'ACTION row');
  assert.ok(html.includes('CURRENT STATE'), 'CURRENT STATE row');
  assert.ok(html.includes('IMPACT'), 'IMPACT row');
  assert.ok(html.includes('RECOVERY'), 'RECOVERY row');
  assert.ok(html.includes('CONFIRM RESTART'), 'verb-specific confirm button');
  assert.ok(!html.includes('Are you sure'), 'never generic');
});
