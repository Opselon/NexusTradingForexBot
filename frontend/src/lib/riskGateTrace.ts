/**
 * riskGateTrace — derivation for the last-proposal gate matrix.
 *
 * WHY THIS EXISTS: `proposal.risk_checks` (signals/policy.py) is an EVIDENCE
 * record of flat scalar metric PAIRS (value + limit in sibling keys), NOT a
 * gate-verdict map. A `risk_checks` entry like `"rr": 1, "min_rr": 2.2`
 * carries no `passed`/`allowed` boolean, so the older gateVerdict() funnels
 * honestly reported every row as UNKNOWN — the whole matrix read "0 pass ·
 * 0 fail · 20 unknown" while the engine had real, comparable evidence.
 *
 * This layer pairs the sibling keys and compares them. It still invents no
 * verdict words: every pass/fail is the arithmetic restatement of two
 * backend-supplied numbers (`value` vs its declared limit), and every entry
 * that cannot be paired is reported UNKNOWN, never FAIL.
 *
 * Direction is part of the evidence, not a heuristic here: for every pair,
 * the producer's own gate operator is known (>= floor or <= ceiling). A pair
 * whose limit is absent is "no comparable limit" — the value is still shown,
 * but never coloured as satisfied.
 *
 * Erasable TypeScript only (no enums / namespaces / parameter properties):
 * imported directly by a node:test file that relies on type stripping.
 */

import type { RiskChecks } from "@/types/domain";

export type GateVerdict = "pass" | "fail" | "unknown";

/** Every gate operator the backend evidence model actually uses. */
export type GateDirection = "ge" | "le";

export interface GateEvidenceRow {
  /** Backend key name, verbatim. */
  name: string;
  verdict: GateVerdict;
  /** The metric value the decision actually saw. */
  value: number | null;
  /** The limit the producer compares that value against. */
  limit: number | null;
  /** Comparison direction of the producer's own gate. */
  direction: GateDirection;
  /** Units/meter label for the value column, or null when unknown. */
  unit: string | null;
  /** Backend-supplied string context echoed verbatim (no fallback text). */
  detail: string | null;
}

export interface GateEvidenceResult {
  pass: number;
  fail: number;
  unknown: number;
  total: number;
  rows: GateEvidenceRow[];
}

/**
 * The canonical value→limit pairs (backend keys, signals/policy.py
 * risk_checks_dict). Keyed by the VALUE key; `limitKey` is its sibling.
 * `direction` mirrors the producer's gate operator at the decision site:
 *   ge — a FLOOR: value >= limit passes (zone quality, RR, confidence).
 *   le — a CEILING: value <= limit passes (spread cost ratios).
 */
interface PairSpec {
  limitKey: string;
  direction: GateDirection;
  unit: string;
}

const PAIR_SPECS: Record<string, PairSpec> = {
  zone_quality: { limitKey: "min_zone_quality", direction: "ge", unit: "" },
  rr: { limitKey: "min_rr", direction: "ge", unit: "" },
  model_confidence: { limitKey: "effective_threshold", direction: "ge", unit: "" },
  spread_atr_ratio: { limitKey: "max_spread_atr_ratio", direction: "le", unit: " × ATR" },
  spread_tp_ratio: { limitKey: "max_spread_pct_of_tp", direction: "le", unit: " × TP" },
  spread_session_percentile_value: { limitKey: "spread_session_percentile", direction: "le", unit: " $ spread" },
  spread_usd: { limitKey: "max_spread_points", direction: "le", unit: " $" },
};

/**
 * Threshold-metric pairs: the value is a gate INPUT and the limit is the
 * gate's own configured threshold — the same >= floor semantics as the
 * metrics above, but these belong to the confidence-gate arithmetic
 * (base − penalty + adjustment = effective) rather than to a comparison the
 * producer stamps as a single ratio. They render in the matrix as evidence
 * of how the effective threshold was built, not as pass/fail gates.
 */
const THRESHOLD_COMPONENTS: Record<string, string> = {
  base_threshold: "confidence gate floor before regime/mode adjustments",
  range_penalty: "confidence subtracted in a ranging regime",
  survival_mode_adjustment: "confidence added under survival mode",
  effective_threshold: "threshold the model confidence was compared against",
};

/** Unit label for the known non-numeric evidence keys, verbatim semantics. */
const DETAIL_KEYS = new Set([
  "confidence_source",
  "expected_symbol",
  "expected_magic",
  "tp_distance_usd",
]);

const NUM_RE = /^-?\d+(\.\d+)?$/;

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function asNumber(v: unknown): number | null {
  if (isFiniteNumber(v)) return v;
  if (typeof v === "string" && NUM_RE.test(v.trim())) {
    const n = Number(v.trim());
    if (isFiniteNumber(n)) return n;
  }
  return null;
}

/**
 * Compare a value against its limit in the producer's own direction.
 *
 * Returns "unknown" — never "fail" — when either side is missing, non-finite,
 * or when a ceiling has a non-positive limit (which would divide-by-zero or
 * read an unsatisfied budget as headroom). Absence of evidence is not
 * evidence of failure; this is the whole point of the layer.
 */
export function comparePair(
  value: number | null,
  limit: number | null,
  direction: GateDirection,
): GateVerdict {
  if (!isFiniteNumber(value) || !isFiniteNumber(limit)) return "unknown";
  if (direction === "le" && limit <= 0) return "unknown";
  return direction === "ge" ? (value >= limit ? "pass" : "fail") : value <= limit ? "pass" : "fail";
}

/** Backend string context echoed verbatim; blank/non-string collapses to null. */
function detailOf(checks: RiskChecks, key: string, raw: unknown): string | null {
  if (key === "model_confidence") {
    const src = checks["confidence_source"];
    if (typeof src === "string" && src.trim() !== "") return `confidence_source ${src.trim()}`;
  }
  if (key === "expected_magic" || key === "expected_symbol") {
    const other = checks[key === "expected_magic" ? "expected_symbol" : "expected_magic"];
    if (typeof other === "string" && other.trim() !== "") return `identity pair: ${other.trim()}`;
  }
  if (typeof raw === "string" && raw.trim() !== "" && DETAIL_KEYS.has(key)) return raw.trim();
  return null;
}

/**
 * Last-proposal `risk_checks` -> gate evidence rows.
 *
 * The evidence model is flat: a VALUE key and its LIMIT key are SIBLINGS in
 * the same dict, so the pairing is structural (not inferred from magnitudes).
 * Any key that is not part of a declared pair, or whose partner is absent,
 * yields an UNKNOWN row whose value is still displayed — the operator sees
 * the number the decision saw, without a verdict invented for it.
 */
export function gateEvidence(checks: RiskChecks | null | undefined): GateEvidenceResult {
  const rows: GateEvidenceRow[] = [];
  if (!checks || typeof checks !== "object") return { pass: 0, fail: 0, unknown: 0, total: 0, rows };
  const seen = new Set<string>();

  // Pass 1: declared value→limit pairs.
  for (const [valueKey, spec] of Object.entries(PAIR_SPECS)) {
    if (!(valueKey in checks)) continue;
    seen.add(valueKey);
    seen.add(spec.limitKey);
    const value = asNumber(checks[valueKey]);
    const limit = asNumber(checks[spec.limitKey]);
    rows.push({
      name: valueKey,
      verdict: comparePair(value, limit, spec.direction),
      value,
      limit,
      direction: spec.direction,
      unit: spec.unit,
      detail: detailOf(checks, valueKey, checks[valueKey]),
    });
  }

  // Pass 2: threshold components (evidence only — no verdict, by design).
  for (const key of Object.keys(THRESHOLD_COMPONENTS)) {
    if (key in checks && !seen.has(key)) {
      seen.add(key);
      rows.push({
        name: key,
        verdict: "unknown",
        value: asNumber(checks[key]),
        limit: null,
        direction: "ge",
        unit: null,
        detail: THRESHOLD_COMPONENTS[key] ?? null,
      });
    }
  }

  // Pass 3: every remaining key is reported UNKNOWN, value still shown.
  for (const [key, raw] of Object.entries(checks)) {
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      name: key,
      verdict: "unknown",
      value: asNumber(raw),
      limit: null,
      direction: "ge",
      unit: null,
      detail: detailOf(checks, key, raw),
    });
  }

  const pass = rows.filter((r) => r.verdict === "pass").length;
  const fail = rows.filter((r) => r.verdict === "fail").length;
  return { pass, fail, unknown: rows.length - pass - fail, total: rows.length, rows };
}

/** Human label for a gate row's verdict (used by the matrix + funnel). */
export function verdictWord(verdict: GateVerdict): string {
  return verdict === "pass" ? "PASS" : verdict === "fail" ? "FAIL" : "UNKNOWN";
}

/** Operator symbol for the limit column, from the producer's own gate. */
export function directionWord(direction: GateDirection): string {
  return direction === "ge" ? "min" : "max";
}
