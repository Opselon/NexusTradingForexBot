/**
 * Article drawer — full detail from GET /api/news/{id} + analysis from
 * GET /api/news/analysis/{id}. Both endpoints are read on open; the drawer
 * shows each block only when the backend provided it (no placeholder rows).
 */

import { useMemo, useRef } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import { DataTable, EmptyState, ErrorState, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import { HeatBar } from "@/components/viz";
import { useI18n } from "@/stores/i18nStore";
import { useNewsAnalysis, useNewsArticle, useNewsProAnswers } from "../hooks";
import { decodeStringList } from "../model";
import type { NewsAiAnalysisRow, NewsFeedArticle } from "../types";
import { asErrorText } from "./shared";

function ratio(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : formatPct(v * 100, 0);
}

/** Verbatim cell: only backend text, null/undefined/'' all render as "—". */
function verbatim(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const s = String(v);
  return s.trim() === "" ? "—" : s;
}

export function ArticleDrawer({
  articleId,
  fallback,
  busy,
  analyzeNote,
  onClose,
  onAnalyze,
}: {
  articleId: string;
  fallback?: NewsFeedArticle;
  busy?: boolean;
  /** Last backend verdict for the analyze command (QUEUED / SKIPPED / refusal). */
  analyzeNote?: string | null;
  onClose: () => void;
  onAnalyze: (force: boolean) => void;
}) {
  const detail = useNewsArticle(articleId);
  const t = useI18n((s) => s.t);
  const analysis = useNewsAnalysis(articleId);
  /* GET /api/news/{id} carries no ai_analysis block (verified in
   * news_liquidity_mslie_routes.py get_news_detail), so the drawer's LLM verdict
   * comes from the feed row the list already loaded, or — when the feed row has
   * none yet — from the newest rows of /api/news/pro/latest-answers that match
   * this article_id. Both are backend reads; nothing is synthesized. */
  const proAnswers = useNewsProAnswers(100);

  const aiFromAnswers = useMemo<NewsAiAnalysisRow | null>(() => {
    const rows = proAnswers.data ?? [];
    return rows.find((r) => r.article_id === articleId) ?? null;
  }, [proAnswers.data, articleId]);

  const boxRef = useRef<HTMLElement | null>(null);
  useDialogA11y(boxRef, onClose);

  const art = detail.data?.article ?? null;
  const ana = detail.data?.analysis ?? analysis.data?.analysis ?? null;
  const ai = fallback?.ai_analysis ?? aiFromAnswers ?? null;
  // perf: key_facts / uncertainties arrive as JSON array strings from the
  // backend (db_analysis json.dumps) — decode once per analysis payload
  // instead of parsing + re-parsing inside the render body.
  const keyFacts = useMemo(() => decodeStringList(ai?.key_facts), [ai?.key_facts]);
  const uncertainties = useMemo(() => decodeStringList(ai?.uncertainties), [ai?.uncertainties]);
  const impacts = detail.data?.impacts ?? [];
  const consensus = detail.data?.consensus ?? fallback?.consensus ?? null;
  const tradeLinks = detail.data?.trade_links ?? [];
  const related = detail.data?.related ?? [];
  const postEvents = detail.data?.post_event_validation ?? [];

  // perf: pretty-printed block serialized once per payload identity instead of
  // on every drawer render (the article/analysis queries refetch on focus).
  const postEventsText = useMemo(
    () => (postEvents.length > 0 ? JSON.stringify(postEvents, null, 2) : null),
    [postEvents],
  );

  return (
    <div className="news-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside ref={boxRef} className="news-drawer" role="dialog" aria-modal="true" aria-label={t("news.article.aria", "News article detail")}>
        <header aria-label={t("news.article.header_aria", "Article")}>
          <span>{t("news.article.header", "Article detail")}</span>
          <span className="inline-mono tiny faint">#{articleId.slice(0, 10)}</span>
          <button className="btn small close" onClick={onClose}>
            {t("news.article.close", "Close")} <kbd>esc</kbd>
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
                    {busy ? t("news.feed.analyzing", "analyzing…") : t("news.feed.analyze_ai", "Analyze with AI")}
                  </button>
                  <button className="btn small" onClick={() => onAnalyze(true)} disabled={busy}>
                    {busy ? t("news.feed.analyzing", "analyzing…") : t("news.article.force_reanalyze", "Force re-analyze")}
                  </button>
                  <button className="btn small ghost" onClick={() => detail.refetch()} disabled={detail.isFetching}>
                    {detail.isFetching ? t("news.article.loading", "loading…") : t("news.article.reload", "Reload detail")}
                  </button>
                </div>
                {analyzeNote && <div className="news-status-line" style={{ marginTop: 6 }}>{analyzeNote}</div>}
              </section>

              <section>
                <div className="section-title">{t("news.article.det_title", "Deterministic analysis")}</div>
                {ana ? (
                  <>
                    <dl className="kv">
                      <dt>{t("news.article.f_direction", "direction")}</dt>
                      <dd>{String(ana.direction ?? "PENDING")}</dd>
                      <dt>{t("news.article.f_importance_score", "importance_score")}</dt>
                      <dd>{ana.importance_score != null ? ratio(ana.importance_score) : "—"}</dd>
                      <dt>{t("news.article.f_relevance", "relevance XAUUSD / USD")}</dt>
                      <dd>
                        {ratio(ana.relevance_to_xauusd)} / {ratio(ana.relevance_to_usd)}
                      </dd>
                      <dt>{t("news.article.f_impact_conf", "impact_strength / confidence")}</dt>
                      <dd>
                        {formatNumber(ana.impact_strength, 3)} / {formatNumber(ana.confidence, 3)}
                      </dd>
                      <dt>{t("news.article.f_horizon_novelty", "horizon · novelty")}</dt>
                      <dd>
                        {ana.horizon ?? "—"} · {ana.novelty ?? "—"}
                      </dd>
                      <dt>{t("news.article.f_analyzed_at", "analyzed_at")}</dt>
                      <dd>{ana.analyzed_at ? formatDateTime(ana.analyzed_at) : "—"}</dd>
                      {ana.surprise_assessment && (
                        <>
                          <dt>{t("news.article.f_surprise", "surprise")}</dt>
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
                  <EmptyState message={t("news.article.det_empty", "No deterministic analysis stored for this article yet.")} hint={t("news.article.det_empty_hint", "Analyze with AI or wait for the worker (auto-analysis toggle).")} />
                )}
                {analysis.data?.run && (
                  <div className="tiny faint inline-mono" style={{ marginTop: 6 }}>
                    {t("news.article.run_line", "run {id} · {status} · {provider}", { id: String(analysis.data.run.run_id ?? "—"), status: String(analysis.data.run.status ?? "—"), provider: String(analysis.data.run.provider ?? "local") })}
                    {analysis.data.run.error ? t("news.article.run_error", " · error: {e}", { e: String(analysis.data.run.error) }) : ""}
                  </div>
                )}
              </section>

              {ai ? (
                <section>
                  <div className="section-title">{t("news.article.ai_title_verbatim", "AI analysis (LLM) — verdict fields verbatim")}</div>
                  <div className="news-ai-card">
                    <div className="statline">
                      <StatusBadge status={ai.analysis_status ?? "UNKNOWN"} />
                      {ai.sentiment && <span>{t("news.article.sentiment", "sentiment {s}", { s: ai.sentiment })}</span>}
                      {ai.provider && <span>{ai.provider}{ai.model ? ` ${ai.model}` : ""}</span>}
                      {ai.analysis_version && <span>v{ai.analysis_version}</span>}
                    </div>
                    <dl className="kv" style={{ marginTop: 6 }}>
                      <dt>{t("news.article.f_verdict", "verdict (summary)")}</dt>
                      <dd style={{ textAlign: "start" }}>{verbatim(ai.summary)}</dd>
                      <dt>{t("news.article.f_impact", "impact")}</dt>
                      <dd style={{ textAlign: "start" }}>{verbatim(ai.potential_market_impact)}</dd>
                      <dt>{t("news.article.f_confidence", "confidence")}</dt>
                      <dd>{ana?.confidence != null ? formatPct(ana.confidence * 100, 0) : "—"}</dd>
                      <dt>{t("news.article.f_sentiment_importance", "sentiment / importance")}</dt>
                      <dd>
                        {verbatim(ai.sentiment)} / {verbatim(ai.importance_assessment)}
                      </dd>
                      <dt>{t("news.article.f_market_relevance", "market / XAUUSD relevance")}</dt>
                      <dd style={{ textAlign: "start" }}>
                        {verbatim(ai.market_relevance)} / {verbatim(ai.xauusd_relevance)}
                      </dd>
                      <dt>{t("news.article.f_analyzed_provider", "analyzed_at · provider/model")}</dt>
                      <dd>
                        {ai.analyzed_at ? formatDateTime(ai.analyzed_at) : "—"} ·{" "}
                        {[ai.provider, ai.model].filter(Boolean).join(" ") || "—"}
                      </dd>
                    </dl>
                    {ai.analysis_status === "failed" && ai.error_detail && (
                      <div className="tiny" style={{ color: "var(--red)", marginTop: 4 }}>{ai.error_detail}</div>
                    )}
                    {ai.insufficient_evidence && <div className="badge warn" style={{ marginTop: 4 }}>{t("news.feed.insufficient_evidence", "INSUFFICIENT EVIDENCE")}</div>}
                    {keyFacts.length > 0 && (
                      <div style={{ marginTop: 4 }}>
                        {keyFacts.map((f) => (
                          <span className="fact" key={f}>{f}</span>
                        ))}
                      </div>
                    )}
                    {uncertainties.length > 0 && (
                      <div className="unc">{t("news.article.uncertainties", "uncertainties: {list}", { list: uncertainties.join("; ") })}</div>
                    )}
                  </div>
                  {proAnswers.isPending && !fallback?.ai_analysis && (
                    <div className="tiny faint" style={{ marginTop: 4 }}>{t("news.article.reading_answers", "reading latest AI answers…")}</div>
                  )}
                </section>
              ) : (
                <section>
                  <div className="section-title">{t("news.article.ai_title", "AI analysis (LLM)")}</div>
                  <EmptyState
                    message={t("news.article.ai_empty", "No AI analysis stored for this article.")}
                    hint={t("news.article.ai_empty_hint", "Press \"Analyze with AI\" above — the feed/drawer refresh with whatever the backend returns (queued jobs fill in on the next worker pass).")}
                  />
                </section>
              )}

              <section>
                <div className="section-title">{t("news.article.impacts_title", "Asset impacts")}</div>
                {impacts.length === 0 ? (
                  <EmptyState message={t("news.article.impacts_empty", "No impact records for this article.")} />
                ) : (
                  <HeatBar
                    items={impacts.map((im) => ({
                      label: `${im.asset ?? "—"} ${im.direction ?? ""}`.trim(),
                      value: im.relevance ?? null,
                      caption: t("news.article.impact_caption", "{p} · strength {strength}", { p: (im.relevance ?? 0) * 100 > 0 ? formatPct((im.relevance ?? 0) * 100, 0) : "—", strength: formatNumber(im.strength, 2) }),
                      tone: im.direction === "BULLISH" ? "pos" : im.direction === "BEARISH" ? "bad" : "neu",
                      title: t("news.article.impact_title", "{direction} · strength {s} · confidence {c}", { direction: im.direction ?? "NEUTRAL", s: formatNumber(im.strength, 3), c: formatNumber(im.confidence, 3) }),
                    }))}
                    scaleCaptions={["0", t("news.article.scale_relevance", "relevance 1.0")]}
                  />
                )}
              </section>

              <section>
                <div className="section-title">{t("news.article.consensus_title", "Consensus across sources")}</div>
                {consensus ? (
                  <dl className="kv">
                    <dt>{t("news.article.f_sources", "sources / independent")}</dt>
                    <dd>
                      {consensus.source_count ?? "—"} / {consensus.independent_count ?? "—"}
                    </dd>
                    <dt>{t("news.article.f_agreement", "agreement / conflict")}</dt>
                    <dd>
                      {ratio(consensus.agreement)} / {ratio(consensus.conflict)}
                    </dd>
                    <dt>{t("news.article.f_weighted", "weighted direction")}</dt>
                    <dd>{consensus.weighted_direction ?? "—"}</dd>
                    <dt>{t("news.article.f_confidence", "confidence")}</dt>
                    <dd>{ratio(consensus.confidence)}</dd>
                  </dl>
                ) : (
                  <EmptyState message={t("news.article.consensus_empty", "No consensus record (single-source story or not evaluated).")} />
                )}
              </section>

              <section>
                <div className="section-title">{t("news.article.linkage_title", "Trade linkage (news → decisions)")}</div>
                {tradeLinks.length === 0 ? (
                  <EmptyState message={t("news.article.linkage_empty", "This article is not linked to any trade record.")} />
                ) : (
                  <DataTable headers={[{ label: t("news.article.h_trade", "trade") }, { label: t("news.article.h_strategy", "strategy") }, { label: t("news.article.h_link", "link") }, { label: t("news.article.h_at", "at") }]}>
                    {tradeLinks.map((l, i) => (
                      <tr key={`${String(l.trade_id ?? l.ticket ?? "link")}-${i}`}>
                        <td>{String(l.trade_id ?? l.ticket ?? "—")}</td>
                        <td>{String(l.strategy_id ?? "—")}</td>
                        <td>{l.linked_at && !l.link_type ? t("news.article.linked", "linked") : String(l.link_type ?? "—")}</td>
                        <td>{formatDateTime(String(l.linked_at ?? ""))}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </section>

              {related.length > 0 && (
                <section>
                  <div className="section-title">{t("news.article.related_title", "Related coverage")}</div>
                  <ul className="small muted" style={{ margin: 0, paddingInlineStart: 16 }}>
                    {related.map((r, i) => (
                      <li key={`${String(r.article_id ?? r.title ?? "rel")}-${i}`}>{String(r.title ?? r.article_id ?? "—")}</li>
                    ))}
                  </ul>
                </section>
              )}

              {postEventsText && (
                <section>
                  <div className="section-title">{t("news.article.post_event_title", "Post-event validation")}</div>
                  <pre tabIndex={0} className="tiny inline-mono" style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, overflowX: "auto", margin: 0 }}>
                    {postEventsText}
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
