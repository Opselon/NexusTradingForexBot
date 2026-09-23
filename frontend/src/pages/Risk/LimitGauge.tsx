/**
 * PURPOSE:  Radial limit-utilization gauge (inline SVG arc, stroke-dasharray)
 *           for one backend value vs its backend limit.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: ./riskThresholds (RiskLimitRow, utilTone, SOFT/HARD thresholds),
 *           lib/riskVizMath.limitUtilization, lib/format
 * PROVIDES: <LimitGauge row={…} />
 * INVARIANTS: arc geometry only — a null limit renders an indeterminate
 *             (dashed, empty) ring with "no backend limit"; the centre never
 *             shows a percentage that was not computed from two payload
 *             numbers, and never a verdict word.
 * EXTEND:   ceiling metrics only (higher = worse). Floor metrics such as
 *           margin level keep using the pro kit's MarginArc.
 */

import { limitUtilization } from "@/lib/riskVizMath";
import { formatNumber } from "@/lib/format";
import { utilTone, type RskTone, type RiskLimitRow } from "./riskThresholds";

/** Ring geometry: r=40 → circumference ≈ 251.33, rotated so 0% starts at 12 o'clock. */
const R = 40;
const CIRC = 2 * Math.PI * R;

/** Centre type scales with the string so long numbers never spill over the ring. */
function centreSize(text: string): number {
  if (text.length > 8) return 10;
  if (text.length > 6) return 12;
  return 15;
}

export function LimitGauge({ row }: { row: RiskLimitRow }) {
  const util = limitUtilization(row.value, row.limit);
  const tone: RskTone = utilTone(util);
  const hasValue = row.value !== null;
  const hasLimit = row.limit !== null && util !== null;

  const centre = hasValue ? formatNumber(row.value, row.digits) : "—";
  const centreWithUnit = hasValue && row.unit ? `${centre}${row.unit}` : centre;
  const pct = util === null ? null : Math.round(util * 100);
  // Sweep geometry: clamp01 — over-limit still draws a full ring (the tint,
  // the centre value and the caption carry the "past the limit" depth).
  const sweep = util === null ? null : Math.max(0, Math.min(1, util));
  const shown = sweep === null ? 0 : sweep * CIRC;

  const caption = hasLimit
    ? `${pct}% of the backend limit (${formatNumber(row.limit, row.digits)}${row.unit})`
    : hasValue
      ? row.limit === null
        ? "no backend limit in payload — ring indeterminate, never satisfied"
        : "no comparable limit — ring indeterminate, never satisfied"
      : "no backend value in payload — nothing measured";

  return (
    <section
      className={`rsk-gauge tone-${tone}`}
      aria-label={`${row.label}: ${hasValue ? `${centreWithUnit}` : "unknown"}${pct !== null ? `, ${pct}% of backend limit` : ", no backend limit"}`}
      title={`${row.label} — ${row.field}`}
    >
      <svg viewBox="0 0 120 120" width={118} height={118} role="img" aria-hidden="true">
        <circle className="rsk-gauge__track" cx="60" cy="58" r={R} fill="none" strokeWidth={8} />
        <circle
          className="rsk-gauge__arc"
          cx="60"
          cy="58"
          r={R}
          fill="none"
          strokeWidth={8}
          strokeLinecap="round"
          strokeDasharray={`${shown} ${CIRC}`}
          transform="rotate(-90 60 58)"
          opacity={sweep === null ? 0 : 1}
        />
        <text className="rsk-gauge__value" x="60" y="57" textAnchor="middle" fontSize={centreSize(centreWithUnit)}>
          {centreWithUnit}
        </text>
        <text className="rsk-gauge__pct" x="60" y="73" textAnchor="middle">
          {pct === null ? "no limit" : `${pct}% of limit`}
        </text>
      </svg>
      <span className="rsk-gauge__lab">{row.label}</span>
      <span className={`rsk-gauge__cap${hasLimit ? "" : " indeterminate"}`}>{caption}</span>
    </section>
  );
}
