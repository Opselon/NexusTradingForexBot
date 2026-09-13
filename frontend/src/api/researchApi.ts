/**
 * researchApi — research pipeline (v1 reads + legacy control actions).
 *
 * v1 envelope (api_v1/research.py — getV1):
 *  GET /api/v1/research/datasets | /runs | /status | /strategies | /strategies/{id}
 * Legacy raw JSON (debug_research_routes.py — getLegacy/send):
 *  GET  /api/research/diagnostics | /preflight
 *  POST /api/research/{discover,validate,cancel,self-heal,retry-gate,
 *       repair-outcomes,recover-missing-outcomes}
 * (POST /api/research/promote lives in governanceApi — its home tab.)
 */

import { getLegacy, getV1, send } from "@/core/transport";
import type { ResearchDataset, ResearchRun, ResearchStatus, ResearchStrategy } from "@/types/features";

export const researchApi = {
  // ------------------------------------------------------------------- v1
  datasets: (signal?: AbortSignal): Promise<ResearchDataset[]> =>
    getV1<ResearchDataset[]>("/api/v1/research/datasets", signal),

  runs: (signal?: AbortSignal): Promise<ResearchRun[]> =>
    getV1<ResearchRun[]>("/api/v1/research/runs", signal),

  status: (signal?: AbortSignal): Promise<ResearchStatus> =>
    getV1<ResearchStatus>("/api/v1/research/status", signal),

  strategies: (signal?: AbortSignal): Promise<ResearchStrategy[]> =>
    getV1<ResearchStrategy[]>("/api/v1/research/strategies", signal),

  strategy: (strategyId: string, signal?: AbortSignal): Promise<ResearchStrategy> =>
    getV1<ResearchStrategy>(`/api/v1/research/strategies/${encodeURIComponent(strategyId)}`, signal),

  // --------------------------------------------------------------- legacy
  diagnostics: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/research/diagnostics", signal),

  preflight: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/research/preflight", signal),

  discover: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/discover", payload),

  validate: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/validate", payload),

  cancel: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/cancel", payload),

  selfHeal: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/self-heal", payload),

  retryGate: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/retry-gate", payload),

  repairOutcomes: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/repair-outcomes", payload),

  recoverMissingOutcomes: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/research/recover-missing-outcomes", payload),
};
