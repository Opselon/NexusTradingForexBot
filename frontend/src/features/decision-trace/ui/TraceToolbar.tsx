/**
 * TraceToolbar — the live telemetry rail for the observer surface.
 *
 * Every figure is a verbatim backend field (§38 verbatim rendering). Missing
 * data renders "—", never a synthesized value. Observer lifecycle toggles
 * the detailed path (never engine behavior); the no-subscriber auto-stop on
 * the backend covers an unmounted tab.
 */

import { MetricCard } from "@/components/primitives";
import { useLatencyQuery, useObserverQuery, useStartObserver, useStopObserver } from "../useCases";
import type { LatencyStats, ObserverSnapshot } from "../types";

function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v < 1) return `${v.toFixed(2)}ms`;
  return `${v.toFixed(1)}ms`;
}

function totalP99(lat: LatencyStats | undefined): string {
  if (!lat || !lat.stages) return "—";
  let worst = 0;
  for (const stage of Object.values(lat.stages)) {
    const p99 = stage?.p99_us;
    if (typeof p99 === "number" && !Number.isNaN(p99)) worst = Math.max(worst, p99);
  }
  if (!worst) return "—";
  return fmtMs(worst / 1000);
}

function stageCount(lat: LatencyStats | undefined): string {
  if (!lat || !lat.stages) return "0";
  return String(Object.keys(lat.stages).length);
}

function counter(obs: ObserverSnapshot | undefined, key: string): string {
  const c = obs?.counters;
  if (!c) return "0";
  const v = c[key];
  return v === undefined || v === null ? "0" : String(v);
}

export function TraceToolbar({
  paused,
  onTogglePause,
}: {
  paused: boolean;
  onTogglePause: () => void;
}) {
  const obsQ = useObserverQuery(paused);
  const latQ = useLatencyQuery(paused);
  const startMut = useStartObserver();
  const stopMut = useStopObserver();
  const obs = obsQ.data as ObserverSnapshot | undefined;

  const status = obs?.status ?? "OFF";
  const isOn = status === "ACTIVE";
  const bus = obs ?? undefined;

  return (
    <div className="dt-toolbar" role="region" aria-label="Observer telemetry">
      <div className="dt-tb-status">
        <span className={`dt-tb-dot ${isOn ? "on" : "off"}`} aria-hidden="true" />
        <span className="dt-tb-label">OBSERVER</span>
        <span className={`dt-tb-value ${isOn ? "on" : "off"}`}>{status}</span>
        <span className="dt-tb-note">
          {isOn ? "detailed tracing active" : "low-overhead path (no observers)"}
        </span>
      </div>

      <div className="dt-tb-actions">
        <button
          className="dt-tb-btn primary"
          disabled={isOn || startMut.isPending}
          onClick={() => void startMut.mutateAsync()}
        >
          {startMut.isPending ? "starting…" : "Start detailed trace"}
        </button>
        <button
          className="dt-tb-btn"
          disabled={!isOn || stopMut.isPending}
          onClick={() => void stopMut.mutateAsync()}
        >
          {stopMut.isPending ? "stopping…" : "Stop"}
        </button>
        <button className="dt-tb-btn" onClick={onTogglePause}>
          {paused ? "Resume polling" : "Pause polling"}
        </button>
      </div>

      <div className="dt-tb-metrics">
        <MetricCard label="Events captured" value={counter(bus, "events")} />
        <MetricCard label="Decisions" value={String(bus?.decisions_retained ?? 0)} />
        <MetricCard label="Latency p99 (worst stage)" value={totalP99(latQ.data as LatencyStats | undefined)} />
        <MetricCard label="Stages timed" value={stageCount(latQ.data as LatencyStats | undefined)} />
        <MetricCard label="Sessions" value={String(bus?.recent_sessions?.length ?? 0)} />
        <MetricCard label="Subscribers" value={String(bus?.subscribers?.length ?? 0)} />
        <MetricCard label="Coalesced" value={counter(bus, "coalesced_events")} />
        <MetricCard label="Dropped (visual)" value={counter(bus, "dropped_visual_events")} />
        <MetricCard
          label="Ring usage"
          value={
            bus && bus.events_ring_capacity
              ? `${bus.events_retained}/${bus.events_ring_capacity}`
              : "—"
          }
        />
      </div>
    </div>
  );
}
