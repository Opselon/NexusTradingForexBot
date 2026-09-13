/**
 * Health — typed transport for the system-health bounded context.
 *
 * Endpoints verified in src/nexus_scalp/web/:
 *  GET /api/debug/health          subsystem widgets (debug_research_routes.py)
 *  GET /api/v1/system/health      {verdict, checks[], critical_failures[]}
 *  GET /api/v1/system/readiness   {ready, verdict, required_layers[], optional_layers[]}
 *  GET /api/v1/system/workers     {workers[], engine_attached}
 *  GET /api/v1/system/version     build identity (product/version/commit/channel/…)
 *  GET /api/v1/system/capabilities api platform discovery
 *  GET /api/v1/system/runtime     {engine_attached,engine_running,warmup_state,inference_enabled,freshness,mode,effective_mode}
 *  GET /api/v1/system/status      high-level (health_verdict + version + runtime)
 *  GET /api/mt5/status            broker connection truth (server.py)
 *  GET /api/news/health           news subsystem + worker telemetry
 *  GET /api/forensics/health      forensic check matrix (debug_research_routes.py)
 *  GET /health                    docker/native probe (raw legacy shape)
 *
 * v1 routes come wrapped in {data, meta} — getV1 unwraps and drops meta, so
 * every function below also exposes nothing else. Feature-local surface over
 * the stable `@/api/client` (lane 1 owns src/api/* — no import races).
 */

import { getLegacy, getV1 } from "@/api/client";

/* ------------------------------ v1 system ------------------------------ */

export interface HealthCheck {
  category: string;
  verdict: string;
  reason?: string;
  suggestion?: string;
  state?: string;
  optional?: boolean;
  [key: string]: unknown;
}

export interface SystemHealthV1 {
  verdict: string;
  checks: HealthCheck[];
  critical_failures: string[];
}

export interface SystemReadinessV1 {
  ready: boolean;
  verdict: string;
  required_layers: HealthCheck[];
  optional_layers: HealthCheck[];
}

export interface WorkerRow {
  name: string;
  state: string;
  attached?: boolean;
  [key: string]: unknown;
}

export interface SystemWorkersV1 {
  workers: WorkerRow[];
  engine_attached: boolean;
}

export interface SystemVersionV1 {
  product?: string | null;
  version?: string | null;
  commit?: string | null;
  dirty?: boolean | string;
  build_timestamp?: string | null;
  platform?: string | null;
  architecture?: string | null;
  python?: string | null;
  channel?: string | null;
  mode?: string | null;
  schema?: string | null;
  [key: string]: unknown;
}

export interface SystemCapabilitiesV1 {
  api_version: string;
  spec: string;
  read_only: boolean;
  domains: Record<string, number>;
  domain_count: number;
  endpoint_count: number;
  pagination?: Record<string, unknown>;
  generated_at?: string;
}

export interface SystemRuntimeV1 {
  engine_attached: boolean;
  engine_running: boolean;
  warmup_state: string | null;
  inference_enabled: boolean;
  freshness: Record<string, unknown> | null;
  mode?: string | null;
  effective_mode?: string | null;
}

export interface SystemStatusV1 {
  health_verdict: string;
  critical_failures: string[];
  version: { product: string | null; version: string | null; commit: string | null; channel: string | null };
  runtime: { engine_attached: boolean; engine_running: boolean; mode: string | null; freshness_overall: string | null };
  checks_count: number;
}

/* ------------------------------ subsystems ------------------------------ */

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

/** /api/mt5/status — only the fields this matrix reads (rest stays untyped). */
export interface Mt5Health {
  available: boolean;
  reason?: string;
  connection?: Record<string, unknown>;
  account?: Record<string, unknown>;
  terminal?: Record<string, unknown>;
  symbol_spec?: Record<string, unknown>;
  error_state?: string | null;
}

export interface NewsHealth {
  available: boolean;
  enabled?: boolean;
  health?: Record<string, unknown>;
  worker?: string | null;
  llm_budget?: Record<string, unknown>;
  calendar?: Record<string, unknown>;
  event_gate?: Record<string, unknown>;
  error?: { code?: string; message?: string };
}

export interface ForensicHealth {
  available: boolean;
  reason?: string;
  error?: { code?: string; message?: string; request_id?: string };
  [key: string]: unknown;
}

export interface ProbeHealth {
  status?: string;
  verdict?: string;
  [key: string]: unknown;
}

export const healthApi = {
  debugHealth: (signal?: AbortSignal): Promise<DebugHealth> => getLegacy<DebugHealth>("/api/debug/health", signal),
  systemHealth: (signal?: AbortSignal): Promise<SystemHealthV1> => getV1<SystemHealthV1>("/api/v1/system/health", signal),
  readiness: (signal?: AbortSignal): Promise<SystemReadinessV1> => getV1<SystemReadinessV1>("/api/v1/system/readiness", signal),
  workers: (signal?: AbortSignal): Promise<SystemWorkersV1> => getV1<SystemWorkersV1>("/api/v1/system/workers", signal),
  version: (signal?: AbortSignal): Promise<SystemVersionV1> => getV1<SystemVersionV1>("/api/v1/system/version", signal),
  capabilities: (signal?: AbortSignal): Promise<SystemCapabilitiesV1> => getV1<SystemCapabilitiesV1>("/api/v1/system/capabilities", signal),
  runtime: (signal?: AbortSignal): Promise<SystemRuntimeV1> => getV1<SystemRuntimeV1>("/api/v1/system/runtime", signal),
  status: (signal?: AbortSignal): Promise<SystemStatusV1> => getV1<SystemStatusV1>("/api/v1/system/status", signal),
  mt5: (signal?: AbortSignal): Promise<Mt5Health> => getLegacy<Mt5Health>("/api/mt5/status", signal),
  news: (signal?: AbortSignal): Promise<NewsHealth> => getLegacy<NewsHealth>("/api/news/health", signal),
  forensics: (signal?: AbortSignal): Promise<ForensicHealth> => getLegacy<ForensicHealth>("/api/forensics/health", signal),
  probe: (signal?: AbortSignal): Promise<ProbeHealth> => getLegacy<ProbeHealth>("/health", signal),
};
