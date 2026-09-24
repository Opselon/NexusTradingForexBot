/**
 * TraceTimeline — latency waterfall of a trace's stages (§26/§74).
 *
 * Bars are positioned by each stage's ACTUAL offset (monotonic_ns) and
 * latency (latency_us) from observed events. A stage with no observed
 * latency renders a zero-width marker labelled NOT OBSERVED — never an
 * inferred bar.
 */

import { memo } from "react";
import { useI18n } from "@/stores/i18nStore";
import type { TimelineEntry } from "../traceGraph";

interface Props {
  entries: TimelineEntry[];
  onSelectStage?: (stage: string | null) => void;
  selectedStage?: string | null;
}

export const TraceTimeline = memo(function TraceTimeline({
  entries,
  onSelectStage,
  selectedStage,
}: Props) {
  const t = useI18n((s) => s.t);
  if (!entries.length) {
    return (
      <div className="dt-tl-empty">
        <div className="dt-tl-empty-title">{t("trace.timeline.empty_title", "NO LATENCY OBSERVED")}</div>
        <div className="dt-tl-empty-sub">
          {t("trace.timeline.empty_sub", "The trace holds no stage timing yet — timing appears as the runtime emits stage events.")}
        </div>
      </div>
    );
  }

  const totalUs = entries.reduce((a, e) => {
    const end = e.offset_us + (e.event.latency_us ?? 0);
    return Math.max(a, end);
  }, 0);
  const span = Math.max(totalUs, 1);
  const labelW = 132;
  const trackW = `calc(100% - ${labelW + 56}px)`;

  return (
    <div className="dt-tl" role="table" aria-label={t("trace.timeline.aria", "Stage latency timeline")} dir="ltr">
      <div className="dt-tl-scale" style={{ marginLeft: labelW, width: trackW }}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <span key={f} className="dt-tl-tick" style={{ left: `${f * 100}%` }}>
            {(span * f / 1000).toFixed(1)}ms
          </span>
        ))}
      </div>
      {entries.map((entry) => {
        const ev = entry.event;
        const observed = ev.latency_us != null;
        const left = Math.min(100, (entry.offset_us / span) * 100);
        const w = Math.max(observed ? 0.4 : 0.2, ((ev.latency_us ?? 0) / span) * 100);
        return (
          <button
            key={ev.event_id}
            className={`dt-tl-row ${selectedStage === ev.stage ? "selected" : ""} ${observed ? "" : "unobserved"}`}
            onClick={() => onSelectStage?.(selectedStage === ev.stage ? null : ev.stage)}
            title={`${ev.stage}: ${observed ? `${(ev.latency_us! / 1000).toFixed(2)}ms` : t("trace.marker.not_observed", "NOT OBSERVED")}`}
          >
            <span className="dt-tl-label" style={{ width: labelW }}>{ev.stage}</span>
            <span className="dt-tl-track" style={{ width: trackW }}>
              <span
                className={`dt-tl-bar ${observed ? "observed" : "unobserved"}`}
                style={{ left: `${left}%`, width: `${Math.min(w, 100 - left)}%` }}
              />
            </span>
            <span className="dt-tl-dur">{observed ? `${(ev.latency_us! / 1000).toFixed(2)}ms` : "—"}</span>
          </button>
        );
      })}
    </div>
  );
});
