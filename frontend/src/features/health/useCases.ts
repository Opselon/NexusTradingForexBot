/**
 * Health: application services. Ten independent reads polled on a 10s budget;
 * every panel owns its query so one dead endpoint degrades one cell, never the
 * whole matrix. Nothing here invents a verdict when a read fails — the UI
 * shows the failed cell with its error.
 */

import { useQuery, type QueryKey } from "@tanstack/react-query";
import { healthApi } from "./api";

export const HEALTH_POLL_MS = 10_000;

export const HEALTH_KEYS = {
  debug: ["health", "debug"] as QueryKey,
  system: ["health", "v1-system"] as QueryKey,
  readiness: ["health", "readiness"] as QueryKey,
  workers: ["health", "workers"] as QueryKey,
  version: ["health", "version"] as QueryKey,
  capabilities: ["health", "capabilities"] as QueryKey,
  runtime: ["health", "runtime"] as QueryKey,
  status: ["health", "status"] as QueryKey,
  mt5: ["health", "mt5"] as QueryKey,
  news: ["health", "news"] as QueryKey,
  forensics: ["health", "forensics"] as QueryKey,
  probe: ["health", "probe"] as QueryKey,
};

function polled<T>(key: QueryKey, fn: (signal: AbortSignal) => Promise<T>, paused: boolean, intervalMs = HEALTH_POLL_MS) {
  return useQuery<T>({
    queryKey: key,
    queryFn: ({ signal }) => fn(signal),
    refetchInterval: paused ? false : intervalMs,
    staleTime: 2_000,
    retry: 0,
  });
}

export function useDebugHealth(paused = false) {
  return polled(HEALTH_KEYS.debug, healthApi.debugHealth, paused);
}
export function useSystemHealth(paused = false) {
  return polled(HEALTH_KEYS.system, healthApi.systemHealth, paused);
}
export function useReadiness(paused = false) {
  return polled(HEALTH_KEYS.readiness, healthApi.readiness, paused);
}
export function useWorkers(paused = false) {
  return polled(HEALTH_KEYS.workers, healthApi.workers, paused);
}
export function useVersion(paused = false) {
  return polled(HEALTH_KEYS.version, healthApi.version, paused, 60_000);
}
export function useCapabilities(paused = false) {
  return polled(HEALTH_KEYS.capabilities, healthApi.capabilities, paused, 60_000);
}
export function useRuntime(paused = false) {
  return polled(HEALTH_KEYS.runtime, healthApi.runtime, paused);
}
export function useSystemStatus(paused = false) {
  return polled(HEALTH_KEYS.status, healthApi.status, paused);
}
export function useMt5(paused = false) {
  return polled(HEALTH_KEYS.mt5, healthApi.mt5, paused);
}
export function useNewsHealth(paused = false) {
  return polled(HEALTH_KEYS.news, healthApi.news, paused, 15_000);
}
export function useForensicsHealth(paused = false) {
  return polled(HEALTH_KEYS.forensics, healthApi.forensics, paused, 30_000);
}
export function useProbe(paused = false) {
  return polled(HEALTH_KEYS.probe, healthApi.probe, paused);
}
