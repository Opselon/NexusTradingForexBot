/**
 * Chart query perf helpers (wave-3 lane C).
 *
 * The chart panel is the only surface that pulls a deep history window: hover,
 * crosshair, zoom and timeframe switching all happen on the SAME data, so a
 * refetch storm there is felt as dropped frames. These helpers centralise the
 * query key + staleness decisions so the tuning lives in one testable place
 * (DashboardPage keeps its existing useQuery; nothing else calls these).
 *
 * Presentation/caching only — lane-09 invariant: NO derived indicator math.
 */

/** Query-key for the broker history window. One entry per (symbol, tf) so each
 *  timeframe caches independently; `null` timeframe = the engine's own. */
export function chartHistoryKey(symbol: string, timeframe: string | null): readonly [string, string, string] {
  return ["chart-history", symbol, timeframe ?? "engine"] as const;
}

/**
 * staleTime for chart history.
 *
 * The backend pushes live ticks via SSE, so bars on screen are always fresher
 * than any refetch could make them; the 60s refetchInterval is only the
 * safety net for a dead tick stream. Serving the cached window during that
 * window costs nothing in accuracy and removes the second fetch hover/zoom
 * would otherwise trigger (component re-render -> useQuery re-reads options ->
 * staleTime-0 fetch). Below the floor we are lying to the operator about bar
 * freshness, so never return 0.
 */
export function chartStaleMs(): number {
  return 30_000;
}

/**
 * Latest complete-bar timestamp, for cheap "is this cache still live" checks
 * that avoid touching the whole bar array (3000-entry map per hover frame is
 * the exact cost we are removing). Returns null for empty/unknown histories.
 */
export function lastBarTimeMs(bars: readonly { time?: string | null }[] | undefined): number | null {
  if (!bars || bars.length === 0) return null;
  const last = bars[bars.length - 1];
  if (!last || !last.time) return null;
  const ms = Date.parse(last.time);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * True when the cached window is recent enough to keep drawing through a
 * hover/zoom pass instead of refetching. `nowMs` is the shared 1s UI ticker,
 * never Date.now() inside render (deterministic re-renders).
 */
export function chartCacheLive(bars: readonly { time?: string | null }[] | undefined, nowMs: number): boolean {
  const last = lastBarTimeMs(bars);
  if (last === null) return false;
  return nowMs - last <= chartStaleMs();
}
