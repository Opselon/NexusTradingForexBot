/**
 * geometry — pure math for the hand-rolled SVG kit (no deps, unit-testable).
 *
 * Every function here is presentation-only: it maps VALUES THE BACKEND SENT
 * into coordinates. Nothing infers, smooths or fabricates a missing point —
 * a `null` sample becomes a GAP in the path, never a straight line over it.
 */

export interface Pt {
  x: number;
  y: number;
}

/** Min/max of a sample set, ignoring null/undefined/NaN. null when nothing is real. */
export function extent(values: Array<number | null | undefined>): [number, number] | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v === null || v === undefined || Number.isNaN(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return Number.isFinite(min) && Number.isFinite(max) ? [min, max] : null;
}

/** Linear map from a data domain to a pixel range. Flat domain -> midpoint (never divide by 0). */
export function scaleLinear(dMin: number, dMax: number, rMin: number, rMax: number): (v: number) => number {
  const span = dMax - dMin;
  if (span === 0 || !Number.isFinite(span)) return () => (rMin + rMax) / 2;
  return (v: number) => rMin + ((v - dMin) / span) * (rMax - rMin);
}

/**
 * Polyline path over points where `null` means "no sample": the gap breaks the
 * line into subpaths so the chart can never imply a value that wasn't reported.
 */
export function linePath(points: Array<Pt | null>): string {
  let d = "";
  let pen = false;
  for (const p of points) {
    if (!p) {
      pen = false;
      continue;
    }
    d += pen ? ` L${p.x.toFixed(2)},${p.y.toFixed(2)}` : ` M${p.x.toFixed(2)},${p.y.toFixed(2)}`;
    pen = true;
  }
  return d.trim();
}

/** Area path (line + close to baseline). Returns "" when there is no real sample. */
export function areaPath(points: Array<Pt | null>, baseline: number): string {
  const solid = points.filter((p): p is Pt => p !== null);
  if (solid.length === 0) return "";
  const line = linePath(points);
  if (!line) return "";
  const first = solid[0];
  const last = solid[solid.length - 1];
  if (!first || !last) return "";
  return `${line} L${last.x.toFixed(2)},${baseline.toFixed(2)} L${first.x.toFixed(2)},${baseline.toFixed(2)} Z`;
}

/** Vertical bars for a signed series (waterfall/columns). null = no bar. */
export interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
  positive: boolean;
}

export function signedBars(
  values: Array<number | null | undefined>,
  opts: { width: number; height: number; zeroY: number; toY: (v: number) => number; gap?: number },
): Bar[] {
  const n = values.length;
  if (n === 0) return [];
  const gap = opts.gap ?? 1;
  const step = opts.width / n;
  const w = Math.max(1, step - gap * 2);
  const bars: Bar[] = [];
  values.forEach((v, i) => {
    if (v === null || v === undefined || Number.isNaN(v)) return;
    const y = opts.toY(v);
    const top = Math.min(y, opts.zeroY);
    const h = Math.max(1, Math.abs(opts.zeroY - y));
    bars.push({ x: i * step + gap, y: top, w, h, positive: v >= 0 });
  });
  return bars;
}

/** "Nice" tick values covering [min,max] — used for axis captions only. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [min];
  const span = max - min;
  const raw = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const stepUnit = norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1;
  const step = stepUnit * mag;
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + step * 0.001 && out.length < 32; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Compact numeric label for axes (1234 -> 1.2k). Never used to derive state. */
export function fmtCompact(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(digits)}M`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(digits)}k`;
  if (abs >= 1) return v.toFixed(Math.max(0, digits - 1));
  return v.toFixed(2);
}

/** Clamp to [0,1] for widths/intensities; null passes through as "unknown". */
export function clampRatio(v: number | null | undefined): number | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  return Math.max(0, Math.min(1, v));
}

/** Polar -> cartesian for arc gauges (angle in degrees, 0 = pointing up). */
export function polar(cx: number, cy: number, r: number, angleDeg: number): Pt {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/** SVG arc path for a gauge sweep between two angles. */
export function arcPath(cx: number, cy: number, r: number, fromDeg: number, toDeg: number): string {
  const a = polar(cx, cy, r, fromDeg);
  const b = polar(cx, cy, r, toDeg);
  const large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
  const sweep = toDeg >= fromDeg ? 1 : 0;
  return `M${a.x.toFixed(2)},${a.y.toFixed(2)} A${r},${r} 0 ${large} ${sweep} ${b.x.toFixed(2)},${b.y.toFixed(2)}`;
}
