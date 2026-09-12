/**
 * mlVizMath — pure derivation helpers for the ML/model pro-visualization suite
 * (LANE ml-viz, ALT-UI-PRO 3/5). Presentation math ONLY: no fetching, no
 * state decisions, no JSX. Every helper is backend-truth:
 *
 *   - a value the backend did not send stays `null` and renders UNKNOWN;
 *   - a status/class string is passed through VERBATIM (never normalized to
 *     'ok', never translated into a claim);
 *   - bar geometry may clamp a fraction for pixels, but the displayed number
 *     is always the raw backend value.
 *
 * Backend sources (verified in this repo at HEAD):
 *   web/server.py            `features_payload` -> {index,name,value,status}
 *                            statuses VALID | NAN | INF | NON_NUMERIC | UNAVAILABLE
 *   web/server.py model_meta -> latency_ms, latency_breakdown, model_forward_ms,
 *                              feature_ms, e2e_ms
 *   features/latency_tracer.py to_dict() -> feature_ms / scaling_ms / tensor_ms /
 *                              model_ms / postprocess_ms / decision_ms / queue_ms /
 *                              pipeline_ms / e2e_ms  (NOTE: the forward stage is
 *                              keyed `model_ms` in the breakdown, `model_forward_ms`
 *                              on the flattened ModelMeta)
 *   web/model_governance_routes.py /api/models/shadow70/summary ->
 *                              store.disagreement_counts + store.recent_observations
 *   shadow/shadow70/models.py DisagreementClass taxonomy (13 literals)
 *
 * Reason-code wording is reused from lib/signal (REASONS) — an unknown code
 * falls back to the raw code verbatim, never an invented explanation.
 *
 * Erasable TypeScript only (no enums / namespaces / parameter properties) so
 * the module is runnable directly under `node --strip-types` in tests.
 */

import type { ModelMeta, Probabilities, Shadow70Observation } from "@/types/domain";
import { formatAgeMs, formatNumber } from "@/lib/format";
import { REASONS } from "@/lib/signal";

/** The literal the UI shows when the backend sent nothing. Never "ok"/"0". */
export const UNKNOWN = "UNKNOWN";

const NO_VALUE = "—";

// ---------------------------------------------------------------------------
// shared primitives
// ---------------------------------------------------------------------------

/** Backend status/class string passthrough: missing/blank -> UNKNOWN (not 'ok'). */
export function statusText(raw: unknown): string {
  if (raw === null || raw === undefined) return UNKNOWN;
  const s = typeof raw === "string" ? raw : String(raw);
  return s.trim() === "" ? UNKNOWN : s;
}

/** Finite-number guard used everywhere a backend metric may be null/NaN. */
export function finiteOrNull(raw: unknown): number | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  return null; // booleans / objects: no honest number exists
}

/** Milliseconds as sent by the backend (never defaulted to 0). */
export function msText(value: number | null | undefined, digits = 1): string {
  const n = finiteOrNull(value);
  return n === null ? NO_VALUE : `${formatNumber(n, digits)} ms`;
}

/** Freshness age (backend seconds) reused through lib/format's age formatter. */
export function formatAgeSec(sec: number | null | undefined): string {
  const n = finiteOrNull(sec);
  return n === null ? NO_VALUE : formatAgeMs(n * 1000);
}

/** Freshness age (backend ms) — direct reuse of lib/format#formatAgeMs. */
export function formatAgeMsText(ms: number | null | undefined): string {
  return formatAgeMs(finiteOrNull(ms));
}

/** 0..1 fraction -> percent text; null stays the UNKNOWN marker. */
export function probPctText(value: number | null | undefined, digits = 1): string {
  const n = finiteOrNull(value);
  return n === null ? UNKNOWN : `${(n * 100).toFixed(digits)}%`;
}

// ---------------------------------------------------------------------------
// 1. feature contributions
// ---------------------------------------------------------------------------

/** Feature statuses the backend emits in `features_payload[].status`. */
export const BACKEND_FEATURE_STATUSES = [
  "VALID",
  "NAN",
  "INF",
  "NON_NUMERIC",
  "UNAVAILABLE",
] as const;

export type VizStatusTone = "ok" | "warn" | "bad" | "unknown";

/**
 * Status -> semantic tone. Only the literal backend values decide: an
 * unrecognized status is `unknown`, never treated as healthy.
 */
export function featureStatusTone(status: unknown): VizStatusTone {
  const s = statusText(status);
  if (s === UNKNOWN) return "unknown";
  switch (s.toUpperCase()) {
    case "VALID":
      return "ok";
    case "NAN":
    case "INF":
    case "NON_NUMERIC":
      return "bad";
    case "UNAVAILABLE":
      return "warn";
    default:
      return "unknown";
  }
}

export interface VizFeatureInput {
  index?: number | null;
  name?: string | null;
  value?: number | null;
  status?: string | null;
}

export interface VizContribution {
  /** Display label (backend name; positional fallback when name absent). */
  label: string;
  /** Backend name verbatim, or null when the backend sent none. */
  name: string | null;
  /** Backend index verbatim, or null. */
  index: number | null;
  /** Backend value verbatim (null when not sent / not finite). */
  value: number | null;
  /** Backend status string verbatim; missing -> UNKNOWN (never 'ok'). */
  status: string;
  statusTone: VizStatusTone;
  /** |value| — ranking + bar magnitude (null value -> null). */
  magnitude: number | null;
  /** Diverging direction for the bar. */
  direction: "pos" | "neg" | "zero" | "unknown";
  /** 0..1 bar fraction against the largest magnitude in the selection. */
  fraction: number;
  /** True when this feature name is in V1FeaturesStatus.missing_features. */
  missing: boolean;
  /** True when a magnitude bar can honestly be drawn (finite value). */
  renderable: boolean;
}

export interface TopNOptions {
  /** V1FeaturesStatus.missing_features — backend-reported gaps, display only. */
  missingFeatures?: readonly string[] | null;
  /** Tie-break: keep backend index order (default true). */
  stableByIndex?: boolean;
}

/**
 * Rank features by |value| and return the top `n`.
 *
 * Ranking is magnitude-only. Null / non-finite values are never treated as 0
 * for ordering: they sort after every finite magnitude, in backend index
 * order, and keep their real status word so the chart can label them.
 */
export function topNContributions(
  features: readonly VizFeatureInput[] | null | undefined,
  n: number,
  options: TopNOptions = {},
): VizContribution[] {
  const list = Array.isArray(features) ? features : [];
  const want = Math.max(0, Math.trunc(finiteOrNull(n) ?? 0));
  const missingSet = new Set(
    (options.missingFeatures ?? []).filter((x): x is string => typeof x === "string"),
  );
  const stable = options.stableByIndex !== false;

  const rows: VizContribution[] = list.map((f, pos) => {
    const idx = finiteOrNull(f?.index ?? null);
    const rawName = typeof f?.name === "string" && f.name.trim() !== "" ? f.name : null;
    const value = finiteOrNull(f?.value ?? null);
    const status = statusText(f?.status);
    const magnitude = value === null ? null : Math.abs(value);
    const direction: VizContribution["direction"] =
      value === null ? "unknown" : value > 0 ? "pos" : value < 0 ? "neg" : "zero";
    return {
      label: rawName ?? (idx !== null ? `feature_${idx}` : `feature_${pos}`),
      name: rawName,
      index: idx,
      value,
      status,
      statusTone: featureStatusTone(status),
      magnitude,
      direction,
      fraction: 0,
      missing: rawName !== null && missingSet.has(rawName),
      renderable: magnitude !== null,
    };
  });

  const orderKey = (row: VizContribution, pos: number): number =>
    row.index !== null ? row.index : pos;

  const ranked = rows
    .map((row, pos) => ({ row, pos }))
    .sort((a, b) => {
      const am = a.row.magnitude;
      const bm = b.row.magnitude;
      if (am === null && bm === null) {
        return (stable ? orderKey(a.row, a.pos) : a.pos) - (stable ? orderKey(b.row, b.pos) : b.pos);
      }
      if (am === null) return 1;
      if (bm === null) return -1;
      if (bm !== am) return bm - am;
      return stable ? orderKey(a.row, a.pos) - orderKey(b.row, b.pos) : a.pos - b.pos;
    })
    .map((x) => x.row);

  const top = want === 0 ? [] : ranked.slice(0, want);
  let max = 0;
  for (const row of top) if (row.magnitude !== null && row.magnitude > max) max = row.magnitude;
  return top.map((row) => ({
    ...row,
    fraction: row.magnitude === null || max <= 0 ? 0 : row.magnitude / max,
  }));
}

/** Count of entries per backend status word (no invented buckets). */
export function featureStatusTally(
  features: readonly VizFeatureInput[] | null | undefined,
): Record<string, number> {
  const out: Record<string, number> = {};
  const list = Array.isArray(features) ? features : [];
  for (const f of list) {
    const s = statusText(f?.status);
    out[s] = (out[s] ?? 0) + 1;
  }
  return out;
}

// ---------------------------------------------------------------------------
// 2. probability triple
// ---------------------------------------------------------------------------

export type ProbKey = "no_trade" | "buy" | "sell";
export type ProbTone = "flat" | "buy" | "sell";

export interface VizProbRow {
  key: ProbKey;
  /** Display label (fixed vocabulary: P(NO_TRADE) / P(BUY) / P(SELL)). */
  label: string;
  /** Backend value verbatim; null when absent (never coerced to 0). */
  value: number | null;
  /** Bar fraction for geometry only (clamped 0..1; null -> 0 length). */
  fraction: number;
  /** Verbatim numeric text, or UNKNOWN. */
  valueText: string;
  /** Derived percent text (display only), or UNKNOWN. */
  pctText: string;
  tone: ProbTone;
  /** True when a value exists AND the backend marked probs available. */
  real: boolean;
}

export interface VizProbTriple {
  rows: VizProbRow[];
  /** Backend availability flag passthrough (missing flag -> false). */
  available: boolean;
  /** Sum of the backend values that are present (null when any is missing). */
  sum: number | null;
  /** Backend inference timestamp verbatim, or null. */
  inferenceTimestamp: string | null;
}

const PROB_SPEC: Array<{ key: ProbKey; label: string; tone: ProbTone }> = [
  { key: "no_trade", label: "P(NO_TRADE)", tone: "flat" },
  { key: "buy", label: "P(BUY)", tone: "buy" },
  { key: "sell", label: "P(SELL)", tone: "sell" },
];

/**
 * Normalize a `Probabilities` payload into three display tuples.
 *
 * "Normalized" here means display-scale normalization (fraction for bar
 * geometry + percent text). The values themselves are never re-scaled to sum
 * to 1 — renormalizing backend output would fabricate probabilities. A null
 * value, or `available: false`, renders UNKNOWN.
 */
export function probTriple(
  probs: Probabilities | Partial<Probabilities> | null | undefined,
): VizProbTriple {
  const available = probs?.available === true;
  const rawValues = probs ?? {};
  const values: Record<ProbKey, number | null> = {
    no_trade: finiteOrNull(rawValues.no_trade ?? null),
    buy: finiteOrNull(rawValues.buy ?? null),
    sell: finiteOrNull(rawValues.sell ?? null),
  };
  const present = PROB_SPEC.every((s) => values[s.key] !== null);
  const rows: VizProbRow[] = PROB_SPEC.map((s) => {
    const v = values[s.key];
    const real = available && v !== null;
    return {
      key: s.key,
      label: s.label,
      value: v,
      fraction: v === null ? 0 : Math.max(0, Math.min(1, v)),
      valueText: v === null ? UNKNOWN : String(v),
      pctText: probPctText(v),
      tone: s.tone,
      real,
    };
  });
  return {
    rows,
    available,
    sum: present ? PROB_SPEC.reduce((acc, s) => acc + (values[s.key] ?? 0), 0) : null,
    inferenceTimestamp:
      typeof probs?.inference_timestamp === "string" && probs.inference_timestamp !== ""
        ? probs.inference_timestamp
        : null,
  };
}

// ---------------------------------------------------------------------------
// 3. inference latency split
// ---------------------------------------------------------------------------

export interface VizLatencySplit {
  /** T2-T1 (70D feature build). null when the backend did not measure it. */
  feature_ms: number | null;
  /** T6-T5 (honest model forward). null when not measured. */
  model_forward_ms: number | null;
  /** T10-T0 (market event -> published decision). null when not measured. */
  e2e_ms: number | null;
  /** Backend `latency_ms` verbatim (the flattened single-number metric). */
  latency_ms: number | null;
  /** Any other backend-measured stages present in the breakdown, verbatim. */
  stages: Array<{ key: string; value: number | null }>;
  /** True when at least one stage number exists. */
  hasData: boolean;
  /** True when a sub-metric is missing while e2e exists (renders as a gap). */
  partial: boolean;
}

/** Stage keys the tracer emits besides the three headline splits. */
const EXTRA_STAGE_KEYS = [
  "queue_ms",
  "scaling_ms",
  "tensor_ms",
  "postprocess_ms",
  "decision_ms",
  "pipeline_ms",
] as const;

function pickBreakdown(
  bd: Record<string, number | null> | null | undefined,
  keys: readonly string[],
): number | null {
  if (!bd || typeof bd !== "object") return null;
  for (const k of keys) {
    if (Object.prototype.hasOwnProperty.call(bd, k)) {
      const v = finiteOrNull((bd as Record<string, unknown>)[k]);
      if (v !== null) return v;
    }
  }
  return null;
}

/**
 * Split the inference latency into its honest stages.
 *
 * Precedence: flattened ModelMeta field -> backend `latency_breakdown` key.
 * `model_forward_ms` maps to the breakdown's `model_ms` (the tracer names the
 * T6-T5 stage `model_ms`). A metric the backend never produced stays null —
 * the bar renders it as a GAP, and no arithmetic invents a residual segment.
 */
export function latencySplit(
  model: ModelMeta | Partial<ModelMeta> | null | undefined,
): VizLatencySplit {
  const bd = (model?.latency_breakdown ?? null) as Record<string, number | null> | null;
  const feature_ms = finiteOrNull(model?.feature_ms ?? null) ?? pickBreakdown(bd, ["feature_ms"]);
  const model_forward_ms =
    finiteOrNull(model?.model_forward_ms ?? null) ??
    pickBreakdown(bd, ["model_forward_ms", "model_ms"]);
  const e2e_ms = finiteOrNull(model?.e2e_ms ?? null) ?? pickBreakdown(bd, ["e2e_ms"]);
  const latency_ms = finiteOrNull(model?.latency_ms ?? null);

  const stages: Array<{ key: string; value: number | null }> = EXTRA_STAGE_KEYS.filter(
    (k) => bd && Object.prototype.hasOwnProperty.call(bd, k),
  ).map((k) => ({ key: k, value: finiteOrNull((bd as Record<string, unknown>)[k] ?? null) }));

  const headline = [feature_ms, model_forward_ms, e2e_ms];
  const hasData = headline.some((v) => v !== null) || latency_ms !== null;
  const partial = e2e_ms !== null && headline.some((v) => v === null);

  return { feature_ms, model_forward_ms, e2e_ms, latency_ms, stages, hasData, partial };
}

// ---------------------------------------------------------------------------
// 4. 70D shadow disagreement taxonomy
// ---------------------------------------------------------------------------

/**
 * The backend's own DisagreementClass literals
 * (src/nexus_scalp/shadow/shadow70/models.py). Listed here ONLY to map colour
 * semantics — the strings shown to the operator are always the backend value
 * verbatim, and a value outside this list falls back to the neutral/unknown
 * badge level rather than being guessed into a bucket.
 */
export const DISAGREEMENT_CLASSES = [
  "AGREEMENT",
  "ACTION_DISAGREEMENT",
  "DIRECTION_DISAGREEMENT",
  "CONFIDENCE_DIVERGENCE",
  "NO_TRADE_DISAGREEMENT",
  "CHAMPION_BUYS_SHADOW_NO_TRADE",
  "CHAMPION_SELLS_SHADOW_NO_TRADE",
  "CHAMPION_NO_TRADE_SHADOW_BUYS",
  "CHAMPION_NO_TRADE_SHADOW_SELLS",
  "BUY_VS_SELL",
  "HIGH_CONFIDENCE_DISAGREEMENT",
  "LOW_CONFIDENCE_DISAGREEMENT",
] as const;

export type VizBadgeLevel = "good" | "warn" | "bad" | "neutral" | "unknown";

/** Agreement bucket (backend literal). */
export const AGREEMENT_CLASS = "AGREEMENT";

/**
 * Disagreement class -> existing badge level vocabulary (`good|warn|bad|
 * neutral|unknown`, i.e. the `.badge.*` classes in styles/theme.css).
 * Colour only: the label stays the backend string.
 */
export function disagreementBadgeLevel(raw: unknown): VizBadgeLevel {
  const s = statusText(raw);
  if (s === UNKNOWN) return "unknown";
  switch (s.toUpperCase()) {
    case AGREEMENT_CLASS:
      return "good";
    case "CONFIDENCE_DIVERGENCE":
    case "LOW_CONFIDENCE_DISAGREEMENT":
    case "NO_TRADE_DISAGREEMENT":
      return "warn";
    case "ACTION_DISAGREEMENT":
    case "DIRECTION_DISAGREEMENT":
    case "BUY_VS_SELL":
    case "HIGH_CONFIDENCE_DISAGREEMENT":
    case "CHAMPION_BUYS_SHADOW_NO_TRADE":
    case "CHAMPION_SELLS_SHADOW_NO_TRADE":
    case "CHAMPION_NO_TRADE_SHADOW_BUYS":
    case "CHAMPION_NO_TRADE_SHADOW_SELLS":
      return "bad";
    default:
      return "unknown";
  }
}

export interface VizDisagreementBucket {
  /** Backend class string verbatim (UNKNOWN for blank/missing). */
  class: string;
  count: number;
  level: VizBadgeLevel;
}

export interface VizDisagreementTally {
  buckets: VizDisagreementBucket[];
  total: number;
  /** Rows whose class is exactly the backend AGREEMENT literal. */
  agreements: number;
  /** Rows carrying a real (non-UNKNOWN) class that is not AGREEMENT. */
  disagreements: number;
  /** Rows where the backend sent no class (blank/missing) — kept visible. */
  unclassified: number;
}

/**
 * Tally observations by the backend `disagreement` string ONLY.
 *
 * No derived taxonomy, no percentage smoothing. A blank/missing class is
 * counted under UNKNOWN so unclassified rows stay visible instead of silently
 * joining an agreement/disagreement bucket.
 */
export function disagreementTally(
  observations: readonly Pick<Shadow70Observation, "disagreement">[] | null | undefined,
): VizDisagreementTally {
  const list = Array.isArray(observations) ? observations : [];
  const counts: Record<string, number> = {};
  for (const o of list) {
    const key = statusText(o?.disagreement);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return finalizeTally(counts, list.length);
}

/** Normalize the backend's own `store.disagreement_counts` histogram. */
export function tallyFromCounts(
  counts: Record<string, number> | null | undefined,
): VizDisagreementTally {
  const src: Record<string, number> = {};
  if (counts && typeof counts === "object") {
    for (const [k, v] of Object.entries(counts)) {
      const n = finiteOrNull(v);
      if (n === null) continue;
      const key = statusText(k);
      src[key] = (src[key] ?? 0) + n;
    }
  }
  const total = Object.values(src).reduce((a, b) => a + b, 0);
  return finalizeTally(src, total);
}

function finalizeTally(counts: Record<string, number>, total: number): VizDisagreementTally {
  const buckets: VizDisagreementBucket[] = Object.entries(counts)
    .map(([klass, count]) => ({ class: klass, count, level: disagreementBadgeLevel(klass) }))
    .sort((a, b) => b.count - a.count || a.class.localeCompare(b.class));
  const agreements = counts[AGREEMENT_CLASS] ?? 0;
  const unclassified = counts[UNKNOWN] ?? 0;
  return {
    buckets,
    total,
    agreements,
    disagreements: Math.max(0, total - agreements - unclassified),
    unclassified,
  };
}

/** Agreement share as text; "—" when there is nothing valid to divide by. */
export function agreementRateText(tally: VizDisagreementTally, digits = 1): string {
  if (!tally || tally.total <= 0) return NO_VALUE;
  return `${((tally.agreements / tally.total) * 100).toFixed(digits)}%`;
}

export interface VizShadowRow {
  observationId: string;
  timeText: string;
  timestamp: string | null;
  championAction: string;
  shadowAction: string;
  championConfidence: number | null;
  shadowConfidence: number | null;
  /** Backend disagreement class verbatim (UNKNOWN when blank/missing). */
  disagreement: string;
  level: VizBadgeLevel;
  regime: string;
  newsState: string;
  liquidityState: string;
  outcome: string;
  /** Backend strings differ (e.g. BUY vs NO_TRADE) — display marker only. */
  actionDiffers: boolean;
}

/**
 * Reshape `store.recent_observations` for the strip. Every string is passed
 * through verbatim (blank -> UNKNOWN); confidences stay null when absent.
 */
export function shadowRows(
  observations: readonly Partial<Shadow70Observation>[] | null | undefined,
  limit?: number,
): VizShadowRow[] {
  const list = Array.isArray(observations) ? observations : [];
  const want = Math.max(0, Math.trunc(finiteOrNull(limit ?? list.length) ?? list.length));
  return list.slice(0, want).map((o, i) => {
    const champ = statusText(o?.champion_action);
    const shadow = statusText(o?.shadow_action);
    const ts = typeof o?.timestamp === "string" && o.timestamp.trim() !== "" ? o.timestamp : null;
    return {
      observationId:
        typeof o?.observation_id === "string" && o.observation_id !== ""
          ? o.observation_id
          : `obs_${i}`,
      timeText: ts ? ts.slice(0, 19) : UNKNOWN,
      timestamp: ts,
      championAction: champ,
      shadowAction: shadow,
      championConfidence: finiteOrNull(o?.champion_confidence ?? null),
      shadowConfidence: finiteOrNull(o?.shadow_confidence ?? null),
      disagreement: statusText(o?.disagreement),
      level: disagreementBadgeLevel(o?.disagreement),
      regime: statusText(o?.regime),
      newsState: statusText(o?.news_state),
      liquidityState: statusText(o?.liquidity_state),
      outcome: statusText(o?.outcome),
      actionDiffers: champ !== shadow || champ === UNKNOWN,
    };
  });
}

// ---------------------------------------------------------------------------
// 5. reason-code wording (reused from lib/signal — never invented)
// ---------------------------------------------------------------------------

export interface VizReasonCopy {
  /** Raw backend code verbatim. */
  code: string;
  /** Human wording from lib/signal REASONS, or null for an unknown code. */
  simple: string | null;
  /** Technical detail from REASONS, or the raw code for an unknown one. */
  detail: string;
  known: boolean;
}

/**
 * Exact-match reason wording. Unknown codes show the code itself and produce
 * no prose (parity with lib/signal's truth rule).
 */
export function reasonCopy(raw: string | null | undefined): VizReasonCopy {
  const code = raw === null || raw === undefined || String(raw).trim() === "" ? "" : String(raw);
  if (code === "") {
    return { code: "", simple: null, detail: UNKNOWN, known: false };
  }
  const known = REASONS[code];
  if (known) return { code, simple: known.simple, detail: known.detail, known: true };
  return { code, simple: null, detail: code, known: false };
}
