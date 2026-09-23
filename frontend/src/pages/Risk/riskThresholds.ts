/**
 * PURPOSE:  Threshold constants + pure colour/age math behind the Risk page's
 *           utilization gauges, matrix cell tints and freshness chips.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: lib/riskVizMath.limitUtilization (value-vs-limit arithmetic only)
 * PROVIDES: SOFT_UTIL/HARD_UTIL/FRESH_* constants, RskTone, utilTone,
 *           pressureOf, rampStyle, freshnessTone, ageWord, RiskLimitRow
 * INVARIANTS: colours are not verdicts — every number here is arithmetic on
 *             backend-supplied values; a missing side resolves to `unknown`
 *             and may never render in the "ok" colour.
 * EXTEND:   new thresholds belong here as named, commented constants — never
 *           as magic numbers inside a component.
 */

import { limitUtilization } from "@/lib/riskVizMath";

/** Lane token for any local storage key this page may add later. */
export const RSK_LS_PREFIX = "w5.risk.";

/**
 * SOFT threshold — 80 % of the backend limit. At or below it the cell stays
 * untinted ("ok" colour for the arc stroke); above it the tint ramps with
 * depth. This mirrors the meter convention RiskPage already used
 * (`util > 0.8 ? "warn" : "ok"`), so the ramp does not move any goalposts.
 */
export const SOFT_UTIL = 0.8;

/**
 * HARD threshold — 1.00 = the backend limit itself. Past this the ramp
 * switches to the "down" colour; deeper past the limit = stronger tint
 * (capped at 2× the limit, where the tint saturates).
 */
export const HARD_UTIL = 1.0;

/**
 * Freshness thresholds for probe ages. The three risk queries refetch every
 * 10 s, so an age above 30 s already means "missed at least two refetches"
 * (warn) and above 120 s means the section is not being fed at all (stale).
 * These gate COLOUR ONLY — the chip always prints the real age.
 */
export const FRESH_WARN_SEC = 30;
export const FRESH_STALE_SEC = 120;

/** Colour ramp steps: ok → warn → down → unknown (never "ok" without proof). */
export type RskTone = "ok" | "warn" | "down" | "unknown";

/** Comparison direction of the backend's own gate (le = ceiling, ge = floor). */
export type RskDirection = "le" | "ge";

/** One value-vs-limit pair the page renders (all fields backend-supplied). */
export interface RiskLimitRow {
  id: string;
  /** Operator-facing label (stable string, no payload invention). */
  label: string;
  /** Payload paths the numbers came from — echoed in tooltips, verbatim. */
  field: string;
  /** Backend-measured value; null = absent, never 0. */
  value: number | null;
  /** Backend-configured limit; null = absent, never "unlimited". */
  limit: number | null;
  unit: string;
  digits: number;
  direction: RskDirection;
}

/**
 * Gauge/cell tone from utilization (value ÷ limit, both backend numbers).
 *  null  → unknown (hatched/indeterminate — never coloured satisfied)
 *  ≤0.80 → ok      (SOFT_UTIL: within the soft threshold)
 *  ≤1.00 → warn    (inside the limit, but close enough to tint)
 *  >1.00 → down    (past the backend limit — arithmetic, not a verdict word)
 */
export function utilTone(util: number | null): RskTone {
  if (util === null || !Number.isFinite(util)) return "unknown";
  if (util > HARD_UTIL) return "down";
  if (util > SOFT_UTIL) return "warn";
  return "ok";
}

/**
 * "Depth past the soft threshold" as a single ratio for the colour ramp:
 *  null          → no comparable pair (missing value/limit, or a non-positive
 *                   denominator) — caller renders no tint at all
 *  ≤ 1.00        → at/below the backend limit (1.00 = exactly at the limit)
 *  > 1.00        → past the limit (1.25 = 25 % over)
 *
 * Direction-aware: for a floor gate (`ge`, e.g. min_rr) the pressure is
 * limit/value, so "deeper" always means "further past the backend's own
 * threshold" in the failing direction.
 */
export function pressureOf(value: number | null, limit: number | null, direction: RskDirection): number | null {
  if (value === null || limit === null) return null;
  if (!Number.isFinite(value) || !Number.isFinite(limit)) return null;
  if (direction === "le") return limitUtilization(value, limit);
  // Floor: value must be a positive number to divide by; 0/negative floors
  // would read as infinite depth, so they stay unproven (null).
  if (value <= 0) return null;
  const p = limit / value;
  return Number.isFinite(p) ? p : null;
}

/**
 * Cell tint from `pressureOf`. Alpha gradient over the theme's own accent
 * colours — the only hex/rgb literals this lane writes (documented here as
 * required by the contract): rgba(235,161,63) = --amber, rgba(242,86,77) =
 * --red. Below SOFT_UTIL there is no tint at all; between SOFT and HARD the
 * amber alpha ramps 0.10 → 0.45; past HARD the red alpha ramps 0.30 → 0.65
 * over one full limit of depth (capped at 2× the limit).
 */
export function rampStyle(pressure: number | null): { backgroundColor?: string } {
  if (pressure === null || !Number.isFinite(pressure) || pressure <= SOFT_UTIL) return {};
  if (pressure <= HARD_UTIL) {
    const t = (pressure - SOFT_UTIL) / (HARD_UTIL - SOFT_UTIL);
    return { backgroundColor: `rgba(235, 161, 63, ${(0.1 + 0.35 * t).toFixed(3)})` };
  }
  const t = Math.min(1, (pressure - HARD_UTIL) / HARD_UTIL);
  return { backgroundColor: `rgba(242, 86, 77, ${(0.3 + 0.35 * t).toFixed(3)})` };
}

/** Freshness tone from an age in seconds (thresholds documented above). */
export function freshnessTone(ageSec: number): RskTone {
  if (!Number.isFinite(ageSec)) return "unknown";
  if (ageSec > FRESH_STALE_SEC) return "down";
  if (ageSec > FRESH_WARN_SEC) return "warn";
  return "ok";
}

/**
 * Relative age for the freshness chip: "updated 42s ago" / "updated 3m ago" /
 * "updated 2h ago". Returns null for a non-finite age so the caller renders
 * NO chip instead of an invented "0s ago" (an unproven zero reads as fresh).
 */
export function ageWord(ageSec: number | null | undefined): string | null {
  if (ageSec === null || ageSec === undefined || !Number.isFinite(ageSec) || ageSec < 0) return null;
  if (ageSec < 90) return `updated ${Math.floor(ageSec)}s ago`;
  if (ageSec < 7200) return `updated ${Math.floor(ageSec / 60)}m ago`;
  return `updated ${Math.floor(ageSec / 3600)}h ago`;
}
