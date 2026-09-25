/**
 * Debug — typed transport for the debug/diagnostics bounded context.
 *
 * Endpoints verified in src/nexus_scalp/web/debug_research_routes.py:
 *  GET  /api/debug/state                   canonical snapshot (server also pushes to ring)
 *  GET  /api/debug/health                  subsystem health widgets
 *  GET  /api/debug/features                live feature values + staleness
 *  GET  /api/debug/freshness               live-freshness + no-cache freeze diagnostic
 *  GET  /api/debug/ipc-telemetry?limit=    recent MT5 IPC order events
 *  GET  /api/debug/snapshots               rolling history (ids/timestamps)
 *  GET  /api/debug/snapshots/{id}          one stored snapshot
 *  GET  /api/debug/compare?a=&b=           server diff of two stored snapshots
 *  GET  /api/debug/trace/{execution_id}    forensic trace join (signals+orders)
 *  POST /api/debug/model-test              instant ScalpNet inference on a vector
 * Research-debug reads (read-only): /api/research/diagnostics|events|evidence|gates|history|trace
 *
 * NOTE: this folder must be added with `git add -f` — the repo .gitignore
 * pattern `[Dd]ebug/` (a build-artifact rule) also matches this source path
 * and swallowed the scaffold stub (BACKEND-REQUEST note in lane progress:
 * the wave owns a .gitignore exception for frontend/src/features/debug/).
 */

import { getLegacy, send } from "@/api/client";

/* ------------------------------ state ------------------------------ */

export interface DebugSection {
  _section?: string;
  available?: boolean;
  reason?: string;
  correlation_id?: string;
  [key: string]: unknown;
}

export interface DebugState {
  snapshot_id: string | null;
  correlation_id?: string;
  timestamp: string;
  engine_attached?: boolean;
  available?: boolean;
  reason?: string;
  runtime?: DebugSection;
  contract?: DebugSection;
  features?: DebugSection;
  model?: DebugSection;
  confidence?: DebugSection;
  policy?: DebugSection;
  risk?: DebugSection;
  exposure?: DebugSection;
  execution?: DebugSection;
  positions?: DebugSection;
  exit?: DebugSection;
  liquidity?: DebugSection;
  mslie?: DebugSection;
  news?: DebugSection;
  workers?: DebugSection;
  database?: DebugSection;
  caches?: DebugSection;
  chart?: DebugSection;
  sse?: DebugSection;
  errors?: DebugSection;
  [key: string]: unknown;
}

/* ------------------------------ health ------------------------------ */

export interface DebugSubsystem {
  name: string;
  status: string;
  detail: string;
  metrics: Record<string, unknown>;
}

export interface DebugHealth {
  overall_status: string;
  subsystems: DebugSubsystem[];
  checked_at: string;
}

/* ------------------------------ features ------------------------------ */

export interface DebugFeatureRow {
  index: number;
  key: string;
  name: string;
  value: number | null;
  status: string;
  is_valid: boolean;
}

export interface DebugFeatures {
  engine_online: boolean;
  feature_count: number;
  features: DebugFeatureRow[];
  nan_count: number;
  inf_count: number;
  anomaly_count: number;
  all_valid: boolean;
  timestamp_utc: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
}

/* ------------------------------ freshness ------------------------------ */

export interface DebugFreshness {
  available: boolean;
  reason?: string;
  frozen_at?: string;
  live_freshness?: Record<string, unknown>;
  diagnostic?: Record<string, unknown>;
  checked_at?: string;
}

/* ------------------------------ ipc telemetry ------------------------------ */

export interface IpcEvent {
  [key: string]: unknown;
}

export interface IpcTelemetry {
  events: IpcEvent[];
  event_count: number;
  avg_latency_ms: number;
  exposure: { positions: number; pendings: number };
  max_total_exposure: number;
  fetched_at: string;
}

/* ------------------------------ snapshots / compare ------------------------------ */

export interface SnapshotMeta {
  snapshot_id: string | null;
  correlation_id?: string;
  timestamp?: string;
}

export interface SnapshotList {
  available: boolean;
  snapshots: SnapshotMeta[];
  reason?: string;
}

export interface FeatureDiffRow {
  index: number;
  name: string | null;
  family?: string | null;
  t0: number;
  t1: number;
  delta: number;
}

export interface CompareResult {
  available?: boolean;
  reason?: string;
  a_id?: string | null;
  b_id?: string | null;
  a_timestamp?: string | null;
  b_timestamp?: string | null;
  feature_diffs?: FeatureDiffRow[];
  model?: Record<string, { t0: unknown; t1: unknown }>;
  confidence?: Record<string, { t0: unknown; t1: unknown }>;
  regime?: Record<string, { t0: unknown; t1: unknown }>;
  liquidity?: Record<string, { t0: unknown; t1: unknown }>;
  news?: Record<string, { t0: unknown; t1: unknown }>;
  policy?: Record<string, { t0: unknown; t1: unknown }>;
  risk?: Record<string, { t0: unknown; t1: unknown }>;
}

/* ------------------------------ model test ------------------------------ */

export interface ModelTestResult {
  success?: boolean;
  feature_source?: string;
  model_source?: string;
  sanitized_inputs?: number;
  ai_no_trade?: number;
  ai_buy?: number;
  ai_sell?: number;
  ai_wait?: number;
  probabilities?: number[];
  predicted_class_index?: number;
  predicted_label?: string;
  confidence?: number;
  latency_ms?: number;
  latency_breakdown?: Record<string, unknown>;
  model_forward_ms?: number | null;
  feature_ms?: number | null;
  e2e_ms?: number | null;
  evaluated_at?: string;
  detail?: unknown;
}

/* ------------------------------ trace ------------------------------ */

export interface DebugTrace {
  execution_id: string;
  available: boolean;
  reason?: string;
  signal: Array<Record<string, unknown>> | null;
  orders: Array<Record<string, unknown>>;
  db_path?: string;
}

/* ------------------------------ research reads ------------------------------ */

export interface ResearchRead {
  available: boolean;
  error?: { code?: string; message?: string; request_id?: string };
  [key: string]: unknown;
}

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
};

export const debugApi = {
  state: (signal?: AbortSignal): Promise<DebugState> => getLegacy<DebugState>("/api/debug/state", signal),

  health: (signal?: AbortSignal): Promise<DebugHealth> => getLegacy<DebugHealth>("/api/debug/health", signal),

  features: (signal?: AbortSignal): Promise<DebugFeatures> => getLegacy<DebugFeatures>("/api/debug/features", signal),

  freshness: (signal?: AbortSignal): Promise<DebugFreshness> => getLegacy<DebugFreshness>("/api/debug/freshness", signal),

  ipcTelemetry: (limit = 60, signal?: AbortSignal): Promise<IpcTelemetry> =>
    getLegacy<IpcTelemetry>(`/api/debug/ipc-telemetry${qs({ limit })}`, signal),

  snapshots: (signal?: AbortSignal): Promise<SnapshotList> => getLegacy<SnapshotList>("/api/debug/snapshots", signal),

  snapshot: (id: string, signal?: AbortSignal): Promise<DebugState> =>
    getLegacy<DebugState>(`/api/debug/snapshots/${encodeURIComponent(id)}`, signal),

  compare: (a: string, b: string, signal?: AbortSignal): Promise<CompareResult> =>
    getLegacy<CompareResult>(`/api/debug/compare${qs({ a, b })}`, signal),

  trace: (executionId: string, signal?: AbortSignal): Promise<DebugTrace> =>
    getLegacy<DebugTrace>(`/api/debug/trace/${encodeURIComponent(executionId)}`, signal),

  modelTest: (payload: { features?: number[] | null; use_live_features: boolean }): Promise<ModelTestResult> =>
    send<ModelTestResult>("/api/debug/model-test", payload),

  researchDiagnostics: (signal?: AbortSignal): Promise<ResearchRead> =>
    getLegacy<ResearchRead>("/api/research/diagnostics", signal),

  researchEvents: (params: { strategy_id?: string; research_run_id?: string; limit?: number }, signal?: AbortSignal): Promise<ResearchRead> =>
    getLegacy<ResearchRead>(`/api/research/events${qs(params)}`, signal),

  researchEvidence: (params: { strategy_id?: string; research_run_id?: string; limit?: number }, signal?: AbortSignal): Promise<ResearchRead> =>
    getLegacy<ResearchRead>(`/api/research/evidence${qs(params)}`, signal),

  researchGates: (params: { strategy_id?: string; research_run_id?: string; limit?: number }, signal?: AbortSignal): Promise<ResearchRead> =>
    getLegacy<ResearchRead>(`/api/research/gates${qs(params)}`, signal),

  researchHistory: (signal?: AbortSignal): Promise<ResearchRead> => getLegacy<ResearchRead>("/api/research/history", signal),

  researchTrace: (params: { strategy_id?: string; research_run_id?: string; gate_id?: string; evidence_id?: string }, signal?: AbortSignal): Promise<ResearchRead> =>
    getLegacy<ResearchRead>(`/api/research/trace${qs(params)}`, signal),
};
