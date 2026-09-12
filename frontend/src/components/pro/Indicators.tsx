/**
 * Indicators — PRO console SVG chart primitives (presentation only).
 *
 * These components draw results that `@/lib/indicatorMath` already computed:
 * they take arrays of `number | null`, never a fetcher, never a query key, and
 * they never invent a value. The rules that make the pictures honest:
 *
 *   1. A null point is a GAP, not a zero. `seriesGeometry` splits the line at
 *      every hole, so the polyline never bridges missing data and never drops
 *      to the baseline. Isolated points become dots (a 2-point segment cannot
 *      exist). The gap count is shown in the caption and in `data-missing`.
 *   2. An empty / all-null / non-finite series renders the explicit UNKNOWN
 *      placeholder (`IndicatorUnknown`), stating WHICH series is unknown. The
 *      chart area is never filled with a flat line, a random walk, or 0.
 *   3. Geometry is deterministic: fixed `viewBox` units, linear index -> x and
 *      value -> y maps, domain pinned where the indicator has a canonical
 *      range (RSI 0..100) and otherwise taken from the finite values. No
 *      measuring the DOM, no randomness, no animation seeds — the same props
 *      always produce the same SVG (assertable in tests and replay).
 *   4. Threshold lines (RSI 30/50/70, a supplied spread limit) are drawn as
 *      guides with labels; no component converts a value into a verdict word.
 *
 * Namespace: all classes are prefixed `pi-` in ./pro-indicators.css so nothing
 * here can restyle an existing console surface.
 */

import type { ReactElement, ReactNode } from "react";
import {
  RSI_OVERBOUGHT,
  RSI_OVERSOLD,
  UNKNOWN_WORD,
  areaPath,
  countFinite,
  lastFinite,
  pointsAttr,
  seriesDomain,
  seriesGeometry,
  spreadStats,
  type Series,
  type SeriesGeometry,
  type SpreadStats,
} from "@/lib/indicatorMath";
import "./pro-indicators.css";

// ---------------------------------------------------------------------------
// Shared framing
// ---------------------------------------------------------------------------

/** viewBox height defaults per strip kind (width is always 100 units). */
const VIEW_W = 100;

export interface StripProps {
  /** viewBox height in units (default per component). Geometry is linear in it. */
  height?: number;
  /** Caption shown under the chart; the component supplies an honest default. */
  caption?: string;
  /** Accessible name for the <svg>. */
  label?: string;
  className?: string;
}

/**
 * IndicatorUnknown — the single placeholder every primitive falls back to.
 * `subject` names the series that is unknown; `reason` says why in facts
 * ("no samples", "0 of 40 samples carry a value"), never a guess.
 */
export function IndicatorUnknown({
  subject,
  reason,
  className,
}: {
  subject: string;
  reason?: string | null;
  className?: string;
}): ReactElement {
  return (
    <div
      className={`pi-unknown${className ? ` ${className}` : ""}`}
      data-state="unknown"
      data-subject={subject}
      role="note"
      aria-label={`${subject} ${UNKNOWN_WORD}${reason ? `: ${reason}` : ""}`}
    >
      <span className="pi-unknown__word">{UNKNOWN_WORD}</span>
      <span className="pi-unknown__subject">{subject}</span>
      {reason ? <span className="pi-unknown__reason">{reason}</span> : null}
    </div>
  );
}

function missingReason(values: readonly (number | null)[]): string {
  const total = values.length;
  const have = countFinite(values);
  if (total === 0) return "no samples supplied";
  return `${have} of ${total} samples carry a value`;
}

function ChartFrame({
  geo,
  label,
  caption,
  className,
  children,
}: {
  geo: SeriesGeometry;
  label: string;
  caption: string;
  className?: string;
  children: ReactNode;
}): ReactElement {
  return (
    <figure className={`pi-chart${className ? ` ${className}` : ""}`}>
      <svg
        className="pi-svg"
        viewBox={`0 0 ${geo.width} ${geo.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={label}
        data-total={geo.total}
        data-missing={geo.missing}
      >
        {children}
      </svg>
      <figcaption className="pi-caption">
        <span className="pi-caption__text">{caption}</span>
        {geo.missing > 0 ? (
          <span className="pi-caption__gaps" data-gaps={geo.missing}>
            {geo.missing} gap{geo.missing === 1 ? "" : "s"}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}

/** One <polyline> per consecutive finite run — the gap-truthful line painter. */
function Runs({
  geo,
  className,
  strokeWidth = 1.4,
  dashedWhenSingle = true,
}: {
  geo: SeriesGeometry;
  className: string;
  strokeWidth?: number;
  dashedWhenSingle?: boolean;
}): ReactElement | null {
  const out: ReactElement[] = [];
  geo.segments.forEach((seg, i) => {
    out.push(
      <polyline
        key={`s${i}`}
        className={className}
        points={pointsAttr(seg)}
        strokeWidth={strokeWidth}
        vectorEffect="non-scaling-stroke"
      />,
    );
  });
  if (dashedWhenSingle) {
    geo.dots.forEach((d, i) => {
      out.push(
        <circle
          key={`d${i}`}
          className={`${className}__dot`}
          cx={d.x}
          cy={d.y}
          r={strokeWidth}
          vectorEffect="non-scaling-stroke"
        />,
      );
    });
  }
  return out.length > 0 ? <g data-runs={out.length}>{out}</g> : null;
}

// ---------------------------------------------------------------------------
// SparkLine — close series with real gap segments
// ---------------------------------------------------------------------------

export interface SparkLineProps extends StripProps {
  /** Values to draw (index-aligned; null = gap). Feed `closes(snapshot.bars)`. */
  values: Series;
  /** Optional overlay (e.g. `sma(closes, 20)`) drawn in a second colour. */
  overlay?: Series | null;
  overlayLabel?: string;
}

/**
 * SparkLine — the compact close series. Splits at every gap, marks the last
 * real value, and never connects two points that were not adjacent in data.
 */
export function SparkLine({
  values,
  overlay,
  overlayLabel,
  height = 30,
  caption,
  label = "Close series",
  className,
}: SparkLineProps): ReactElement {
  if (countFinite(values) === 0) {
    return <IndicatorUnknown subject="Spark line (closes)" reason={missingReason(values)} className={className} />;
  }
  const last = lastFinite(values) as number;
  const lastIndex = lastIndexFinite(values);
  // shared domain so an overlay can never be drawn off-canvas
  const dom = seriesDomain(values, overlay ?? []);
  const geo = seriesGeometry(values, { width: VIEW_W, height, domainMin: dom.min, domainMax: dom.max });
  const overlayGeo = overlay
    ? seriesGeometry(overlay, { width: VIEW_W, height, domainMin: dom.min, domainMax: dom.max })
    : null;
  const overlayLast = overlay ? lastFinite(overlay) : null;
  return (
    <ChartFrame
      geo={geo}
      label={label}
      caption={
        caption ??
        `${fmt(last)} · ${countFinite(values)} pts${overlayLast !== null ? ` · ${overlayLabel ?? "overlay"} ${fmt(overlayLast)}` : ""}`
      }
      className={className}
    >
      <line
        className="pi-baseline"
        x1={0}
        x2={geo.width}
        y1={geo.height - geo.pad}
        y2={geo.height - geo.pad}
        vectorEffect="non-scaling-stroke"
      />
      {overlayGeo ? <Runs geo={overlayGeo} className="pi-line pi-line--overlay" strokeWidth={1.1} /> : null}
      <Runs geo={geo} className="pi-line" />
      {lastIndex === null ? null : (
        <circle
          className="pi-last"
          cx={geo.xAt(lastIndex)}
          cy={geo.yAt(last)}
          r={1.8}
          vectorEffect="non-scaling-stroke"
          data-value={last}
        />
      )}
    </ChartFrame>
  );
}

function lastIndexFinite(values: readonly (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (typeof v === "number" && Number.isFinite(v)) return i;
  }
  return null;
}

// ---------------------------------------------------------------------------
// PriceBand — high/low area + last-price marker
// ---------------------------------------------------------------------------

export interface PriceBandProps extends StripProps {
  /** Per-index high values (null = gap in that bar's high). */
  highs: Series;
  lows: Series;
  /** Optional close line drawn inside the band. */
  closes?: Series | null;
  /** Last traded price to mark (backend `bid`/`ask`/close — caller decides). */
  lastPrice?: number | null;
  lastPriceLabel?: string;
  /** Index of the last price on the x axis; defaults to the final slot. */
  lastPriceIndex?: number | null;
}

/**
 * PriceBand — the H/L envelope as a filled area plus an optional close line and
 * an explicit last-price marker.
 *
 * Gap honesty: a bar missing its high or its low contributes NO corner to the
 * band, so the envelope is built from per-run ribbons (never a bridge over a
 * missing bar). A missing `lastPrice` renders the marker as absent plus an
 * UNKNOWN note in the caption — never as 0.
 */
export function PriceBand({
  highs,
  lows,
  closes: closeSeries,
  lastPrice,
  lastPriceLabel,
  lastPriceIndex,
  height = 46,
  caption,
  label = "High/low band",
  className,
}: PriceBandProps): ReactElement {
  const n = Math.max(highs.length, lows.length);
  if (n === 0) {
    return <IndicatorUnknown subject="Price band (high/low)" reason="no bars supplied" className={className} />;
  }
  // Pair the two series: a corner only exists where BOTH are finite.
  const paired: Array<{ i: number; hi: number; lo: number }> = [];
  for (let i = 0; i < n; i += 1) {
    const hi = highs[i];
    const lo = lows[i];
    if (typeof hi === "number" && Number.isFinite(hi) && typeof lo === "number" && Number.isFinite(lo) && lo <= hi) {
      paired.push({ i, hi, lo });
    }
  }
  if (paired.length === 0) {
    return <IndicatorUnknown subject="Price band (high/low)" reason={missingReason(highs)} className={className} />;
  }
  const dom = seriesDomain(highs, lows, closeSeries ?? []);
  const min = dom.min ?? 0;
  const max = dom.max ?? 1;
  const geo = seriesGeometry(highs.map((v, i) => (typeof v === "number" && typeof lows[i] === "number" ? v : null)), {
    width: VIEW_W,
    height,
    domainMin: min,
    domainMax: max,
  });
  const pad = geo.pad;
  const innerH = Math.max(0, geo.height - pad * 2);
  const yOf = (v: number): number => (max === min ? geo.height / 2 : pad + ((max - v) * innerH) / (max - min));
  const xOf = (i: number): number => geo.xAt(i);

  // Ribbons over consecutive-index runs (a missing bar breaks the envelope).
  const ribbons: ReactElement[] = [];
  let run: Array<{ i: number; hi: number; lo: number }> = [];
  const flush = (): void => {
    if (run.length >= 2) {
      const top = run.map((p) => ({ x: r3(xOf(p.i)), y: r3(yOf(p.hi)) }));
      const bottom = run.map((p) => ({ x: r3(xOf(p.i)), y: r3(yOf(p.lo)) }));
      ribbons.push(
        <path key={`b${ribbons.length}`} className="pi-band" d={areaPath(top, bottom)} vectorEffect="non-scaling-stroke" />,
      );
    } else if (run.length === 1) {
      const p = run[0];
      if (p) {
        ribbons.push(
          <line
            key={`b${ribbons.length}`}
            className="pi-band pi-band--single"
            x1={xOf(p.i)}
            x2={xOf(p.i)}
            y1={yOf(p.hi)}
            y2={yOf(p.lo)}
            vectorEffect="non-scaling-stroke"
          />,
        );
      }
    }
    run = [];
  };
  let prevIndex = -2;
  for (const p of paired) {
    if (p.i !== prevIndex + 1) flush();
    run.push(p);
    prevIndex = p.i;
  }
  flush();

  const closeGeo = closeSeries ? seriesGeometry(closeSeries, { width: VIEW_W, height, domainMin: min, domainMax: max }) : null;
  const hasLast = typeof lastPrice === "number" && Number.isFinite(lastPrice);
  const lastIdx =
    typeof lastPriceIndex === "number" && Number.isFinite(lastPriceIndex)
      ? Math.max(0, Math.min(n - 1, lastPriceIndex))
      : n - 1;
  const gaps = paired.length < n ? n - paired.length : geo.missing;

  return (
    <ChartFrame
      geo={{ ...geo, missing: gaps, total: n }}
      label={label}
      caption={
        caption ??
        (hasLast
          ? `last ${lastPriceLabel ?? "price"} ${fmt(lastPrice as number)} · band ${fmt(min)}–${fmt(max)}`
          : `band ${fmt(min)}–${fmt(max)} · last price ${UNKNOWN_WORD}`)
      }
      className={className}
    >
      {ribbons}
      {closeGeo ? <Runs geo={closeGeo} className="pi-line" strokeWidth={1.1} /> : null}
      {hasLast ? (
        <g className="pi-lastmark" data-value={lastPrice}>
          <line
            className="pi-lastmark__rule"
            x1={0}
            x2={geo.width}
            y1={yOf(lastPrice as number)}
            y2={yOf(lastPrice as number)}
            vectorEffect="non-scaling-stroke"
          />
          <circle cx={xOf(lastIdx)} cy={yOf(lastPrice as number)} r={2} vectorEffect="non-scaling-stroke" />
        </g>
      ) : (
        <g className="pi-lastmark pi-lastmark--unknown" />
      )}
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------------
// SpreadStrip — per-sample spread with limit guide + stats
// ---------------------------------------------------------------------------

export interface SpreadStripProps extends StripProps {
  /** Spread samples (points or price units — the unit is the caller's caption). */
  values: Series;
  /** Backend spread limit (`algo_config` / risk `max_spread_points`). null = none. */
  limit?: number | null;
  /** Unit word for labels, e.g. "pts". Never assumed by this component. */
  unit?: string | null;
  /** Hide the stats line (returns the chart only). */
  hideStats?: boolean;
}

/**
 * SpreadStrip — one column per sample, drawn from zero-height at the series
 * floor up, with the backend's limit as a labelled guide when it supplied one.
 * A missing sample draws NO column (the gutter is the gap), and the stats row
 * reports how many samples were missing instead of implying a full window.
 */
export function SpreadStrip({
  values,
  limit,
  unit,
  hideStats,
  height = 34,
  caption,
  label = "Spread samples",
  className,
}: SpreadStripProps): ReactElement {
  const stats: SpreadStats = spreadStats(values);
  if (stats.samples === 0) {
    return <IndicatorUnknown subject="Spread series" reason={missingReason(values)} className={className} />;
  }
  const hasLimit = typeof limit === "number" && Number.isFinite(limit) && limit > 0;
  const top = Math.max(stats.max as number, hasLimit ? (limit as number) : -Infinity);
  const bottom = 0;
  const geo = seriesGeometry(values, { width: VIEW_W, height, domainMin: bottom, domainMax: top });
  const slots = Math.max(1, values.length);
  const w = Math.max(0.6, (geo.width - geo.pad * 2) / slots - 0.4);
  const baseY = geo.yAt(bottom);
  const over = hasLimit && (stats.max as number) > (limit as number);

  const bars: ReactElement[] = [];
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (typeof v !== "number" || !Number.isFinite(v)) continue; // gap = no column
    const y = geo.yAt(v);
    bars.push(
      <rect
        key={i}
        className={`pi-spread__bar${hasLimit && v > (limit as number) ? " pi-spread__bar--over" : ""}`}
        x={r3(geo.xAt(i) - w / 2)}
        y={r3(Math.min(y, baseY))}
        width={r3(w)}
        height={r3(Math.abs(baseY - y))}
        vectorEffect="non-scaling-stroke"
      />,
    );
  }

  return (
    <figure className={`pi-chart pi-chart--strip${className ? ` ${className}` : ""}`}>
      <svg
        className="pi-svg"
        viewBox={`0 0 ${geo.width} ${geo.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={label}
        data-total={values.length}
        data-missing={stats.missing}
        data-limit={hasLimit ? limit : "none"}
      >
        {bars}
        {hasLimit ? (
          <g className="pi-guide">
            <line
              className="pi-guide__line"
              x1={0}
              x2={geo.width}
              y1={r3(geo.yAt(limit as number))}
              y2={r3(geo.yAt(limit as number))}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        ) : null}
      </svg>
      <figcaption className="pi-caption">
        <span className="pi-caption__text">
          {caption ??
            `${fmtNumber(stats.median)} med · ${fmtNumber(stats.p95)} p95 · ${fmtNumber(stats.max)} max${unit ? ` ${unit}` : ""}${
              hasLimit ? ` · limit ${fmtNumber(limit as number)}` : " · no backend limit"
            }${over ? " · max above limit" : ""}`}
        </span>
        {stats.missing > 0 ? (
          <span className="pi-caption__gaps" data-gaps={stats.missing}>
            {stats.missing} missing
          </span>
        ) : null}
      </figcaption>
      {!hideStats ? <SpreadStatRow stats={stats} limit={hasLimit ? (limit as number) : null} unit={unit ?? null} /> : null}
    </figure>
  );
}

function SpreadStatRow({
  stats,
  limit,
  unit,
}: {
  stats: SpreadStats;
  limit: number | null;
  unit: string | null;
}): ReactElement {
  const cells: Array<[string, string]> = [
    ["n", String(stats.samples)],
    ["last", fmtNumber(stats.last)],
    ["mean", fmtNumber(stats.mean)],
    ["p95", fmtNumber(stats.p95)],
    ["σ", stats.stdev === null ? UNKNOWN_WORD : fmtNumber(stats.stdev)],
    ["limit", limit === null ? UNKNOWN_WORD : fmtNumber(limit)],
  ];
  return (
    <dl className="pi-stats" data-complete={stats.complete ? "yes" : "no"} data-missing={stats.missing}>
      {cells.map(([k, v]) => (
        <div key={k} className="pi-stats__cell">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
      {unit ? <div className="pi-stats__unit">{unit}</div> : null}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// RsiStrip — oscillator track with 30/50/70 guides
// ---------------------------------------------------------------------------

export interface RsiStripProps extends StripProps {
  /** RSI series from `rsi(closes, period)` (null = gap or insufficient data). */
  values: Series;
  /** Period actually used, for the caption (default 14, the spec's value). */
  period?: number;
}

/**
 * RsiStrip — domain pinned to the canonical 0..100 range so vertical position
 * always means the same thing across screens, with 30/50/70 guides labelled
 * from the spec's own thresholds. Values outside 0..100 cannot exist for RSI;
 * a non-finite point is a gap and is drawn as one.
 */
export function RsiStrip({
  values,
  period = 14,
  height = 34,
  caption,
  label = "RSI series",
  className,
}: RsiStripProps): ReactElement {
  if (countFinite(values) === 0) {
    return <IndicatorUnknown subject={`RSI(${period})`} reason={missingReason(values)} className={className} />;
  }
  const geo = seriesGeometry(values, { width: VIEW_W, height, domainMin: 0, domainMax: 100 });
  const last = lastFinite(values) as number;
  const guides: ReactElement[] = [RSI_OVERSOLD, 50, RSI_OVERBOUGHT].map((lvl) => (
    <g key={lvl} className={`pi-guide${lvl === 50 ? " pi-guide--mid" : ""}`} data-level={lvl}>
      <line
        className="pi-guide__line"
        x1={0}
        x2={geo.width}
        y1={r3(geo.yAt(lvl))}
        y2={r3(geo.yAt(lvl))}
        vectorEffect="non-scaling-stroke"
      />
    </g>
  ));
  return (
    <ChartFrame
      geo={geo}
      label={label}
      caption={caption ?? `RSI(${period}) ${fmt(last)} · guides ${RSI_OVERSOLD}/50/${RSI_OVERBOUGHT}`}
      className={className}
    >
      {guides}
      <Runs geo={geo} className="pi-line pi-line--osc" strokeWidth={1.5} />
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------------
// AtrStrip — volatility track
// ---------------------------------------------------------------------------

export interface AtrStripProps extends StripProps {
  /** ATR series from `atr(bars, n)` or `atrWilder(bars, n)` (null = gap). */
  values: Series;
  /** Which formula produced the series — the number is NOT interchangeable. */
  flavour?: "engine-mean" | "wilder";
  /** Reference value from the payload (e.g. `snapshot.atr`) for a marker line. */
  reference?: number | null;
  referenceLabel?: string;
  /** Price series used only to express ATR as % of price in the caption. */
  price?: number | null;
  period?: number;
  digits?: number;
}

/**
 * AtrStrip — volatility track. The flavour is part of the label, because the
 * engine's 14-bar arithmetic mean (`atr_m1`, scalp_features.py) and a
 * Wilder-smoothed ATR are different numbers over the same bars; a chart that
 * hides which one it drew is lying by omission.
 */
export function AtrStrip({
  values,
  flavour = "engine-mean",
  reference,
  referenceLabel,
  price,
  period = 14,
  digits = 2,
  height = 34,
  caption,
  label,
  className,
}: AtrStripProps): ReactElement {
  if (countFinite(values) === 0) {
    return <IndicatorUnknown subject={`ATR(${period})`} reason={missingReason(values)} className={className} />;
  }
  const hasRef = typeof reference === "number" && Number.isFinite(reference);
  const dom = seriesDomain(values, hasRef ? [reference as number] : []);
  const geo = seriesGeometry(values, {
    width: VIEW_W,
    height,
    domainMin: 0,
    domainMax: dom.max,
  });
  const last = lastFinite(values) as number;
  const pctOfPrice =
    typeof price === "number" && Number.isFinite(price) && price > 0 ? `${((last / price) * 100).toFixed(2)}% of price` : null;
  return (
    <ChartFrame
      geo={geo}
      label={label ?? `ATR(${period}) ${flavour === "wilder" ? "Wilder" : "14-bar mean"}`}
      caption={
        caption ??
        `ATR(${period}) ${last.toFixed(digits)}${pctOfPrice ? ` · ${pctOfPrice}` : ""} · ${
          flavour === "wilder" ? "Wilder-smoothed" : "engine 14-bar mean"
        }`
      }
      className={className}
    >
      {hasRef ? (
        <g className="pi-guide pi-guide--ref" data-reference={reference}>
          <line
            className="pi-guide__line"
            x1={0}
            x2={geo.width}
            y1={r3(geo.yAt(reference as number))}
            y2={r3(geo.yAt(reference as number))}
            vectorEffect="non-scaling-stroke"
          />
          <title>{`${referenceLabel ?? "backend ATR"} ${String(reference)}`}</title>
        </g>
      ) : null}
      <Runs geo={geo} className="pi-line pi-line--atr" strokeWidth={1.4} />
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------------
// tiny local formatting (kept here so the strip has no other dependency)
// ---------------------------------------------------------------------------

function fmt(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return UNKNOWN_WORD;
  return value.toFixed(2);
}

function fmtNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return UNKNOWN_WORD;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function r3(n: number): number {
  return Math.round(n * 1000) / 1000;
}
