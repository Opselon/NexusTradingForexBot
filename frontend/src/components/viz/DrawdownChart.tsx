/**
 * DrawdownChart — underwater (<=0) area of the backend drawdown series.
 *
 * The drawdown percentages come from the accounting core (single
 * methodology). This renderer flips nothing and estimates nothing: null
 * samples are gaps.
 */

import { useMemo } from "react";
import { extent, fmtCompact, linePath, niceTicks, scaleLinear, type Pt } from "./geometry";
import { useI18n } from "@/stores/i18nStore";
import "./viz.css";

export interface DrawdownPoint {
  timestamp: string;
  drawdown_pct: number | null;
}

export interface DrawdownChartProps {
  points: DrawdownPoint[];
  height?: number;
  /** Optional worst-drawdown marker straight from the backend report. */
  maxDrawdownPct?: number | null;
  emptyHint?: string;
}

const W = 640;

export function DrawdownChart({ points, height = 150, maxDrawdownPct, emptyHint }: DrawdownChartProps) {
  const t = useI18n((s) => s.t);
  const emptyHintText = emptyHint ?? t("ui.viz.dd_empty", "no drawdown samples from the backend");
  // The geometry is a pure function of `points` (identity changes only when a
  // fetch lands). Memoizing keeps an unchanged series from being re-walked on
  // every parent render; the emitted SVG is byte-identical.
  const geo = useMemo(() => {
    const values = points.map((p) => p.drawdown_pct ?? null);
    const real = values.filter((v): v is number => v !== null && !Number.isNaN(v));
    if (real.length < 2) return null;
    const padT = 10;
    const padB = 18;
    const lo = Math.min(...real, 0);
    const hi = Math.max(...real, 0);
    const ext: [number, number] = extent([lo, hi]) ?? [0, 0];
    const toY = scaleLinear(ext[0], ext[1], height - padB, padT);
    const step = W / Math.max(1, points.length - 1);
    const pts: Array<Pt | null> = values.map((v, i) => (v === null ? null : { x: i * step, y: toY(v) }));
    const zeroY = toY(0);
    const line = linePath(pts);
    const area = `${line} L${((points.length - 1) * step).toFixed(2)},${zeroY.toFixed(2)} L0,${zeroY.toFixed(2)} Z`;
    const worst = Math.min(...real);
    const ticks = niceTicks(ext[0], ext[1], 3);
    return { toY, ticks, line, area, zeroY, worst, ext };
  }, [points, height]);

  if (geo === null) return <div className="viz-empty">{emptyHintText}</div>;
  const { toY, ticks, line, area, zeroY, worst, ext } = geo;
  return (
    <div className="viz-frame">
      <svg className="viz" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={t("ui.viz.dd_aria", "drawdown, worst {w}%", { w: worst.toFixed(2) })}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="viz-grid" x1={0} x2={W} y1={toY(t)} y2={toY(t)} />
            <text className="viz-axis" x={2} y={toY(t) - 3}>
              {fmtCompact(t, 1)}%
            </text>
          </g>
        ))}
        <line className="viz-zero" x1={0} x2={W} y1={zeroY} y2={zeroY} />
        <path className="viz-area neg" d={area} stroke="none" />
        <path className="viz-line neg" d={line} />
        {maxDrawdownPct !== null && maxDrawdownPct !== undefined && (
          <text className="viz-axis" x={W} y={toY(Math.min(maxDrawdownPct, ext[1])) - 3} textAnchor="end" style={{ fill: "var(--amber)" }}>
            {t("ui.viz.max_pct", "max {v}%", { v: maxDrawdownPct.toFixed(2) })}
          </text>
        )}
      </svg>
      <div className="viz-legend">
        <span>
          <i className="sw neg" />
          {t("ui.viz.dd_legend", "drawdown_pct (running peak, accounting core)")}
        </span>
      </div>
    </div>
  );
}
