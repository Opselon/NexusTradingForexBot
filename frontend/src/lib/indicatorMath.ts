/**
 * indicatorMath — the PRO console's indicator engine (pure, dependency-free).
 *
 * WHAT THIS IS
 *   A clean TypeScript re-implementation of the indicator math the operators
 *   already trust, so the alternative console draws the same numbers as the
 *   main dashboard and the engine itself:
 *     - agents/xauusd_indicator_math.md  §B (SMA/EMA definitions), §A.1 (RSI)
 *     - src/nexus_scalp/indicators/calculators.py  (_sma, _ema, rsi_series)
 *     - src/nexus_scalp/features/scalp_features.py (canonical ATR: TR over the
 *       last 14 bars, prior-close reference, arithmetic mean -> atr_m1)
 *     - src/nexus_scalp/accounting/aggregation.py  (compute_drawdown /
 *       intraperiod_drawdown: peak-to-trough, percent of the RUNNING peak)
 *   `Web/xauusd_indicator_math.js` named in the task brief does not exist in
 *   the repo (verified with find + `git log --all --diff-filter=D` across the
 *   clone); the files above are its authoritative equivalents, and
 *   tests/js/xauusd_indicator_math.test.js is the spec-pinning suite.
 *
 * TRUTH RULES (pinned by tests/js/pro_indicators.test.mjs)
 *   1. MISSING IS MISSING. A null / undefined / NaN / ±Infinity input point
 *      produces a null output point at the SAME index. Nothing is ever
 *      zero-filled, forward-filled, or interpolated. All functions are
 *      index-aligned with their input, so the caller can draw the gap.
 *   2. A gap BREAKS a window. SMA/EMA/RSI/ATR restart only over consecutive
 *      finite runs: they never bridge a hole by silently skipping it, because
 *      "average of the 14 values I happened to have" is a different number
 *      from "ATR(14)" and must not be presented as one.
 *   3. NOT ENOUGH DATA -> null, never a partial value. Legacy parity:
 *      SMA needs `period` points, EMA needs `period`, RSI needs `period + 1`
 *      (calculators.py:76), ATR needs one prior close plus `period` TRs.
 *   4. DEGENERATE RATIOS ARE UNKNOWN. avgLoss == 0 with no gains (a flat
 *      window) is 0/0: calculators.py answers 100.0, which reads as maximum
 *      strength for a market that did not move. Here it is null. Every other
 *      RSI case keeps the legacy formula exactly.
 *   5. TONE IS GEOMETRY, NOT A VERDICT. This module exports the spec's own
 *      thresholds (RSI 70/30) and honest statistics; it never emits words.
 *
 * ERASABLE TYPESCRIPT ONLY (no enums / namespaces / parameter properties):
 * this module is imported directly by a node:test file that relies on Node
 * >= 22.18 type stripping (verified on the Node in this workspace).
 */

import type { Bar, EngineSnapshot } from "@/types/domain";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Anything a series can hold: real numbers, plus every shape of "no data". */
export type NumericPoint = number | null | undefined;

/** An index-aligned indicator series. `null` = UNKNOWN at that index. */
export type Series = Array<number | null>;

/** The subset of `EngineSnapshot` this engine reads. Never more than that. */
export type IndicatorSnapshot = Pick<EngineSnapshot, "bars" | "bid" | "ask" | "spread" | "atr">;

/** Bar-shaped input; mirrors `types/domain.ts` `Bar` (fields may be null). */
export type IndicatorBar = Pick<Bar, "high" | "low" | "close"> & Partial<Bar>;

/** True only for a real, finite JS number (guards null/NaN/Infinity/strings). */
export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** The one word this layer is allowed to invent. */
export const UNKNOWN_WORD = "UNKNOWN";

/** Spec thresholds (agents/xauusd_indicator_math.md §A.1 vote rule). */
export const RSI_OVERBOUGHT = 70;
export const RSI_OVERSOLD = 30;

/** Legacy default periods used across the dashboard. */
export const DEFAULT_RSI_PERIOD = 14;
export const DEFAULT_ATR_PERIOD = 14;

// ---------------------------------------------------------------------------
// Series plumbing
// ---------------------------------------------------------------------------

/** Coerce any raw payload into an index-aligned series of number | null. */
export function toSeries(values: readonly NumericPoint[] | null | undefined): Series {
  if (!Array.isArray(values)) return [];
  return values.map((v) => (isFiniteNumber(v) ? v : null));
}

/** How many points carry a real number. */
export function countFinite(values: readonly NumericPoint[]): number {
  let n = 0;
  for (const v of values) if (isFiniteNumber(v)) n += 1;
  return n;
}

/** Last real value in a series, or null when it holds none (never 0). */
export function lastFinite(values: readonly NumericPoint[]): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (isFiniteNumber(v)) return v;
  }
  return null;
}

/** `EngineSnapshot.bars` -> close series, gaps preserved (null stays null). */
export function closes(bars: readonly IndicatorBar[] | null | undefined): Series {
  if (!Array.isArray(bars)) return [];
  return bars.map((b) => (b && isFiniteNumber(b.close) ? b.close : null));
}

/** `EngineSnapshot.bars` -> high series (gaps preserved). */
export function highs(bars: readonly IndicatorBar[] | null | undefined): Series {
  if (!Array.isArray(bars)) return [];
  return bars.map((b) => (b && isFiniteNumber(b.high) ? b.high : null));
}

/** `EngineSnapshot.bars` -> low series (gaps preserved). */
export function lows(bars: readonly IndicatorBar[] | null | undefined): Series {
  if (!Array.isArray(bars)) return [];
  return bars.map((b) => (b && isFiniteNumber(b.low) ? b.low : null));
}

/** Half-open [start, end) ranges of consecutive finite points. */
export function finiteRuns(
  values: readonly NumericPoint[],
  minRunLength = 1,
): Array<[number, number]> {
  const runs: Array<[number, number]> = [];
  let start = -1;
  for (let i = 0; i <= values.length; i += 1) {
    const finite = i < values.length && isFiniteNumber(values[i]);
    if (finite && start < 0) start = i;
    if (!finite && start >= 0) {
      if (i - start >= minRunLength) runs.push([start, i]);
      start = -1;
    }
  }
  return runs;
}

/** min/max over the finite points; {min:null,max:null} when there are none. */
export function seriesDomain(
  ...seriesList: ReadonlyArray<readonly NumericPoint[]>
): { min: number | null; max: number | null } {
  let min: number | null = null;
  let max: number | null = null;
  for (const series of seriesList) {
    for (const v of series) {
      if (!isFiniteNumber(v)) continue;
      if (min === null || v < min) min = v;
      if (max === null || v > max) max = v;
    }
  }
  return { min, max };
}

// ---------------------------------------------------------------------------
// Moving averages — spec §B, parity with calculators.py _sma / _ema
// ---------------------------------------------------------------------------

/**
 * Simple moving average, one value per input index.
 *
 * Legacy `_sma(values, period)` = mean of the last `period` closes; here the
 * same arithmetic streamed, so index i holds the window ENDING at i (null
 * until `period` consecutive finite points exist — calculators.py:19 returns
 * None for a short window; here every short prefix is null).
 */
export function sma(values: readonly NumericPoint[], period: number): Series {
  const out: Series = new Array<number | null>(values.length).fill(null);
  if (!Number.isInteger(period) || period <= 0) return out;
  const window: number[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (!isFiniteNumber(v)) {
      window.length = 0; // a gap breaks the window — never skip over it
      sum = 0;
      continue;
    }
    window.push(v);
    sum += v;
    if (window.length > period) sum -= window.shift() as number;
    if (window.length === period) out[i] = sum / period;
  }
  return out;
}

/**
 * Exponential moving average, one value per input index.
 *
 * Legacy §B: `k = 2/(n+1)`, `EMA_t = C_t·k + EMA_{t-1}·(1-k)`, seeded with the
 * SMA of the FIRST `n` closes of the run (calculators.py:28). The seed is what
 * makes this differ from the engine feature pipeline's `_compute_ema`, which
 * starts at prices[0] — the dashboard series use the spec/`calculators.py`
 * flavour, and so does this.
 */
export function ema(values: readonly NumericPoint[], period: number): Series {
  const out: Series = new Array<number | null>(values.length).fill(null);
  if (!Number.isInteger(period) || period <= 0) return out;
  const k = 2 / (period + 1);
  for (const [start, end] of finiteRuns(values)) {
    let seedSum = 0;
    let prev: number | null = null;
    for (let i = start; i < end; i += 1) {
      const v = values[i] as number;
      const seen = i - start + 1;
      if (seen < period) {
        seedSum += v;
        continue; // not enough data yet: null stays visible
      }
      if (seen === period) {
        prev = (seedSum + v) / period;
      } else {
        prev = v * k + (prev as number) * (1 - k);
      }
      out[i] = prev;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// RSI — spec §A.1 (Wilder), parity with calculators.py rsi_series
// ---------------------------------------------------------------------------

/**
 * Wilder RSI, one value per bar index (null for i < period, §A.1).
 *
 * Seed = simple mean of the first `period` gains/losses; then
 * `avgGain = (avgGain·(n-1) + gain)/n`. `avgLoss == 0` with real gains means
 * nothing gave back in the window: RSI = 100 (calculators.py:58). Zero gains
 * AND zero losses is 0/0 — a flat window carries no strength information, so
 * the answer is null (truth rule 4), NOT 100.
 */
export function rsi(values: readonly NumericPoint[], period: number = DEFAULT_RSI_PERIOD): Series {
  const out: Series = new Array<number | null>(values.length).fill(null);
  if (!Number.isInteger(period) || period <= 0) return out;
  for (const [start, end] of finiteRuns(values, period + 1)) {
    const gains: number[] = [];
    const losses: number[] = [];
    for (let i = start + 1; i < end; i += 1) {
      const delta = (values[i] as number) - (values[i - 1] as number);
      gains.push(Math.max(delta, 0));
      losses.push(Math.max(-delta, 0));
    }
    if (gains.length < period) continue;
    let avgGain = 0;
    let avgLoss = 0;
    for (let i = 0; i < period; i += 1) {
      avgGain += (gains[i] as number) / period;
      avgLoss += (losses[i] as number) / period;
    }
    out[start + period] = rsiFromAverages(avgGain, avgLoss);
    for (let i = period; i < gains.length; i += 1) {
      avgGain = (avgGain * (period - 1) + (gains[i] as number)) / period;
      avgLoss = (avgLoss * (period - 1) + (losses[i] as number)) / period;
      out[start + i + 1] = rsiFromAverages(avgGain, avgLoss);
    }
  }
  return out;
}

/** Last RSI value for a close series (spec: needs period+1 points, else null). */
export function rsiLast(values: readonly NumericPoint[], period: number = DEFAULT_RSI_PERIOD): number | null {
  return lastFinite(rsi(values, period));
}

function rsiFromAverages(avgGain: number, avgLoss: number): number | null {
  if (avgLoss === 0) return avgGain > 0 ? 100 : null; // 0/0 = no information
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

// ---------------------------------------------------------------------------
// True range / ATR — parity with scalp_features.py atr_m1
// ---------------------------------------------------------------------------

/**
 * True range per bar. Index 0 is null: TR needs a PRIOR close (§A.4, and
 * scalp_features.py:582 uses `closes[-15:-1]`).
 *
 * `TR_t = max(H_t − L_t, |H_t − C_{t−1}|, |L_t − C_{t−1}|)`.
 * When the prior close is missing there is no prior reference, so the point is
 * null (gap) rather than a truncated `H − L` guess; a forming bar with a real
 * H/L/C still produces a real TR from the last completed close.
 */
export function trueRanges(bars: readonly IndicatorBar[] | null | undefined): Series {
  const out: Series = [];
  if (!Array.isArray(bars)) return out;
  for (let i = 0; i < bars.length; i += 1) {
    const bar = bars[i];
    const prev = i > 0 ? bars[i - 1] : undefined;
    const hi = bar && isFiniteNumber(bar.high) ? bar.high : null;
    const lo = bar && isFiniteNumber(bar.low) ? bar.low : null;
    const prevClose = prev && isFiniteNumber(prev.close) ? prev.close : null;
    if (hi === null || lo === null || hi < lo || prevClose === null) {
      out.push(null);
      continue;
    }
    out.push(Math.max(hi - lo, Math.abs(hi - prevClose), Math.abs(lo - prevClose)));
  }
  return out;
}

/**
 * Engine ATR (the `atr_m1` flavour): arithmetic mean of the last `period`
 * true ranges. scalp_features.py:586 computes exactly this over `tr` of the
 * final 14 bars, so the last point of this series is directly comparable with
 * `EngineSnapshot.atr`. Null until `period` consecutive TRs exist.
 */
export function atr(bars: readonly IndicatorBar[] | null | undefined, period: number = DEFAULT_ATR_PERIOD): Series {
  return sma(trueRanges(bars), period);
}

/**
 * TradingView/Wilder ATR series: seed = mean of the first `period` TRs, then
 * `ATR_t = (ATR_{t-1}·(n-1) + TR_t)/n` (the same recursion shape as §A.1).
 * Provided because a 14-period simple mean and a Wilder-smoothed ATR of the
 * same bars are legitimately different numbers; a chart must say which it
 * drew, hence `AtrStrip`'s `flavour` prop.
 */
export function atrWilder(
  bars: readonly IndicatorBar[] | null | undefined,
  period: number = DEFAULT_ATR_PERIOD,
): Series {
  const tr = trueRanges(bars);
  const out: Series = new Array<number | null>(tr.length).fill(null);
  if (!Number.isInteger(period) || period <= 0) return out;
  for (const [start, end] of finiteRuns(tr, period)) {
    let seed = 0;
    let prev: number | null = null;
    for (let i = start; i < end; i += 1) {
      const v = tr[i] as number;
      const seen = i - start + 1;
      if (seen < period) {
        seed += v;
        continue;
      }
      prev = seen === period ? (seed + v) / period : ((prev as number) * (period - 1) + v) / period;
      out[i] = prev;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Drawdown — parity with accounting/aggregation.py
// ---------------------------------------------------------------------------

export interface DrawdownPoint {
  /** Index into the supplied equity series. */
  index: number;
  /** The equity value at this index; null when the sample was missing. */
  equity: number | null;
  /** Running peak up to and including this index (null before any sample). */
  peak: number | null;
  /** (peak − equity) / peak · 100, per `compute_drawdown`. null = UNKNOWN. */
  drawdownPct: number | null;
  /** peak − equity in the series' own units. null = UNKNOWN. */
  drawdownAbs: number | null;
}

export interface DrawdownResult {
  points: DrawdownPoint[];
  /** Deepest peak-to-trough trough percentage over the window. */
  maxDrawdownPct: number | null;
  maxDrawdownAbs: number | null;
  /** Index of the deepest trough (null when no drawdown was computable). */
  maxDrawdownIndex: number | null;
  /** Drawdown still open at the last usable sample (0 when at a new peak). */
  currentDrawdownPct: number | null;
  currentDrawdownAbs: number | null;
  /** How many samples carried a real number. */
  samples: number;
  /** How many samples were missing (gaps), so the UI can say so out loud. */
  missing: number;
}

/**
 * Rolling peak-to-trough drawdown of an equity (or balance) series.
 *
 * Same methodology as `compute_drawdown` / `intraperiod_drawdown`: percent OF
 * THE RUNNING PEAK, never of the starting equity. A missing sample carries no
 * drop claim (null) and neither moves nor resets the peak. A single usable
 * sample cannot express a drawdown at all (aggregation.py:260), so every
 * aggregate stays null until two samples exist. A peak of 0 or less makes the
 * percentage meaningless — skipped, exactly like the Python `if peak <= 0: continue`.
 */
export function rollingMaxDrawdown(equity: readonly NumericPoint[]): DrawdownResult {
  const points: DrawdownPoint[] = [];
  let peak: number | null = null;
  let maxPct: number | null = null;
  let maxAbs: number | null = null;
  let maxIndex: number | null = null;
  let samples = 0;
  let missing = 0;
  let currentPct: number | null = null;
  let currentAbs: number | null = null;

  for (let i = 0; i < equity.length; i += 1) {
    const raw = equity[i];
    if (!isFiniteNumber(raw)) {
      missing += 1;
      points.push({ index: i, equity: null, peak, drawdownPct: null, drawdownAbs: null });
      continue;
    }
    samples += 1;
    if (peak === null || raw > peak) peak = raw;
    let pct: number | null = null;
    let abs: number | null = null;
    if (samples >= 2 && peak > 0) {
      abs = Math.max(peak - raw, 0);
      pct = (abs / peak) * 100;
      currentPct = pct;
      currentAbs = abs;
      if (maxPct === null || pct > maxPct) {
        maxPct = pct;
        maxAbs = abs;
        maxIndex = i;
      }
    }
    points.push({ index: i, equity: raw, peak, drawdownPct: pct, drawdownAbs: abs });
  }

  return {
    points,
    maxDrawdownPct: maxPct,
    maxDrawdownAbs: maxAbs,
    maxDrawdownIndex: maxIndex,
    currentDrawdownPct: currentPct,
    currentDrawdownAbs: currentAbs,
    samples,
    missing,
  };
}

// ---------------------------------------------------------------------------
// Spread statistics
// ---------------------------------------------------------------------------

export interface SpreadStats {
  /** Real numbers examined. */
  samples: number;
  /** Points that carried no number (rendered as gaps, never as 0). */
  missing: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  /** Nearest-rank 95th percentile (index ceil(0.95·n) from the sorted values). */
  p95: number | null;
  /** Sample stdev (n−1 denominator); null for fewer than two samples. */
  stdev: number | null;
  /** Last real value, or null. */
  last: number | null;
  /** True only when every input point was a real number and n > 0. */
  complete: boolean;
}

/**
 * Descriptive statistics over a spread sample series (points or price units —
 * this layer does not know the unit and never claims one).
 *
 * Only finite values enter the math; a series with holes reports `missing > 0`
 * and `complete: false` so the strip can say "3 of 40 samples missing" instead
 * of implying a full window. An all-missing series returns nulls, not zeros.
 */
export function spreadStats(values: readonly NumericPoint[]): SpreadStats {
  const finite: number[] = [];
  let missing = 0;
  let last: number | null = null;
  for (const v of values) {
    if (isFiniteNumber(v)) {
      finite.push(v);
      last = v;
    } else {
      missing += 1;
    }
  }
  const n = finite.length;
  if (n === 0) {
    return {
      samples: 0,
      missing,
      min: null,
      max: null,
      mean: null,
      median: null,
      p95: null,
      stdev: null,
      last: null,
      complete: false,
    };
  }
  const sorted = [...finite].sort((a, b) => a - b);
  const sum = finite.reduce((acc, v) => acc + v, 0);
  const mean = sum / n;
  const mid = Math.floor(n / 2);
  const median = n % 2 === 1 ? (sorted[mid] as number) : ((sorted[mid - 1] as number) + (sorted[mid] as number)) / 2;
  const rank = Math.max(1, Math.ceil(0.95 * n));
  const p95 = sorted[rank - 1] as number;
  let stdev: number | null = null;
  if (n > 1) {
    const ssr = finite.reduce((acc, v) => acc + (v - mean) ** 2, 0);
    stdev = Math.sqrt(ssr / (n - 1));
  }
  return {
    samples: n,
    missing,
    min: sorted[0] as number,
    max: sorted[n - 1] as number,
    mean,
    median,
    p95,
    stdev,
    last,
    complete: missing === 0,
  };
}

// ---------------------------------------------------------------------------
// Deterministic viewBox geometry (shared by the SVG primitives)
// ---------------------------------------------------------------------------

export interface GeoPoint {
  x: number;
  y: number;
}

export interface GeometryOptions {
  /** viewBox units. */
  width: number;
  height: number;
  /** Inner padding on all four sides, viewBox units (default 2). */
  pad?: number;
  /** Pin the domain (e.g. RSI 0..100); anything non-finite falls back. */
  domainMin?: number | null;
  domainMax?: number | null;
}

export interface SeriesGeometry {
  width: number;
  height: number;
  pad: number;
  /** Domain actually used (null = nothing drawable). */
  min: number | null;
  max: number | null;
  /** Consecutive finite runs with ≥ 2 points — one polyline each. */
  segments: GeoPoint[][];
  /** Isolated points (a run of one) — a polyline cannot express them. */
  dots: GeoPoint[];
  /** Points dropped as gaps. */
  missing: number;
  /** Input length. */
  total: number;
  /** Index -> x (viewBox units). */
  xAt: (index: number) => number;
  /** Value -> y (viewBox units; non-finite falls back to the mid line). */
  yAt: (value: number | null) => number;
}

/**
 * Map a series into a fixed viewBox WITHOUT measuring the DOM, splitting the
 * line wherever a value is missing so a gap on screen means a gap in the data
 * (never a straight jump between the neighbours, never a drop to zero).
 */
export function seriesGeometry(values: readonly NumericPoint[], options: GeometryOptions): SeriesGeometry {
  const width = isFiniteNumber(options.width) && options.width > 0 ? options.width : 1;
  const height = isFiniteNumber(options.height) && options.height > 0 ? options.height : 1;
  const pad = isFiniteNumber(options.pad) && options.pad >= 0 ? options.pad : 2;
  const total = values.length;

  let min = isFiniteNumber(options.domainMin) ? options.domainMin : null;
  let max = isFiniteNumber(options.domainMax) ? options.domainMax : null;
  if (min === null || max === null) {
    const dom = seriesDomain(values);
    if (min === null) min = dom.min;
    if (max === null) max = dom.max;
  }
  if (min !== null && max !== null && min > max) min = max;

  const innerW = Math.max(0, width - pad * 2);
  const innerH = Math.max(0, height - pad * 2);
  const midY = height / 2;

  const xAt = (index: number): number => {
    if (!isFiniteNumber(index) || index < 0) return pad;
    if (total <= 1) return width / 2;
    return pad + (index * innerW) / (total - 1);
  };
  const yAt = (value: number | null): number => {
    if (!isFiniteNumber(value) || min === null || max === null) return midY;
    if (max === min) return midY; // a flat series is a line, not a spike
    return pad + ((max - value) * innerH) / (max - min);
  };

  const segments: GeoPoint[][] = [];
  const dots: GeoPoint[] = [];
  let missing = 0;
  if (min === null || max === null || total === 0) {
    missing = total;
  } else {
    for (const [start, end] of finiteRuns(values)) {
      const pts: GeoPoint[] = [];
      for (let i = start; i < end; i += 1) {
        pts.push({ x: round3(xAt(i)), y: round3(yAt(values[i] as number)) });
      }
      if (pts.length >= 2) segments.push(pts);
      else if (pts.length === 1) dots.push(pts[0] as GeoPoint);
    }
    missing = total - countFinite(values);
  }

  return { width, height, pad, min, max, segments, dots, missing, total, xAt, yAt };
}

/** "x,y x,y …" for SVG points/polyline attributes. */
export function pointsAttr(points: readonly GeoPoint[]): string {
  return points.map((p) => `${p.x},${p.y}`).join(" ");
}

/** Closed area path over a top line and its reversed bottom line. */
export function areaPath(top: readonly GeoPoint[], bottom: readonly GeoPoint[]): string {
  if (top.length === 0 || bottom.length === 0) return "";
  const up = top.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ");
  const down = [...bottom].reverse().map((p) => `L${p.x} ${p.y}`).join(" ");
  return `${up} ${down} Z`;
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}
