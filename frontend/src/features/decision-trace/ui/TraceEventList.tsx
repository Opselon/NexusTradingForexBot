/**
 * TraceEventList — live event feed (§39-42).
 *
 * Renders event rows newest-first from the bounded store list. Every row
 * carries its sequence + stage + the honest reason a verdict produced
 * (verbatim from the runtime detail). Selecting a row drills into its trace.
 */

import { memo, useMemo } from "react";
import { ErrorState, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { TraceEvent } from "../types";

interface Props {
  events: TraceEvent[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  limit?: number;
  /** True while the observer stream is still opening and nothing arrived yet. */
  pending?: boolean;
  /** Backend's own stream failure words — rendered instead of the empty state. */
  error?: string | null;
}

const STAGE_GROUPS: Record<string, string> = {
  MARKET: "market",
  FEATURES: "feature",
  MODEL: "model",
  INFERENCE: "model",
  REGIME: "regime",
  POLICY: "policy",
  POST_POLICY: "policy",
  GATE: "gate",
  RULE: "gate",
  RISK: "risk",
  DECISION: "decision",
  EXECUTION: "exec",
  GATEWAY: "gateway",
  MT5: "gateway",
  ORDER: "exec",
  POSITION: "exec",
};

function pickReason(detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  for (const k of [
    "reason",
    "rejection_reason",
    "reason_code",
    "verdict",
    "action",
    "status",
    "result",
    "model_id",
    "regime",
    "signal",
  ]) {
    const v = detail[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}

function hmsec(ts: string): string {
  try {
    const d = new Date(ts);
    return (
      String(d.getHours()).padStart(2, "0") +
      ":" +
      String(d.getMinutes()).padStart(2, "0") +
      ":" +
      String(d.getSeconds()).padStart(2, "0") +
      "." +
      String(d.getMilliseconds()).padStart(3, "0")
    );
  } catch {
    return ts;
  }
}

export const TraceEventRow = memo(function TraceEventRow({
  ev,
  selected,
  onSelect,
}: {
  ev: TraceEvent;
  selected: boolean;
  onSelect: (id: string | null) => void;
}) {
  const t = useI18n((s) => s.t);
  const reason = pickReason(ev.detail);
  const symbol = typeof ev.detail?.symbol === "string" ? (ev.detail.symbol as string) : null;
  return (
    <button
      className={`dt-ev-row ${selected ? "selected" : ""} ${ev.terminal ? "terminal" : ""} ${ev.unmapped ? "unmapped" : ""}`}
      onClick={() => onSelect(selected ? null : ev.event_id)}
      title={t("trace.events.row_title", "{stage} @ {ts} · seq {seq}{unmapped}{gap}", { stage: ev.stage, ts: ev.timestamp, seq: ev.sequence, unmapped: ev.unmapped ? t("trace.events.row_unmapped", " · UNMAPPED") : "", gap: ev.provenance_gap ? t("trace.events.row_gap", " · PROVENANCE GAP") : "" })}
    >
      <span className="dt-ev-time">{hmsec(ev.timestamp)}</span>
      <span className="dt-ev-seq">{ev.sequence}</span>
      <span className={`dt-ev-stage ${STAGE_GROUPS[ev.stage] ?? "other"}`}>{ev.stage}</span>
      {symbol ? <span className="dt-ev-sym">{symbol}</span> : <span className="dt-ev-sym none">—</span>}
      <span className="dt-ev-reason">{reason}</span>
      <span className="dt-ev-ms">{ev.latency_us != null ? `${(ev.latency_us / 1000).toFixed(1)}ms` : ""}</span>
    </button>
  );
});

export function TraceEventList({ events, selectedId, onSelect, limit = 140, pending, error }: Props) {
  const t = useI18n((s) => s.t);
  const rows = useMemo(() => {
    const sorted = events.slice().sort((a, b) => b.sequence - a.sequence);
    return sorted.slice(0, limit);
  }, [events, limit]);

  // State order (pages/_shared/SectionState quality bar): loading → error → empty.
  if (!rows.length && pending) {
    return (
      <div className="dt-ev-empty" role="status">
        <div className="dt-ev-empty-title">{t("ui.state.loading", "Loading backend state…")}</div>
        <Skeleton count={6} height={12} />
      </div>
    );
  }

  if (!rows.length && error) {
    return <ErrorState message={error} />;
  }

  if (!rows.length) {
    return (
      <div className="dt-ev-empty">
        <div className="dt-ev-empty-title">{t("trace.events.empty_title", "NO EVENTS OBSERVED")}</div>
        <div className="dt-ev-empty-sub">
          {t("trace.events.empty_sub", "The stream is connected but no decision event has been recorded yet.")}
        </div>
      </div>
    );
  }

  return (
    <div className="dt-ev-list" role="list" aria-label={t("trace.events.aria", "Live decision events")}>
      {rows.map((ev) => (
        <TraceEventRow
          key={ev.event_id}
          ev={ev}
          selected={selectedId === ev.event_id}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
