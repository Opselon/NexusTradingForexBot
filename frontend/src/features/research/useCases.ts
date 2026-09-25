/**
 * Research: application use cases (query/mutation orchestration).
 *
 * Backend-authoritative rule: every list/status read returns exactly what the
 * backend reports ({available:false + reason} when the research subsystem is
 * detached); commands re-read the affected queries after the backend answers —
 * the UI never locally fakes a state change.
 */

import { researchApi } from "./api";
import { toGateVo, toStrategyVo, type Row } from "./model";

export const researchQueries = {
  summary: (signal?: AbortSignal) => researchApi.summary(signal),
  registry: (lifecycle: string | undefined, signal?: AbortSignal) => researchApi.registry(lifecycle, signal),
  runs: (strategyId: string | undefined, signal?: AbortSignal) => researchApi.runs(strategyId, signal),
  health: (signal?: AbortSignal) => researchApi.health(signal),
  detail: (strategyId: string, signal?: AbortSignal) => researchApi.detail(strategyId, signal),
  gates: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal) =>
    researchApi.gates(strategyId, runId, signal),
  events: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal) =>
    researchApi.events(strategyId, runId, signal),
  evidence: (strategyId: string | undefined, runId: string | undefined, signal?: AbortSignal) =>
    researchApi.evidence(strategyId, runId, signal),
  history: (signal?: AbortSignal) => researchApi.history(signal),
  worker: (signal?: AbortSignal) => researchApi.worker(signal),
  queue: (signal?: AbortSignal) => researchApi.queue(signal),
  analytics: (signal?: AbortSignal) => researchApi.analytics(signal),
  preflight: (strategyId: string, signal?: AbortSignal) => researchApi.preflight(strategyId, signal),
  diagnostics: (signal?: AbortSignal) => researchApi.diagnostics(signal),
  v1Status: (signal?: AbortSignal) => researchApi.v1Status(signal),
  v1Strategies: (lifecycle: string | undefined, page: number, signal?: AbortSignal) =>
    researchApi.v1Strategies(lifecycle, page, signal),
  v1StrategyDetail: (strategyId: string, signal?: AbortSignal) => researchApi.v1StrategyDetail(strategyId, signal),
  v1Datasets: (signal?: AbortSignal) => researchApi.v1Datasets(signal),
};

export const researchUseCases = {
  /** Registry rows -> sorted VO list (pipeline stage first, newest updated first). */
  registryList(rows: Row[]) {
    return rows.map(toStrategyVo).sort((a, b) => {
      if (a.terminal !== b.terminal) return a.terminal ? 1 : -1;
      if (a.lifecycleStage !== b.lifecycleStage) return a.lifecycleStage - b.lifecycleStage;
      return (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "");
    });
  },
  gateList(rows: Row[]) {
    return rows.map(toGateVo);
  },

  // Commands (mutation surface). Return the raw backend verdict object; the
  // caller mirrors it inline + toast via useMutationFeedback-style state.
  discover: () => researchApi.discover(),
  validate: (strategyId: string) => researchApi.validate(strategyId),
  cancelRun: (runId: string) => researchApi.cancelRun(runId),
  retryGate: (gateId: string) => researchApi.retryGate(gateId),
  promote: (strategyId: string, target: "SHADOW" | "ACTIVE", actor: string, reason: string) =>
    researchApi.promote({ strategy_id: strategyId, target_lifecycle: target, actor, reason }),
  selfHeal: () => researchApi.selfHeal(),
  recoverMissingOutcomes: (dryRun: boolean) => researchApi.recoverMissingOutcomes(dryRun),
  repairOutcomes: () => researchApi.repairOutcomes(),
};
