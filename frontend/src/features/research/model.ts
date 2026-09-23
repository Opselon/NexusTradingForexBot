/**
 * Research: DTO contracts + domain VO mappers (pure, unit-testable).
 *
 * Shapes verified against src/nexus_scalp/web/debug_research_routes.py
 * (/api/research/*) and api_v1/research.py (/api/v1/research/*). Legacy
 * research routes answer with {available: true|false, ...} raw JSON — when
 * `available` is false the payload carries the backend's `reason`, which the
 * UI renders verbatim (backend-authoritative, never inferred).
 */

import type { V1Page } from "@/types/domain";

/** Loose record for backend rows whose full column set is DB-driven. */
export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null =>
  typeof v === "string" && v.length > 0 ? v : typeof v === "number" ? String(v) : null;

export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null;

export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});

// ---- DTOs -------------------------------------------------------------------

export interface ResearchSummaryDto {
  available?: boolean;
  reason?: string;
  summary?: {
    total?: number;
    available?: boolean;
    by_lifecycle?: Record<string, number>;
    outcome_quality?: Row;
    worker?: Row;
  };
  health?: Row;
}

export interface ResearchRegistryDto {
  available?: boolean;
  reason?: string;
  registry?: Row[];
  entry?: Row;
}

export interface ResearchRunsDto {
  available?: boolean;
  reason?: string;
  runs?: Row[];
}

export interface ResearchDetailDto {
  available?: boolean;
  reason?: string;
  detail?: Row & {
    strategy_id?: string;
    lifecycle?: string;
    gates?: Row[];
    events?: Row[];
    evidence?: Row[];
    runs?: Row[];
    blocked_reason?: string | null;
    invariant?: Row;
  };
}

export interface ResearchGatesDto {
  available?: boolean;
  reason?: string;
  gates?: Row[];
}

export interface ResearchEventsDto {
  available?: boolean;
  reason?: string;
  events?: Row[];
}

export interface ResearchEvidenceDto {
  available?: boolean;
  reason?: string;
  evidence?: Row[];
}

export interface ResearchHistoryDto {
  available?: boolean;
  reason?: string;
  retention?: {
    events_live?: number;
    events_archived?: number;
    evidence_live?: number;
    evidence_archived?: number;
  };
}

export interface ResearchWorkerDto {
  available?: boolean;
  reason?: string;
  worker?: Row & { health?: string; heartbeat?: Row | null; runtime?: Row | null };
}

export interface ResearchQueueDto {
  available?: boolean;
  reason?: string;
  queue?: {
    available?: boolean;
    queued?: Record<string, Record<string, number>>;
    running?: Row[];
    last_errors?: Record<string, Row>;
  };
}

export interface ResearchAnalyticsDto {
  available?: boolean;
  reason?: string;
  heatmap?: { by_gate?: Record<string, number>; total_failures?: number; rejection_reasons?: Record<string, number> };
  families?: { families?: Record<string, Row> };
}

export interface ResearchPreflightDto {
  available?: boolean;
  reason?: string;
  preflight?: { status?: string; checks?: Record<string, unknown>; blockers?: string[] };
}

export interface ResearchDiagnosticsDto {
  available?: boolean;
  reason?: string;
  worker?: Row;
  queue?: ResearchQueueDto["queue"];
  heatmap?: ResearchAnalyticsDto["heatmap"];
  blocked_gates?: Row[];
}

export interface ResearchV1StatusDto {
  available?: boolean;
  health?: Row | null;
  registry?: Row | null;
  generated_at?: string;
}

export interface ResearchV1StrategiesDto extends V1Page<Row> {}

export interface ResearchV1StrategyDetailDto {
  entry?: Row;
  invariant?: unknown;
}

export interface ResearchDatasetsDto {
  datasets?: Array<{ dataset_id?: string; run_count?: number }>;
  distinct?: number;
  generated_at?: string;
}

// ---- VO mapping -------------------------------------------------------------

export const LIFECYCLE_ORDER = [
  "DISCOVERED",
  "BACKTEST_RUN",
  "WALK_FORWARD_TESTED",
  "WALK_FORWARD_PASSED",
  "OOS_TESTED",
  "OOS_PASSED",
  "ROBUSTNESS_TESTED",
  "ROBUSTNESS_PASSED",
  "SCORING_COMPLETED",
  "VALIDATED",
  "SHADOW",
  "ACTIVE",
] as const;

const TERMINAL_STATES = new Set(["REJECTED", "DEGRADED", "RETIRED", "CANCELLED"]);

/** Registry row -> VO used by the fleet/registry table + drawer headers. */
export interface ResearchStrategyVo {
  strategyId: string;
  version: string | null;
  lifecycle: string;
  lifecycleStage: number; // -1 = terminal/unknown, 0..N = pipeline index
  terminal: boolean;
  confidence: number | null;
  sampleCount: number | null;
  score: number | null;
  updatedAt: string | null;
  retirementReason: string | null;
  raw: Row;
}

export function toStrategyVo(row: Row): ResearchStrategyVo {
  const lifecycle = str(row.lifecycle) ?? "UNKNOWN";
  const stage = (LIFECYCLE_ORDER as readonly string[]).indexOf(lifecycle);
  const scoreObj = obj(row.score);
  return {
    strategyId: str(row.strategy_id) ?? "—",
    version: str(row.strategy_version),
    lifecycle,
    lifecycleStage: stage,
    terminal: TERMINAL_STATES.has(lifecycle),
    confidence: num(row.confidence),
    sampleCount: num(row.sample_count),
    score: num(row.score) ?? num(scoreObj.final_score),
    updatedAt: str(row.updated_at) ?? str(row.registered_at),
    retirementReason: str(row.retirement_reason),
    raw: row,
  };
}

/** Gate row -> stepper entry; status/reason always the backend's own values. */
export interface GateVo {
  gateId: string | null;
  name: string;
  status: string;
  reason: string | null;
  failureClass: string | null;
  retryable: boolean;
  evidenceId: string | null;
  durationMs: number | null;
  raw: Row;
}

export function toGateVo(row: Row): GateVo {
  const summary = obj(row.result_summary);
  return {
    gateId: str(row.gate_id),
    name: str(row.gate_type) ?? str(row.gate) ?? "GATE",
    status: str(row.status) ?? "UNKNOWN",
    reason: str(row.failure_reason) ?? str(summary.reason) ?? str(summary.primary_failure) ?? str(row.reason),
    failureClass: str(row.failure_class),
    retryable: bool(row.retryable) ?? false,
    evidenceId: str(row.evidence_id),
    durationMs: num(row.duration_ms),
    raw: row,
  };
}

/** Outcome counters from /api/research/summary — no lifecycle inference. */
export function registryCounters(summary: ResearchSummaryDto["summary"]): Array<{ label: string; value: number }> {
  const by = obj(summary?.by_lifecycle);
  return Object.entries(by)
    .map(([k, v]) => ({ label: k, value: typeof v === "number" ? v : 0 }))
    .sort((a, b) => b.value - a.value);
}

/** Normalize a command response (available/success/reason) into a verdict.
 *  `t` (i18n wave) is OPTIONAL: pure/unit-testable callers keep the English
 *  fallbacks with {var} interpolation; UI callers pass their translate fn. */
export function commandVerdict(
  res: unknown,
  t?: (key: string, fallback: string, vars?: Record<string, string | number>) => string,
): { ok: boolean; message: string } {
  t =
    t ??
    ((_: string, fallback: string, vars?: Record<string, string | number>) =>
      vars
        ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
        : fallback);
  const o = obj(res);
  const err = obj(o.error);
  if (err.code || err.message) {
    return {
      ok: false,
      message:
        str(err.message) ??
        t("research.model.backend_error", "Backend error: {code}", { code: str(err.code) ?? "UNKNOWN" }),
    };
  }
  const available = bool(o.available);
  const success = bool(o.success) ?? bool(o.cancelled);
  if (available === false || success === false) {
    return { ok: false, message: str(o.reason) ?? str(o.error) ?? t("research.model.refused", "Backend refused the command.") };
  }
  if (available === true || success === true) {
    const detail = str(o.status) ?? str(o.dataset_id) ?? (o.repaired !== undefined ? `repaired=${String(o.repaired)}` : "");
    return {
      ok: true,
      message: detail
        ? t("research.model.accepted", "Backend accepted: {s}", { s: detail })
        : t("research.model.accepted_generic", "Backend accepted the command."),
    };
  }
  return { ok: true, message: t("research.model.no_verdict", "Backend responded without an explicit verdict.") };
}
