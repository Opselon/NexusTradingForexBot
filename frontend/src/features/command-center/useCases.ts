/**
 * Command Center: application use cases + query bindings.
 *
 * The time machine fetches frames LAZILY: bounds load once, the slider emits
 * a debounced ISO instant, and only that instant's frame is fetched
 * (placeholderData keeps the previous frame on screen while swapping).
 */

import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@/types/api";
import { commandCenterApi } from "./api";
import { riskOrder, type CcFleetRowDto } from "./model";

/**
 * Data-delivery policy for EVERY command-center query ("data must send for
 * sure"): retry only what a retry can fix — transport timeouts, network
 * failures, 429/5xx (ApiError.retryable or status 0/5xx) — up to 3 attempts
 * with exponential backoff (1s/2s/4s, capped 8s). A 4xx logic error is never
 * retried (it would just fail again).
 */
export function ccRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 3) return false;
  if (error instanceof ApiError) return error.retryable || error.status === 0 || error.status >= 500;
  // Caller aborts (query cancelled/unmounted) must not retry.
  if (error instanceof DOMException && error.name === "AbortError") return false;
  return true;
}

export const ccRetryDelay = (attempt: number): number => Math.min(1000 * 2 ** attempt, 8000);

export const commandCenterQueries = {
  overview: (signal?: AbortSignal) => commandCenterApi.overview(signal),
  fleet: (lifecycle: string | undefined, executionFilter: string | undefined, signal?: AbortSignal) =>
    commandCenterApi.fleet(lifecycle, executionFilter, signal),
  spatial: (signal?: AbortSignal) => commandCenterApi.spatial(signal),
  inspector: (id: string, signal?: AbortSignal) => commandCenterApi.inspector(id, signal),
  safety: (id: string, signal?: AbortSignal) => commandCenterApi.executionSafety(id, signal),
  timeline: (id: string, signal?: AbortSignal) => commandCenterApi.timeline(id, signal),
  tmBounds: (signal?: AbortSignal) => commandCenterApi.tmBounds(signal),
  tmFrame: (at: string, signal?: AbortSignal) => commandCenterApi.tmFrame(at, signal),
};

export const commandCenterUseCases = {
  /** Risk-first fleet ordering (blocked/unknown/stale float to the top). */
  fleetByRisk(rows: CcFleetRowDto[], nowMs: number): CcFleetRowDto[] {
    return riskOrder(rows, nowMs);
  },
};

/** Debounced lazy frame query hook (slider -> fetch only the settled value). */
export function useTimeMachineFrame(debouncedAt: string | null, delayGuardMs = 0) {
  void delayGuardMs;
  return useQuery({
    queryKey: ["command-center", "tm-frame", debouncedAt],
    queryFn: ({ signal }) => commandCenterQueries.tmFrame(debouncedAt ?? "", signal),
    enabled: Boolean(debouncedAt),
    placeholderData: (prev) => prev,
    staleTime: 60_000,
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });
}
