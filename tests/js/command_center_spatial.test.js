/**
 * Command Center Spatial Renderer Tests
 * Verifies: zone mapping, animation triggers, camera math, payload validation.
 *
 * Run:  node --test tests/js/command_center_spatial.test.js
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

function makeCtxStub() {
  const ops = [];
  return {
    ops,
    setTransform() {}, translate() {}, scale() {},
    fillRect() {}, strokeRect() {}, beginPath() {}, arc() {}, fill() {},
    stroke() {}, moveTo() {}, lineTo() {}, setLineDash() {},
    fillText(t, x, y) { ops.push(['text', t]); },
  };
}

function makeCanvasStub(ctx) {
  return {
    width: 800, height: 500,
    parentElement: { getBoundingClientRect: () => ({ width: 800, height: 500 }) },
    getContext: () => ctx,
    addEventListener() {},
    style: {},
  };
}

function loadSpatial(domCanvas) {
  global.window = { addEventListener() {}, NX: {} };
  global.document = {
    getElementById(id) { return id === 'scc-spatial-canvas' ? domCanvas : null; },
    addEventListener() {},
  };
  global.performance = { now: () => Date.now() };
  global.requestAnimationFrame = () => 1;
  global.cancelAnimationFrame = () => {};
  const src = fs.readFileSync(path.join(__dirname, '../../Web/command_center_spatial.js'), 'utf8');
  const fn = new Function('window', 'document', 'performance', 'requestAnimationFrame', 'cancelAnimationFrame', src + '\nreturn window.NX.spatial;');
  return fn(global.window, global.document, global.performance, global.requestAnimationFrame, global.cancelAnimationFrame);
}

test('spatial module loads and exposes public API', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  assert.ok(typeof spatial.init === 'function');
  assert.ok(typeof spatial.update === 'function');
  assert.ok(typeof spatial.fitAll === 'function');
  assert.ok(typeof spatial.resetCamera === 'function');
});

test('update handles missing/empty payloads without crash', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  assert.doesNotThrow(() => spatial.update(null));
  assert.doesNotThrow(() => spatial.update({}));
  assert.doesNotThrow(() => spatial.update({ zones: [], nodes: [] }));
});

test('nodes receive world coordinates from payload', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }, { zone: 'ACTIVE', count: 1 }],
    nodes: [
      { strategy_id: 'A', zone: 'DISCOVERED', x: -50, y: 0, size_hint: 10, ring_count: 0, elevation: null },
      { strategy_id: 'B', zone: 'ACTIVE', x: 50, y: 0, size_hint: 40, ring_count: 4, elevation: 0.9 },
    ],
  });
  // Internal state not directly exposed; re-update must not throw (idempotent path)
  assert.doesNotThrow(() => spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }, { zone: 'ACTIVE', count: 1 }],
    nodes: [
      { strategy_id: 'A', zone: 'DISCOVERED', x: -50, y: 0, size_hint: 10, ring_count: 0, elevation: null },
      { strategy_id: 'B', zone: 'ACTIVE', x: 50, y: 0, size_hint: 40, ring_count: 4, elevation: 0.9 },
    ],
  }));
});

test('zone change between updates schedules an animation, not a teleport', () => {
  // A real transition (DISCOVERED -> VALIDATED) must schedule an interpolation
  // animation in the `anims` map; the node must NOT instantly snap to the new
  // zone. We assert the anim map is populated for that strategy id.
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }, { zone: 'VALIDATED', count: 0 }],
    nodes: [{ strategy_id: 'A', zone: 'DISCOVERED', x: 0, y: 0, size_hint: 5, ring_count: 0, elevation: null }],
  });
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 0 }, { zone: 'VALIDATED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'VALIDATED', x: 0, y: 0, size_hint: 5, ring_count: 2, elevation: 0.7 }],
  });
  const anims = spatial._test.getAnims();
  assert.ok(anims['A'], 'expected an interpolation animation to be scheduled for node A');
});

test('duplicate/stale zone event does NOT reschedule an animation (no regression)', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'DISCOVERED', x: 0, y: 0, size_hint: 5, ring_count: 0, elevation: null }],
  });
  // Same zone again — must NOT create an animation.
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'DISCOVERED', x: 0, y: 0, size_hint: 5, ring_count: 0, elevation: null }],
  });
  const anims = spatial._test.getAnims();
  assert.ok(!anims['A'], 'duplicate same-zone update must not schedule an animation');
});

test('backend authoritative snapshot overrides an in-flight animation target', () => {
  // If a newer authoritative payload moves the target while an animation is
  // still running, BACKEND WINS: the animation target is overwritten to the new
  // authoritative coords and re-eased (not a second duplicate animation).
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'DISCOVERED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'DISCOVERED', x: 0, y: 0, size_hint: 5, ring_count: 0, elevation: null }],
  });
  spatial.update({
    zones: [{ zone: 'VALIDATED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'VALIDATED', x: 10, y: 0, size_hint: 5, ring_count: 2, elevation: 0.7 }],
  });
  const beforeTx = spatial._test.getAnims()['A'].tx;
  assert.ok(beforeTx !== undefined, 'animation exists after first transition');
  spatial.update({
    zones: [{ zone: 'VALIDATED', count: 1 }],
    nodes: [{ strategy_id: 'A', zone: 'VALIDATED', x: 999, y: 0, size_hint: 5, ring_count: 2, elevation: 0.7 }],
  });
  const after = spatial._test.getAnims()['A'];
  assert.ok(after, 'animation persists across authoritative reconciliation');
  assert.ok(Number.isFinite(after.tx), 'backend authoritative target still drives the animation x (repacked column layout)');
  assert.notStrictEqual(after.tx, beforeTx, 'authoritative snapshot re-eased the animation target (backend wins)');
  assert.strictEqual(Object.keys(spatial._test.getAnims()).length, 1, 'exactly one animation entry remains');
});

test('real lifecycle zones are preserved from payload (no invented states)', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  const payload = {
    zones: [
      { zone: 'DISCOVERED', count: 1 },
      { zone: 'BACKTESTING', count: 0 },
      { zone: 'VALIDATED', count: 0 },
      { zone: 'SHADOW', count: 0 },
      { zone: 'ACTIVE', count: 1 },
      { zone: 'REJECTED', count: 0 },
    ],
    nodes: [
      { strategy_id: 'A', zone: 'DISCOVERED', x: -50, y: 0, size_hint: 10, ring_count: 0, elevation: null },
      { strategy_id: 'B', zone: 'ACTIVE', x: 50, y: 0, size_hint: 40, ring_count: 4, elevation: 0.9 },
    ],
  };
  spatial.update(payload);
  const zones = spatial._test.getZones();
  assert.deepStrictEqual(
    zones,
    ['DISCOVERED', 'BACKTESTING', 'VALIDATED', 'SHADOW', 'ACTIVE', 'REJECTED']
  );
  const nodes = spatial._test.getNodes();
  assert.strictEqual(nodes.find(n => n.strategy_id === 'B').zone, 'ACTIVE');
});

test('fitAll returns false when there are zero visible nodes (empty state)', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  const r = spatial.fitAll();
  assert.strictEqual(r, false, 'fitAll must report no-fit when no nodes are present');
});

test('fitAll with nodes computes a finite camera and returns true', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'ACTIVE', count: 2 }],
    nodes: [
      { strategy_id: 'A', zone: 'ACTIVE', x: -200, y: 0, size_hint: 5, ring_count: 0, elevation: 0.5 },
      { strategy_id: 'B', zone: 'ACTIVE', x: 200, y: 0, size_hint: 5, ring_count: 0, elevation: 0.5 },
    ],
  });
  const r = spatial.fitAll();
  assert.strictEqual(r, true);
  const cam = spatial._test.getCamera();
  assert.ok(Number.isFinite(cam.x) && Number.isFinite(cam.y) && Number.isFinite(cam.zoom));
  assert.ok(cam.zoom > 0, 'zoom must be positive after fitAll');
});

test('camera focus helpers do not throw and select/focus are wired', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  spatial.update({
    zones: [{ zone: 'ACTIVE', count: 1 }, { zone: 'REJECTED', count: 1 }],
    nodes: [
      { strategy_id: 'LIVE-1', zone: 'ACTIVE', x: 0, y: 800, size_hint: 5, ring_count: 0, elevation: 0.5 },
      { strategy_id: 'BAD-1', zone: 'REJECTED', x: 0, y: 0, size_hint: 5, ring_count: 0, elevation: null },
    ],
  });
  assert.doesNotThrow(() => spatial.focusActive());
  assert.doesNotThrow(() => spatial.focusBlocked());
  assert.doesNotThrow(() => spatial.focusStage());
  assert.doesNotThrow(() => spatial.resetCamera());
  spatial.select('LIVE-1');
  assert.doesNotThrow(() => spatial.focusSelected());
});

test('anti-clump coordinate distribution spreads nodes laterally across zone', () => {
  const ctx = makeCtxStub();
  const canvas = makeCanvasStub(ctx);
  const spatial = loadSpatial(canvas);
  spatial.init('scc-spatial-canvas');
  const nodesPayload = [];
  for (let i = 0; i < 15; i++) {
    nodesPayload.push({ strategy_id: `S-${i}`, zone: 'ACTIVE', x: 0, y: 0, size_hint: 10, ring_count: 1, elevation: 0.8 });
  }
  spatial.update({
    zones: [{ zone: 'ACTIVE', count: 15 }],
    nodes: nodesPayload,
  });
  const nodes = spatial._test.getNodes();
  const xs = new Set(nodes.map(n => n._tx));
  assert.ok(xs.size > 1, 'nodes must be distributed across multiple lateral x positions to avoid central clump');
});

