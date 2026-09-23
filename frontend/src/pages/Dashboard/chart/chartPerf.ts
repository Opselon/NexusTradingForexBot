/**
 * Wave-3 LANE A seam: chart perf instrumentation (opt-in).
 *
 * Production default is OFF — the badge reads the localStorage flag
 * "nse.chart.perf" only. Metrics: rolling FPS (own rAF loop, only while the
 * flag is on), frames observed, and the cold/warm draw-call accounting for the
 * static-layer cache (staticRepaints vs cachedFrames — fed by
 * ./paintCache's per-frame counters).
 *
 * No new network, no new fetches, no indicator math — observation only.
 */
import { useEffect, useRef, useState } from "react";
import { readPaintCounters } from "./paintCache";

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

const ZERO: ChartPerfMetrics = { fps: 0, frames: 0, staticRepaints: 0, cachedFrames: 0 };
/** Rolling FPS window (frames) and the state-push throttle (ms). */
const FPS_WINDOW = 60;
const PUSH_EVERY_MS = 250;

/**
 * Rolling FPS + cache hit-rate metrics. The rAF loop runs ONLY while enabled
 * (the badge is null in production by default, so this costs nothing there);
 * state is pushed at most every PUSH_EVERY_MS to avoid re-render churn.
 */
export function useChartPerf(enabled: boolean): ChartPerfMetrics {
  const [metrics, setMetrics] = useState<ChartPerfMetrics>(ZERO);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      setMetrics(ZERO);
      return;
    }
    let alive = true;
    const intervals: number[] = []; // rolling window (ms between frames)
    let last = 0;
    let lastPush = 0;
    let base = readPaintCounters(); // deltas, not absolutes, across mounts

    const loop = (now: number) => {
      if (!alive) return;
      if (last > 0) {
        intervals.push(now - last);
        if (intervals.length > FPS_WINDOW) intervals.shift();
      }
      last = now;
      if (now - lastPush >= PUSH_EVERY_MS) {
        lastPush = now;
        const sum = intervals.reduce((a, b) => a + b, 0);
        const cur = readPaintCounters();
        const snap: ChartPerfMetrics = {
          fps: sum > 0 ? (intervals.length / sum) * 1000 : 0,
          frames: cur.frames - base.frames,
          staticRepaints: cur.staticRepaints - base.staticRepaints,
          cachedFrames: cur.cachedFrames - base.cachedFrames,
        };
        // Rebase so the counters never grow unbounded across a long session.
        if (snap.frames > 1000) base = { ...cur };
        setMetrics(snap);
      }
      rafRef.current = window.requestAnimationFrame(loop);
    };
    rafRef.current = window.requestAnimationFrame(loop);

    return () => {
      alive = false;
      if (rafRef.current !== null) window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [enabled]);

  return metrics;
}
