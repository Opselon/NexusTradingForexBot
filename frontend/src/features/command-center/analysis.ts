/**
 * Command Center: analysis derivations over BACKEND payloads (pure).
 *
 * Contract (viz kit rule): every number here is a count/ratio of values the
 * backend already reported — nothing is smoothed, imputed, extrapolated or
 * given a verdict word. Missing data stays missing and is rendered as an
 * explicit gap/UNKNOWN, never as a fabricated zero:
 *  - a gate absent from evaluation_metrics is DROPPED (not "0% pass")
 *  - a rate over total=0 is null (UNKNOWN), not 0.0
 *  - a non-finite sample is counted as missing, not clamped into a bucket
 *
 * Scope discipline (backend docstring, command_center_routes.evaluation_metrics):
 * evaluation counts are transient pipeline TELEMETRY and must never be mixed
 * with by_lifecycle/terminal (persistent lifecycle) counts. The funnel uses
 * only evaluation_pipeline; the donut uses only lifecycle/terminal.
 */

import { arr, num, obj, str, type CcFleetDto, type CcOverviewDto, type Row } from "./model";
import { EVAL_GATES } from "./model";

/* ------------------------------------------------------------------ gates */

export interface GateOutcome {
  gate: string;
  pass: number;
  fail: number;
  running: number;
  inconclusive: number;
  total: number;
  /** null = UNKNOWN (gate never tested) — never a fabricated 0.0. */
  pass_rate: number | null;
  fail_rate: number | null;
}

/** Per-gate pass/fail outcomes, pipeline order, absent gates dropped. */
export function gateOutcomes(o: CcOverviewDto | undefined): GateOutcome[] {
  const metrics = obj(o?.evaluation_metrics);
  const out: GateOutcome[] = [];
  for (const gate of EVAL_GATES) {
    const m = obj(metrics[gate]);
    if (Object.keys(m).length === 0) continue;
    const total = num(m.total) ?? 0;
    const tested = total > 0;
    out.push({
      gate,
      pass: num(m.pass) ?? 0,
      fail: num(m.fail) ?? 0,
      running: num(m.running) ?? 0,
      inconclusive: num(m.inconclusive) ?? 0,
      total,
      pass_rate: tested ? (num(m.pass_rate) ?? 0) : null,
      fail_rate: tested ? (num(m.fail_rate) ?? 0) : null,
    });
  }
  return out;
}

/* ---------------------------------------------------------------- funnel */

export interface FunnelStage {
  label: string;
  /** reached THIS stage (passed the gate / completed the step). */
  value: number;
  /** entered the stage; null = no distinct base (caption shows raw count). */
  base: number | null;
  /** value/base when both known, else null (UNKNOWN — never 0). */
  conversion: number | null;
  tone: "dim" | "pos" | "neg" | "warn";
}

/**
 * Evaluation-pipeline drop-off funnel (evaluation_pipeline ONLY — one source,
 * so stages are comparable with each other).
 */
export function pipelineFunnel(o: CcOverviewDto | undefined): FunnelStage[] {
  const p = obj(o?.evaluation_pipeline);
  const registry = num(o?.total_strategies) ?? null;
  const g = (k: string): number => num(p[k]) ?? 0;
  const stage = (label: string, value: number, base: number | null, tone: FunnelStage["tone"]): FunnelStage => ({
    label,
    value,
    base,
    conversion: base !== null && base > 0 ? value / base : null,
    tone,
  });
  const wfTested = g("WALK_FORWARD_TESTED");
  const oosTested = g("OOS_TESTED");
  const robTested = g("ROBUSTNESS_TESTED");
  const stages = [
    stage("registry", registry ?? 0, registry, "dim"),
    stage("backtest run", g("BACKTEST_RUN"), registry, "dim"),
    stage("walk-forward passed", g("WALK_FORWARD_PASSED"), wfTested, "pos"),
    stage("oos passed", g("OOS_PASSED"), oosTested, "pos"),
    stage("robustness passed", g("ROBUSTNESS_PASSED"), robTested, "pos"),
    stage("scored", g("SCORING_COMPLETED"), registry, "dim"),
  ];
  return stages
    // A stage nobody entered (value AND base both 0) was never reported —
    // drop it rather than draw a fabricated zero row. value=0 with base>0
    // ("tested but none passed") is a real fact and stays.
    .filter((s) => !(s.value === 0 && (!s.base || s.base === 0)))
    // Backend-inconsistent (passed > tested) → rate UNKNOWN, not >100%.
    .map((s) => (s.base !== null && s.value > s.base ? { ...s, conversion: null } : s));
}

/* ------------------------------------------------------------ histogram */

export interface Histogram {
  buckets: Array<{ lo: number; hi: number; count: number }>;
  max: number;
  /** samples that were null/undefined/NaN (rendered as an explicit gap). */
  missing: number;
  /** samples strictly below lo / above hi (kept visible, never clamped). */
  below: number;
  above: number;
  total: number;
}

/** Equal-width bucketing over raw backend samples. Pure presentation. */
export function histogram(
  values: Array<number | null | undefined>,
  opts: { lo: number; hi: number; bins: number },
): Histogram {
  const { lo, hi, bins } = opts;
  const width = (hi - lo) / bins;
  const buckets = Array.from({ length: bins }, (_, i) => ({ lo: lo + i * width, hi: lo + (i + 1) * width, count: 0 }));
  let missing = 0;
  let below = 0;
  let above = 0;
  let total = 0;
  for (const raw of values) {
    const v = num(raw);
    total += 1;
    if (v === null) {
      missing += 1;
      continue;
    }
    if (v < lo) {
      below += 1;
      continue;
    }
    if (v > hi) {
      above += 1;
      continue;
    }
    const idx = Math.min(bins - 1, Math.floor((v - lo) / width));
    const b = buckets[idx];
    if (b) b.count += 1;
  }
  return { buckets, max: Math.max(1, ...buckets.map((b) => b.count)), missing, below, above, total };
}

/* ------------------------------------------------------- evidence rings */

export interface EvidenceRings {
  /** buckets[0..4] = strategies with N PASS evidence statuses. */
  buckets: number[];
  /** rows without an evidence object (rendered as its own gap row). */
  missing: number;
  total: number;
}

const RING_KEYS = ["backtest_status", "walkforward_status", "oos_status", "robustness_status"] as const;

/** Distribution of PASS-evidence depth (0..4) across the fleet. */
export function evidenceRings(fleet: CcFleetDto | undefined): EvidenceRings {
  const rows = arr(fleet?.rows);
  const buckets = [0, 0, 0, 0, 0];
  let missing = 0;
  for (const r of rows) {
    const raw = r.evidence;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      missing += 1;
      continue;
    }
    const ev = raw as Row;
    let pass = 0;
    for (const k of RING_KEYS) if (str(ev[k]) === "PASS") pass += 1;
    buckets[pass] = (buckets[pass] ?? 0) + 1;
  }
  return { buckets, missing, total: rows.length };
}

/* ---------------------------------------------------------- eligibility */

export interface EligibilityCount {
  state: string;
  count: number;
}

const ELIGIBILITY_ORDER = ["YES", "SHADOW_ONLY", "CONDITIONAL", "BLOCKED", "UNKNOWN"];

/** Fleet eligibility census (backend eligibility_state verbatim). */
export function eligibilityCounts(fleet: CcFleetDto | undefined): EligibilityCount[] {
  const rows = arr(fleet?.rows);
  const counts = new Map<string, number>();
  for (const r of rows) {
    const s = str(r.eligibility_state) ?? "UNKNOWN";
    counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  const known = ELIGIBILITY_ORDER.filter((s) => counts.has(s)).map((s) => ({ state: s, count: counts.get(s) ?? 0 }));
  const others = [...counts.entries()]
    .filter(([s]) => !ELIGIBILITY_ORDER.includes(s))
    .sort((a, b) => b[1] - a[1])
    .map(([state, count]) => ({ state, count }));
  return [...known, ...others];
}

/* ------------------------------------------------------------- lifecycle */

export interface LifecycleSegment {
  key: string;
  count: number;
  terminal: boolean;
  /** count / sum(count) — presentation share of the reported census. */
  share: number;
}

const PIPELINE_KEYS = [
  "DISCOVERED",
  "BACKTESTING",
  "VALIDATING",
  "OOS_TESTING",
  "ROBUSTNESS_TESTING",
  "VALIDATED",
  "SHADOW",
  "ACTIVE",
];
const TERMINAL_KEYS = ["REJECTED", "DEGRADED", "RETIRED"];

/** Lifecycle + terminal census for the donut (persistent lifecycle scope). */
export function lifecycleSegments(o: CcOverviewDto | undefined): LifecycleSegment[] {
  const life = obj(o?.by_lifecycle);
  const term = obj(o?.terminal);
  const segs: LifecycleSegment[] = [];
  for (const k of PIPELINE_KEYS) {
    const c = num(life[k]) ?? 0;
    if (c > 0) segs.push({ key: k, count: c, terminal: false, share: 0 });
  }
  for (const k of TERMINAL_KEYS) {
    const c = num(term[k]) ?? 0;
    if (c > 0) segs.push({ key: k, count: c, terminal: true, share: 0 });
  }
  const sum = segs.reduce((a, s) => a + s.count, 0);
  for (const s of segs) s.share = sum > 0 ? s.count / sum : 0;
  return segs;
}

/* ------------------------------------------------------------ misc views */

/** Fleet sample-count histogram input (0..max over reported counts). */
export function fleetConfidenceSamples(fleet: CcFleetDto | undefined): Array<number | null> {
  return arr(fleet?.rows).map((r: Row) => num(r.confidence));
}

export function fleetHealthSamples(fleet: CcFleetDto | undefined): Array<number | null> {
  return arr(fleet?.rows).map((r: Row) => num(r.health_final));
}
