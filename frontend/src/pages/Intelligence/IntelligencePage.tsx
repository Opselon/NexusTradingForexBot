/**
 * Intelligence — news / market-context / trade intelligence + structure.
 *
 * Stale or unavailable information is ALWAYS labeled as such (backend
 * provides `stale`, `freshness`, `available` flags). No investment decisions
 * or interpretations are computed in the frontend — raw backend signals only.
 *
 * Sections:
 *  - News state KPI strip + subsystem health (existing parity)
 *  - Liquidity governor panel — READ-ONLY render of /api/liquidity/state
 *    (the liquidity tab itself belongs to lane 5; this page only surfaces
 *    the causal state / pools / feature values for context)
 *  - mSLIE structure panel — /api/mslie/status: status word, market context
 *    (structure/bias), liquidity map bands, last sweep
 *  - Regime readout — canonical snapshot regime + /api/v1/market/regime
 *    evidence (no inference yet renders as the backend's own note)
 *  - Trade intelligence summary, articles, autopsies (parity)
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "@/api/intelligenceApi";
import { contextApi, marketApi2 } from "@/pages/_shared/edgeApi";
import type { EngineSnapshot } from "@/types/domain";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { AgeNote, SectionState, errorText, fmtAge, TriBadge } from "@/pages/_shared/SectionState";
import { InfoChip } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import { useI18n } from "@/stores/i18nStore";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot?: EngineSnapshot | undefined;
}

/** Echo a backend record's scalar fields as kv rows (objects collapsed). */
function scalarRows(rec: Record<string, unknown> | null | undefined, max = 12): Array<[string, string]> {
  if (!rec) return [];
  return Object.entries(rec)
    .filter(([, v]) => v === null || typeof v !== "object")
    .slice(0, max)
    .map(([k, v]) => [k.replace(/_/g, " "), typeof v === "number" ? formatNumber(v, Number.isInteger(v) ? 0 : 3) : String(v ?? "—")] as [string, string]);
}

export default function IntelligencePage({ snapshot }: Props) {
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
  const liqQuery = useQuery({
    queryKey: ["liquidity-state"],
    queryFn: ({ signal }) => contextApi.liquidityState(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const mslieQuery = useQuery({
    queryKey: ["mslie-status"],
    queryFn: ({ signal }) => contextApi.msilieStatus(signal),
    refetchInterval: 20_000,
    retry: false,
  });
  const regimeQuery = useQuery({
    queryKey: ["market-regime"],
    queryFn: ({ signal }) => marketApi2.regime(signal),
    refetchInterval: 15_000,
    retry: false,
  });

  const ns = stateQuery.data;
  const liq = liqQuery.data;
  const ms = mslieQuery.data;
  const regime = regimeQuery.data;

  const ctxRows = useMemo(() => scalarRows(ms?.market_context ?? null), [ms?.market_context]);
  const liqFeatureRows = useMemo(() => Object.entries(liq?.features ?? {}).slice(0, 14), [liq?.features]);

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard
          label={t("intelligence.kpi.news_state", "News state (backend)")}
          value={stateQuery.isPending ? "…" : ns?.available ? (ns.state ?? "—") : t("intelligence.status.unavailable", "UNAVAILABLE")}
          tone={ns?.stale ? undefined : ns?.state === "BREAKING" || ns?.state === "HIGH_IMPACT" ? "neg" : "dim"}
          sub={ns?.available ? (ns.stale ? t("intelligence.kpi.news_stale", "⚠ backend marks context STALE") : t("intelligence.kpi.freshness", "freshness {v}", { v: ns.freshness ?? "—" })) : t("intelligence.kpi.news_offline", "news subsystem disabled or offline")}
        />
        <MetricCard
          label={t("intelligence.kpi.bullish_bearish", "Bullish / Bearish")}
          value={ns?.available ? `${formatPct((ns.bullish_score ?? 0) * 100, 0)} / ${formatPct((ns.bearish_score ?? 0) * 100, 0)}` : "—"}
          tone="dim"
          sub={t("intelligence.kpi.sentiment_sub", "backend-scored sentiment (display only)")}
        />
        <MetricCard
          label={t("intelligence.kpi.context_confidence", "Context confidence")}
          value={ns?.confidence === null || ns?.confidence === undefined ? "—" : formatPct(ns.confidence * 100, 0)}
          tone="dim"
          sub={t("intelligence.kpi.relevance", "XAUUSD relevance {v}", { v: ns?.xauusd_relevance === null || ns?.xauusd_relevance === undefined ? "—" : formatPct(ns.xauusd_relevance * 100, 0) })}
        />
        <MetricCard
          label={t("intelligence.kpi.active_events", "Active events")}
          value={ns?.active_event_count ?? "—"}
          tone="dim"
          sub={t("intelligence.kpi.context_time", "context time {v}", { v: ns?.timestamp ? formatDateTime(ns.timestamp) : "—" })}
        />
      </div>

      {ns?.stale && (
        <div className="banner stale" style={{ border: "1px solid rgba(235,161,63,0.4)", borderRadius: 6, marginTop: 10 }}>
          <span>{"⚠ "}{t("intelligence.banner.stale", "News context is STALE per the backend — no recent fetch. Values above are not current market context.")}</span>
        </div>
      )}

      {/* Structure context: liquidity governor + mSLIE (read-only here) */}
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel
          title={t("intelligence.panel.liquidity", "Liquidity governor (read-only)")}
          right={
            <>
              <InfoChip k={t("intelligence.chip.availability", "availability")} v={liq?.feature_availability ?? "—"} tone={liq?.feature_availability === "AVAILABLE" ? "good" : liq?.feature_availability === "STALE_CACHE" ? "warn" : ""} />
              <button className="btn small ghost" onClick={() => void liqQuery.refetch()} disabled={liqQuery.isFetching}>⟳</button>
            </>
          }
        >
          {liqQuery.isPending && !liqQuery.data ? (
            <Skeleton count={4} />
          ) : liqQuery.isError ? (
            <ErrorState message={errorText(liqQuery.error, t("intelligence.error.liquidity", "Liquidity state endpoint failed."))} requestId={liqQuery.error instanceof ApiError ? liqQuery.error.requestId : null} onRetry={() => void liqQuery.refetch()} />
          ) : liq ? (
            <>
              <dl className="kv">
                <dt>{t("intelligence.kv.enabled", "enabled")}</dt>
                <dd><TriBadge value={liq.enabled} on={t("intelligence.yes", "YES")} off={t("intelligence.no", "NO")} /></dd>
                <dt>{t("intelligence.kv.status_causal", "status / causal")}</dt>
                <dd><StatusBadge status={liq.status ?? null} /> <StatusBadge status={liq.causal_state ?? null} /></dd>
                <dt>{t("intelligence.kv.calculation", "calculation")}</dt>
                <dd><StatusBadge status={liq.calculation_status ?? null} /></dd>
                <dt>{t("intelligence.kv.source", "source")}</dt>
                <dd className="small">{liq.source ?? "—"} (source_status {liq.source_status ?? "—"})</dd>
                <dt>{t("intelligence.kv.age_latency", "age / latency")}</dt>
                <dd>{fmtAge(liq.age_sec)} · {liq.latency_ms === null || liq.latency_ms === undefined ? "—" : `${liq.latency_ms.toFixed(1)} ms`}</dd>
                <dt>{t("intelligence.kv.snapshot", "snapshot (decision)")}</dt>
                <dd className="small">{liq.snapshot_timestamp ? formatDateTime(liq.snapshot_timestamp) : "—"}</dd>
                <dt>{t("intelligence.kv.algorithm", "algorithm")}</dt>
                <dd className="small">{liq.algorithm_version ?? "—"}</dd>
              </dl>
              {liq.error && <div className="l4-note warn">{t("intelligence.kv.backend_error", "backend error")}{liq.error_at ? ` @ ${liq.error_at}` : ""}: {liq.error}</div>}
              {liqFeatureRows.length > 0 && (
                <>
                  <div className="section-title" style={{ marginTop: 10 }}>{t("intelligence.section.feature_values", "Feature values ({n})", { n: liq.feature_count ?? liqFeatureRows.length })}</div>
                  <div className="l4-features" style={{ maxBlockSize: 160 }}>
                    {liqFeatureRows.map(([k, v]) => (
                      <div key={k} className="l4-feature" title={k}>
                        <div className="n">{k}</div>
                        <div className="v">{typeof v === "number" ? v.toFixed(3) : "—"}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {(liq.pools?.length ?? 0) > 0 && (
                <>
                  <div className="section-title" style={{ marginTop: 10 }}>{t("intelligence.section.liquidity_pools", "Liquidity pools")}</div>
                  <DataTable headers={[{ label: t("intelligence.th.side", "Side") }, { label: t("intelligence.th.price", "Price"), num: true }, { label: t("intelligence.th.state", "State") }, { label: t("intelligence.th.source", "Source") }, { label: t("intelligence.th.confirmed", "Confirmed") }]}>
                    {liq.pools!.slice(0, 8).map((p, i) => (
                      <tr key={i}>
                        <td><span className={`l4-chip ${String(p.side ?? "").toUpperCase() === "BUY" ? "good" : "bad"}`}>{p.side ?? "—"}</span></td>
                        <td className="num">{p.price === null || p.price === undefined ? "—" : formatNumber(p.price, 2)}</td>
                        <td>{p.state ?? "—"}</td>
                        <td className="small">{p.source ?? "—"}</td>
                        <td className="small">{p.confirmed_at ? formatDateTime(p.confirmed_at) : "—"}</td>
                      </tr>
                    ))}
                  </DataTable>
                </>
              )}
              <div className="l4-note" style={{ marginTop: 8 }}>
                {t("intelligence.section.liquidity_readonly", "Read-only view (governor report). The toggle and the full contract live on the Liquidity tab; enabling/disabling here would bypass that same feature gate.")}
              </div>
            </>
          ) : null}
        </Panel>

        <Panel
          title={t("intelligence.panel.mslie", "mSLIE market structure")}
          right={<InfoChip k={t("intelligence.chip.engine", "engine")} v={ms?.status ?? (mslieQuery.isPending ? "…" : "—")} tone={ms?.status === "ONLINE" ? "good" : ms?.status === "DEGRADED" ? "bad" : "warn"} />}
        >
          {mslieQuery.isPending && !mslieQuery.data ? (
            <Skeleton count={4} />
          ) : mslieQuery.isError ? (
            <ErrorState message={errorText(mslieQuery.error, t("intelligence.error.mslie", "mSLIE status endpoint failed."))} onRetry={() => void mslieQuery.refetch()} />
          ) : ms?.available === false ? (
            <EmptyState message={t("intelligence.mslie.empty", "mSLIE has not produced a feature vector yet.")} hint={ms.reason ?? t("intelligence.mslie.empty_hint", "status STANDBY — structure intelligence is computed from bars once the stream warms.")} />
          ) : ms ? (
            <>
              <dl className="kv">
                {ctxRows.length > 0 && <dt className="section-title" style={{ gridColumn: "1 / -1" }}>{t("intelligence.kv.market_context", "market context")}</dt>}
                {ctxRows.map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k}</dt>
                    <dd className="small">{v}</dd>
                  </div>
                ))}
                <dt>{t("intelligence.kv.algorithm_version", "algorithm version")}</dt>
                <dd className="small">{ms.algorithm_version ?? "—"}</dd>
              </dl>
              {(ms.liquidity_map?.length ?? 0) > 0 && (
                <>
                  <div className="section-title" style={{ marginTop: 10 }}>{t("intelligence.section.liquidity_map", "Liquidity map ({n} bands)", { n: ms.liquidity_map!.length })}</div>
                  <DataTable headers={[{ label: t("intelligence.th.price_range", "Price range") }, { label: t("intelligence.th.type", "Type") }, { label: t("intelligence.th.touches", "Touches"), num: true }, { label: t("intelligence.th.strength", "Strength"), num: true }]}>
                    {ms.liquidity_map!.slice(0, 8).map((z, i) => (
                      <tr key={i}>
                        <td className="num">{String(z.low ?? "—")} – {String(z.high ?? "—")}</td>
                        <td>{String(z.type ?? z.kind ?? "—")}</td>
                        <td className="num">{String(z.touches ?? "—")}</td>
                        <td className="num">{typeof z.strength === "number" ? z.strength.toFixed(2) : String(z.strength ?? "—")}</td>
                      </tr>
                    ))}
                  </DataTable>
                </>
              )}
              {ms.last_sweep && (
                <div className="l4-note" style={{ marginTop: 8 }}>
                  {t("intelligence.mslie.last_sweep", "last sweep:")} {String(ms.last_sweep.type ?? ms.last_sweep.side ?? "—")} @ {String(ms.last_sweep.price ?? "—")}
                  {ms.last_sweep.time ? ` · ${formatDateTime(String(ms.last_sweep.time))}` : ""}
                </div>
              )}
            </>
          ) : null}
        </Panel>
      </div>

      {/* Regime readout */}
      <Panel
        title={t("intelligence.panel.regime", "Regime (engine classifier)")}
        right={
          <>
            <AgeNote label={t("intelligence.age.inference", "inference age")} ageSec={snapshot?.diagnostics.inference_age_sec} />
            <button className="btn small ghost" onClick={() => void regimeQuery.refetch()} disabled={regimeQuery.isFetching}>⟳</button>
          </>
        }
      >
        <div className="grid cols-2">
          <div>
            <div className="section-title">{t("intelligence.section.canonical_snapshot", "canonical snapshot")}</div>
            <dl className="kv">
              <dt>{t("intelligence.kv.regime", "regime")}</dt>
              <dd>{snapshot?.regime ?? "—"}</dd>
              <dt>{t("intelligence.kv.news_adjustment", "news adjustment")}</dt>
              <dd>{ns?.news_adjustment === null || ns?.news_adjustment === undefined ? "—" : formatNumber(ns.news_adjustment, 3)}</dd>
              <dt>{t("intelligence.kv.decision", "decision")}</dt>
              <dd>{snapshot?.ai_decision ?? "—"} {snapshot?.ai_confidence !== null && snapshot?.ai_confidence !== undefined ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            </dl>
          </div>
          <div>
            <div className="section-title">{t("intelligence.section.regime_evidence", "/api/v1/market/regime evidence")}</div>
            <SectionState
              query={regimeQuery}
              emptyMessage={regime?.note ?? t("intelligence.regime.empty", "No regime evidence.")}
              emptyHint={t("intelligence.regime.empty_hint", "The classifier emits no state before the first inference — that is reported, not guessed.")}
              errorFallback={t("intelligence.error.regime", "Regime endpoint failed.")}
              emptyWhen={(d) => d.regime === null || d.regime === undefined}
            >
              {(d) => (
                <dl className="kv">
                  {scalarRows(d.regime, 14).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{k}</dt>
                      <dd className="small">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </SectionState>
          </div>
        </div>
      </Panel>

      <div className="grid cols-2">
        <Panel title={t("intelligence.panel.news_health", "News subsystem health")}>
          {healthQuery.isPending ? (
            <Skeleton count={4} />
          ) : healthQuery.isError ? (
            <ErrorState message={errorText(healthQuery.error, t("intelligence.error.news_health", "News health endpoint failed."))} onRetry={() => void healthQuery.refetch()} />
          ) : healthQuery.data?.available ? (
            <dl className="kv">
              <dt>{t("intelligence.kv.enabled", "enabled")}</dt>
              <dd><TriBadge value={healthQuery.data.enabled} on={t("intelligence.yes", "YES")} off={t("intelligence.no", "NO")} /></dd>
              <dt>{t("intelligence.kv.worker", "worker")}</dt>
              <dd><StatusBadge status={String((healthQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>{t("intelligence.kv.calendar_worker", "calendar worker")}</dt>
              <dd>{healthQuery.data.calendar ? String((healthQuery.data.calendar as { state?: string }).state ?? t("intelligence.kv.present", "present")) : "—"}</dd>
              <dt>{t("intelligence.kv.event_gate", "event gate")}</dt>
              <dd>{healthQuery.data.event_gate ? String((healthQuery.data.event_gate as { verdict?: string }).verdict ?? t("intelligence.kv.present", "present")) : "—"}</dd>
              <dt>{t("intelligence.kv.llm_budget", "llm budget")}</dt>
              <dd className="small">{healthQuery.data.llm_budget ? t("intelligence.kv.llm_reported", "reported (see payload)") : "—"}</dd>
            </dl>
          ) : (
            <EmptyState message={t("intelligence.news.empty", "News subsystem unavailable.")} hint={t("intelligence.news.empty_hint", "Engine offline or news_engine not attached — shown as UNAVAILABLE.")} />
          )}
        </Panel>

        <Panel title={t("intelligence.panel.summary", "Trade intelligence summary")}>
          {summaryQuery.isPending ? (
            <Skeleton count={4} />
          ) : summaryQuery.data?.available ? (
            <dl className="kv">
              <dt>{t("intelligence.kv.lifecycle_events", "lifecycle events")}</dt>
              <dd>{summaryQuery.data.lifecycle_events ?? 0}</dd>
              <dt>{t("intelligence.kv.autopsies", "autopsies")}</dt>
              <dd>{summaryQuery.data.autopsies ?? 0}</dd>
              <dt>{t("intelligence.kv.worker", "worker")}</dt>
              <dd><StatusBadge status={String((summaryQuery.data.worker as { state?: string } | undefined)?.state ?? null)} /></dd>
              <dt>{t("intelligence.kv.fetch_time", "fetch time")}</dt>
              <dd>{summaryQuery.data.fetch_time ? formatDateTime(summaryQuery.data.fetch_time) : "—"}</dd>
              {summaryQuery.data.reasons && (
                <>
                  <dt>{t("intelligence.kv.reasons", "reasons")}</dt>
                  <dd className="small">{summaryQuery.data.reasons}</dd>
                </>
              )}
            </dl>
          ) : summaryQuery.isError ? (
            <ErrorState message={t("intelligence.error.summary", "Intelligence summary endpoint failed.")} onRetry={() => void summaryQuery.refetch()} />
          ) : (
            <EmptyState message={t("intelligence.summary.empty", "Trade intelligence unavailable (engine offline).")} hint={t("intelligence.summary.empty_hint", "available:false — counts are not zero-filled.")} />
          )}
        </Panel>
      </div>

      <Panel
        title={t("intelligence.panel.articles", "Latest canonical articles")}
        tight
        right={
          articlesQuery.data?.articles && articlesQuery.data.articles.length > 0 ? (
            <button
              className="btn small ghost"
              onClick={() =>
                downloadCsv({
                  filename: `nse-news-${stampForFilename()}.csv`,
                  headers: ["article_id", "published_at", "source", "importance", "status", "title"],
                  rows: (articlesQuery.data.articles ?? []).map((a) => [a.article_id, a.published_at ?? "", a.source_name ?? "", String(a.importance ?? ""), a.article_status ?? "", a.title]),
                })
              }
              title={t("intelligence.csv.articles_title", "exports exactly the rows returned by /api/news/latest")}
            >
              ⇩ CSV
            </button>
          ) : undefined
        }
      >
        {articlesQuery.isPending ? (
          <div style={{ padding: 12 }}><Skeleton count={3} /></div>
        ) : articlesQuery.data?.available && articlesQuery.data.articles && articlesQuery.data.articles.length > 0 ? (
          <DataTable headers={[{ label: t("intelligence.th.published", "Published") }, { label: t("intelligence.th.source", "Source") }, { label: t("intelligence.th.title", "Title") }, { label: t("intelligence.th.importance", "Importance") }]}>
            {articlesQuery.data.articles.map((a) => (
              <tr key={a.article_id}>
                <td>{a.published_at ? formatDateTime(a.published_at) : "—"}</td>
                <td>{a.source_name ?? "—"}</td>
                <td style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.title}>{a.title}</td>
                <td>{String(a.importance ?? "—")}</td>
              </tr>
            ))}
          </DataTable>
        ) : articlesQuery.isError ? (
          <ErrorState message={t("intelligence.error.news_feed", "News feed endpoint failed.")} onRetry={() => void articlesQuery.refetch()} />
        ) : (
          <EmptyState message={t("intelligence.articles.empty", "No articles available.")} hint={t("intelligence.articles.empty_hint", "The feed endpoint answered with an empty list — the parser or the source may be warming up.")} />
        )}
      </Panel>

      <Panel title={t("intelligence.panel.autopsies", "Recent trade autopsies (why trades won/lost)")} tight>
        {autopsyQuery.isPending ? (
          <div style={{ padding: 12 }}><Skeleton count={3} /></div>
        ) : autopsyQuery.data?.available && autopsyQuery.data.autopsies && autopsyQuery.data.autopsies.length > 0 ? (
          <DataTable
            headers={[{ label: t("intelligence.th.ticket", "Ticket") }, { label: t("intelligence.th.strategy", "Strategy") }, { label: t("intelligence.th.outcome", "Outcome") }, { label: t("intelligence.th.realized_r", "Realized R"), num: true }, { label: t("intelligence.th.exit_reason", "Exit reason") }]}
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
          <ErrorState message={t("intelligence.error.autopsy", "Autopsy endpoint failed.")} onRetry={() => void autopsyQuery.refetch()} />
        ) : (
          <EmptyState message={t("intelligence.autopsy.empty", "No autopsies recorded.")} />
        )}
      </Panel>
    </div>
  );
}
