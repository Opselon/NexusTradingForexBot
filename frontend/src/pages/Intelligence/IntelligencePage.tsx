/**
 * Intelligence — news / market context / trade intelligence.
 *
 * Stale or unavailable information is ALWAYS labeled as such (backend
 * provides `stale`, `freshness`, `available` flags). No investment decisions
 * or interpretations are computed in the frontend — raw backend signals only.
 */

import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "@/api/intelligenceApi";
import type { EngineSnapshot } from "@/types/domain";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatPct } from "@/lib/format";

interface Props {
  snapshot?: EngineSnapshot | undefined;
}

export default function IntelligencePage({ snapshot: _snapshot }: Props) {
  const stateQuery = useQuery({
    queryKey: ["news-state"],
    queryFn: ({ signal }) => intelligenceApi.newsState(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const healthQuery = useQuery({
    queryKey: ["news-health"],
    queryFn: ({ signal }) => intelligenceApi.newsHealth(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const articlesQuery = useQuery({
    queryKey: ["news-latest"],
    queryFn: ({ signal }) => intelligenceApi.newsLatest(12, signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const summaryQuery = useQuery({
    queryKey: ["intel-summary"],
    queryFn: ({ signal }) => intelligenceApi.intelligenceSummary(signal),
    refetchInterval: 20_000,
    retry: false,
  });
  const autopsyQuery = useQuery({
    queryKey: ["intel-autopsies"],
    queryFn: ({ signal }) => intelligenceApi.autopsies(15, signal),
    refetchInterval: 45_000,
    retry: false,
  });

  const ns = stateQuery.data;

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label="News state (backend)"
          value={stateQuery.isPending ? "…" : ns?.available ? (ns.state ?? "—") : "UNAVAILABLE"}
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
          sub={`XAUUSD relevance ${ns?.xauusd_relevance === null || ns?.xauusd_relevance === undefined ? "—" : formatPct(ns.xauusd_relevance * 100, 0)}`}
        />
        <MetricCard
          label="Active events"
          value={ns?.active_event_count ?? "—"}
          tone="dim"
          sub={`context time ${ns?.timestamp ? formatDateTime(ns.timestamp) : "—"}`}
        />
      </div>

      {ns?.stale && (
        <div className="banner stale" style={{ border: "1px solid rgba(232,161,60,0.4)", borderRadius: 6, marginTop: 10 }}>
          <span>⚠ News context is STALE per the backend — no recent fetch. Values above are not current market context.</span>
        </div>
      )}

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="News subsystem health">
          {healthQuery.isPending ? (
            <div className="muted small">loading…</div>
          ) : healthQuery.data?.available ? (
            <dl className="kv">
              <dt>enabled</dt>
              <dd>{healthQuery.data.enabled ? <span className="badge good">YES</span> : <span className="badge warn">NO</span>}</dd>
              <dt>worker</dt>
              <dd><StatusBadge status={String((healthQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>calendar worker</dt>
              <dd>{healthQuery.data.calendar ? String((healthQuery.data.calendar as { state?: string }).state ?? "present") : "—"}</dd>
              <dt>event gate</dt>
              <dd>{healthQuery.data.event_gate ? String((healthQuery.data.event_gate as { verdict?: string }).verdict ?? "present") : "—"}</dd>
              <dt>llm budget</dt>
              <dd className="small">{healthQuery.data.llm_budget ? "reported (see payload)" : "—"}</dd>
            </dl>
          ) : (
            <EmptyState message="News subsystem unavailable." hint="Engine offline or news_engine not attached — shown as UNAVAILABLE." />
          )}
        </Panel>

        <Panel title="Trade intelligence summary">
          {summaryQuery.isPending ? (
            <div className="muted small">loading…</div>
          ) : summaryQuery.data?.available ? (
            <dl className="kv">
              <dt>lifecycle events</dt>
              <dd>{summaryQuery.data.lifecycle_events ?? 0}</dd>
              <dt>autopsies</dt>
              <dd>{summaryQuery.data.autopsies ?? 0}</dd>
              <dt>worker</dt>
              <dd><StatusBadge status={String((summaryQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>fetch time</dt>
              <dd>{summaryQuery.data.fetch_time ? formatDateTime(summaryQuery.data.fetch_time) : "—"}</dd>
            </dl>
          ) : summaryQuery.isError ? (
            <ErrorState message="Intelligence summary endpoint failed." onRetry={() => summaryQuery.refetch()} />
          ) : (
            <EmptyState message="Trade intelligence unavailable (engine offline)." />
          )}
        </Panel>
      </div>

      <Panel title="Latest canonical articles" tight>
        {articlesQuery.isPending ? (
          <div className="state-block"><div className="spinner" /></div>
        ) : articlesQuery.data?.available && articlesQuery.data.articles && articlesQuery.data.articles.length > 0 ? (
          <DataTable headers={[{ label: "Published" }, { label: "Source" }, { label: "Title" }, { label: "Importance" }]}>
            {articlesQuery.data.articles.map((a) => (
              <tr key={a.article_id}>
                <td>{a.published_at ? formatDateTime(a.published_at) : "—"}</td>
                <td>{a.source_name ?? "—"}</td>
                <td style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.title}</td>
                <td>{String(a.importance ?? "—")}</td>
              </tr>
            ))}
          </DataTable>
        ) : articlesQuery.isError ? (
          <ErrorState message="News feed endpoint failed." onRetry={() => articlesQuery.refetch()} />
        ) : (
          <EmptyState message="No articles available." />
        )}
      </Panel>

      <Panel title="Recent trade autopsies (why trades won/lost)" tight>
        {autopsyQuery.isPending ? (
          <div className="state-block"><div className="spinner" /></div>
        ) : autopsyQuery.data?.available && autopsyQuery.data.autopsies && autopsyQuery.data.autopsies.length > 0 ? (
          <DataTable
            headers={[{ label: "Ticket" }, { label: "Strategy" }, { label: "Outcome" }, { label: "Realized R", num: true }, { label: "Exit reason" }]}
          >
            {autopsyQuery.data.autopsies.map((a, i) => (
              <tr key={String(a.ticket ?? i)}>
                <td>{String(a.ticket ?? "—")}</td>
                <td>{a.strategy_id ?? "—"}</td>
                <td>{a.outcome ?? "—"}</td>
                <td className={`num ${a.realized_r !== null && a.realized_r !== undefined && a.realized_r >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                  {a.realized_r === null || a.realized_r === undefined ? "—" : a.realized_r.toFixed(2)}
                </td>
                <td>{a.exit_reason ?? "—"}</td>
              </tr>
            ))}
          </DataTable>
        ) : autopsyQuery.isError ? (
          <ErrorState message="Autopsy endpoint failed." onRetry={() => autopsyQuery.refetch()} />
        ) : (
          <EmptyState message="No autopsies recorded." />
        )}
      </Panel>
    </div>
  );
}
