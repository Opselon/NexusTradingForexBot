/**
 * Command Center: application use cases + query bindings.
 *
 * The time machine fetches frames LAZILY: bounds load once, the slider emits
 * a debounced ISO instant, and only that instant's frame is fetched
 * (placeholderData keeps the previous frame on screen while swapping).
 */

import { useQuery } from "@tanstack/react-query";
import { commandCenterApi } from "./api";
import { riskOrder, type CcFleetRowDto } from "./model";

export const commandCenterQueries = {
  overview: (signal?: AbortSignal) => commandCenterApi.overview(signal),
  fleet: (lifecycle: string | undefined, executionFilter: string | undefined, signal?: AbortSignal) =>
    commandCenterApi.fleet(lifecycle, executionFilter, signal),
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
    retry: false,
  });
}
