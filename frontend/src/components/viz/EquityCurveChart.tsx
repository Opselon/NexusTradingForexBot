/**
 * EquityCurveChart — balance/equity time series over a backend-provided point list.
 *
 * Rows arrive from /api/account/equity-curve (`{timestamp, balance, equity,
 * peak_equity, drawdown_pct, floating_pnl}`). This component draws them; it
 * does not compute drawdown, does not resample, and does not connect gaps.
 */

import { useId, useMemo } from "react";
import { areaPath, extent, fmtCompact, linePath, niceTicks, scaleLinear, type Pt } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./viz.css";

export interface EquityPoint {
  timestamp: string;
  balance: number | null;
  equity: number | null;
  peak_equity?: number | null;
  drawdown_pct?: number | null;
  floating_pnl?: number | null;
}

export interface EquityCurveChartProps {
  points: EquityPoint[];
  /** Which backend column to draw. */
  field?: "equity" | "balance";
  height?: number;
  /** Show the backend `peak_equity` line as a reference (never derived locally). */
  showPeak?: boolean;
  formatValue?: (v: number) => string;
  emptyHint?: string;
}

const W = 640;

export function EquityCurveChart({
  points,
  field = "equity",
  height = 200,
  showPeak = false,
  formatValue,
  emptyHint,
}: EquityCurveChartProps) {
  const t = useI18n((s) => s.t);
  const emptyHintText = emptyHint ?? t("ui.viz.eq_empty", "no equity samples from the backend");
  const gid = useId().replace(/:/g, "");
  // The geometry is a pure function of `points` + the numeric props; memoizing
  // keeps an unchanged series from being re-walked on every parent render.
  // The emitted SVG is byte-identical (same values, same tick rounding).
  const geo = useMemo(() => {
    const values = points.map((p) => p[field] ?? null);
    const real = values.filter((v): v is number => v !== null && !Number.isNaN(v));
    if (real.length < 2) return null;
    const padT = 12;
    const padB = 22;
    const ext = extent(values)!;
    const lo = ext[0];
    const hi = ext[1];
    const toY = scaleLinear(lo, hi, height - padB, padT);
    const step = W / Math.max(1, points.length - 1);
    const pts: Array<Pt | null> = values.map((v, i) => (v === null ? null : { x: i * step, y: toY(v) }));
    const peakVals = showPeak ? points.map((p) => p.peak_equity ?? null) : [];
    const peakPts: Array<Pt | null> = peakVals.map((v, i) => (v === null ? null : { x: i * step, y: toY(v) }));
    const first = real[0] ?? 0;
    const last = real[real.length - 1] ?? 0;
    const rising = last >= first;
    const ticks = niceTicks(lo, hi, 4);
    return { toY, step, pts, peakPts, first, last, rising, ticks, real, padB };
  }, [points, field, height, showPeak]);

  if (geo === null) {
    return <div className="viz-empty">{emptyHintText}</div>;
  }
  const { toY, step, pts, peakPts, first, last, rising, ticks, real, padB } = geo;
  const fmt = formatValue ?? ((v: number) => fmtCompact(v, 1));
  const x0 = points[0]?.timestamp ?? "";
  const x1 = points[points.length - 1]?.timestamp ?? "";
  const short = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : `${d.getMonth() + 1}/${d.getDate()}`;
  };
  return (
    <div className="viz-frame">
      <svg className="viz" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={t("ui.viz.eq_aria", "equity curve, {n} samples, from {a} to {b}", { n: real.length, a: fmt(first), b: fmt(last) })}>
        <defs>
          <linearGradient id={`eq-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={rising ? "var(--green)" : "var(--red)"} stopOpacity="0.22" />
            <stop offset="100%" stopColor={rising ? "var(--green)" : "var(--red)"} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line className="viz-grid" x1={0} x2={W} y1={toY(t)} y2={toY(t)} />
            <text className="viz-axis" x={2} y={toY(t) - 3}>
              {fmt(t)}
            </text>
          </g>
        ))}
        <path d={areaPath(pts, height - padB)} fill={`url(#eq-${gid})`} />
        {showPeak && <path className="viz-line dim" strokeDasharray="3 3" d={linePath(peakPts)} />}
        <path className={`viz-line ${rising ? "pos" : "neg"}`} d={linePath(pts)} />
        <circle className={`viz-dot ${rising ? "pos" : "neg"}`} cx={(points.length - 1) * step} cy={toY(last)} r={2.6} fill="currentColor" style={{ color: rising ? "var(--green)" : "var(--red)" }} />
        <text className="viz-axis" x={0} y={height - 6}>
          {short(x0)}
        </text>
        <text className="viz-axis" x={W} y={height - 6} textAnchor="end">
          {short(x1)}
        </text>
      </svg>
      {showPeak && (
        <div className="viz-legend">
          <span>
            <i className={`sw ${rising ? "pos" : "neg"}`} />
            {field}
          </span>
          <span>
            <i className="sw flat" />
            {t("ui.viz.eq_peak", "peak_equity (backend)")}
          </span>
        </div>
      )}
    </div>
  );
}
