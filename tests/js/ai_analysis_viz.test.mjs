/**
 * AI-Analysis chart math — node runnable, no bundler:
 *   node tests/js/ai_analysis_viz.test.mjs
 * (node >= 24 strips .ts type annotations; vizMath.ts has ZERO imports, so the
 * REAL production code is exercised — same pattern as pro_risk_viz.test.mjs.)
 *
 * Covers the honesty contract: explicit action-family classification, KPI
 * arithmetic over backend counts, donut geometry conservation, R:R null-ness,
 * timeline sort/drop/gap rules.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  actionFamily,
  familyWord,
  actionKpi,
  topEntry,
  countRows,
  familyTotals,
  donutSegments,
  rrRatio,
  timelineSeries,
} from "../../frontend/src/features/ai-analysis/ui/vizMath.ts";

test("actionFamily: explicit prefix/exact classification, never guessed", () => {
  assert.equal(actionFamily("BUY"), "buy");
  assert.equal(actionFamily(" buy_limit "), "buy");
  assert.equal(actionFamily("SELL_MARKET"), "sell");
  assert.equal(actionFamily("SELL_LIMIT"), "sell");
  assert.equal(actionFamily("NO_TRADE"), "none");
  assert.equal(actionFamily("no_trade"), "none");
  assert.equal(actionFamily("CLOSE_POSITION"), "close");
  // Unrecognised labels stay unknown — HOLD is NOT silently a family.
  assert.equal(actionFamily("HOLD"), "unknown");
  assert.equal(actionFamily(""), "unknown");
  assert.equal(actionFamily(null), "unknown");
  assert.equal(actionFamily(undefined), "unknown");
});

test("familyWord: caption words", () => {
  assert.equal(familyWord("none"), "no trade");
  assert.equal(familyWord("unknown"), "other");
  assert.equal(familyWord("buy"), "buy");
});

test("actionKpi: arithmetic over backend by_action counts", () => {
  const k = actionKpi({ BUY: 2, SELL: 1, NO_TRADE: 7 });
  assert.equal(k.total, 10);
  assert.equal(k.noTrade, 7);
  assert.equal(k.trade, 3);
  assert.ok(Math.abs(k.tradeShare - 0.3) < 1e-12);
  assert.ok(Math.abs(k.noTradeShare - 0.7) < 1e-12);
  // Non-finite / non-positive counts are skipped, not summed.
  const junk = actionKpi({ BUY: Number.NaN, SELL: -5, HOLD: 0 });
  assert.equal(junk.total, 0);
  assert.equal(junk.tradeShare, null);
  assert.equal(junk.noTradeShare, null);
  const empty = actionKpi(undefined);
  assert.deepEqual(empty, { total: 0, noTrade: 0, trade: 0, tradeShare: null, noTradeShare: null });
  // CLOSE_POSITION counts as a trade action (non-NO_TRADE), incl. in `trade`.
  const withClose = actionKpi({ CLOSE_POSITION: 4, NO_TRADE: 6 });
  assert.equal(withClose.trade, 4);
  assert.equal(withClose.noTrade, 6);
});

test("topEntry: dominant stage; ties keep first insertion", () => {
  assert.deepEqual(topEntry({ CONFIDENCE_GATE: 5, FINAL_DECISION: 9, X: 1 }), { label: "FINAL_DECISION", count: 9 });
  assert.deepEqual(topEntry({ A: 9, B: 9 }), { label: "A", count: 9 });
  assert.equal(topEntry(undefined), null);
  assert.equal(topEntry({ A: 0, B: -3 }), null);
});

test("countRows: sorted desc with share-of-map-total percentages", () => {
  const rows = countRows({ x: 1, y: 3 });
  assert.deepEqual(rows.map((r) => r.label), ["y", "x"]);
  assert.equal(rows[0].count, 3);
  assert.ok(Math.abs(rows[0].pct - 75) < 1e-9);
  assert.ok(Math.abs(rows[1].pct - 25) < 1e-9);
  // Labels with equal counts fall back to locale order (stable output).
  const tie = countRows({ b: 2, a: 2 });
  assert.deepEqual(tie.map((r) => r.label), ["a", "b"]);
  // Zero-total map: rows kept with pct 0 (no divide-by-zero, no lie).
  const zero = countRows({ x: 0 });
  assert.deepEqual(zero, [{ label: "x", count: 0, pct: 0 }]);
  assert.deepEqual(countRows(undefined), []);
});

test("familyTotals: collapse by_action into families, sorted desc", () => {
  const fams = familyTotals({ BUY: 2, BUY_LIMIT: 3, SELL: 1, NO_TRADE: 7, HOLD: 4 });
  assert.deepEqual(
    fams.map((f) => [f.family, f.count]),
    [
      ["none", 7],
      ["buy", 5],
      ["unknown", 4],
      ["sell", 1],
    ],
  );
  assert.deepEqual(familyTotals(undefined), []);
});

test("donutSegments: arc lengths conserve the full circumference", () => {
  const radius = 10;
  const circ = 2 * Math.PI * radius;
  const { segs, total } = donutSegments(
    [
      { family: "buy", count: 3 },
      { family: "sell", count: 1 },
    ],
    radius,
  );
  assert.equal(total, 4);
  assert.equal(segs.length, 2);
  const sum = segs.reduce((s, x) => s + x.len, 0);
  assert.ok(Math.abs(sum - circ) < 1e-9, `sum ${sum} must equal ${circ}`);
  assert.equal(segs[0].offset, 0);
  assert.ok(Math.abs(segs[1].offset + segs[0].len) < 1e-9);
  // Degenerate inputs yield NO segments (empty donut, never a fake 100%).
  assert.deepEqual(donutSegments([], 44), { segs: [], total: 0 });
  assert.deepEqual(donutSegments([{ family: "buy", count: 0 }], 44), { segs: [], total: 0 });
  assert.deepEqual(donutSegments([{ family: "buy", count: 5 }], 0), { segs: [], total: 0 });
});

test("rrRatio: direction-agnostic arithmetic, null when incomplete", () => {
  assert.equal(rrRatio(100, 90, 120), 2); // reward 20 / risk 10
  assert.equal(rrRatio(100, 110, 80), 2); // SELL-shaped mirror, same math
  assert.equal(rrRatio(null, 90, 120), null);
  assert.equal(rrRatio(100, null, 120), null);
  assert.equal(rrRatio(100, 90, null), null);
  assert.equal(rrRatio(100, 100, 120), null); // zero risk -> null, never Infinity
  assert.equal(rrRatio(Number.NaN, 90, 120), null);
  assert.equal(rrRatio(Infinity, 90, 120), null);
});

test("timelineSeries: sorts, drops bad timestamps, clamps, keeps gaps", () => {
  const series = timelineSeries([
    { generated_at: "2026-09-23T10:00:05Z", action: "NO_TRADE", conf01: 0.4 },
    { generated_at: "2026-09-23T10:00:00Z", action: "BUY", conf01: 1.5 }, // out-of-range -> clamp 1
    { generated_at: "not-a-date", action: "SELL", conf01: 0.9 }, // dropped + counted
    { generated_at: "2026-09-23T10:00:10Z", action: null, conf01: null }, // gap sample
    { generated_at: "2026-09-23T10:00:15Z", action: "HOLD", conf01: -0.2 }, // clamp 0
  ]);
  assert.equal(series.dropped, 1);
  assert.equal(series.points.length, 4);
  // oldest -> newest ordering (input was newest-first for the first two rows)
  const times = series.points.map((p) => p.t);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
  assert.equal(series.from, times[0]);
  assert.equal(series.to, times[times.length - 1]);
  assert.equal(series.points[0].v, 1); // clamped from 1.5
  assert.equal(series.points.find((p) => p.action === null).v, null); // gap stays null
  assert.equal(series.points[series.points.length - 1].v, 0); // clamped from -0.2
  assert.equal(series.points.find((p) => p.t === series.from).action, "BUY");

  const empty = timelineSeries([]);
  assert.deepEqual(empty, { points: [], dropped: 0, from: null, to: null });
  assert.deepEqual(timelineSeries(undefined), { points: [], dropped: 0, from: null, to: null });
});
