import { useChartPerf, perfFlagEnabled } from "./chartPerf";

/**
 * Wave-3 LANE A slot: live perf readout, OFF unless localStorage
 * "nse.chart.perf" === "1". Renders nothing in production by default.
 */
export function ChartPerfBadge() {
  const enabled = perfFlagEnabled();
  const m = useChartPerf(enabled);
  if (!enabled) return null;
  return (
    <div className="cx-perf" title="chart performance instrumentation (localStorage nse.chart.perf)">
      {Math.round(m.fps)} fps · {m.frames}f · cache {Math.round((m.cachedFrames / Math.max(1, m.frames)) * 100)}%
    </div>
  );
}
