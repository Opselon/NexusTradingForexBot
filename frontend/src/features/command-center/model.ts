/**
 * Command Center: DTO contracts + VO mappers (pure).
 *
 * overview: {available,total_strategies,by_lifecycle,terminal,
 *   evaluation_pipeline{...},evaluation_metrics,running_evaluations,
 *   execution_eligible_count,blocked_count,stuck_strategies[]}
 * fleet: {available,count,rows[{strategy_id,strategy_version,lifecycle,
 *   confidence,sample_count,health_final,eligibility_state,
 *   eligibility_reason,evidence,updated_at}]}
 * inspector: snapshot dump + events/evidence_completeness/invariant_check/
 *   evaluation + ai_attribution + debug_intelligence.
 * timemachine bounds: {available,earliest,latest,total_events};
 * frame: {nodes[{strategy_id,zone,maturity,...}], transitions, ...}.
 */

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface CcOverviewDto {
  available?: boolean;
  reason?: string;
  total_strategies?: number;
  by_lifecycle?: Record<string, number>;
  terminal?: Record<string, number>;
  evaluation_pipeline?: Record<string, number>;
  evaluation_metrics?: Record<string, unknown>;
  running_evaluations?: number;
  execution_eligible_count?: number;
  blocked_count?: number;
  stuck_strategies?: Array<{ strategy_id?: string; state?: string; hours_in_state?: number }>;
}

export interface CcFleetRowDto {
  strategy_id?: string;
  strategy_version?: string;
  lifecycle?: string;
  confidence?: number | null;
  sample_count?: number | null;
  health_final?: number | null;
  eligibility_state?: string;
  eligibility_reason?: string;
  evidence?: Row;
  updated_at?: string;
}

export interface CcFleetDto {
  available?: boolean;
  reason?: string;
  count?: number;
  rows?: CcFleetRowDto[];
}

/** Transient evaluation pipeline projection on a spatial node (telemetry,
 *  NOT lifecycle) — shape from web/command_center_routes.evaluation_detail. */
export interface CcSpatialEvaluationDto {
  gates?: Record<string, string>;
  current_stage?: string | null;
  passed_gates?: number;
  resolved_gates?: number;
  progress?: number;
  is_running?: boolean;
  running_stage?: string | null;
}

/** Node from GET /api/command-center/spatial (research/spatial_layout). */
export interface CcSpatialNodeDto {
  strategy_id?: string;
  strategy_version?: string;
  zone?: string;
  x?: number;
  y?: number;
  z?: number;
  size_hint?: number | null;
  ring_count?: number | null;
  elevation?: number | null;
  confidence?: number | null;
  eligibility_state?: string;
  evaluation?: CcSpatialEvaluationDto | null;
}

export interface CcSpatialZoneDto {
  zone?: string;
  y_index?: number;
  count?: number;
  terminal?: boolean;
}

export interface CcSpatialDto {
  available?: boolean;
  reason?: string;
  /** true only for historical replay frames (legacy NX.spatial.setHistorical). */
  historical?: boolean;
  zones?: CcSpatialZoneDto[];
  nodes?: CcSpatialNodeDto[];
  meta?: { zone_spacing?: number; node_spacing?: number; max_columns?: number; total_nodes?: number };
}

export interface CcInspectorDto extends Row {
  available?: boolean;
  error?: string;
  events?: Row[];
  evidence_completeness?: Row;
  invariant_check?: Row;
  evaluation?: Row;
  ai_attribution?: Row;
  debug_intelligence?: Row;
  execution_eligibility?: Row;
  health_score?: Row;
}

export interface CcExecutionSafetyDto {
  available?: boolean;
  strategy_id?: string;
  lifecycle?: string;
  eligibility_state?: string;
  can_trade?: boolean;
  reason?: string;
  required_gate?: string;
  blockers?: unknown[];
  invariant_check?: Row;
  error?: string;
}

export interface CcTimelineDto {
  available?: boolean;
  strategy_id?: string;
  events?: Row[];
  count?: number;
}

export interface TimeMachineBoundsDto {
  available?: boolean;
  earliest?: string;
  latest?: string;
  total_events?: number;
  reason?: string;
}

export interface TimeMachineFrameDto {
  available?: boolean;
  nodes?: Array<Row & { strategy_id?: string; zone?: string; maturity?: number; transitioning?: boolean }>;
  transitions?: Row[];
  console_events?: Row[];
  as_of?: string;
  reason?: string;
}

/** Lifecycle zones that mean "live execution" (renderer pulse semantics). */
export const SPATIAL_LIVE_ZONES = new Set(["ACTIVE"]);
export const SPATIAL_TERMINAL_ZONES = new Set(["REJECTED", "DEGRADED", "RETIRED"]);

/** Evaluation gates in pipeline order + result colors — presentation mirror of
 *  the legacy Web/command_center_spatial.js constants (rendering only; the
 *  gate VALUES always come from the backend payload). */
export const EVAL_GATES = ["BACKTEST", "WALK_FORWARD", "OOS", "ROBUSTNESS", "SCORE"] as const;

/** Honest empty-state facts for the spatial canvas (no fabricated counts):
 *  exactly what the backend said vs. what the current filter shows. */
export function spatialEmptyFacts(
  payload: CcSpatialDto | undefined,
  visibleCount: number,
  filterLabel: string,
): { backendTotal: string; filter: string; matching: string } {
  const backendTotal = payload?.meta?.total_nodes ?? payload?.nodes?.length ?? "—";
  return {
    backendTotal: String(backendTotal),
    filter: filterLabel || "ALL",
    matching: String(visibleCount),
  };
}

/** Risk-first sort for the fleet grid: BLOCKED first, then by ascending
 *  health, then by descending hours-in-state via updated_at age. The order
 *  is a *presentation* rule over backend fields; no state is inferred. */
export function riskOrder(rows: CcFleetRowDto[], nowMs: number): CcFleetRowDto[] {
  const weight = (r: CcFleetRowDto): number => {
    const e = (r.eligibility_state ?? "").toUpperCase();
    if (e === "BLOCKED") return 0;
    if (e === "UNKNOWN") return 1;
    if (e === "CONDITIONAL") return 2;
    return 3; // YES
  };
  const ageMs = (r: CcFleetRowDto): number => {
    const t = r.updated_at ? new Date(r.updated_at).getTime() : NaN;
    return Number.isNaN(t) ? Number.POSITIVE_INFINITY : nowMs - t;
  };
  return [...rows].sort((a, b) => {
    const w = weight(a) - weight(b);
    if (w !== 0) return w;
    const h = (a.health_final ?? -1) - (b.health_final ?? -1);
    if (h !== 0) return h;
    return ageMs(b) - ageMs(a);
  });
}

/** Elapsed hours an ACTIVE-listed strategy has been stuck (from overview). */
export function stuckRows(o: CcOverviewDto | undefined): Array<{ strategy_id: string; state: string; hours: number | null }> {
  return (o?.stuck_strategies ?? [])
    .map((s) => ({ strategy_id: str(s.strategy_id) ?? "—", state: str(s.state) ?? "UNKNOWN", hours: num(s.hours_in_state) }))
    .slice(0, 10);
}
