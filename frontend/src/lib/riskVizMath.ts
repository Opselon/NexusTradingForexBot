/**
 * riskVizMath — pure derivation layer for the pro risk/guardian visuals.
 *
 * SAFETY CONTRACT (pinned by tests/js/pro_risk_viz.test.mjs):
 *   1. Every verdict WORD shown by the risk visuals is either a backend string
 *      echoed verbatim or "UNKNOWN". Nothing here ever manufactures SAFE,
 *      BLOCKED, HALTED, WITHIN LIMIT, BREACHED, or any other verdict.
 *   2. A gate whose payload carries neither `passed` nor `allowed` is UNKNOWN —
 *      never FAIL. Absence of evidence is not evidence of failure.
 *   3. A missing limit is not a satisfied limit: `limitUtilization` returns
 *      null and the caller must render "no backend limit", never 0 % or 100 %.
 *   4. Tones are colours, not claims. They may only be derived from the value
 *      the backend actually sent (counter > 0, value > backend limit). An
 *      unproven value can never resolve to the "clean" tone.
 *
 * Erasable TypeScript only (no enums / namespaces / parameter properties): this
 * module is imported directly by a node:test file that relies on type stripping.
 */

import type { RiskCheckValue, RiskChecks } from "@/types/domain";

/** The one word this layer is allowed to invent. */
export const UNKNOWN_WORD = "UNKNOWN";

export type GateVerdict = "pass" | "fail" | "unknown";

export type CounterTone = "ok" | "warn" | "bad";

export interface GateRow {
  name: string;
  verdict: GateVerdict;
  /** Backend reason text, verbatim; null when the payload carried none. */
  reason: string | null;
}

export interface GateFunnelResult {
  pass: number;
  fail: number;
  unknown: number;
  total: number;
  rows: GateRow[];
}

export interface ExposureTotals {
  /** How many symbol rows the backend reported. */
  symbols: number;
  positions: number | null;
  volume: number | null;
  profit: number | null;
}

/** True only for a real, finite JS number (guards null/undefined/NaN/Infinity). */
export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Backend status string echoed as a word; missing/blank collapses to UNKNOWN. */
export function backendWord(word: string | null | undefined): string {
  const trimmed = typeof word === "string" ? word.trim() : "";
  return trimmed === "" ? UNKNOWN_WORD : trimmed.toUpperCase();
}

/** Visual clamp for bar/arc fractions. Not a verdict — geometry only. */
export function clamp01(value: number | null): number | null {
  if (!isFiniteNumber(value)) return null;
  return Math.max(0, Math.min(1, value));
}

/**
 * One `risk_checks` entry -> verdict, from backend booleans ONLY.
 *
 * Accepted truth forms (all real backend shapes, see api_v1/risk.py
 * `sanitize_config`):
 *   true / false                        — bare boolean gate
 *   { passed: true|false, ... }         — gate result object
 *   { allowed: true|false, ... }        — allowance-style gate (news/liquidity)
 *   { value, limit, reason } with NO boolean, null, or a non-boolean
 *
 * `passed === false` IS a backend statement, so it is a FAIL. A payload that
 * says nothing (both booleans absent, null, or non-boolean) is UNKNOWN — this
 * is the whole point of the function: a missing gate must never be read as a
 * failed gate, and a failed gate must never be read as missing.
 *
 * Precedence when both booleans are present mirrors RiskPage's existing
 * `ok ? PASS : bad ? FAIL : "—"` ordering, so the funnel and the table cannot
 * disagree about the same payload.
 */
export function gateVerdict(raw: unknown): GateVerdict {
  if (raw === true) return "pass";
  if (raw === false) return "fail";
  if (typeof raw !== "object" || raw === null) return "unknown";
  const entry = raw as RiskCheckValue;
  if (entry.passed === true || entry.allowed === true) return "pass";
  if (entry.passed === false || entry.allowed === false) return "fail";
  return "unknown";
}

/** Backend `reason` text if the payload carries one — never a fallback string. */
function gateReason(raw: unknown): string | null {
  if (typeof raw !== "object" || raw === null) return null;
  const reason = (raw as RiskCheckValue).reason;
  return typeof reason === "string" && reason.trim() !== "" ? reason : null;
}

/**
 * Last-proposal gate trace -> funnel counts.
 *
 * `null` / `{}` / a non-object payload all yield an EMPTY funnel (total 0):
 * "no gates recorded", which is a different claim from "N gates unknown" and is
 * rendered differently upstream.
 */
export function gateFunnel(checks: RiskChecks | null | undefined): GateFunnelResult {
  const rows: GateRow[] = [];
  let pass = 0;
  let fail = 0;
  let unknown = 0;
  if (checks && typeof checks === "object") {
    for (const [name, raw] of Object.entries(checks)) {
      const verdict = gateVerdict(raw);
      if (verdict === "pass") pass += 1;
      else if (verdict === "fail") fail += 1;
      else unknown += 1;
      rows.push({ name, verdict, reason: gateReason(raw) });
    }
  }
  return { pass, fail, unknown, total: rows.length, rows };
}

/**
 * value / limit as a fraction (0.5 = half of the backend limit consumed).
 *
 * null when EITHER side is missing or unusable — including `limit <= 0`, which
 * would otherwise produce Infinity and read as "budget all spent". Callers must
 * treat null as "no comparable limit supplied" and render the actual alone.
 */
export function limitUtilization(
  value: number | null | undefined,
  limit: number | null | undefined,
): number | null {
  if (!isFiniteNumber(value) || !isFiniteNumber(limit)) return null;
  if (limit <= 0) return null;
  return value / limit;
}

/**
 * Aggregate the backend's `exposure.by_symbol` rows.
 *
 * Returns null when the backend supplied no rows — an absent exposure block is
 * not "zero exposure". Within a present block, a field whose rows carried no
 * finite number stays null rather than collapsing to 0.
 */
export function exposureTotals(
  bySymbol: Record<string, { volume?: number; profit?: number; positions?: number }> | null | undefined,
): ExposureTotals | null {
  if (!bySymbol || typeof bySymbol !== "object") return null;
  const entries = Object.entries(bySymbol);
  if (entries.length === 0) return null;
  let volume: number | null = null;
  let profit: number | null = null;
  let positions: number | null = null;
  for (const [, row] of entries) {
    if (row && typeof row === "object") {
      if (isFiniteNumber(row.volume)) volume = (volume ?? 0) + row.volume;
      if (isFiniteNumber(row.profit)) profit = (profit ?? 0) + row.profit;
      if (isFiniteNumber(row.positions)) positions = (positions ?? 0) + row.positions;
    }
  }
  return { symbols: entries.length, positions, volume, profit };
}

/**
 * Counter -> colour tone. Derived from `value > 0` ONLY — no severity ladder is
 * invented, no threshold is assumed:
 *   0      -> 'ok'    (the backend counted nothing)
 *   > 0    -> 'bad'   (the backend counted something; drop-lines are facts)
 *   other  -> 'warn'  (missing/NaN/non-number: an unproven value never gets the
 *                      clean colour; the WORD rendered beside it is UNKNOWN)
 */
export function counterTone(n: number | null | undefined): CounterTone {
  if (!isFiniteNumber(n)) return "warn";
  if (n > 0) return "bad";
  return "ok";
}

/**
 * Actual-vs-backend-limit tone (drawdown, margin usage — metrics where a BIGGER
 * value is worse and the backend supplied the maximum). The comparison is
 * arithmetic on two backend numbers — it is not a verdict word. A null limit
 * forces 'warn': no budget was supplied, so nothing may be coloured as safe.
 */
export function limitTone(utilization: number | null): CounterTone {
  if (!isFiniteNumber(utilization)) return "warn";
  if (utilization > 1) return "bad";
  return "ok";
}

/**
 * Ceiling-metric pair (higher-is-worse value against the backend's own max) ->
 * tone. Returns null when either side is missing so the caller must fall back
 * to 'warn' rather than painting a number that has no budget to judge it.
 */
export function ceilingPairTone(value: number | null | undefined, limit: number | null | undefined): CounterTone | null {
  const util = limitUtilization(value, limit);
  if (util === null) return null;
  return util > 1 ? "bad" : "ok";
}

/**
 * Floor-metric tone (margin level against the backend's own MINIMUM threshold —
 * direction matters: a margin level ABOVE the backend floor is the clean side).
 * Still only arithmetic against a backend-supplied number; no threshold and no
 * stop-out level is assumed here — a missing threshold returns null so the arc
 * renders indeterminate instead of green.
 */
export function floorPairTone(value: number | null | undefined, threshold: number | null | undefined): CounterTone | null {
  if (!isFiniteNumber(value) || !isFiniteNumber(threshold)) return null;
  return value >= threshold ? "ok" : "bad";
}

