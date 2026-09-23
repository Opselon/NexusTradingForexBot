/**
 * Wave-3 LANE A — static-layer paint cache correctness + perf contract.
 *
 * Run:  node --test tests/js/w3_paint_cache.test.mjs
 *   or: node tests/js/w3_paint_cache.test.mjs
 *
 * Imports the REAL production module (Node 24 strips the TS types;
 * paintCache.ts has ZERO runtime imports, so the whole seam runs in Node —
 * same pattern as ai_analysis_viz.test.mjs / pro_dependency_viz.test.mjs).
 *
 * Contract pinned by this suite:
 *  1. CACHE HIT: a warm frame issues strictly fewer paint calls than the cold
 *     frame (drawImage only — the static stage set does not run).
 *  2. ANY invalidator (bars identity, slot geometry, price scale, digits,
 *     kind, overlay contents, canvas size, palette/font) forces a full
 *     repaint: paintStaticLayers returns true AND the static stages run.
 *  3. STAGE PARITY: the cached static layer (backdrop+grid, volume, series)
 *     is byte-for-byte the same pixels chartPainter's inline statics and
 *     volumeOverlay/seriesRender produce — a cache may never alter what the
 *     operator sees (a stale OR a DRIFTED static layer is a lie).
 *  4. A reused layer is composited identically to a repainted one (the layer
 *     is opaque → the blit reproduces the same pixels, not an alpha stack).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  cacheKeyFor,
  paintStaticLayers,
  paintStaticStages,
  paintStaticBackdropGrid,
  paintStaticVolume,
  paintStaticSeries,
  staticPriceTicks,
  readPaintCounters,
} from "../../frontend/src/pages/Dashboard/chart/paintCache.ts";

// ---------------------------------------------------------------------------
// Test scaffolding: a counting 2D ctx + a minimal canvas/document shim.
// ---------------------------------------------------------------------------

function makeCtx(dpr = 1) {
  const calls = [];
  const state = { fillStyle: "#000000", strokeStyle: "#000000", lineWidth: 1, font: "", textAlign: "left", globalAlpha: 1, lineDash: [] };
  const ctx = {
    calls,
    // paintChart runs under setTransform(dpr,…) — expose it for the seam.
    getTransform: () => ({ a: dpr, b: 0, c: 0, d: dpr, e: 0, f: 0 }),
    get fillStyle() {
      return state.fillStyle;
    },
    set fillStyle(v) {
      state.fillStyle = String(v);
      calls.push(["setFillStyle", String(v)]);
    },
    get strokeStyle() {
      return state.strokeStyle;
    },
    set strokeStyle(v) {
      state.strokeStyle = String(v);
      calls.push(["setStrokeStyle", String(v)]);
    },
    get lineWidth() {
      return state.lineWidth;
    },
    set lineWidth(v) {
      state.lineWidth = v;
      calls.push(["setLineWidth", v]);
    },
    get font() {
      return state.font;
    },
    set font(v) {
      state.font = String(v);
      calls.push(["setFont", String(v)]);
    },
    get textAlign() {
      return state.textAlign;
    },
    set textAlign(v) {
      state.textAlign = String(v);
      calls.push(["setTextAlign", String(v)]);
    },
    get globalAlpha() {
      return state.globalAlpha;
    },
    set globalAlpha(v) {
      state.globalAlpha = v;
      calls.push(["setGlobalAlpha", v]);
    },
    // The recorders below are what the cold-vs-warm assertion counts.
    fillRect: (...a) => calls.push(["fillRect", ...a]),
    strokeRect: (...a) => calls.push(["strokeRect", ...a]),
    fillText: (...a) => calls.push(["fillText", ...a]),
    strokeText: (...a) => calls.push(["strokeText", ...a]),
    beginPath: () => calls.push(["beginPath"]),
    moveTo: (...a) => calls.push(["moveTo", ...a]),
    lineTo: (...a) => calls.push(["lineTo", ...a]),
    closePath: () => calls.push(["closePath"]),
    stroke: () => calls.push(["stroke"]),
    fill: () => calls.push(["fill"]),
    rect: (...a) => calls.push(["rect", ...a]),
    roundRect: (...a) => calls.push(["roundRect", ...a]),
    clip: () => calls.push(["clip"]),
    save: () => calls.push(["save"]),
    restore: () => calls.push(["restore"]),
    setLineDash: (d) => {
      state.lineDash = d.slice();
      calls.push(["setLineDash", ...d]);
    },
    setTransform: (...a) => calls.push(["setTransform", ...a]),
    createLinearGradient: (...a) => {
      calls.push(["createLinearGradient", ...a]);
      return {
        addColorStop: (...b) => calls.push(["addColorStop", ...b]),
      };
    },
    measureText: (s) => ({ width: String(s).length * 6 }),
    // SEAM: the only path into the layer canvas.
    drawImage: (...a) => calls.push(["drawImage", ...a]),
  };
  return ctx;
}

/** Calls issued by the static stage set (paintStaticStages) — the cold-frame
 *  signature; a warm frame must do strictly less than this. */
function staticStageCalls(calls) {
  return calls.filter((c) => c[0] !== "drawImage" && c[0] !== "setTransform");
}

/** Layer canvas shim (Node has no document): every repaint asks for a fresh
 *  2d context, so `contexts` growth proves the static stages re-ran. */
function makeCanvas() {
  const cv = { width: 0, height: 0, contexts: [], getContext() {
    const c = makeCtx();
    this.contexts.push(c);
    return c;
  } };
  return cv;
}

const PAL = {
  bg: "#0a0f16",
  grid: "#1c2836",
  axisText: "#56697d",
  text: "#dbe4ee",
  dim: "#8398ad",
  green: "#3ecf8e",
  red: "#f2564d",
  amber: "#eba13f",
  violet: "#9d7bf0",
  accent: "#41a6f2",
  accentStrong: "#6cc0ff",
  panel: "#0d131b",
  borderStrong: "#2b3d51",
};

function makeGeom(w = 640, h = 320, opts = {}) {
  const { lo = 100, hi = 110, count = 60, priceH, volH = 40 } = opts;
  const plotW = w - 60 - 6;
  const plotH = h - 10 - 22;
  const vH = volH ?? Math.round(Math.min(96, Math.max(56, plotH * 0.18)));
  const pH = priceH ?? Math.max(40, plotH - vH);
  const x = (i) => 6 + (i * plotW) / count;
  const y = (p) => 10 + ((hi - p) / (hi - lo)) * pH;
  return {
    x,
    y,
    bw: plotW / count,
    plotW,
    priceH: pH,
    volY0: 10 + pH,
    volH: vH,
    w,
    h,
    left: 0,
  };
}

function makeBars(n, seed = 1) {
  const out = [];
  let v = 100;
  let s = seed;
  const rnd = () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
  for (let i = 0; i < n; i++) {
    v += (rnd() - 0.45) * 2;
    out.push({
      time: `2026-09-23T10:${String(i % 60).padStart(2, "0")}:00`,
      open: v,
      high: v + 0.5,
      low: v - 0.5,
      close: v + 0.2,
      tick_volume: 100 + Math.floor(rnd() * 900),
      is_complete: true,
    });
  }
  return out;
}

function makeScene(opts = {}) {
  const bars = opts.bars ?? makeBars(60);
  const count = opts.count ?? bars.length;
  return {
    shown: bars,
    left: opts.left ?? 0,
    count,
    kind: opts.kind ?? "candles",
    overlays: opts.overlays ?? {},
    liveBid: opts.liveBid ?? null,
    cursorIso: opts.cursorIso ?? null,
    digits: opts.digits ?? 2,
    hoverIdx: -1,
    hoverY: null,
    timeIndex: opts.timeIndex ?? new Map(bars.map((b, i) => [b.time, i])),
    indexAtOrBefore: opts.indexAtOrBefore ?? (() => -1),
  };
}

/** A cache handle the way PriceChart hands it over — the caller owns the
 *  ref; the layer canvas is where the static stages get painted. */
function makeCache() {
  return { key: "", canvas: makeCanvas() };
}

// ---------------------------------------------------------------------------
// 1. Cold vs warm draw-call accounting.
// ---------------------------------------------------------------------------

test("cold frame paints the full static stage set, warm frame blits only", () => {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  const main = makeCtx();
  const cold = paintStaticLayers(main, sc, geom, PAL, "ui-monospace", cache);
  assert.equal(cold, true, "first frame is always a repaint");
  const coldOff = cache.canvas.contexts;
  assert.equal(coldOff.length, 1, "cold frame paints into one fresh layer context");
  const coldWork = staticStageCalls(coldOff[0].calls);
  assert.ok(coldWork.length > 50, `cold frame did real work (${coldWork.length} stage calls)`);
  const coldCalls = coldWork.length + main.calls.length;

  const warm = makeCtx();
  const ctxsBefore = cache.canvas.contexts.length;
  const warmRet = paintStaticLayers(warm, sc, geom, PAL, "ui-monospace", cache);
  assert.equal(warmRet, false, "identical frame is served from the cache");
  assert.equal(
    cache.canvas.contexts.length,
    ctxsBefore,
    "warm frame requests NO new layer context — the static stages do not run",
  );
  assert.deepEqual(
    warm.calls.map((c) => c[0]),
    ["drawImage"],
    "warm frame issues exactly the layer blit on the main ctx",
  );
  assert.ok(
    warm.calls.length < coldCalls,
    `warm frame issues strictly fewer paint calls than cold (${warm.calls.length} < ${coldCalls})`,
  );
});

test("counters: frames/staticRepaints/cachedFrames track the seam", () => {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  const base = readPaintCounters();
  paintStaticLayers(makeCtx(), sc, geom, PAL, "m", cache);
  paintStaticLayers(makeCtx(), sc, geom, PAL, "m", cache);
  paintStaticLayers(makeCtx(), sc, geom, PAL, "m", cache);
  const after = readPaintCounters();
  assert.equal(after.frames - base.frames, 3);
  assert.equal(after.staticRepaints - base.staticRepaints, 1, "one cold repaint");
  assert.equal(after.cachedFrames - base.cachedFrames, 2, "two warm frames");
});

// ---------------------------------------------------------------------------
// 2. EVERY invalidator forces a full repaint.
// ---------------------------------------------------------------------------

function fullRepaintAfter(mutate, name) {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  paintStaticLayers(makeCtx(), sc, geom, PAL, "ui-monospace", cache);
  const before = cache.key;
  mutate({ sc, geom });
  const main = makeCtx();
  const ctxsBefore = cache.canvas.contexts.length;
  const ret = paintStaticLayers(main, sc, geom, PAL, "ui-monospace", cache);
  assert.equal(ret, true, `${name}: must report a full repaint`);
  assert.notEqual(cache.key, before, `${name}: cache key must change`);
  const off = cache.canvas.contexts.slice(ctxsBefore);
  assert.equal(off.length, 1, `${name}: exactly one fresh layer context`);
  assert.ok(
    staticStageCalls(off[0].calls).length > 50,
    `${name}: static stages must actually re-run`,
  );
}

function keyChangesAfter(mutate, name) {
  const sc = makeScene();
  const geom = makeGeom();
  const before = cacheKeyFor(sc, geom);
  mutate({ sc, geom });
  assert.notEqual(cacheKeyFor(sc, geom), before, `${name}: key must change`);
}

test("invalidators: any contract change forces a repaint + key change", () => {
  fullRepaintAfter(({ sc }) => {
    sc.shown = sc.shown.slice(1); // fewer bars (identity)
  }, "shown.length");
  fullRepaintAfter(({ sc }) => {
    const bars = sc.shown.slice();
    const last = bars[bars.length - 1];
    bars[bars.length - 1] = { ...last, close: (last.close ?? 0) + 5 };
    sc.shown = bars; // same length, different OHLC (identity)
  }, "in-place bar close");
  fullRepaintAfter(({ sc }) => {
    sc.shown = sc.shown.slice();
    sc.shown[0].is_complete = false; // forming-bar flag flip
  }, "is_complete flip");
  fullRepaintAfter(({ sc }) => {
    sc.left = 7; // window pan (slot geometry)
  }, "left");
  fullRepaintAfter(({ sc }) => {
    sc.count = 200; // zoom (slot geometry)
  }, "count");
  fullRepaintAfter(({ sc }) => {
    sc.digits = 5; // axis label precision
  }, "digits");
  fullRepaintAfter(({ sc, geom }) => {
    sc.kind = "line"; // presentation kind
    geom.priceH = 200; // price band height
  }, "kind + priceH");
  fullRepaintAfter(({ geom }) => {
    geom.bw = 4.2; // slot width (zoom geometry)
  }, "bw");
  fullRepaintAfter(({ sc, geom }) => {
    geom.y = (p) => 10 + ((105 - p) / 5) * geom.priceH; // mid-easing price window
  }, "price scale");
  fullRepaintAfter(({ geom }) => {
    geom.w = 900; // canvas width
    geom.h = 500; // canvas height
  }, "canvas size");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { rectangles: [{ type: "FVG_BULLISH", price_low: 101, price_high: 103 }] };
  }, "new zone overlay");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { rectangles: [{ type: "FVG_BEARISH", price_low: 101, price_high: 103 }] };
  }, "overlay type change at equal count");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { bos_lines: [{ price: 104, label: "BOS" }] };
  }, "new BOS overlay");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { midlines: [{ price: 105, label: "50%" }] };
  }, "new midline overlay");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { liq_markers: [{ price: 108, type: "BUY_SIDE", time: sc.shown[0].time }] };
  }, "new liq marker overlay");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { order_lines: { entry_price: 100, sl_price: 98, tp_price: 106 } };
  }, "new order lines");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { order_lines: { entry: 100, stop_loss: 98, take_profit: 106 } }; // mirror keys
  }, "order lines (mirror keys)");
  fullRepaintAfter(({ sc }) => {
    sc.overlays = { order_lines: { entry_price: 101, sl_price: 98, tp_price: 106 } }; // moved entry
  }, "order lines (entry moved, same shape)");
});

test("cacheKeyFor: every invalidator changes the key, stability otherwise", () => {
  keyChangesAfter(({ sc }) => {
    sc.shown = sc.shown.slice(0, 40);
  }, "shown.length");
  keyChangesAfter(({ sc }) => {
    const bars = sc.shown.slice();
    bars[3] = { ...bars[3], low: (bars[3].low ?? 0) - 3 };
    sc.shown = bars;
  }, "in-place bar low");
  keyChangesAfter(({ sc }) => {
    sc.count = 240;
  }, "count");
  keyChangesAfter(({ sc }) => {
    sc.left = 3;
  }, "left");
  keyChangesAfter(({ sc }) => {
    sc.digits = 3;
  }, "digits");
  keyChangesAfter(({ sc }) => {
    sc.kind = "area";
  }, "kind");
  keyChangesAfter(({ geom }) => {
    geom.w = 800;
  }, "geom.w");
  keyChangesAfter(({ geom }) => {
    geom.h = 400;
  }, "geom.h");
  keyChangesAfter(({ geom }) => {
    geom.priceH = 180;
  }, "geom.priceH");
  keyChangesAfter(({ geom }) => {
    geom.volH = 70;
  }, "geom.volH");
  keyChangesAfter(({ geom }) => {
    geom.y = (p) => 10 + ((104 - p) / 6) * geom.priceH;
  }, "price scale via y()");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { rectangles: [{ type: "ORDER_BLOCK", price_low: 99, price_high: 102 }] };
  }, "rectangles");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { rectangles: [{ type: "ORDER_BLOCK", price_low: 99, price_high: 103 }] };
  }, "rectangles content change");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { bos_lines: [{ price: 103 }] };
  }, "bos_lines");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { midlines: [{ price: 104 }] };
  }, "midlines");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { liq_markers: [{ price: 107 }] };
  }, "liq_markers");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { order_lines: { entry_price: 1 } };
  }, "order_lines");
  keyChangesAfter(({ sc }) => {
    sc.overlays = { order_lines: { entry: 1 } }; // mirror key path
  }, "order_lines mirror key");

  // STABILITY: dynamic-only mutations must NOT churn the key (hover, quote,
  // cursor, volume of nothing, and the same bars at the same geometry).
  const sc = makeScene();
  const geom = makeGeom();
  const k0 = cacheKeyFor(sc, geom);
  sc.hoverIdx = 12;
  sc.hoverY = 150;
  sc.liveBid = 104.5;
  sc.cursorIso = "2026-09-23T10:30:00";
  assert.equal(cacheKeyFor(sc, geom), k0, "dynamic state must not invalidate the static layer");
  const k1 = cacheKeyFor(sc, geom);
  assert.equal(cacheKeyFor(sc, { ...geom }), k1, "identical inputs keep the key");
});

// ---------------------------------------------------------------------------
// 3. Stage parity — the layer may never alter what the operator sees.
// ---------------------------------------------------------------------------

test("staticPriceTicks is byte-for-byte the real niceTicks module", async () => {
  const { niceTicks } = await import("../../frontend/src/components/viz/geometry.ts");
  const cases = [
    [100, 110, 4],
    [1.0842, 1.0931, 5],
    [0, 1, 5],
    [-5, 5, 4],
    [1000, 1000.0001, 4],
    [NaN, 5, 4],
  ];
  for (const [a, b, n] of cases) {
    assert.deepEqual(staticPriceTicks(a, b, n), niceTicks(a, b, n), `niceTicks(${a},${b},${n}) parity`);
  }
});

test("paintStaticVolume is byte-for-byte the real volumeOverlay module", async () => {
  const { paintVolume } = await import("../../frontend/src/pages/Dashboard/chart/volumeOverlay.ts");
  const volumes = [undefined, 40, 70, 96, 150];
  for (const volH of volumes) {
    const sc = makeScene();
    const geom = makeGeom(640, 320, { volH });
    const mine = makeCtx();
    const real = makeCtx();
    paintStaticVolume(mine, sc, geom, PAL, "mono");
    paintVolume(real, sc, geom, PAL, "mono");
    assert.deepEqual(mine.calls, real.calls, `paintVolume parity at volH=${volH}`);
  }
  // Honest gaps: a null tick_volume leaves the slot empty everywhere.
  const sc = makeScene();
  sc.shown[3].tick_volume = null;
  sc.shown[7].volume = null;
  const geom = makeGeom();
  const mine = makeCtx();
  const real = makeCtx();
  paintStaticVolume(mine, sc, geom, PAL, "mono");
  paintVolume(real, sc, geom, PAL, "mono");
  assert.deepEqual(mine.calls, real.calls, "paintVolume parity with gaps");
});

test("paintStaticSeries is byte-for-byte the real seriesRender module", async () => {
  const { paintSeries } = await import("../../frontend/src/pages/Dashboard/chart/renderers/seriesRender.ts");
  for (const kind of ["line", "area", "hollow"]) {
    const sc = makeScene({ kind });
    const geom = makeGeom();
    const mine = makeCtx();
    const real = makeCtx();
    paintStaticSeries(kind, mine, sc, geom, PAL);
    paintSeries(kind, real, sc, geom, PAL, "mono");
    assert.deepEqual(mine.calls, real.calls, `paintSeries parity for kind=${kind}`);
  }
  // Segments break at null closes (honest gaps, never interpolated).
  const sc = makeScene({ kind: "line" });
  sc.shown[4].close = null;
  const geom = makeGeom();
  const mine = makeCtx();
  const real = makeCtx();
  paintStaticSeries("line", mine, sc, geom, PAL);
  paintSeries("line", real, sc, geom, PAL, "mono");
  assert.deepEqual(mine.calls, real.calls, "paintSeries parity across gaps");
});

test("backdrop+grid parity vs the inline paintChart static block", async () => {
  const { niceTicks } = await import("../../frontend/src/components/viz/geometry.ts");
  const { formatPrice } = await import("../../frontend/src/lib/format.ts");
  // Re-declares paintChart's pre-clip static half inline (its only static
  // half not covered by the module seam painters above).
  const PAD_TOP = 10;
  const PAD_BOTTOM = 22;
  const AXIS_W = 60;
  const PAD_LEFT = 6;
  function inlineBackdropGrid(ctx, sc, geom, pal, mono) {
    const { w, h, priceH, plotW, y } = geom;
    const lo = 100;
    const hi = 110;
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, w, h);
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, "rgba(65,166,242,0.06)");
    grad.addColorStop(0.45, "rgba(65,166,242,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = pal.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = pal.axisText;
    ctx.font = `9px ${mono}`;
    ctx.textAlign = "left";
    for (const tv of niceTicks(lo, hi, 5)) {
      const gy = Math.round(y(tv)) + 0.5;
      if (gy < PAD_TOP || gy > PAD_TOP + priceH) continue;
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w - AXIS_W, gy);
      ctx.stroke();
      ctx.fillText(formatPrice(tv, sc.digits), w - AXIS_W + 6, gy + 3);
    }
    const vstep = Math.max(60, geom.bw * 30);
    for (let gx = PAD_LEFT + plotW; gx > 0; gx -= vstep) {
      ctx.beginPath();
      ctx.moveTo(Math.round(gx) + 0.5, 0);
      ctx.lineTo(Math.round(gx) + 0.5, h - PAD_BOTTOM);
      ctx.stroke();
    }
  }
  for (const digits of [2, 5]) {
    const sc = makeScene({ digits });
    const geom = makeGeom();
    const mine = makeCtx();
    const real = makeCtx();
    paintStaticBackdropGrid(mine, sc, geom, PAL, "mono");
    inlineBackdropGrid(real, sc, geom, PAL, "mono");
    assert.deepEqual(mine.calls, real.calls, `backdrop+grid parity at digits=${digits}`);
  }
});

test("paintStaticStages = backdrop+grid then clipped overlays/series/volume", () => {
  const sc = makeScene({ kind: "line" });
  const geom = makeGeom();
  const mine = makeCtx();
  paintStaticStages(mine, sc, geom, PAL, "mono");
  const seq = mine.calls.map((c) => c[0]);
  const bg = seq.indexOf("clip");
  assert.ok(bg > 0, "the clip opens the plot-scoped stages");
  assert.equal(seq[seq.length - 1], "restore", "the clip closes with a restore");
  // The stage ORDER the operator sees must not change: backdrop first…
  const firstFillRect = seq.indexOf("fillRect");
  assert.ok(firstFillRect >= 0 && firstFillRect < bg, "backdrop precedes the clip");
  // …then grid strokes before the clip, and series/volume strokes INSIDE it.
  assert.ok(seq.indexOf("stroke") < bg, "grid precedes the clip");
  assert.ok(seq.slice(bg).includes("stroke"), "series work happens inside the clip");
  assert.ok(seq.slice(bg).includes("fillRect"), "volume/candles work happens inside the clip");
});

// ---------------------------------------------------------------------------
// 4. Compositing: a reused layer reproduces the repainted pixels.
// ---------------------------------------------------------------------------

test("the layer is opaque — reuse composites identically to a repaint", () => {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  // Cold: static stages run into the layer; main ctx gets ONE blit.
  const cold = makeCtx();
  paintStaticLayers(cold, sc, geom, PAL, "ui-monospace", cache);
  const coldBlits = cold.calls.filter((c) => c[0] === "drawImage");
  assert.equal(coldBlits.length, 1, "cold frame blits the freshly painted layer");
  assert.deepEqual(coldBlits[0].slice(1, 5), [cache.canvas, 0, 0, geom.w], "blit covers the frame");

  // Warm: the same layer, same geometry → the same single blit.
  const warm = makeCtx();
  paintStaticLayers(warm, sc, geom, PAL, "ui-monospace", cache);
  const warmBlits = warm.calls.filter((c) => c[0] === "drawImage");
  assert.equal(warmBlits.length, 1);
  assert.deepEqual(warmBlits[0], coldBlits[0], "identical layer + identical target geometry");

  // A repaint is a fresh layer: the blit target geometry is unchanged, so the
  // operator sees the same region filled with the new static frame.
  const sc2 = makeScene({ bars: makeBars(60, 9) });
  const repaint = makeCtx();
  paintStaticLayers(repaint, sc2, sc2.kind ? geom : geom, PAL, "ui-monospace", cache);
  const repBlits = repaint.calls.filter((c) => c[0] === "drawImage");
  assert.equal(repBlits.length, 1);
  assert.deepEqual(repBlits[0].slice(1), [cache.canvas, 0, 0, geom.w, geom.h]);
});

test("backing store follows the caller transform (dpr) and resizes", () => {
  const sc = makeScene();
  const geom = makeGeom(640, 320);
  const cache = makeCache();
  paintStaticLayers(makeCtx(), sc, geom, PAL, "m", cache);
  assert.equal(cache.canvas.width, 640, "layer tracks the caller's backing store");
  assert.equal(cache.canvas.height, 320);

  // A dpr=2 caller (paintChart under setTransform(2,…)) doubles the store.
  const cache2 = makeCache();
  paintStaticLayers(makeCtx(2), sc, geom, PAL, "m", cache2);
  assert.equal(cache2.canvas.width, 1280, "dpr=2 backing store (caller transform)");
  assert.equal(cache2.canvas.height, 640);

  // A size change invalidates AND resizes the layer.
  const g2 = makeGeom(800, 400);
  const main = makeCtx();
  const ctxsBefore = cache.canvas.contexts.length;
  const ret = paintStaticLayers(main, sc, g2, PAL, "m", cache);
  assert.equal(ret, true, "canvas size change forces a repaint");
  assert.equal(cache.canvas.width, 800);
  assert.equal(cache.canvas.height, 400);
  const off = cache.canvas.contexts.slice(ctxsBefore);
  assert.equal(off[0].calls.filter((c) => c[0] === "setTransform").length, 1);
});

test("a palette or font switch repaints the layer (no stale colours)", () => {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  paintStaticLayers(makeCtx(), sc, geom, PAL, "ui-monospace", cache);
  const before = cache.key;
  const main = makeCtx();
  const ret = paintStaticLayers(main, sc, geom, { ...PAL, bg: "#111111" }, "ui-monospace", cache);
  assert.equal(ret, true, "palette change must repaint");
  assert.notEqual(cache.key, before);
  const main2 = makeCtx();
  const ret2 = paintStaticLayers(main2, sc, geom, PAL, "other-font", cache);
  assert.equal(ret2, true, "font change must repaint");
});

test("dynamic state never repaints the static layer", () => {
  const sc = makeScene();
  const geom = makeGeom();
  const cache = makeCache();
  paintStaticLayers(makeCtx(), sc, geom, PAL, "m", cache);
  const before = cache.key;
  const main = makeCtx();
  sc.hoverIdx = 5;
  sc.hoverY = 42;
  sc.liveBid = 104;
  sc.cursorIso = sc.shown[10].time;
  const ret = paintStaticLayers(main, sc, geom, PAL, "m", cache);
  assert.equal(ret, false, "crosshair/quote/cursor are dynamic — layer stays warm");
  assert.equal(cache.key, before);
});
