/**
 * AI-Analysis: DTO contracts + VO mappers (pure).
 *
 * Verified against api_v1/{signals,decisions,indicators,shadow}.py:
 *  - signal rows: audit_signals projection (request_id, symbol, action,
 *    confidence, proposed_entry/stop_loss/take_profit, regime, generated_at,
 *    payload(sanitized), execution_mode, reason_code, decision_stage,
 *    blocked_by, htf_score, smc_score, confidence_{before,after}_filters).
 *  - indicators: {name, value, action} rows + gauges {label,sell,neutral,buy,
 *    angle_deg} + summary {Sell,Neutral,Buy}.
 * Values the backend never recorded stay null — the UI renders "—".
 */

import { ApiError } from "@/types/api";

export type Row = Record<string, unknown>;

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null);
export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null;
export const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});
export const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

export interface SignalDto extends Row {
  request_id?: string;
  symbol?: string;
  action?: string;
  confidence?: number | null;
  proposed_entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  regime?: string | null;
  generated_at?: string | null;
  payload?: Row;
  execution_mode?: string | null;
  reason_code?: string | null;
  decision_stage?: string | null;
  blocked_by?: string | null;
  htf_score?: number | null;
  smc_score?: number | null;
  confidence_before_filters?: number | null;
  confidence_after_filters?: number | null;
}

export interface DecisionSummaryDto {
  decision_id?: string | null;
  symbol?: string | null;
  action?: string | null;
  confidence?: number | null;
  regime?: string | null;
  generated_at?: string | null;
  execution_mode?: string | null;
  decision_stage?: string | null;
  blocked_by?: string | null;
  reason_code?: string | null;
  proposed_entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  confidence_before_filters?: number | null;
  confidence_after_filters?: number | null;
  risk_checks?: Row;
}

export type DecisionDetailDto = DecisionSummaryDto;

export interface DecisionStatsDto {
  window_hours: number;
  total: number;
  by_action: Record<string, number>;
  by_stage: Record<string, number>;
}

export interface NoTradeReasonsDto {
  total: number;
  reasons: Record<string, number>;
}

export interface DecisionGatesDto {
  decision_id: string;
  gates: Array<{ gate: string; value: unknown; passed: boolean }>;
}

export interface DecisionEvidenceDto {
  evidence: Row;
}

export interface DecisionExplanationDto {
  decision_id: string;
  explanation: string;
}

/** Indicator row: backend emits EXACTLY {name, value, action} (ports.py
 *  IndicatorResult.as_api_dict). value null = insufficient history — render
 *  "—", never a guess. action ∈ Buy/Sell/Neutral/Strong buy/Strong sell. */
export interface IndicatorRowDto {
  name: string;
  value: number | null;
  action: string;
}

export interface GaugeDto {
  label?: string;
  sell?: number | null;
  neutral?: number | null;
  buy?: number | null;
  angle_deg?: number | null;
}

export interface IndicatorsSnapshotPart extends Row {
  gauges?: Record<string, GaugeDto>;
  summary?: Record<string, number>;
  oscillators?: IndicatorRowDto[];
  moving_averages?: IndicatorRowDto[];
}

export interface IndicatorSummaryDto {
  symbol?: string;
  timeframe?: string;
  bar_count?: number;
  last_close?: number | null;
  gauges?: Record<string, GaugeDto>;
  summary?: Record<string, number>;
}

export interface PivotsDto {
  pivots?: { levels?: string[]; columns?: string[]; rows?: Record<string, Record<string, number | null>> };
}

export interface Shadow70dDto {
  summary?: Row | null;
  disagreement_counts?: Row | null;
  drift_alerts?: Row[] | null;
  feature_health?: Row[] | null;
  generated_at?: string;
}

/** Snapshot DTOs consumed by the indicators console: re-exported from the
 *  canonical feature-types file so the console imports inside its module.
 *  (Backend truth: types/features.ts documents ports.py + service.py shapes.) */
export type { IndicatorGauge, IndicatorPivots, IndicatorReading, IndicatorsSnapshot } from "@/types/features";

/** Verdict vocabulary the backend gauges/actions use (service.py
 *  _gauge_from_rating labels). Display-classification union only: labels map
 *  verbatim; an unrecognized label is handled as unknown, never guessed. */
export type GaugeVerdict = "strong sell" | "sell" | "neutral" | "buy" | "strong buy";

/** Signal -> probability-bar tone for the action (backend action decides).
 *  Wave 2b (#34): prefix semantics, so BUY_LIMIT/SELL_LIMIT get the same
 *  tone as their chips (actionTone and actionFamily can never disagree). */
export function actionTone(action: string | null | undefined): "buy" | "sell" | "flat" {
  const a = (action ?? "").toUpperCase();
  if (a.startsWith("BUY")) return "buy";
  if (a.startsWith("SELL")) return "sell";
  return "flat";
}

/** One truth for the not-found honesty branch (page hero + decision drawer):
 *  404 OR RESOURCE_NOT_FOUND code — the drawer previously only checked 404. */
export function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
}

/** Confidence may arrive 0..1 or 0..100 depending on ledger vintage —
 *  normalize with an explicit rule, never a guess about unknown scales. */
export function confidence01(v: number | null | undefined): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (v >= 0 && v <= 1) return v;
  if (v > 1 && v <= 100) return v / 100;
  return null;
}

export function distRows(rec: Record<string, number> | undefined): Array<{ label: string; count: number }> {
  return Object.entries(rec ?? {})
    .map(([label, count]) => ({ label, count: Number(count) || 0 }))
    .sort((a, b) => b.count - a.count);
}
