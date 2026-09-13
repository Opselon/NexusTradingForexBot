/**
 * Debug: application services (queries + the one mutation).
 *
 * All reads are backend-authoritative snapshots; polling is operator-controlled
 * (pause/resume on the page passes `paused` down here). The model-test mutation
 * validates the vector FIRST (dimension + finiteness + bounds via the shared
 * validation layer) and refuses to send an invalid payload.
 */

import { useMutation, useQuery, type QueryKey } from "@tanstack/react-query";
import { debugApi, type DebugState, type ModelTestResult } from "./api";
import { checkNumericVector } from "@/features/config/validation";
import { parseVector, type VectorSpec } from "./model";

export const DEBUG_STATE_KEY: QueryKey = ["debug", "state"];
export const DEBUG_HEALTH_KEY: QueryKey = ["debug", "health"];
export const DEBUG_FEATURES_KEY: QueryKey = ["debug", "features"];
export const DEBUG_FRESHNESS_KEY: QueryKey = ["debug", "freshness"];
export const DEBUG_IPC_KEY: QueryKey = ["debug", "ipc"];
export const DEBUG_SNAPSHOTS_KEY: QueryKey = ["debug", "snapshots"];
export const RESEARCH_READS_KEY: QueryKey = ["debug", "research"];

export function useDebugStateQuery(paused = false) {
  return useQuery({
    queryKey: DEBUG_STATE_KEY,
    queryFn: ({ signal }) => debugApi.state(signal),
    refetchInterval: paused ? false : 20_000,
  });
}

export function useDebugHealthQuery(paused = false) {
  return useQuery({
    queryKey: DEBUG_HEALTH_KEY,
    queryFn: ({ signal }) => debugApi.health(signal),
    refetchInterval: paused ? false : 15_000,
  });
}

export function useDebugFeaturesQuery(paused = false) {
  return useQuery({
    queryKey: DEBUG_FEATURES_KEY,
    queryFn: ({ signal }) => debugApi.features(signal),
    refetchInterval: paused ? false : 15_000,
  });
}

/** Freshness runs a live no-cache re-diagnosis on the backend — keep the
 *  default interval slow and let the operator trigger manual refetches. */
export function useDebugFreshnessQuery(paused = true, intervalMs = 60_000) {
  return useQuery({
    queryKey: DEBUG_FRESHNESS_KEY,
    queryFn: ({ signal }) => debugApi.freshness(signal),
    refetchInterval: paused ? false : intervalMs,
  });
}

export function useIpcTelemetryQuery(paused = false) {
  return useQuery({
    queryKey: DEBUG_IPC_KEY,
    queryFn: ({ signal }) => debugApi.ipcTelemetry(60, signal),
    refetchInterval: paused ? false : 20_000,
  });
}

export function useSnapshotsQuery(paused = false) {
  return useQuery({
    queryKey: DEBUG_SNAPSHOTS_KEY,
    queryFn: ({ signal }) => debugApi.snapshots(signal),
    refetchInterval: paused ? false : 30_000,
  });
}

/** A stored snapshot by id (captured one is read back for compare/detail). */
export function useSnapshotDetail(id: string | null) {
  return useQuery({
    queryKey: ["debug", "snapshot", id],
    queryFn: ({ signal }) => debugApi.snapshot(id ?? "", signal),
    enabled: !!id,
    staleTime: 60_000,
  });
}

export function useCompareQuery(a: string | null, b: string | null) {
  return useQuery({
    queryKey: ["debug", "compare", a, b],
    queryFn: ({ signal }) => debugApi.compare(a ?? "", b ?? "", signal),
    enabled: !!a && !!b && a !== b,
    staleTime: 30_000,
  });
}

export function useTraceQuery(executionId: string | null) {
  return useQuery({
    queryKey: ["debug", "trace", executionId],
    queryFn: ({ signal }) => debugApi.trace(executionId ?? "", signal),
    enabled: !!executionId && executionId.trim() !== "",
    staleTime: 60_000,
  });
}

/* ---------------- model test (the lane's mutation) ---------------- */

export interface ModelTestOutcome {
  ok: boolean;
  message: string;
  requestId: string | null;
  result: ModelTestResult | null;
}

/**
 * Validate-then-POST. `spec` (dimension/bounds) comes from the live feature
 * contract; when the contract is not loaded yet the call is blocked, because
 * guessing a width would guarantee a 422.
 */
export function useModelTest() {
  return useMutation<ModelTestOutcome, Error, { spec: VectorSpec | null; cells: string[]; useLive: boolean }>({
    mutationFn: async ({ spec, cells, useLive }): Promise<ModelTestOutcome> => {
      if (useLive) {
        try {
          const r = await debugApi.modelTest({ use_live_features: true });
          return { ok: r.success === true, message: r.success ? `Live-vector probe evaluated (${r.model_source ?? "?"}).` : "Backend refused the live probe.", requestId: null, result: r };
        } catch (e) {
          return { ok: false, message: e instanceof Error ? e.message : "Live probe failed.", requestId: (e as { requestId?: string } | null)?.requestId ?? null, result: null };
        }
      }
      if (!spec) {
        return { ok: false, message: "Feature contract not loaded yet — dimension unknown, refusing to send a guessed vector.", requestId: null, result: null };
      }
      const parsed = parseVector(cells);
      const check = checkNumericVector(parsed, { length: spec.dimension, min: spec.min, max: spec.max, label: "features" });
      if (!check.ok) {
        const shown = check.errors.slice(0, 6).join(" · ");
        return {
          ok: false,
          message: `Client validation blocked the POST (${check.errors.length} problem${check.errors.length === 1 ? "" : "s"}): ${shown}${check.errors.length > 6 ? " …" : ""}`,
          requestId: null,
          result: null,
        };
      }
      try {
        const r = await debugApi.modelTest({ features: check.values, use_live_features: false });
        return {
          ok: r.success === true,
          message: r.success ? `Inference on the ${r.model_source ?? "?"} model completed (${r.latency_ms ?? "?"} ms).` : "Backend refused the inference.",
          requestId: null,
          result: r,
        };
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Model test failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
          result: null,
        };
      }
    },
  });
}

/* ---------------- research read-only panels ---------------- */

export function useResearchRead<T = Record<string, unknown>>(kind: "diagnostics" | "events" | "evidence" | "gates" | "history" | "trace", params: Record<string, string> = {}, enabled = true, paused = false) {
  return useQuery({
    queryKey: [...RESEARCH_READS_KEY, kind, params],
    queryFn: ({ signal }) => {
      switch (kind) {
        case "diagnostics":
          return debugApi.researchDiagnostics(signal) as Promise<T>;
        case "events":
          return debugApi.researchEvents(params, signal) as Promise<T>;
        case "evidence":
          return debugApi.researchEvidence(params, signal) as Promise<T>;
        case "gates":
          return debugApi.researchGates(params, signal) as Promise<T>;
        case "history":
          return debugApi.researchHistory(signal) as Promise<T>;
        case "trace":
          return debugApi.researchTrace(params, signal) as Promise<T>;
      }
    },
    enabled,
    refetchInterval: paused ? false : 30_000,
  });
}

export type { DebugState };
