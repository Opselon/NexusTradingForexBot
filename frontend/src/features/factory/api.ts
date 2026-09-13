/**
 * Factory: typed transport surface over @/api/client.
 *
 * Routes verified in src/nexus_scalp/web/factory_routes.py
 * (router prefix /api/factory; legacy raw envelopes {available, ...}):
 *   GET  status, generations, generations/{id}, candidates, benchmarks,
 *        events, failures, ranking, memory, llm-config, provider-health
 *   POST generate {size?, mode?}, evaluate/{candidate_id},
 *        complete/{generation_id}, llm-config {...}, provider-test,
 *        provider-toggle {enabled}, loop/start|pause|resume|stop
 * The factory never touches the live path (module docstring) — surfaced in
 * the UI banner, not just assumed.
 */

import { getLegacy, send } from "@/api/client";
import type {
  FactoryBenchmarksDto,
  FactoryCandidatesDto,
  FactoryCommandDto,
  FactoryEventsDto,
  FactoryFailuresDto,
  FactoryGenerationDto,
  FactoryGenerationsDto,
  FactoryLlmConfigDto,
  FactoryMemoryDto,
  FactoryProviderHealthDto,
  FactoryRankingDto,
  FactoryStatusDto,
} from "./model";

export const factoryApi = {
  status: (signal?: AbortSignal): Promise<FactoryStatusDto> => getLegacy<FactoryStatusDto>("/api/factory/status", signal),

  generations: (signal?: AbortSignal): Promise<FactoryGenerationsDto> =>
    getLegacy<FactoryGenerationsDto>("/api/factory/generations?limit=20", signal),

  generation: (id: string, signal?: AbortSignal): Promise<FactoryGenerationDto> =>
    getLegacy<FactoryGenerationDto>(`/api/factory/generations/${encodeURIComponent(id)}`, signal),

  candidates: (generationId: string | undefined, signal?: AbortSignal): Promise<FactoryCandidatesDto> =>
    getLegacy<FactoryCandidatesDto>(
      `/api/factory/candidates?limit=200${generationId ? `&generation_id=${encodeURIComponent(generationId)}` : ""}`,
      signal,
    ),

  benchmarks: (generationId: string | undefined, signal?: AbortSignal): Promise<FactoryBenchmarksDto> =>
    getLegacy<FactoryBenchmarksDto>(
      `/api/factory/benchmarks?limit=200${generationId ? `&generation_id=${encodeURIComponent(generationId)}` : ""}`,
      signal,
    ),

  events: (generationId: string | undefined, signal?: AbortSignal): Promise<FactoryEventsDto> =>
    getLegacy<FactoryEventsDto>(
      `/api/factory/events?limit=200${generationId ? `&generation_id=${encodeURIComponent(generationId)}` : ""}`,
      signal,
    ),

  failures: (signal?: AbortSignal): Promise<FactoryFailuresDto> =>
    getLegacy<FactoryFailuresDto>("/api/factory/failures?limit=200", signal),

  ranking: (dimension: string, signal?: AbortSignal): Promise<FactoryRankingDto> =>
    getLegacy<FactoryRankingDto>(`/api/factory/ranking?dimension=${encodeURIComponent(dimension)}&limit=50`, signal),

  memory: (signal?: AbortSignal): Promise<FactoryMemoryDto> => getLegacy<FactoryMemoryDto>("/api/factory/memory", signal),

  llmConfig: (signal?: AbortSignal): Promise<FactoryLlmConfigDto> => getLegacy<FactoryLlmConfigDto>("/api/factory/llm-config", signal),

  providerHealth: (signal?: AbortSignal): Promise<FactoryProviderHealthDto> =>
    getLegacy<FactoryProviderHealthDto>("/api/factory/provider-health", signal),

  // ---- commands -----------------------------------------------------------
  generate: (size: number | undefined): Promise<FactoryCommandDto> =>
    send<FactoryCommandDto>("/api/factory/generate", size ? { size } : {}),

  evaluate: (candidateId: string): Promise<FactoryCommandDto> =>
    send<FactoryCommandDto>(`/api/factory/evaluate/${encodeURIComponent(candidateId)}`, {}),

  complete: (generationId: string): Promise<FactoryCommandDto> =>
    send<FactoryCommandDto>(`/api/factory/complete/${encodeURIComponent(generationId)}`, {}),

  loopStart: (): Promise<FactoryCommandDto> => send<FactoryCommandDto>("/api/factory/loop/start", {}),
  loopPause: (): Promise<FactoryCommandDto> => send<FactoryCommandDto>("/api/factory/loop/pause", {}),
  loopResume: (): Promise<FactoryCommandDto> => send<FactoryCommandDto>("/api/factory/loop/resume", {}),
  loopStop: (): Promise<FactoryCommandDto> => send<FactoryCommandDto>("/api/factory/loop/stop", {}),

  providerTest: (): Promise<FactoryCommandDto> => send<FactoryCommandDto>("/api/factory/provider-test", {}),

  /** THE single user control for the factory external feature (CHG-0034).
   *  Enabling re-validates config without a network probe; the trading
   *  engine is never affected. */
  providerToggle: (enabled: boolean): Promise<FactoryProviderHealthDto & { enabled?: boolean; config_block?: Record<string, unknown> }> =>
    send<FactoryProviderHealthDto & { enabled?: boolean; config_block?: Record<string, unknown> }>("/api/factory/provider-toggle", { enabled }),

  saveLlmConfig: (payload: {
    base_url?: string;
    model?: string;
    temperature?: number;
    request_timeout_sec?: number;
    max_requests_per_generation?: number;
  }): Promise<FactoryLlmConfigDto> => send<FactoryLlmConfigDto>("/api/factory/llm-config", payload),
};
