import { useChartPerf, perfFlagEnabled } from "./chartPerf";
import { useI18n } from "@/stores/i18nStore";

/**
 * Wave-3 LANE A slot: live perf readout, OFF unless localStorage
 * "nse.chart.perf" === "1". Renders nothing in production by default
 * (the hook's rAF loop also stays dormant while the flag is off).
 */
export function ChartPerfBadge() {
  const t = useI18n((s) => s.t);
  const enabled = perfFlagEnabled();
  const m = useChartPerf(enabled);
  if (!enabled) return null;
  const cachePct = Math.round((m.cachedFrames / Math.max(1, m.frames)) * 100);
  return (
    <div className="cx-perf" title={t("dash.chart.perf_title", "chart performance instrumentation (localStorage nse.chart.perf)")}>
      {t("dash.chart.perf_line", "{fps} fps · {frames}f · static {static} · cached {cached} ({pct}%)", {
        fps: Math.round(m.fps),
        frames: m.frames,
        static: m.staticRepaints,
        cached: m.cachedFrames,
        pct: cachePct,
      })}
    </div>
  );
}
