/**
 * Control Center: DTO contracts + VO mappers (pure).
 *
 * operator_routes.py truth rules mirrored here: values the ledger never
 * recorded stay null and render as NOT RECORDED (never 0, never invented);
 * rows with payload_ok:false are kept and flagged; funnel numbers are
 * TERMINAL distributions (backend note rendered verbatim).
 */

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface WarningDto {
  code?: string;
  severity?: string;
  what?: string;
  why?: string;
  impact?: string;
  what_to_do?: string;
}

export interface OperatorSummaryDto {
  available?: boolean;
  runtime?: {
    engine_running?: boolean;
    execution_mode?: string;
    runtime_mode?: string;
    symbol?: string;
    regime?: string;
    bid?: number | null;
    ask?: number | null;
    spread?: number | null;
    tick_stale?: boolean;
    tick_freshness_ms?: number | null;
    state_version?: number | null;
    snapshot_timestamp?: string | null;
    provenance?: Row;
    health?: Row;
  };
  identity?: Row | null;
  ledger?: { available?: boolean; reason?: string; scanned_rows?: number; window?: number; total?: number; actions?: Record<string, number>; latest_decision_at?: string | null };
  warnings?: WarningDto[];
}

export interface OperatorDecisionRow extends Row {
  id?: number;
  request_id?: string;
  symbol?: string;
  action?: string;
  confidence?: number | null;
  regime?: string | null;
  generated_at?: string | null;
  execution_mode?: string | null;
  reason_code?: string | null;
  decision_stage?: string | null;
  blocked_by?: string | null;
  payload_ok?: boolean;
  probabilities?: { buy?: number | null; sell?: number | null; no_trade?: number | null; wait?: number | null; model_action?: string | null; raw?: Row | null };
}

export interface OperatorDecisionsDto {
  available?: boolean;
  filters?: Row;
  count?: number;
  rows?: OperatorDecisionRow[];
  error?: { code?: string; message?: string };
}

export interface OperatorDecisionDetailDto {
  available?: boolean;
  decision?: Row & { orders?: Row[]; correlation_method?: string };
  error?: { code?: string; message?: string; reason?: string };
}

export interface OperatorFunnelDto {
  available?: boolean;
  window?: number;
  scanned_rows?: number;
  total?: number;
  stages?: Array<{ stage?: string; count?: number }>;
  gates?: Array<{ gate?: string; count?: number }>;
  actions?: Array<{ action?: string; count?: number }>;
  note?: string;
}

export interface OperatorNoTradeDto {
  available?: boolean;
  total?: number;
  gates?: Array<{ gate?: string; count?: number }>;
  regimes?: Array<{ regime?: string; count?: number }>;
  reasons?: Array<{ reason?: string; count?: number }>;
  hourly_trend?: Array<{ hour?: string; count?: number }>;
  model_direction_unresolved?: number;
  recent?: OperatorDecisionRow[];
}

export interface OperatorOrdersDto {
  available?: boolean;
  count?: number;
  latency?: { n?: number; p50_ms?: number; p95_ms?: number; p99_ms?: number } | null;
  rows?: Row[];
}

export interface CalibrationDto {
  available?: boolean;
  serving_fingerprint?: string | null;
  artifact_status?: string;
  calibration_status?: string;
  matches_serving?: boolean;
  calibration_split?: number;
  validation_split?: number;
  required_per_split?: number;
  deficit?: number;
  risk_multiplier?: number | null;
  min_risk_multiplier_floor?: number | null;
  ece?: number | null;
  brier?: number | null;
  collector_status?: string;
  excluded?: Record<string, number>;
  oos_cutoff?: string | null;
}

/** Render helper: backend-null values always say NOT RECORDED (cc truth rule). */
export const notRecorded = (v: string | number | null | undefined): string =>
  v === null || v === undefined || v === "" ? "NOT RECORDED" : String(v);
