/**
 * Wave-3 Lane B — `niceTimeTicks` (chart/timeAxis.ts).
 *
 * WHY: the pre-scaffold painter drew 4 fixed labels off shown-bar
 * timestamps — colliding on dense TFs and duplicating text across dates.
 * These tests pin the replacement contract: TF-aware FORMAT (M1/M5/M10 ->
 * HH:MM, H1/H4 -> MM-DD HH:MM, D1/W1 -> MM-DD + " 'YY" on year change),
 * dense non-colliding placement (<= maxLabels, >= 58px apart, deduped by
 * label text, never under the price axis), visible-window-only ticks, and
 * honest degenerate-input behaviour. Geometry follows the documented seam
 * maths: slots = plotW/slotW, spacing = span/(slots-1), slot 0 = first
 * shown bar (painter x = PAD_LEFT + slot*slotW).
 *
 * Under node --test there is no DOM, so timeAxis falls back to its
 * canonical FALLBACK_PLOT_W (800) — fixtures size slotW against that.
 *
 * RUN: node --test tests/js/w3_timeaxis.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { niceTimeTicks } from "../../frontend/src/pages/Dashboard/chart/timeAxis.ts";

// ---- fixtures -------------------------------------------------------------
const PLOT_W = 800; // timeAxis.ts FALLBACK_PLOT_W (no DOM under node --test)
const COUNT = 180; // PriceChart default view.visible
const SLOT_W = PLOT_W / COUNT; // what paintChart passes as `bw`
const T0 = Date.UTC(2026, 8, 7, 10, 0, 0); // 2026-09-07T10:00:00Z
const iso = (ms) => new Date(ms).toISOString();

/** A full window (bars = slots) of `bars` bars `spacing` seconds apart. */
const windowOf = (bars, spacing, t0 = T0, slotW = SLOT_W) =>
  niceTimeTicks(iso(t0), iso(t0 + (bars - 1) * spacing * 1000), slotW);

const labels = (ticks) => ticks.map((t) => t.label);
const slots = (ticks) => ticks.map((t) => t.slot);
const HHMM = /^\d{2}:\d{2}$/;
const MD_HHMM = /^\d{2}-\d{2} \d{2}:\d{2}$/;
const MD = /^\d{2}-\d{2}$/;
const MD_OPT_YY = /^\d{2}-\d{2}( '\d{2})?$/; // day class, optional year suffix

/**
 * Invariants every production window must satisfy — this is the "dense /
 * non-colliding / never under the axis" contract in executable form.
 */
function assertDense(ticks, slotW, { count = COUNT, plotW = PLOT_W, max = 8 } = {}) {
  assert.ok(ticks.length >= 1, "window yields at least one label");
  assert.ok(ticks.length <= max, `<= ${max} labels (got ${ticks.length})`);
  assert.equal(new Set(labels(ticks)).size, ticks.length, "deduped by label text");
  let prevSlot = -1;
  let prevPx = -Infinity;
  for (const t of ticks) {
    assert.ok(t.slot > prevSlot, "slots strictly ascend (window-relative)");
    const px = t.slot * slotW;
    if (prevPx > -Infinity) {
      assert.ok(px - prevPx >= 58 - 1e-9, `labels >=58px apart (gap ${px - prevPx})`);
    }
    prevSlot = t.slot;
    prevPx = px;
  }
  assert.ok(ticks[0].slot >= 0, "no label left of the plot origin");
  assert.ok(prevPx <= plotW - 58 + 1e-9, "last label stays clear of the price axis");
  // dense: the axis reaches at least 40% into the visible slot range —
  // catches the dedupe-starvation failure mode (right side left blank).
  assert.ok(prevSlot >= 0.4 * (count - 1), `right side not starved (last slot ${prevSlot})`);
}

// ---- contract formats, one named TF per format bucket ---------------------

test("M1 window -> bare HH:MM labels on a 30-minute grid (contract format)", () => {
  const ticks = windowOf(COUNT, 60);
  assert.deepEqual(labels(ticks), [
    "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
  ]);
  for (const l of labels(ticks)) assert.match(l, HHMM);
  assertDense(ticks, SLOT_W);
});

test("M5 window -> bare HH:MM labels (contract format)", () => {
  const ticks = windowOf(COUNT, 300);
  // 2-hour grid; the 00:00 tick would cross the axis reserve and is dropped.
  assert.deepEqual(labels(ticks), [
    "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00",
  ]);
  for (const l of labels(ticks)) assert.match(l, HHMM);
  assertDense(ticks, SLOT_W);
});

test("M10 window <= 24h -> bare HH:MM labels (contract format)", () => {
  // 144 bars of M10 = 23.8h: full window, single day, no date needed.
  const w = 144;
  const sw = PLOT_W / w;
  const ticks = windowOf(w, 600, T0, sw);
  assert.deepEqual(labels(ticks), [
    "12:00", "15:00", "18:00", "21:00", "00:00", "03:00", "06:00",
  ]);
  for (const l of labels(ticks)) assert.match(l, HHMM);
  assertDense(ticks, sw, { count: w });
});

test("M10 window > 24h escalates to dated labels (density beats the format table; see module header)", () => {
  // 180 M10 bars = 29.8h: bare HH:MM has <= 8 distinct strings per 24h at
  // any nice step, so dedupe would starve the right side — dates carry the
  // uniqueness instead. This is the one deliberate widening of the table.
  const ticks = windowOf(COUNT, 600);
  assert.deepEqual(labels(ticks), [
    "09-07 12:00", "09-07 16:00", "09-07 20:00",
    "09-08 00:00", "09-08 04:00", "09-08 08:00", "09-08 12:00",
  ]);
  for (const l of labels(ticks)) assert.match(l, MD_HHMM);
  assertDense(ticks, SLOT_W);
});

test("H1 window -> MM-DD HH:MM labels on a daily grid (contract format)", () => {
  const ticks = windowOf(COUNT, 3600);
  assert.deepEqual(labels(ticks), [
    "09-08 00:00", "09-09 00:00", "09-10 00:00", "09-11 00:00",
    "09-12 00:00", "09-13 00:00", "09-14 00:00",
  ]);
  for (const l of labels(ticks)) assert.match(l, MD_HHMM);
  assertDense(ticks, SLOT_W);
});

test("H4 window -> MM-DD HH:MM labels on a 3-day grid (contract format)", () => {
  // 29.8-day H4 window: the density budget (~8 labels) lands on the
  // ladder's 3-day rung (the ladder has no 4-day rung; 3d is the densest
  // rung under the budget). Anchoring t0 ON that grid makes the
  // expectation exact: slot 0 = grid start, +18 H4 slots (3d) per label.
  const GRID = 3 * 86400 * 1000;
  const t0 = Math.ceil(Date.UTC(2026, 8, 7, 10, 0, 0) / GRID) * GRID;
  const ticks = windowOf(COUNT, 14400, t0);
  const md = (ms) => new Date(ms).toISOString().slice(5, 10);
  assert.deepEqual(
    labels(ticks),
    [0, 1, 2, 3, 4, 5, 6, 7].map((k) => `${md(t0 + k * GRID)} 00:00`),
  );
  for (const l of labels(ticks)) assert.match(l, MD_HHMM);
  assertDense(ticks, SLOT_W);
});

test("D1 window -> MM-DD labels on a monthly grid, no year suffix within one year (contract format)", () => {
  const t0 = Date.UTC(2026, 0, 5); // 2026-01-05, inside one calendar year
  const t1 = t0 + 252 * 86400 * 1000; // ~180 trading days incl. weekends
  const ticks = niceTimeTicks(iso(t0), iso(t1), SLOT_W);
  assert.deepEqual(labels(ticks), [
    "02-01", "03-01", "04-01", "05-01", "06-01", "07-01", "08-01",
  ]);
  for (const l of labels(ticks)) assert.match(l, MD);
  assertDense(ticks, SLOT_W);
});

test("W1 window across years -> MM-DD with 'YY suffix on year change, all unique (contract format)", () => {
  const t0 = Date.UTC(2025, 10, 3); // 2025-11-03 (a Monday)
  const t1 = t0 + 179 * 7 * 86400 * 1000; // 179 weekly bars, span exact
  const ticks = niceTimeTicks(iso(t0), iso(t1), SLOT_W);
  assert.deepEqual(labels(ticks), [
    "12-01",              // first year: plain
    "06-01 '26",
    "12-01 '26",
    "06-01 '27",
    "12-01 '27",
    "06-01 '28",
    "12-01 '28",
  ]);
  for (const l of labels(ticks)) assert.match(l, MD_OPT_YY);
  assertDense(ticks, SLOT_W);
});

// ---- cross-cutting invariants over the full chip set ----------------------

test("every TF chip's window satisfies density/axis/dedupe invariants", () => {
  const CHIPS = [
    ["M1", 60], ["M3", 180], ["M5", 300], ["M10", 600], ["M15", 900],
    ["M30", 1800], ["H1", 3600], ["H4", 14400], ["D1", 86400], ["W1", 604800],
  ];
  for (const [tf, spacing] of CHIPS) {
    const ticks = windowOf(COUNT, spacing);
    assertDense(ticks, SLOT_W); // throws with tf in the message on failure
  }
});

test("maxLabels caps the result", () => {
  // maxLabels is a density BUDGET, not a truncation: with 3 the step is
  // re-derived coarser (1h on this ~3h M1 window) so the few labels spread
  // across the whole axis instead of bunching at the left edge.
  const three = niceTimeTicks(iso(T0), iso(T0 + (COUNT - 1) * 60 * 1000), SLOT_W, 3);
  assert.ok(three.length <= 3, `<=3 labels (got ${three.length})`);
  assert.ok(three.length >= 1, "still yields labels");
  assert.deepEqual(slots(three), [0, 60, 120]); // T0, +1h, +2h on the 1h grid
  for (const t of three) assert.match(t.label, HHMM);

  // A bigger budget never yields fewer labels on the same window.
  const eight = windowOf(COUNT, 60);
  assert.ok(three.length <= eight.length, "smaller budget -> <= as many labels");
});

// ---- degenerate inputs -> empty set (painter draws nothing) ---------------

test("degenerate windows and slot widths -> []", () => {
  const ok = iso(T0);
  assert.deepEqual(niceTimeTicks(null, null, SLOT_W), []);
  assert.deepEqual(niceTimeTicks(null, ok, SLOT_W), []);
  assert.deepEqual(niceTimeTicks(ok, null, SLOT_W), []);
  assert.deepEqual(niceTimeTicks(ok, ok, SLOT_W), []); // zero-length window
  assert.deepEqual(niceTimeTicks(iso(T0 + 60000), ok, SLOT_W), []); // reversed
  assert.deepEqual(niceTimeTicks("not-a-date", ok, SLOT_W), []);
  assert.deepEqual(niceTimeTicks(ok, "not-a-date", SLOT_W), []);
  assert.deepEqual(niceTimeTicks(ok, iso(T0 + 3600000), 0), []);
  assert.deepEqual(niceTimeTicks(ok, iso(T0 + 3600000), -5), []);
  assert.deepEqual(niceTimeTicks(ok, iso(T0 + 3600000), Number.NaN), []);
  assert.deepEqual(niceTimeTicks(ok, iso(T0 + 3600000), SLOT_W, 0), []);
});

test("zone-less ISO input is read as UTC (labels never shift with viewer TZ)", () => {
  const ticks = niceTimeTicks(
    "2026-09-07T10:00:00",
    "2026-09-07T13:00:00",
    SLOT_W,
  );
  assert.deepEqual(labels(ticks), [
    "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
  ]);
});

// ---- plot width is measured from the mounted stage (browser path) ---------

test("plot width comes from the .mc-stage DOM width, not just the fallback", () => {
  const w = 36; // 36 bars x 40min = 23.3h — a minute-class window
  const sw = 1000 / w; // stage clientWidth 1066 - AXIS_TOTAL_PX 66 => plotW 1000
  const t0 = T0;
  const t1 = t0 + (w - 1) * 2400 * 1000;
  // Force the fallback FIRST (no DOM): count = round(800/sw) = 29,
  // spacing = span/28 = 3000s => H1-class => dated labels.
  assert.equal(typeof document, "undefined", "node has no DOM by default");
  const fallback = niceTimeTicks(iso(t0), iso(t1), sw);
  assert.ok(fallback.length >= 1, "fallback still labels the window");
  for (const l of labels(fallback)) assert.match(l, MD_HHMM, `fallback dated: ${l}`);
  // With the stage present: count = 36, spacing = 2400s => minute-class,
  // span <= 24h => bare HH:MM — proving clientWidth fed the geometry.
  globalThis.document = {
    querySelector: (sel) => {
      assert.equal(sel, ".mc-stage", "reads the painter's stage box");
      return { clientWidth: 1066 };
    },
  };
  try {
    const dom = niceTimeTicks(iso(t0), iso(t1), sw);
    assert.deepEqual(labels(dom), [
      "12:00", "15:00", "18:00", "21:00", "00:00", "03:00", "06:00",
    ]);
    for (const l of labels(dom)) assert.match(l, HHMM, `dom path bare: ${l}`);
    assertDense(dom, sw, { count: w, plotW: 1000 });
  } finally {
    delete globalThis.document;
  }
});
