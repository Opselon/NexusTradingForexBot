/**
 * Gauge — 270° arc meter for ONE backend numeric value.
 *
 * The value, its bounds and any verdict word come from props (backend
 * payload). When the value is null the gauge renders an honest "no data"
 * track — it never defaults to zero.
 */

import { arcPath, clampRatio } from "./geometry";

export type GaugeTone = "pos" | "warn" | "bad" | "neu";

export interface GaugeProps {
  label: string;
  value: number | null | undefined;
  min?: number;
  max?: number;
  /** Format for the center readout (defaults to a 1-2 decimal string). */
  format?: (v: number) => string;
  tone?: GaugeTone;
  /** Optional backend verdict word under the value (echoed verbatim). */
  verdict?: string | null;
  size?: number;
}

const START = -135;
const SWEEP = 270;

export function defaultToneFor(ratio: number | null): GaugeTone {
  if (ratio === null) return "neu";
  if (ratio >= 0.75) return "bad";
  if (ratio >= 0.5) return "warn";
  return "pos";
}

export function Gauge({ label, value, min = 0, max = 1, format, tone, verdict, size = 132 }: GaugeProps) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 12;
  const ratio = clampRatio(value === null || value === undefined ? null : (value - min) / (max - min));
  const angle = START + (ratio ?? 0) * SWEEP;
  const cls = tone ?? defaultToneFor(ratio);
  const readout =
    value === null || value === undefined
      ? "—"
      : format
        ? format(value)
        : Number.isInteger(value)
          ? String(value)
          : value.toFixed(2);
  return (
    <svg className="viz" viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${readout}`}>
      <path className="gauge-track" d={arcPath(cx, cy, r, START, START + SWEEP)} fill="none" strokeWidth={7} strokeLinecap="round" />
      {ratio !== null && <path className={`gauge-value ${cls}`} d={arcPath(cx, cy, r, START, angle)} fill="none" />}
      <text className="gauge-center" x={cx} y={cy + 2} textAnchor="middle" fontSize={size * 0.17}>
        {readout}
      </text>
      <text className="gauge-label" x={cx} y={cy + size * 0.19} textAnchor="middle">
        {label.toUpperCase()}
      </text>
      {verdict && (
        <text className="gauge-label" x={cx} y={cy + size * 0.31} textAnchor="middle" style={{ fill: "var(--text-dim)" }}>
          {verdict.toUpperCase()}
        </text>
      )}
    </svg>
  );
}
