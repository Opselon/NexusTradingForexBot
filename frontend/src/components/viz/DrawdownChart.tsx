/**
 * DrawdownChart — underwater (<=0) area of the backend drawdown series.
 *
 * The drawdown percentages come from the accounting core (single
 * methodology). This renderer flips nothing and estimates nothing: null
 * samples are gaps.
 */

import { extent, fmtCompact, linePath, niceTicks, scaleLinear, type Pt } from "./geometry";
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

export function DrawdownChart({ points, height = 150, maxDrawdownPct, emptyHint = "no drawdown samples from the backend" }: DrawdownChartProps) {
  const values = points.map((p) => p.drawdown_pct ?? null);
  const real = values.filter((v): v is number => v !== null && !Number.isNaN(v));
  if (real.length < 2) return <div className="viz-empty">{emptyHint}</div>;
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
  return (
    <div className="viz-frame">
      <svg className="viz" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`drawdown, worst ${worst.toFixed(2)}%`}>
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
            max {maxDrawdownPct.toFixed(2)}%
          </text>
        )}
      </svg>
      <div className="viz-legend">
        <span>
          <i className="sw neg" />
          drawdown_pct (running peak, accounting core)
        </span>
      </div>
    </div>
  );
}
