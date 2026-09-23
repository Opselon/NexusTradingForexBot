import { useChartPerf, perfFlagEnabled } from "./chartPerf";

/**
 * Wave-3 LANE A slot: live perf readout, OFF unless localStorage
 * "nse.chart.perf" === "1". Renders nothing in production by default
 * (the hook's rAF loop also stays dormant while the flag is off).
 */
export function ChartPerfBadge() {
  const enabled = perfFlagEnabled();
  const m = useChartPerf(enabled);
  if (!enabled) return null;
  const cachePct = Math.round((m.cachedFrames / Math.max(1, m.frames)) * 100);
  return (
    <div className="cx-perf" title="chart performance instrumentation (localStorage nse.chart.perf)">
      {Math.round(m.fps)} fps · {m.frames}f · static {m.staticRepaints} · cached {m.cachedFrames} ({cachePct}%)
    </div>
  );
}
