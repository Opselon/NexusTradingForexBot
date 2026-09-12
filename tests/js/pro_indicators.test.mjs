/**
 * PRO indicator engine — math regression suite (lane UI-1/5).
 *
 * Run:  node --test tests/js/pro_indicators.test.mjs      (from repo root)
 *
 * Imports the REAL frontend module directly: Node >= 22.18 strips the TS types
 * (verified on the Node running this file), and the only non-value import in
 * indicatorMath.ts is `import type { Bar, EngineSnapshot }`, which disappears
 * before resolution — so the `@/` path alias never has to resolve here.
 *
 * Every expected number below is hand-derived from the spec
 * (agents/xauusd_indicator_math.md §A.1 / §B), the Python authority
 * (src/nexus_scalp/indicators/calculators.py,
 *  src/nexus_scalp/features/scalp_features.py atr_m1,
 *  src/nexus_scalp/accounting/aggregation.py compute_drawdown), or plain
 * arithmetic written out in the comment. Truth invariants (gaps stay gaps,
 * no zero-fill, not-enough-data is null) are pinned as hard assertions.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  RSI_OVERBOUGHT,
  RSI_OVERSOLD,
  UNKNOWN_WORD,
  areaPath,
  atr,
  atrWilder,
  closes,
  countFinite,
  ema,
  finiteRuns,
  highs,
  isFiniteNumber,
  lastFinite,
  lows,
  pointsAttr,
  rollingMaxDrawdown,
  rsi,
  rsiLast,
  seriesDomain,
  seriesGeometry,
  sma,
  spreadStats,
  toSeries,
  trueRanges,
} from "../../frontend/src/lib/indicatorMath.ts";

const near = (actual, expected, tol = 1e-9, msg = "") =>
  assert.ok(Math.abs(actual - expected) <= tol, `${msg} expected ${expected}, got ${actual}`);

// ---------------------------------------------------------------------------
// SMA — spec §B: mean of the last n closes; calculators.py returns None for a
// short window, so every short prefix here is null.
// ---------------------------------------------------------------------------

test("sma: index-aligned mean of the last n points", () => {
  const out = sma([1, 2, 3, 4, 5], 3);
  assert.deepEqual(out, [null, null, 2, 3, 4]);
  assert.equal(out[4], (3 + 4 + 5) / 3);
});

test("sma: hand-checked XAUUSD-style prices", () => {
  const out = sma([3350.5, 3351.5, 3352.5, 3353.5], 2);
  assert.deepEqual(out, [null, 3351, 3352, 3353]);
});

test("sma: a gap breaks the window — never skipped over", () => {
  const out = sma([1, 2, null, 4, 5], 2);
  // window ending at index 3 would need 2 and 4 (not adjacent): null.
  assert.deepEqual(out, [null, 1.5, null, null, 4.5]);
});

test("sma: short/invalid input and invalid period yield all-null series", () => {
  assert.deepEqual(sma([1, 2], 5), [null, null]);
  assert.deepEqual(sma([], 3), []);
  assert.equal(sma([1, 2, 3], 0).filter((v) => v !== null).length, 0);
  assert.equal(sma([1, 2, 3], 2.5).filter((v) => v !== null).length, 0);
});

test("sma: NaN / Infinity / undefined inputs are gaps, never zeros", () => {
  const out = sma([1, NaN, Infinity, undefined, null, 6, 7], 2);
  assert.deepEqual(out, [null, null, null, null, null, null, 6.5]);
});

// ---------------------------------------------------------------------------
// EMA — spec §B: k = 2/(n+1), seed = SMA of the first n closes.
// ---------------------------------------------------------------------------

test("ema: seed is the SMA of the first n, then k = 2/(n+1) recursion", () => {
  const series = [1, 2, 3, 4, 5, 6];
  const out = ema(series, 3);
  assert.deepEqual(out.slice(0, 2), [null, null]); // not enough data yet
  const k = 2 / (3 + 1); // 0.5
  const seed = (1 + 2 + 3) / 3; // 2
  assert.equal(out[2], seed); // 2
  assert.equal(out[3], 4 * k + seed * (1 - k)); // 3
  assert.equal(out[4], 5 * k + out[3] * (1 - k)); // 4
  assert.equal(out[5], 6 * k + out[4] * (1 - k)); // 5
});

test("ema: n = 1 collapses to the identity series", () => {
  assert.deepEqual(ema([5, 7, null, 9], 1), [5, 7, null, 9]);
});

test("ema: parity with calculators.py _ema over a full run", () => {
  // calculators.py: ema = mean(values[:period]); for p in values[period:]:
  //                 ema = p*k + ema*(1-k).  Same recursion, same seed.
  const prices = [10, 11, 10.5, 12, 11.5, 13, 12.5];
  const k = 2 / (3 + 1);
  let ref = (10 + 11 + 10.5) / 3;
  for (const p of prices.slice(3)) ref = p * k + ref * (1 - k);
  near(lastFinite(ema(prices, 3)), ref, 1e-12, "final EMA(3) must equal the Python walk");
});

test("ema: gap restarts the seed — no silent bridging", () => {
  const out = ema([1, 2, 3, null, 5, 6, 7], 3);
  assert.equal(out[2], 2); // first run seeded at index 2
  assert.equal(out[3], null); // the gap itself is null
  assert.equal(out[4], null); // single point after the gap: not enough data
  assert.equal(out[5], null);
  assert.equal(out[6], (5 + 6 + 7) / 3); // new seed needs 3 consecutive again
});

// ---------------------------------------------------------------------------
// RSI (14) — spec §A.1 Wilder.
// ---------------------------------------------------------------------------

test("RSI of a strictly rising series is 100 (comfortably > 70)", () => {
  const rising = Array.from({ length: 40 }, (_, i) => 3000 + i);
  assert.ok(rsiLast(rising) > 70, "rising series must read above the overbought line");
  assert.equal(rsiLast(rising), 100); // all gains, zero losses
});

test("RSI of a strictly falling series is 0", () => {
  const falling = Array.from({ length: 30 }, (_, i) => 3000 - i);
  assert.equal(rsiLast(falling), 0);
});

test("RSI of a flat series is UNKNOWN, not 100 and not 0", () => {
  // 0/0 in the spec formula carries no strength information; the legacy
  // Python returns 100.0 here — the truth rule of this engine overrides it.
  assert.equal(rsiLast(Array.from({ length: 20 }, () => 3000)), null);
  assert.equal(rsi(Array.from({ length: 20 }, () => 3000))[14], null);
});

test("RSI(14) hand-checked on the classic 1..14+7,7 alternating series", () => {
  // 28 closes: 14 rises (+1) then 14 falls (−1).
  const up = Array.from({ length: 15 }, (_, i) => i); // 0..14
  const down = Array.from({ length: 14 }, (_, i) => 14 - (i + 1)); // 13..0
  const prices = [...up, ...down];
  assert.equal(prices.length, 29);
  // seed: avgGain = 1, avgLoss = 0 -> 100 at index 14
  const series = rsi(prices, 14);
  assert.equal(series.length, 29);
  assert.deepEqual(series.slice(0, 14), new Array(14).fill(null)); // §A.1: null for i < period
  assert.equal(series[14], 100);
  // bar 15 (first loss): avgGain = 13/14, avgLoss = 1/14 -> RS = 13
  near(series[15], 100 - 100 / (1 + 13), 1e-12, "first Wilder step");
  // bar 16: avgGain = (13/14)*13/14, avgLoss = (1/14)*13/14 + 1/14 -> RS = 169/27
  const g16 = (13 / 14) * (13 / 14);
  const l16 = (1 / 14) * (13 / 14) + 1 / 14;
  near(series[16], 100 - 100 / (1 + g16 / l16), 1e-12, "second Wilder step");
  // monotone decline while the loss streak continues, and never leaves 0..100
  for (let i = 14; i < series.length; i += 1) {
    assert.ok(series[i] >= 0 && series[i] <= 100, `RSI out of range at ${i}: ${series[i]}`);
  }
  assert.ok(series[28] < 50, "14 straight losses must drag RSI below the mid line");
});

test("RSI needs period+1 points: 14 closes give no value (parity with calculators.py:76)", () => {
  const series = rsi(Array.from({ length: 14 }, (_, i) => i), 14);
  assert.equal(countFinite(series), 0);
  assert.equal(rsiLast(Array.from({ length: 14 }, (_, i) => i), 14), null);
});

test("RSI gap handling: window breaks, restarts, never bridges", () => {
  const rising = Array.from({ length: 30 }, (_, i) => 3000 + i);
  const holed = [...rising];
  holed[20] = null;
  const series = rsi(holed, 14);
  assert.equal(series[20], null);
  assert.equal(series[21], null); // only 1 diff after the gap: not enough data
  assert.equal(series.length, 30);
  assert.equal(countFinite(series.slice(14, 20)), 6); // 14..19 defined (20-point run)
  assert.equal(countFinite(series.slice(21)), 0); // 9-point tail run < period+1
  assert.equal(series[19], 100); // still inside the first (rising) run
});

test("RSI thresholds exported match the spec vote rule", () => {
  assert.equal(RSI_OVERBOUGHT, 70);
  assert.equal(RSI_OVERSOLD, 30);
});

// ---------------------------------------------------------------------------
// ATR — parity with scalp_features.py atr_m1: TR over the last 14 bars with
// PRIOR-CLOSE reference, arithmetic mean (NOT Wilder — both provided).
// ---------------------------------------------------------------------------

function barsFrom(closesArr, spread = 1) {
  return closesArr.map((c, i) => ({
    time: `2026-09-13T00:${String(i).padStart(2, "0")}Z`,
    open: c,
    high: c + spread,
    low: c - spread,
    close: c,
  }));
}

test("trueRanges: TR uses the prior close; index 0 is null", () => {
  // closes 100,110,105 -> highs +1, lows −1
  const tr = trueRanges(barsFrom([100, 110, 105], 1));
  assert.equal(tr[0], null); // no prior close
  // bar 1: max(111-109, |111-100|, |109-100|) = 11
  assert.equal(tr[1], 11);
  // bar 2: max(106-104, |106-110|, |104-110|) = 6
  assert.equal(tr[2], 6);
});

test("atr: last value = mean of the last 14 TRs (scalp_features.py flavour)", () => {
  // bars from closes 1..20, H = C+1, L = C-1, prior close = C-1:
  //  TR = max(H-L, |H-Cprev|, |L-Cprev|) = max(2, 2, 0) = 2 on every bar that
  //  has a prior close, so ATR(14) = mean of fourteen 2s = 2 exactly.
  const bars = barsFrom(Array.from({ length: 20 }, (_, i) => i + 1), 1);
  const series = atr(bars, 14);
  assert.deepEqual(series.slice(0, 14), new Array(14).fill(null)); // index 0 TR is null
  assert.equal(series[14], 2);
  assert.equal(series[19], 2);
  // and the engine-mean over a rising series: TR = max(hi-lo, |hi-cPrev|, |lo-cPrev|)
  const rise = barsFrom(Array.from({ length: 16 }, (_, i) => 3000 + 2 * i), 0.5);
  // per bar: hi = c+0.5, lo = c-0.5, prior close = c-2 -> TR = max(1, 2.5, 1.5) = 2.5
  assert.equal(lastFinite(atr(rise, 14)), 2.5);
});

test("atr: fewer than period+1 usable bars -> no value (never a partial mean)", () => {
  const short = barsFrom([1, 2, 3], 1);
  assert.equal(countFinite(atr(short, 14)), 0);
  assert.equal(lastFinite(atr(short, 14)), null);
});

test("atr: a bar missing H/L/C yields a TR gap and breaks the ATR window", () => {
  const bars = barsFrom(Array.from({ length: 20 }, (_, i) => i + 1), 1);
  bars[10] = { ...bars[10], high: null }; // broker sent a broken bar
  const series = atr(bars, 14);
  assert.equal(series[14], null); // window would have to bridge the gap
  assert.equal(series[15], null);
  assert.equal(lastFinite(series), null); // only 4 consecutive TRs at the tail
});

test("atrWilder: seeds with the TR mean, then (prev*13 + TR)/14", () => {
  const bars = barsFrom(Array.from({ length: 20 }, (_, i) => i + 1), 1);
  const series = atrWilder(bars, 14);
  assert.equal(series[14], 2); // seed == mean (all TRs identical)
  assert.equal(series[19], 2); // recursion keeps the constant
  // non-constant: closes 1..20 with per-bar range growing => verify one step
  const wob = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 30];
  const wbars = barsFrom(wob, 1);
  const w = atrWilder(wbars, 14);
  const trs = trueRanges(wbars);
  const seed = trs.slice(1, 15).reduce((a, b) => a + b, 0) / 14;
  assert.equal(w[14], seed);
  assert.equal(w[15], (seed * 13 + trs[15]) / 14);
});

test("atr and atrWilder agree on the constant-TR fixture and can disagree elsewhere", () => {
  const flat = atr(barsFrom(Array.from({ length: 20 }, (_, i) => i + 1), 1), 14);
  const flatW = atrWilder(barsFrom(Array.from({ length: 20 }, (_, i) => i + 1), 1), 14);
  assert.equal(lastFinite(flat), 2);
  assert.equal(lastFinite(flatW), 2);
  // 20 closes with a spike then a snap-back (…18, 100, 19): the 14-bar mean and
  // the Wilder recursion answer differently (13.5 vs ≈13.09) — proving the
  // flavour must be stated, not assumed.
  const spikedBars = barsFrom([...Array.from({ length: 18 }, (_, i) => i + 1), 100, 19], 1);
  const spiked = lastFinite(atr(spikedBars, 14));
  const spikedW = lastFinite(atrWilder(spikedBars, 14));
  assert.equal(spiked, 13.5); // mean of the last 14 TRs (2×13 bars, 81, 82)
  near(spikedW, 13.086734693877, 1e-9, "Wilder damps the spike");
  assert.notEqual(spiked, spikedW);
});

// ---------------------------------------------------------------------------
// Rolling max drawdown — parity with accounting/aggregation.py
// ---------------------------------------------------------------------------

test("rollingMaxDrawdown: peak-to-trough in percent of the RUNNING peak", () => {
  const dd = rollingMaxDrawdown([100, 120, 90, 95, 80]);
  near(dd.maxDrawdownPct, (120 - 80) / 120 * 100, 1e-12, "max dd"); // 33.333%
  assert.equal(dd.maxDrawdownAbs, 40);
  assert.equal(dd.maxDrawdownIndex, 4);
  near(dd.currentDrawdownPct, dd.maxDrawdownPct, 1e-12, "still underwater at the end");
  assert.equal(dd.missing, 0);
  assert.equal(dd.samples, 5);
});

test("rollingMaxDrawdown: recovering to a new peak zeroes the current dd", () => {
  const dd = rollingMaxDrawdown([100, 90, 95, 110]);
  near(dd.maxDrawdownPct, 10, 1e-12, "trough 90 against peak 100");
  assert.equal(dd.currentDrawdownPct, 0);
  assert.equal(dd.points[1].peak, 100);
  assert.equal(dd.points[3].peak, 110);
});

test("rollingMaxDrawdown: a single sample cannot express a drawdown (aggregation.py:260)", () => {
  const one = rollingMaxDrawdown([100]);
  assert.equal(one.maxDrawdownPct, null);
  assert.equal(one.currentDrawdownPct, null);
  assert.equal(one.points[0].drawdownPct, null);
  assert.equal(rollingMaxDrawdown([]).maxDrawdownPct, null);
  assert.equal(rollingMaxDrawdown([null, NaN]).samples, 0);
});

test("rollingMaxDrawdown: gaps carry no drop claim and never move the peak", () => {
  const dd = rollingMaxDrawdown([100, 120, null, 90]);
  assert.equal(dd.missing, 1);
  assert.equal(dd.samples, 3);
  assert.equal(dd.points[2].peak, 120);
  assert.equal(dd.points[2].drawdownPct, null);
  assert.equal(dd.points[2].equity, null);
  near(dd.maxDrawdownPct, 25, 1e-12, "the 90 after the gap still measures vs 120");
});

test("rollingMaxDrawdown: non-positive peaks make the percent meaningless — skipped", () => {
  // mirrors `if peak <= 0: continue` in intraperiod_drawdown
  const dd = rollingMaxDrawdown([0, -10, -5]);
  assert.equal(dd.maxDrawdownPct, null);
  assert.equal(dd.points[2].drawdownPct, null);
});

// ---------------------------------------------------------------------------
// Spread stats
// ---------------------------------------------------------------------------

test("spreadStats: hand-checked sample statistics", () => {
  const s = spreadStats([2, 4, 4, 4, 5, 5, 7, 9]);
  assert.equal(s.samples, 8);
  assert.equal(s.mean, 5);
  assert.equal(s.median, 4.5); // (4+5)/2 — even count averages the middle pair
  assert.equal(s.min, 2);
  assert.equal(s.max, 9);
  // nearest-rank p95: ceil(0.95*8) = 8 -> the 8th sorted value
  assert.equal(s.p95, 9);
  // sample stdev (n-1): squared deviations 9+1+1+1+0+0+4+16 = 32 -> sqrt(32/7)
  near(s.stdev, Math.sqrt(32 / 7), 1e-12, "stdev");
  assert.equal(s.complete, true);
  assert.equal(s.missing, 0);
});

test("spreadStats: missing points are counted, never zero-filled", () => {
  const s = spreadStats([3, null, NaN, 5]);
  assert.deepEqual([s.samples, s.missing, s.last, s.complete], [2, 2, 5, false]);
  near(s.mean, 4, 1e-12);
});

test("spreadStats: empty / all-missing series returns nulls, not zeros", () => {
  for (const bad of [[], [null, NaN, undefined]]) {
    const s = spreadStats(bad);
    assert.equal(s.samples, 0);
    assert.equal(s.mean, null);
    assert.equal(s.median, null);
    assert.equal(s.p95, null);
    assert.equal(s.stdev, null);
    assert.equal(s.last, null);
    assert.equal(s.complete, false);
  }
  assert.equal(spreadStats([7]).stdev, null); // 1 sample: no dispersion claim
});

// ---------------------------------------------------------------------------
// Bar/series plumbing + geometry (what the SVG layer draws)
// ---------------------------------------------------------------------------

test("closes/highs/lows mirror EngineSnapshot.bars with gaps preserved", () => {
  const bars = [
    { time: "a", open: 1, high: 2, low: 0.5, close: 1.5 },
    { time: "b", open: null, high: null, low: null, close: null },
    { time: "c", open: 3, high: 4, low: 2.5, close: 3.5 },
  ];
  assert.deepEqual(closes(bars), [1.5, null, 3.5]);
  assert.deepEqual(highs(bars), [2, null, 4]);
  assert.deepEqual(lows(bars), [0.5, null, 2.5]);
  assert.deepEqual(closes(undefined), []);
});

test("toSeries / countFinite / lastFinite / finiteRuns / seriesDomain", () => {
  const s = toSeries([1, "x", NaN, 4, Infinity, null]);
  assert.deepEqual(s, [1, null, null, 4, null, null]);
  assert.equal(countFinite(s), 2);
  assert.equal(lastFinite(s), 4);
  assert.equal(lastFinite([]), null);
  assert.deepEqual(finiteRuns([1, 2, null, 4]), [[0, 2], [3, 4]]);
  assert.deepEqual(finiteRuns([1, null, 3], 2), []);
  assert.deepEqual(seriesDomain([3, null, 1, 2]), { min: 1, max: 3 });
  assert.deepEqual(seriesDomain([]), { min: null, max: null });
  assert.equal(isFiniteNumber("5"), false);
});

test("seriesGeometry: fixed viewBox, one polyline per run, deterministic coords", () => {
  const geo = seriesGeometry([1, 2, null, 4], { width: 100, height: 30, pad: 2 });
  // domain 1..4 -> y(1) = 28 (bottom), y(4) = 2 (top); x(i) = 2 + i*96/3
  assert.equal(geo.segments.length, 1); // the 2-point run is the only polyline
  assert.equal(geo.segments[0].length, 2);
  assert.equal(geo.dots.length, 1); // the lone 4 after the gap renders as a dot
  assert.equal(geo.segments[0][0].x, 2);
  assert.equal(geo.segments[0][1].x, 34);
  assert.equal(geo.segments[0][0].y, 28);
  near(geo.segments[0][1].y, 2 + ((4 - 2) * 26) / 3, 1e-3, "y scaling");
  assert.equal(geo.missing, 1);
  assert.equal(geo.total, 4);
  // deterministic: same input -> same numbers (JSON-stable)
  const again = seriesGeometry([1, 2, null, 4], { width: 100, height: 30, pad: 2 });
  assert.equal(JSON.stringify(again.segments), JSON.stringify(geo.segments));
  assert.equal(JSON.stringify(again.dots), JSON.stringify(geo.dots));
});

test("seriesGeometry: empty / all-null / flat series behave for the renderer", () => {
  assert.deepEqual(seriesGeometry([], { width: 100, height: 30 }).segments, []);
  assert.equal(seriesGeometry([], { width: 100, height: 30 }).min, null);
  const nulls = seriesGeometry([null, null], { width: 100, height: 30 });
  assert.equal(nulls.min, null);
  assert.equal(nulls.missing, 2);
  const flat = seriesGeometry([5, 5, 5], { width: 100, height: 30 });
  assert.equal(flat.segments.length, 1);
  assert.equal(flat.segments[0][0].y, 15); // max == min -> mid line, not a spike
});

test("pointsAttr / areaPath emit stable SVG strings", () => {
  assert.equal(pointsAttr([{ x: 1, y: 2 }, { x: 3, y: 4 }]), "1,2 3,4");
  assert.equal(
    areaPath([{ x: 0, y: 0 }, { x: 10, y: 0 }], [{ x: 0, y: 5 }, { x: 10, y: 5 }]),
    "M0 0 L10 0 L10 5 L0 5 Z",
  );
  assert.equal(areaPath([], [{ x: 0, y: 0 }]), "");
});

// ---------------------------------------------------------------------------
// Source-level truth invariants (no I/O, no zero-fill vocabulary)
// ---------------------------------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));

test("SAFETY: indicatorMath.ts performs no I/O and declares no fetch/state", async () => {
  const src = await readFile(resolve(here, "..", "..", "frontend", "src", "lib", "indicatorMath.ts"), "utf8");
  for (const banned of ["fetch(", "XMLHttpRequest", "require(", "Date.now(", "Math.random(", "localStorage"]) {
    assert.ok(!src.includes(banned), `indicatorMath must not contain ${banned}`);
  }
  // erasable-syntax check for Node type stripping: no enums / namespaces /
  // parameter properties.
  assert.doesNotMatch(src, /^\s*(export\s+)?(enum|namespace|declare)\s/m);
  assert.doesNotMatch(src, /constructor\s*\([^)]*\b(public|private|protected|readonly)\b/);
});

test("SAFETY: the engine never zero-fills — the only invented constant is UNKNOWN", async () => {
  const src = await readFile(resolve(here, "..", "..", "frontend", "src", "lib", "indicatorMath.ts"), "utf8");
  assert.match(src, /export const UNKNOWN_WORD = "UNKNOWN"/);
  // a null input maps to a null output: no `?? 0` defaulting anywhere.
  assert.doesNotMatch(src, /\?\?\s*0\b/);
  assert.doesNotMatch(src, /\bNaN\s*\?\?/);
  // direct behavioural proof: every series function nulls the gap index, even
  // where the full series has a real value there.
  const s16 = Array.from({ length: 16 }, (_, i) => i + 1);
  const holed = [...s16];
  holed[10] = null;
  const cases = [["sma", sma(s16, 3), sma(holed, 3)], ["ema", ema(s16, 3), ema(holed, 3)], ["rsi", rsi(s16, 3), rsi(holed, 3)]];
  for (const [name, base, out] of cases) {
    assert.equal(out[10], null, `${name} must null out the gap index`);
    assert.notEqual(out[10], 0, `${name} must never zero-fill the gap index`);
    assert.equal(typeof base[10], "number", `${name} baseline must have a value there`);
  }
});

test("SAFETY: Indicators.tsx is props-only (no fetch, no react-query, no deps)", async () => {
  const src = await readFile(resolve(here, "..", "..", "frontend", "src", "components", "pro", "Indicators.tsx"), "utf8");
  for (const banned of ["fetch(", "useQuery", "useMutation", "axios", "EventSource", "WebSocket", "Math.random(", "setInterval", "document.", "localStorage"]) {
    assert.ok(!src.includes(banned), `Indicators.tsx must not contain ${banned}`);
  }
  // every visual entry point exposes an UNKNOWN state
  for (const comp of ["SparkLine", "PriceBand", "SpreadStrip", "RsiStrip", "AtrStrip"]) {
    const body = src.slice(src.indexOf(`export function ${comp}`));
    assert.ok(body.includes("IndicatorUnknown"), `${comp} must render IndicatorUnknown when the series is empty`);
  }
  assert.ok(src.includes('import "./pro-indicators.css"'), "colocated CSS import must be present");
});

test("the UNKNOWN word is literally UNKNOWN", () => {
  assert.equal(UNKNOWN_WORD, "UNKNOWN");
});
