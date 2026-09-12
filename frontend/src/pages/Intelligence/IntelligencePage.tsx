/**
 * Intelligence — news / market context / trade intelligence.
 *
 * Stale or unavailable information is ALWAYS labeled as such (backend
 * provides `stale`, `freshness`, `available` flags). No investment decisions
 * or interpretations are computed in the frontend — raw backend signals only.
 *
 * i18n: backend state words (BREAKING/HIGH_IMPACT, worker states, verdicts)
 * render verbatim; panel titles, metric labels and hint copy translate via
 * alt.intel.*.
 */

import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "@/api/intelligenceApi";
import type { EngineSnapshot } from "@/types/domain";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatPct } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";

interface Props {
  snapshot?: EngineSnapshot | undefined;
}

export default function IntelligencePage({ snapshot: _snapshot }: Props) {
  const t = useI18n((s) => s.t);
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
          label={t("alt.intel.metric_news_state", "News state (backend)")}
          value={stateQuery.isPending ? "…" : ns?.available ? (ns.state ?? "—") : "UNAVAILABLE"}
          tone={ns?.stale ? undefined : ns?.state === "BREAKING" || ns?.state === "HIGH_IMPACT" ? "neg" : "dim"}
          sub={ns?.available ? (ns.stale ? t("alt.intel.sub_context_stale", "⚠ backend marks context STALE") : t("alt.intel.sub_freshness", "freshness {f}", { f: ns.freshness ?? "—" })) : t("alt.intel.sub_disabled", "news subsystem disabled or offline")}
        />
        <MetricCard
          label={t("alt.intel.metric_bull_bear", "Bullish / Bearish")}
          value={ns?.available ? `${formatPct((ns.bullish_score ?? 0) * 100, 0)} / ${formatPct((ns.bearish_score ?? 0) * 100, 0)}` : "—"}
          tone="dim"
          sub={t("alt.intel.sub_sentiment", "backend-scored sentiment (display only)")}
        />
        <MetricCard
          label={t("alt.intel.metric_confidence", "Context confidence")}
          value={ns?.confidence === null || ns?.confidence === undefined ? "—" : formatPct(ns.confidence * 100, 0)}
          tone="dim"
          sub={t("alt.intel.sub_relevance", "XAUUSD relevance {v}", { v: ns?.xauusd_relevance === null || ns?.xauusd_relevance === undefined ? "—" : formatPct(ns.xauusd_relevance * 100, 0) })}
        />
        <MetricCard
          label={t("alt.intel.metric_events", "Active events")}
          value={ns?.active_event_count ?? "—"}
          tone="dim"
          sub={t("alt.intel.sub_context_time", "context time {t}", { t: ns?.timestamp ? formatDateTime(ns.timestamp) : "—" })}
        />
      </div>

      {ns?.stale && (
        <div className="banner stale" style={{ border: "1px solid rgba(232,161,60,0.4)", borderRadius: 6, marginTop: 10 }}>
          <span>{t("alt.intel.stale_banner", "⚠ News context is STALE per the backend — no recent fetch. Values above are not current market context.")}</span>
        </div>
      )}

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("alt.intel.panel_health", "News subsystem health")}>
          {healthQuery.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : healthQuery.data?.available ? (
            <dl className="kv">
              <dt>{t("alt.intel.dt_enabled", "enabled")}</dt>
              <dd>{healthQuery.data.enabled ? <span className="badge good">YES</span> : <span className="badge warn">NO</span>}</dd>
              <dt>{t("alt.ml.dt_worker", "worker")}</dt>
              <dd><StatusBadge status={String((healthQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>{t("alt.intel.dt_calendar", "calendar worker")}</dt>
              <dd>{healthQuery.data.calendar ? String((healthQuery.data.calendar as { state?: string }).state ?? "present") : "—"}</dd>
              <dt>{t("alt.intel.dt_event_gate", "event gate")}</dt>
              <dd>{healthQuery.data.event_gate ? String((healthQuery.data.event_gate as { verdict?: string }).verdict ?? "present") : "—"}</dd>
              <dt>{t("alt.intel.dt_llm_budget", "llm budget")}</dt>
              <dd className="small">{healthQuery.data.llm_budget ? t("alt.intel.llm_reported", "reported (see payload)") : "—"}</dd>
            </dl>
          ) : (
            <EmptyState message={t("alt.intel.empty_health", "News subsystem unavailable.")} hint={t("alt.intel.empty_health_hint", "Engine offline or news_engine not attached — shown as UNAVAILABLE.")} />
          )}
        </Panel>

        <Panel title={t("alt.intel.panel_summary", "Trade intelligence summary")}>
          {summaryQuery.isPending ? (
            <div className="muted small">{t("alt.common.loading", "loading…")}</div>
          ) : summaryQuery.data?.available ? (
            <dl className="kv">
              <dt>{t("alt.intel.dt_lifecycle", "lifecycle events")}</dt>
              <dd>{summaryQuery.data.lifecycle_events ?? 0}</dd>
              <dt>{t("alt.intel.dt_autopsies", "autopsies")}</dt>
              <dd>{summaryQuery.data.autopsies ?? 0}</dd>
              <dt>{t("alt.ml.dt_worker", "worker")}</dt>
              <dd><StatusBadge status={String((summaryQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>{t("alt.intel.dt_fetch_time", "fetch time")}</dt>
              <dd>{summaryQuery.data.fetch_time ? formatDateTime(summaryQuery.data.fetch_time) : "—"}</dd>
            </dl>
          ) : summaryQuery.isError ? (
            <ErrorState message={t("alt.intel.err_summary", "Intelligence summary endpoint failed.")} onRetry={() => summaryQuery.refetch()} />
          ) : (
            <EmptyState message={t("alt.intel.empty_summary", "Trade intelligence unavailable (engine offline).")} />
          )}
        </Panel>
      </div>

      <Panel title={t("alt.intel.panel_articles", "Latest canonical articles")} tight>
        {articlesQuery.isPending ? (
          <div className="state-block"><div className="spinner" /></div>
        ) : articlesQuery.data?.available && articlesQuery.data.articles && articlesQuery.data.articles.length > 0 ? (
          <DataTable headers={[{ label: t("alt.intel.col_published", "Published") }, { label: t("alt.intel.col_source", "Source") }, { label: t("alt.common.col_title", "Title") }, { label: t("alt.intel.col_importance", "Importance") }]}>
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
          <ErrorState message={t("alt.intel.err_articles", "News feed endpoint failed.")} onRetry={() => articlesQuery.refetch()} />
        ) : (
          <EmptyState message={t("alt.intel.empty_articles", "No articles available.")} />
        )}
      </Panel>

      <Panel title={t("alt.intel.panel_autopsies", "Recent trade autopsies (why trades won/lost)")} tight>
        {autopsyQuery.isPending ? (
          <div className="state-block"><div className="spinner" /></div>
        ) : autopsyQuery.data?.available && autopsyQuery.data.autopsies && autopsyQuery.data.autopsies.length > 0 ? (
          <DataTable
            headers={[{ label: t("alt.common.col_ticket", "Ticket") }, { label: t("alt.intel.col_strategy", "Strategy") }, { label: t("alt.common.col_outcome", "Outcome") }, { label: t("alt.intel.col_realized_r", "Realized R"), num: true }, { label: t("alt.intel.col_exit_reason", "Exit reason") }]}
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
          <ErrorState message={t("alt.intel.err_autopsies", "Autopsy endpoint failed.")} onRetry={() => autopsyQuery.refetch()} />
        ) : (
          <EmptyState message={t("alt.intel.empty_autopsies", "No autopsies recorded.")} />
        )}
      </Panel>
    </div>
  );
}
