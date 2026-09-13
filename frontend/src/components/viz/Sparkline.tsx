/**
 * Sparkline — minimal inline trend line for a backend-provided numeric series.
 *
 * Presentation-only: it renders exactly the samples it is given. `null`
 * samples leave a visible gap (no interpolation, no invented points).
 */

import { extent, linePath, scaleLinear, type Pt } from "./geometry";

export interface SparklineProps {
  /** Ordered oldest -> newest values; null = "no sample at this point". */
  values: Array<number | null | undefined>;
  width?: number;
  height?: number;
  /** Semantic line tone; "auto" colors by the sign of the net move. */
  tone?: "auto" | "pos" | "neg" | "neu" | "dim";
  /** Accessible label (announced by screen readers). */
  label?: string;
}

export function Sparkline({ values, width = 96, height = 22, tone = "auto", label }: SparklineProps) {
  const clean = values.map((v) => (v === undefined ? null : v));
  const real = clean.filter((v): v is number => v !== null && !Number.isNaN(v));
  if (real.length < 2) {
    return <span className="faint tiny inline-mono" title={label ?? "insufficient samples"}>—</span>;
  }
  const ext = extent(clean) ?? [0, 1];
  const pad = 2;
  const toY = scaleLinear(ext[0], ext[1], height - pad, pad);
  const step = width / (clean.length - 1);
  const pts: Array<Pt | null> = clean.map((v, i) => (v === null ? null : { x: i * step, y: toY(v) }));
  const first = real[0] ?? 0;
  const last = real[real.length - 1] ?? 0;
  const cls = tone === "auto" ? (last >= first ? "pos" : "neg") : tone;
  const d = linePath(pts);
  return (
    <svg
      className="viz"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label ?? `sparkline ${first} to ${last}`}
      style={{ width, height }}
    >
      <path className={`viz-line ${cls}`} d={d} />
    </svg>
  );
}
