/**
 * Research: typed transport surface over @/api/client (stable getV1/getLegacy/send).
 *
 * Legacy endpoints: /api/research/* (raw JSON, serialize_enums, {available} flags).
 * v1 endpoints:     /api/v1/research/* (envelope {data,meta}; getV1 unwraps).
 * Commands (POST):  query-param body for gate_id/run_id/strategy_id per the
 *                   FastAPI route signatures; JSON body for promote/recovery.
 */

import { getLegacy, getV1, send } from "@/api/client";
import type {
  ResearchAnalyticsDto,
  ResearchDatasetsDto,
  ResearchDetailDto,
  ResearchDiagnosticsDto,
  ResearchEvidenceDto,
  ResearchEventsDto,
  ResearchGatesDto,
  ResearchHistoryDto,
  ResearchPreflightDto,
  ResearchQueueDto,
  ResearchRegistryDto,
  ResearchRunsDto,
  ResearchSummaryDto,
  ResearchV1StatusDto,
  ResearchV1StrategiesDto,
  ResearchV1StrategyDetailDto,
  ResearchWorkerDto,
} from "./model";

const q = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const researchApi = {
  // ---- legacy reads -------------------------------------------------------
  summary: (signal?: AbortSignal): Promise<ResearchSummaryDto> =>
    getLegacy<ResearchSummaryDto>("/api/research/summary", signal),

  registry: (lifecycle: string | undefined, signal?: AbortSignal): Promise<ResearchRegistryDto> =>
    getLegacy<ResearchRegistryDto>(`/api/research/registry${q({ lifecycle, limit: 200 })}`, signal),

  registryEntry: (strategyId: string, signal?: AbortSignal): Promise<ResearchRegistryDto> =>
    getLegacy<ResearchRegistryDto>(`/api/research/registry/${encodeURIComponent(strategyId)}`, signal),

  runs: (strategyId: string | undefined, signal?: AbortSignal): Promise<ResearchRunsDto> =>
    getLegacy<ResearchRunsDto>(`/api/research/runs${q({ strategy_id: strategyId, limit: 100 })}`, signal),

  health: (signal?: AbortSignal): Promise<ResearchSummaryDto> =>
    getLegacy<ResearchSummaryDto>("/api/research/health", signal),

  detail: (strategyId: string, signal?: AbortSignal): Promise<ResearchDetailDto> =>
    getLegacy<ResearchDetailDto>(`/api/research/detail/${encodeURIComponent(strategyId)}`, signal),

  gates: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal): Promise<ResearchGatesDto> =>
    getLegacy<ResearchGatesDto>(`/api/research/gates${q({ strategy_id: strategyId, research_run_id: runId, limit: 500 })}`, signal),

  events: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal): Promise<ResearchEventsDto> =>
    getLegacy<ResearchEventsDto>(`/api/research/events${q({ strategy_id: strategyId, research_run_id: runId, limit: 300 })}`, signal),

  evidence: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal): Promise<ResearchEvidenceDto> =>
    getLegacy<ResearchEvidenceDto>(`/api/research/evidence${q({ strategy_id: strategyId, research_run_id: runId, limit: 500 })}`, signal),

  history: (signal?: AbortSignal): Promise<ResearchHistoryDto> =>
    getLegacy<ResearchHistoryDto>("/api/research/history", signal),

  worker: (signal?: AbortSignal): Promise<ResearchWorkerDto> =>
    getLegacy<ResearchWorkerDto>("/api/research/worker", signal),

  queue: (signal?: AbortSignal): Promise<ResearchQueueDto> =>
    getLegacy<ResearchQueueDto>("/api/research/queue", signal),

  analytics: (signal?: AbortSignal): Promise<ResearchAnalyticsDto> =>
    getLegacy<ResearchAnalyticsDto>("/api/research/analytics", signal),

  preflight: (strategyId: string, signal?: AbortSignal): Promise<ResearchPreflightDto> =>
    getLegacy<ResearchPreflightDto>(`/api/research/preflight${q({ strategy_id: strategyId })}`, signal),

  diagnostics: (signal?: AbortSignal): Promise<ResearchDiagnosticsDto> =>
    getLegacy<ResearchDiagnosticsDto>("/api/research/diagnostics", signal),

  // ---- v1 platform reads ---------------------------------------------------
  v1Status: (signal?: AbortSignal): Promise<ResearchV1StatusDto> =>
    getV1<ResearchV1StatusDto>("/api/v1/research/status", signal),

  v1Strategies: (lifecycle: string | undefined, page: number, signal?: AbortSignal): Promise<ResearchV1StrategiesDto> =>
    getV1<ResearchV1StrategiesDto>(`/api/v1/research/strategies${q({ lifecycle, page, page_size: 50 })}`, signal),

  v1StrategyDetail: (strategyId: string, signal?: AbortSignal): Promise<ResearchV1StrategyDetailDto> =>
    getV1<ResearchV1StrategyDetailDto>(`/api/v1/research/strategies/${encodeURIComponent(strategyId)}`, signal),

  v1Datasets: (signal?: AbortSignal): Promise<ResearchDatasetsDto> =>
    getV1<ResearchDatasetsDto>("/api/v1/research/datasets", signal),

  // ---- commands (POST; backend decides the outcome) -------------------------
  discover: (): Promise<unknown> => send<unknown>("/api/research/discover", {}),

  validate: (strategyId: string): Promise<unknown> =>
    send<unknown>(`/api/research/validate${q({ strategy_id: strategyId })}`, {}),

  cancelRun: (researchRunId: string): Promise<unknown> =>
    send<unknown>(`/api/research/cancel${q({ research_run_id: researchRunId })}`, {}),

  retryGate: (gateId: string): Promise<unknown> =>
    send<unknown>(`/api/research/retry-gate${q({ gate_id: gateId })}`, {}),

  promote: (payload: { strategy_id: string; target_lifecycle: "SHADOW" | "ACTIVE"; actor: string; reason?: string }): Promise<unknown> =>
    send<unknown>("/api/research/promote", payload),

  selfHeal: (): Promise<unknown> => send<unknown>("/api/research/self-heal", {}),

  recoverMissingOutcomes: (dryRun: boolean): Promise<unknown> =>
    send<unknown>("/api/research/recover-missing-outcomes", { dry_run: dryRun }),

  repairOutcomes: (): Promise<unknown> => send<unknown>("/api/research/repair-outcomes", {}),
};
