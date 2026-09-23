/**
 * Wave-3 LANE A seam: chart perf instrumentation (opt-in).
 *
 * Production default is OFF — the badge reads the localStorage flag
 * "nse.chart.perf" only. Metrics: rolling FPS (rAF), frames painted, and the
 * cold/warm draw-call accounting for the static-layer cache
 * (staticRepaints vs cachedFrames).
 */
export interface ChartPerfMetrics {
  fps: number;
  frames: number;
  staticRepaints: number;
  cachedFrames: number;
}

export function perfFlagEnabled(): boolean {
  try {
    return localStorage.getItem("nse.chart.perf") === "1";
  } catch {
    return false;
  }
}

export function useChartPerf(_enabled: boolean): ChartPerfMetrics {
  /* LANE A implements (rAF loop + counters). */
  return { fps: 0, frames: 0, staticRepaints: 0, cachedFrames: 0 };
}
