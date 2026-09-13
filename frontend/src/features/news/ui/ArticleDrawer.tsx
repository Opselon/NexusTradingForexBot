/**
 * Article drawer — full detail from GET /api/news/{id} + analysis from
 * GET /api/news/analysis/{id}. Both endpoints are read on open; the drawer
 * shows each block only when the backend provided it (no placeholder rows).
 */

import { useEffect } from "react";
import { DataTable, EmptyState, ErrorState, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import { HeatBar } from "@/components/viz";
import { useNewsAnalysis, useNewsArticle } from "../hooks";
import type { NewsFeedArticle } from "../types";
import { asErrorText } from "./shared";

function ratio(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : formatPct(v * 100, 0);
}

export function ArticleDrawer({
  articleId,
  fallback,
  busy,
  onClose,
  onAnalyze,
}: {
  articleId: string;
  fallback?: NewsFeedArticle;
  busy?: boolean;
  onClose: () => void;
  onAnalyze: (force: boolean) => void;
}) {
  const detail = useNewsArticle(articleId);
  const analysis = useNewsAnalysis(articleId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const art = detail.data?.article ?? null;
  const ana = detail.data?.analysis ?? analysis.data?.analysis ?? null;
  const ai = fallback?.ai_analysis ?? null;
  const impacts = detail.data?.impacts ?? [];
  const consensus = detail.data?.consensus ?? fallback?.consensus ?? null;
  const tradeLinks = detail.data?.trade_links ?? [];
  const related = detail.data?.related ?? [];
  const postEvents = detail.data?.post_event_validation ?? [];

  return (
    <div className="news-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="news-drawer" role="dialog" aria-modal="true" aria-label="News article detail">
        <header>
          <span>Article detail</span>
          <span className="inline-mono tiny faint">#{articleId.slice(0, 10)}</span>
          <button className="btn small close" onClick={onClose}>
            Close <kbd>esc</kbd>
          </button>
        </header>
        <div className="body">
          {detail.isPending ? (
            <Skeleton count={5} height={40} />
          ) : detail.isError ? (
            <ErrorState message={asErrorText(detail.error)} onRetry={() => detail.refetch()} />
          ) : (
            <>
              <section>
                <div style={{ fontSize: 13.5, fontWeight: 700 }}>{art?.title ?? fallback?.title ?? "—"}</div>
                <div className="statline" style={{ marginTop: 6 }}>
                  <span>{String(art?.source_name ?? fallback?.source_name ?? art?.source_id ?? "—")}</span>
                  <span>{formatDateTime(String(art?.published_at ?? fallback?.published_at ?? ""))}</span>
                  <span>{String(art?.importance ?? fallback?.importance ?? "—")}</span>
                  <StatusBadge status={String(art?.article_status ?? fallback?.article_status ?? "ACTIVE")} />
                </div>
                {(art?.summary as string) || fallback?.summary ? (
                  <p className="small muted" style={{ marginTop: 8 }}>
                    {String(art?.summary ?? fallback?.summary)}
                  </p>
                ) : null}
                {art?.canonical_url ? (
                  <a className="tiny inline-mono" href={String(art.canonical_url)} target="_blank" rel="noreferrer noopener">
                    {String(art.canonical_url)}
                  </a>
                ) : null}
                <div className="news-actions">
                  <button className="btn small primary" onClick={() => onAnalyze(false)} disabled={busy}>
                    Analyze with AI
                  </button>
                  <button className="btn small" onClick={() => onAnalyze(true)} disabled={busy}>
                    Force re-analyze
                  </button>
                  <button className="btn small ghost" onClick={() => detail.refetch()}>
                    Reload detail
                  </button>
                </div>
              </section>

              <section>
                <div className="section-title">Deterministic analysis</div>
                {ana ? (
                  <>
                    <dl className="kv">
                      <dt>direction</dt>
                      <dd>{String(ana.direction ?? "PENDING")}</dd>
                      <dt>importance_score</dt>
                      <dd>{ana.importance_score != null ? ratio(ana.importance_score) : "—"}</dd>
                      <dt>relevance XAUUSD / USD</dt>
                      <dd>
                        {ratio(ana.relevance_to_xauusd)} / {ratio(ana.relevance_to_usd)}
                      </dd>
                      <dt>impact_strength / confidence</dt>
                      <dd>
                        {formatNumber(ana.impact_strength, 3)} / {formatNumber(ana.confidence, 3)}
                      </dd>
                      <dt>horizon · novelty</dt>
                      <dd>
                        {ana.horizon ?? "—"} · {ana.novelty ?? "—"}
                      </dd>
                      <dt>analyzed_at</dt>
                      <dd>{ana.analyzed_at ? formatDateTime(ana.analyzed_at) : "—"}</dd>
                      {ana.surprise_assessment && (
                        <>
                          <dt>surprise</dt>
                          <dd style={{ textAlign: "start" }}>{ana.surprise_assessment}</dd>
                        </>
                      )}
                    </dl>
                    {ana.market_mechanism && <div className="small muted" style={{ marginTop: 6 }}>{ana.market_mechanism}</div>}
                  </>
                ) : analysis.isPending ? (
                  <Skeleton count={2} />
                ) : analysis.isError ? (
                  <ErrorState message={asErrorText(analysis.error)} onRetry={() => analysis.refetch()} />
                ) : (
                  <EmptyState message="No deterministic analysis stored for this article yet." hint="Analyze with AI or wait for the worker (auto-analysis toggle)." />
                )}
                {analysis.data?.run && (
                  <div className="tiny faint inline-mono" style={{ marginTop: 6 }}>
                    run {String(analysis.data.run.run_id ?? "—")} · {String(analysis.data.run.status ?? "—")} · {String(analysis.data.run.provider ?? "local")}
                    {analysis.data.run.error ? ` · error: ${String(analysis.data.run.error)}` : ""}
                  </div>
                )}
              </section>

              {ai && (
                <section>
                  <div className="section-title">AI analysis (LLM)</div>
                  <div className="news-ai-card">
                    <div className="statline">
                      <StatusBadge status={ai.analysis_status ?? "COMPLETE"} />
                      {ai.sentiment && <span>sentiment {ai.sentiment}</span>}
                      {ai.provider && <span>{ai.provider}{ai.model ? ` ${ai.model}` : ""}</span>}
                      {ai.analysis_version && <span>v{ai.analysis_version}</span>}
                    </div>
                    {ai.summary && <div style={{ marginTop: 6 }}>{ai.summary}</div>}
                    {ai.market_relevance && <div className="tiny muted" style={{ marginTop: 4 }}>market rel: {ai.market_relevance}</div>}
                    {ai.xauusd_relevance && <div className="tiny muted">XAUUSD rel: {ai.xauusd_relevance}</div>}
                    {ai.potential_market_impact && <div className="tiny muted">potential impact: {ai.potential_market_impact}</div>}
                    {ai.key_facts?.map((f) => (
                      <span className="fact" key={f}>{f}</span>
                    ))}
                    {ai.uncertainties && ai.uncertainties.length > 0 && (
                      <div className="unc">uncertainties: {ai.uncertainties.join("; ")}</div>
                    )}
                  </div>
                </section>
              )}

              <section>
                <div className="section-title">Asset impacts</div>
                {impacts.length === 0 ? (
                  <EmptyState message="No impact records for this article." />
                ) : (
                  <HeatBar
                    items={impacts.map((im) => ({
                      label: `${im.asset ?? "—"} ${im.direction ?? ""}`.trim(),
                      value: im.relevance ?? null,
                      caption: `${(im.relevance ?? 0) * 100 > 0 ? `${formatPct((im.relevance ?? 0) * 100, 0)}` : "—"} · s ${formatNumber(im.strength, 2)}`,
                      tone: im.direction === "BULLISH" ? "pos" : im.direction === "BEARISH" ? "bad" : "neu",
                      title: `${im.direction ?? "NEUTRAL"} · strength ${formatNumber(im.strength, 3)} · confidence ${formatNumber(im.confidence, 3)}`,
                    }))}
                    scaleCaptions={["0", "relevance 1.0"]}
                  />
                )}
              </section>

              <section>
                <div className="section-title">Consensus across sources</div>
                {consensus ? (
                  <dl className="kv">
                    <dt>sources / independent</dt>
                    <dd>
                      {consensus.source_count ?? "—"} / {consensus.independent_count ?? "—"}
                    </dd>
                    <dt>agreement / conflict</dt>
                    <dd>
                      {ratio(consensus.agreement)} / {ratio(consensus.conflict)}
                    </dd>
                    <dt>weighted direction</dt>
                    <dd>{consensus.weighted_direction ?? "—"}</dd>
                    <dt>confidence</dt>
                    <dd>{ratio(consensus.confidence)}</dd>
                  </dl>
                ) : (
                  <EmptyState message="No consensus record (single-source story or not evaluated)." />
                )}
              </section>

              <section>
                <div className="section-title">Trade linkage (news → decisions)</div>
                {tradeLinks.length === 0 ? (
                  <EmptyState message="This article is not linked to any trade record." />
                ) : (
                  <DataTable headers={[{ label: "trade" }, { label: "strategy" }, { label: "link" }, { label: "at" }]}>
                    {tradeLinks.map((l, i) => (
                      <tr key={i}>
                        <td>{String(l.trade_id ?? l.ticket ?? "—")}</td>
                        <td>{String(l.strategy_id ?? "—")}</td>
                        <td>{String(l.link_type ?? (l.linked_at ? "linked" : "—"))}</td>
                        <td>{formatDateTime(String(l.linked_at ?? ""))}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              {related.length > 0 && (
                <section>
                  <div className="section-title">Related coverage</div>
                  <ul className="small muted" style={{ margin: 0, paddingInlineStart: 16 }}>
                    {related.map((r, i) => (
                      <li key={i}>{String(r.title ?? r.article_id ?? "—")}</li>
                    ))}
                  </ul>
                </section>
              )}

              {postEvents.length > 0 && (
                <section>
                  <div className="section-title">Post-event validation</div>
                  <pre className="tiny inline-mono" style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, overflowX: "auto", margin: 0 }}>
                    {JSON.stringify(postEvents, null, 2)}
                  </pre>
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
