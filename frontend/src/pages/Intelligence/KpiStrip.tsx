/**
 * PURPOSE:  KPI strip for /alt/intelligence — the four backend news-state
 *           metric cards under the hero.
 * OWNER:    ui/w2-lane-a  (future edits to this file belong to lane A)
 * CONSUMES: NewsState payload fields only (state, bullish/bearish_score,
 *           confidence, xauusd_relevance, active_event_count, timestamp,
 *           stale, freshness, available) + the query's pending flag.
 * PROVIDES: default KpiStrip — rendered by IntelligencePage.
 * INVARIANTS: numbers are the backend's own values through the shared
 *           formatters; missing/unavailable renders "—" or the explicit
 *           UNAVAILABLE word — never zero-filled, never inferred.
 * EXTEND:   a new KPI = a new backend field rendered verbatim.
 */

import { MetricCard } from "@/components/primitives";
import type { NewsState } from "@/types/domain";
import { formatDateTime, formatPct } from "@/lib/format";
import { ScoreBar } from "@/pages/Intelligence/signalBits";

interface KpiStripProps {
  ns: NewsState | undefined;
  pending: boolean;
}

export default function KpiStrip({ ns, pending }: KpiStripProps) {
  return (
<div className="grid cols-4 ix-kpis">
  <MetricCard
    label="News state (backend)"
    value={pending ? "…" : ns?.available ? (ns.state ?? "—") : "UNAVAILABLE"}
    tone={ns?.stale ? undefined : ns?.state === "BREAKING" || ns?.state === "HIGH_IMPACT" ? "neg" : "dim"}
    sub={ns?.available ? (ns.stale ? "⚠ backend marks context STALE" : `freshness ${ns.freshness ?? "—"}`) : "news subsystem disabled or offline"}
  />
  <MetricCard
    label="Bullish / Bearish"
    value={ns?.available ? `${formatPct((ns.bullish_score ?? 0) * 100, 0)} / ${formatPct((ns.bearish_score ?? 0) * 100, 0)}` : "—"}
    tone="dim"
    sub="backend-scored sentiment (display only)"
  />
  <MetricCard
    label="Context confidence"
    value={ns?.confidence === null || ns?.confidence === undefined ? "—" : formatPct(ns.confidence * 100, 0)}
    tone="dim"
    sub={
      <>
        <ScoreBar value={ns?.confidence ?? null} label="confidence" />
        <div>
          XAUUSD relevance{" "}
          {ns?.xauusd_relevance === null || ns?.xauusd_relevance === undefined ? "—" : formatPct(ns.xauusd_relevance * 100, 0)}
        </div>
      </>
    }
  />
  <MetricCard
    label="Active events"
    value={ns?.active_event_count ?? "—"}
    tone="dim"
    sub={`context time ${ns?.timestamp ? formatDateTime(ns.timestamp) : "—"}`}
  />
</div>
  );
}
