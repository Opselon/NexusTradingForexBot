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
        <div className="banner stale" style={{ border: "1px solid rgba(235,161,63,0.4)", borderRadius: 6, marginTop: 10 }}>
          <span>⚠ News context is STALE per the backend — no recent fetch. Values above are not current market context.</span>
        </div>
      )}

      {/* Structure context: liquidity governor + mSLIE (read-only here) */}
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel
          title="Liquidity governor (read-only)"
          right={
            <>
              <InfoChip k="availability" v={liq?.feature_availability ?? "—"} tone={liq?.feature_availability === "AVAILABLE" ? "good" : liq?.feature_availability === "STALE_CACHE" ? "warn" : ""} />
              <button aria-label="Refresh liquidity" className="btn small ghost" onClick={() => void liqQuery.refetch()} disabled={liqQuery.isFetching}>⟳</button>
            </>
          }
        >
          {liqQuery.isPending && !liqQuery.data ? (
            <Skeleton count={4} />
          ) : liqQuery.isError ? (
            <ErrorState message={errorText(liqQuery.error, "Liquidity state endpoint failed.")} requestId={liqQuery.error instanceof ApiError ? liqQuery.error.requestId : null} onRetry={() => void liqQuery.refetch()} />
          ) : liq ? (
            <>
              <dl className="kv">
                <dt>enabled</dt>
                <dd><TriBadge value={liq.enabled} on="YES" off="NO" /></dd>
                <dt>status / causal</dt>
                <dd><StatusBadge status={liq.status ?? null} /> <StatusBadge status={liq.causal_state ?? null} /></dd>
                <dt>calculation</dt>
                <dd><StatusBadge status={liq.calculation_status ?? null} /></dd>
                <dt>source</dt>
                <dd className="small">{liq.source ?? "—"} (source_status {liq.source_status ?? "—"})</dd>
                <dt>age / latency</dt>
                <dd>{fmtAge(liq.age_sec)} · {liq.latency_ms === null || liq.latency_ms === undefined ? "—" : `${liq.latency_ms.toFixed(1)} ms`}</dd>
                <dt>snapshot (decision)</dt>
                <dd className="small">{liq.snapshot_timestamp ? formatDateTime(liq.snapshot_timestamp) : "—"}</dd>
                <dt>algorithm</dt>
                <dd className="small">{liq.algorithm_version ?? "—"}</dd>
              </dl>
              {liq.error && <div className="l4-note warn">backend error{liq.error_at ? ` @ ${liq.error_at}` : ""}: {liq.error}</div>}
              {liqFeatureRows.length > 0 && (
                <>
                  <div className="section-title" style={{ marginTop: 10 }}>Feature values ({liq.feature_count ?? liqFeatureRows.length})</div>
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
                  <div className="section-title" style={{ marginTop: 10 }}>Liquidity pools</div>
                  <DataTable headers={[{ label: "Side" }, { label: "Price", num: true }, { label: "State" }, { label: "Source" }, { label: "Confirmed" }]}>
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
                Read-only view (governor report). The toggle and the full contract live on the Liquidity tab; enabling/disabling here would bypass that
                feature's own gate.
              </div>
            </>
          ) : null}
        </Panel>

        <Panel
          title="mSLIE market structure"
          right={<InfoChip k="engine" v={ms?.status ?? (mslieQuery.isPending ? "…" : "—")} tone={ms?.status === "ONLINE" ? "good" : ms?.status === "DEGRADED" ? "bad" : "warn"} />}
        >
          {mslieQuery.isPending && !mslieQuery.data ? (
            <Skeleton count={4} />
          ) : mslieQuery.isError ? (
            <ErrorState message={errorText(mslieQuery.error, "mSLIE status endpoint failed.")} onRetry={() => void mslieQuery.refetch()} />
          ) : ms?.available === false ? (
            <EmptyState message="mSLIE has not produced a feature vector yet." hint={ms.reason ?? "status STANDBY — structure intelligence is computed from bars once the stream warms."} />
          ) : ms ? (
            <>
              <dl className="kv">
                {ctxRows.length > 0 && <dt className="section-title" style={{ gridColumn: "1 / -1" }}>market context</dt>}
                {ctxRows.map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k}</dt>
                    <dd className="small">{v}</dd>
                  </div>
                ))}
                <dt>algorithm version</dt>
                <dd className="small">{ms.algorithm_version ?? "—"}</dd>
              </dl>
              {(ms.liquidity_map?.length ?? 0) > 0 && (
                <>
                  <div className="section-title" style={{ marginTop: 10 }}>Liquidity map ({ms.liquidity_map!.length} bands)</div>
                  <DataTable headers={[{ label: "Price range" }, { label: "Type" }, { label: "Touches", num: true }, { label: "Strength", num: true }]}>
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
                  last sweep: {String(ms.last_sweep.type ?? ms.last_sweep.side ?? "—")} @ {String(ms.last_sweep.price ?? "—")}
                  {ms.last_sweep.time ? ` · ${formatDateTime(String(ms.last_sweep.time))}` : ""}
                </div>
              )}
            </>
          ) : null}
        </Panel>
      </div>

      {/* Regime readout */}
      <Panel
        title="Regime (engine classifier)"
        right={
          <>
            <AgeNote label="inference age" ageSec={snapshot?.diagnostics.inference_age_sec} />
            <button aria-label="Refresh regime" className="btn small ghost" onClick={() => void regimeQuery.refetch()} disabled={regimeQuery.isFetching}>⟳</button>
          </>
        }
      >
        <div className="grid cols-2">
          <div>
            <div className="section-title">canonical snapshot</div>
            <dl className="kv">
              <dt>regime</dt>
              <dd>{snapshot?.regime ?? "—"}</dd>
              <dt>news adjustment</dt>
              <dd>{ns?.news_adjustment === null || ns?.news_adjustment === undefined ? "—" : formatNumber(ns.news_adjustment, 3)}</dd>
              <dt>decision</dt>
              <dd>{snapshot?.ai_decision ?? "—"} {snapshot?.ai_confidence !== null && snapshot?.ai_confidence !== undefined ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            </dl>
          </div>
          <div>
            <div className="section-title">/api/v1/market/regime evidence</div>
            <SectionState
              query={regimeQuery}
              emptyMessage={regime?.note ?? "No regime evidence."}
              emptyHint="The classifier emits no state before the first inference — that is reported, not guessed."
              errorFallback="Regime endpoint failed."
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
        <Panel title="News subsystem health">
          {healthQuery.isPending ? (
            <Skeleton count={4} />
          ) : healthQuery.isError ? (
            <ErrorState message={errorText(healthQuery.error, "News health endpoint failed.")} onRetry={() => void healthQuery.refetch()} />
          ) : healthQuery.data?.available ? (
            <dl className="kv">
              <dt>enabled</dt>
              <dd><TriBadge value={healthQuery.data.enabled} on="YES" off="NO" /></dd>
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
            <Skeleton count={4} />
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
              {summaryQuery.data.reasons && (
                <>
                  <dt>reasons</dt>
                  <dd className="small">{summaryQuery.data.reasons}</dd>
                </>
              )}
            </dl>
          ) : summaryQuery.isError ? (
            <ErrorState message="Intelligence summary endpoint failed." onRetry={() => void summaryQuery.refetch()} />
          ) : (
            <EmptyState message="Trade intelligence unavailable (engine offline)." hint="available:false — counts are not zero-filled." />
          )}
        </Panel>
      </div>

      <Panel
        title="Latest canonical articles"
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
              title="exports exactly the rows returned by /api/news/latest"
            >
              ⇩ CSV
            </button>
          ) : undefined
        }
      >
        {articlesQuery.isPending ? (
          <div style={{ padding: 12 }}><Skeleton count={3} /></div>
        ) : articlesQuery.data?.available && articlesQuery.data.articles && articlesQuery.data.articles.length > 0 ? (
          <DataTable headers={[{ label: "Published" }, { label: "Source" }, { label: "Title" }, { label: "Importance" }]}>
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
          <ErrorState message="News feed endpoint failed." onRetry={() => void articlesQuery.refetch()} />
        ) : (
          <EmptyState message="No articles available." hint="The feed endpoint answered with an empty list — the parser or the source may be warming up." />
        )}
      </Panel>

      <Panel title="Recent trade autopsies (why trades won/lost)" tight>
        {autopsyQuery.isPending ? (
          <div style={{ padding: 12 }}><Skeleton count={3} /></div>
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
          <ErrorState message="Autopsy endpoint failed." onRetry={() => void autopsyQuery.refetch()} />
        ) : (
          <EmptyState message="No autopsies recorded." />
        )}
      </Panel>
    </div>
  );
}
