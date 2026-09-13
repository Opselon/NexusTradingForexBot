/**
 * Factory: application use cases.
 *
 * Loop control (start/pause/resume/stop), generate, evaluate, complete and
 * the provider test/toggle are operator commands: the backend's answer is
 * the only outcome rendered. Sections without a mounted factory keep their
 * {available:false, reason} — honest emptiness over invented dashboards.
 */

import { factoryApi } from "./api";
import { factoryVerdict, toGenerationVo, type FactoryCommandDto, type Row } from "./model";

export const factoryQueries = {
  status: (signal?: AbortSignal) => factoryApi.status(signal),
  generations: (signal?: AbortSignal) => factoryApi.generations(signal),
  generation: (id: string, signal?: AbortSignal) => factoryApi.generation(id, signal),
  candidates: (generationId: string | undefined, signal?: AbortSignal) => factoryApi.candidates(generationId, signal),
  benchmarks: (generationId: string | undefined, signal?: AbortSignal) => factoryApi.benchmarks(generationId, signal),
  events: (generationId: string | undefined, signal?: AbortSignal) => factoryApi.events(generationId, signal),
  failures: (signal?: AbortSignal) => factoryApi.failures(signal),
  ranking: (dimension: string, signal?: AbortSignal) => factoryApi.ranking(dimension, signal),
  memory: (signal?: AbortSignal) => factoryApi.memory(signal),
  llmConfig: (signal?: AbortSignal) => factoryApi.llmConfig(signal),
  providerHealth: (signal?: AbortSignal) => factoryApi.providerHealth(signal),
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
