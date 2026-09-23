/**
 * Factory: application use cases.
 *
 * Loop control (start/pause/resume/stop), generate, evaluate, complete and
 * the provider test/toggle are operator commands: the backend's answer is
 * the only outcome rendered. Sections without a mounted factory keep their
 * {available:false, reason} — honest emptiness over invented dashboards.
 */

import { ApiError } from "@/types/api";
import { factoryApi } from "./api";
import { factoryVerdict, toGenerationVo, type FactoryCommandDto, type Row } from "./model";

/**
 * Safe-envelope guard (logic rule of the perf wave): the transport only
 * rejects NON-2xx answers, but a factory route can answer HTTP 200 with the
 * safe error envelope {error:{code,message,request_id}} (web/errors.py
 * `safe_error_payload`). That is a FAILURE, not data — without this guard the
 * page would render it as "Factory not mounted" (an honest-empty branch) and
 * swallow the backend's request_id. {available:false, reason} WITHOUT an
 * error key stays the unmounted/empty path — that distinction is the guard.
 */
function guardFactoryBody<T>(body: T): T {
  const err = (body as { error?: unknown } | null | undefined)?.error;
  if (err && typeof err === "object") {
    const e = err as { code?: string; message?: string; request_id?: string };
    if (typeof e.code === "string" || typeof e.message === "string") {
      throw new ApiError(
        200,
        typeof e.code === "string" ? e.code : "INTERNAL_ERROR",
        typeof e.message === "string" ? e.message : "Factory backend reported an error envelope.",
        typeof e.request_id === "string" ? e.request_id : null,
        false,
      );
    }
  }
  return body;
}

const guarded = <T,>(p: Promise<T>): Promise<T> => p.then(guardFactoryBody);

export const factoryQueries = {
  status: (signal?: AbortSignal) => guarded(factoryApi.status(signal)),
  generations: (signal?: AbortSignal) => guarded(factoryApi.generations(signal)),
  generation: (id: string, signal?: AbortSignal) => guarded(factoryApi.generation(id, signal)),
  candidates: (generationId: string | undefined, signal?: AbortSignal) => guarded(factoryApi.candidates(generationId, signal)),
  benchmarks: (generationId: string | undefined, signal?: AbortSignal) => guarded(factoryApi.benchmarks(generationId, signal)),
  events: (generationId: string | undefined, signal?: AbortSignal) => guarded(factoryApi.events(generationId, signal)),
  failures: (signal?: AbortSignal) => guarded(factoryApi.failures(signal)),
  ranking: (dimension: string, signal?: AbortSignal) => guarded(factoryApi.ranking(dimension, signal)),
  memory: (signal?: AbortSignal) => guarded(factoryApi.memory(signal)),
  llmConfig: (signal?: AbortSignal) => guarded(factoryApi.llmConfig(signal)),
  providerHealth: (signal?: AbortSignal) => guarded(factoryApi.providerHealth(signal)),
};

export const factoryUseCases = {
  generationList: (rows: Row[]) => rows.map(toGenerationVo),
  generate: async (size: number | undefined): Promise<FactoryCommandDto> => factoryApi.generate(size),
  evaluate: (id: string) => factoryApi.evaluate(id),
  complete: (id: string) => factoryApi.complete(id),
  loopStart: () => factoryApi.loopStart(),
  loopPause: () => factoryApi.loopPause(),
  loopResume: () => factoryApi.loopResume(),
  loopStop: () => factoryApi.loopStop(),
  providerTest: () => factoryApi.providerTest(),
  providerToggle: (enabled: boolean) => factoryApi.providerToggle(enabled),
  saveLlmConfig: (payload: Parameters<typeof factoryApi.saveLlmConfig>[0]) => factoryApi.saveLlmConfig(payload),
  verdict: factoryVerdict,
};
