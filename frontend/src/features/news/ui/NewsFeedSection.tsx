/**
 * News feed — headlines with impact/sentiment badges + the article drawer.
 *
 * Badge words are the backend's own (`analysis.direction`, `article_status`,
 * `ai_analysis.analysis_status`); an unanalyzed article is PENDING, never
 * silently NEUTRAL. AI actions call the analyze endpoints and then let the
 * refetched feed decide what to show (server owns the state).
 */

import { memo, useCallback, useMemo, useState, type KeyboardEvent } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, EmptyState, ErrorState, Panel, Segmented, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useAnalyzeArticle, useAutoPrune, useBatchAnalyze, useNewsAiStatus, useNewsFeed, useRestoreArticle } from "../hooks";
import { batchVerdict, directionOf, dirWord, errorText, impactPct, NEWS_FILTERS, xauusdRelPct } from "../model";
import type { NewsFeedArticle, NewsFilter } from "../types";
import { ArticleDrawer } from "./ArticleDrawer";
import { FreshnessNote, asErrorText } from "./shared";
import "./news.css";

export function NewsFeedSection() {
  const t = useI18n((s) => s.t);
  const [filter, setFilter] = useState<NewsFilter>("ACTIVE");
  const [limit] = useState(50);
  const [selected, setSelected] = useState<string | null>(null);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [note, setNote] = useState<{ text: string; err: boolean } | null>(null);

  const feed = useNewsFeed(filter, limit);
  const aiStatus = useNewsAiStatus();
  const analyze = useAnalyzeArticle();
  const batch = useBatchAnalyze();
  const prune = useAutoPrune();
  const restore = useRestoreArticle();

  const articles = feed.data?.articles ?? [];
  const counts = feed.data?.statusCounts;

  const ids = useMemo(() => articles.map((a) => a.article_id), [articles]);
  // perf: one linear scan per (articles, selection) change instead of a fresh
  // `articles.find(...)` on every render of the feed.
  const selectedArticle = useMemo(
    () => (selected ? articles.find((a) => a.article_id === selected) : undefined),
    [articles, selected],
  );

  const ai = aiStatus.data?.ai_status;

  // perf: row callbacks are created ONCE (deps are the mutation observers'
  // stable `mutate`), so the 50-row list stops re-allocating three closures
  // per row per render — ArticleRow is memoized against them.
  const { mutate: analyzeMutate } = analyze;
  const { mutate: restoreMutate } = restore;
  const handleSelect = useCallback((articleId: string): void => setSelected(articleId), []);

  const runAnalyze = useCallback((articleId: string, force: boolean): void => {
    analyzeMutate(
      { articleId, force },
      {
        onSuccess: (res) =>
          setNote({
            err: res.ok === false || !!res.error,
            text: res.error
              ? `Analyze refused: ${res.error}`
              : res.status === "SKIPPED_ALREADY_ANALYZED"
                ? `Already analyzed (${res.reason ?? "dedup"}) — use Re-analyze to force.`
                : `Analysis ${res.status ?? "QUEUED"} — the feed refreshes with the backend result.`,
          }),
        onError: (e) => setNote({ err: true, text: `Analyze failed: ${asErrorText(e)}` }),
      },
    );
  }, [analyzeMutate]);

  const runBatch = (): void => {
    if (ids.length === 0) {
      setNote({ err: false, text: t("news.feed.batch_empty", "No visible articles to analyze.") });
      return;
    }
    batch.mutate(ids, {
      onSuccess: (res) => {
        const v = batchVerdict(res);
        setNote({ err: !v.ok, text: v.message(t) });
      },
      onError: (e) => setNote({ err: true, text: t("news.feed.batch_failed", "Batch failed: {e}", { e: asErrorText(e) }) }),
    });
  };

  const runPrune = (): void => {
    prune.mutate(undefined, {
      onSuccess: (res) => {
        setPruneOpen(false);
        if (res.available === false || res.error) {
          setNote({ err: true, text: t("news.feed.prune_refused", "Auto-prune refused: {e}", { e: errorText(res.error) }) });
          return;
        }
        setNote({
          err: false,
          text: t("news.feed.prune_done", "Pruning complete — {marked} marked irrelevant, {preserved} preserved ({already} already irrelevant, {failed} failed). Recoverable via the Irrelevant filter.", { marked: res.marked_irrelevant ?? 0, preserved: res.preserved ?? 0, already: res.already_irrelevant ?? 0, failed: res.failed ?? 0 }),
        });
      },
      onError: (e) => {
        setPruneOpen(false);
        setNote({ err: true, text: t("news.feed.prune_failed", "Auto-prune failed: {e}", { e: asErrorText(e) }) });
      },
    });
  };

  const runRestore = useCallback((articleId: string): void => {
    restoreMutate(articleId, {
      onSuccess: (res) => setNote({ err: !!res.error, text: res.error ? t("news.feed.restore_refused", "Restore refused: {e}", { e: res.error }) : t("news.feed.restore_done", "Article restored to ACTIVE (backend-confirmed).") }),
      onError: (e) => setNote({ err: true, text: t("news.feed.restore_failed", "Restore failed: {e}", { e: asErrorText(e) }) }),
    });
  }, [restoreMutate, t]);

  return (
    <Panel
      title={t("news.feed.headlines", "Headlines ({n})", { n: articles.length })}
      tight={false}
      right={
        <>
          <Segmented
            options={NEWS_FILTERS}
            value={filter}
            onChange={(v) => {
              setFilter(v);
              setSelected(null);
            }}
          />
          <button className="btn small primary" onClick={runBatch} disabled={batch.isPending}>
            {batch.isPending ? t("news.feed.analyzing", "analyzing…") : t("news.feed.ai_analyze_visible", "AI-analyze visible")}
          </button>
          <button className="btn small" onClick={() => setPruneOpen(true)} disabled={prune.isPending}>
            {prune.isPending ? t("news.feed.pruning", "pruning…") : t("news.feed.hide_unrelated", "Hide unrelated")}
          </button>
        </>
      }
    >
      <div className="news-toolbar">
        <span className="badge unknown" title={t("news.feed.ai_badge_title", "AI readiness from /api/news/ai-status (secret-free)")}>
          {t("news.feed.ai_badge", "AI {state}", { state: ai?.state ?? "UNKNOWN" })}
          {ai?.provider ? ` · ${ai.provider}` : ""}
          {ai?.model ? ` ${ai.model}` : ""}
        </span>
        {counts && (
          <span className="timestamp-note">
            {t("news.feed.counts", "active {active} · irrelevant {irrelevant}", { active: counts.ACTIVE ?? "—", irrelevant: counts.IRRELEVANT ?? "—" })}
          </span>
        )}
        <span className="spacer" />
        <FreshnessNote updatedAtMs={feed.dataUpdatedAt ?? null} label={t("news.fresh.feed", "feed")} staleAfterMs={150_000} />
      </div>
      {note && <div className={`news-status-line ${note.err ? "err" : ""}`}>{note.text}</div>}

      {feed.isPending ? (
        <Skeleton count={6} height={44} />
      ) : feed.isError ? (
        <ErrorState message={asErrorText(feed.error)} onRetry={() => feed.refetch()} />
      ) : articles.length === 0 ? (
        <EmptyState
          message={t("news.feed.empty_any", "No {filter} articles.", { filter: filter.toLowerCase() })}
          hint={filter === "IRRELEVANT" ? t("news.feed.empty_hint_irrelevant", "Nothing was pruned — the auto-prune pass marks unrelated stories here.") : t("news.feed.empty_hint_fetch", "Use \"Fetch news\" above, or wait for the ingestion worker.")}
        />
      ) : (
        <div tabIndex={0} className="news-list">
          {articles.map((a) => (
            <ArticleRow
              key={a.article_id}
              article={a}
              selected={selected === a.article_id}
              busy={analyze.isPending}
              restoreBusy={restore.isPending}
              onSelect={handleSelect}
              onAnalyze={runAnalyze}
              onRestore={runRestore}
            />
          ))}
        </div>
      )}

      {selected && (
        <ArticleDrawer
          articleId={selected}
          fallback={selectedArticle}
          busy={analyze.isPending}
          analyzeNote={note?.text ?? null}
          onClose={() => setSelected(null)}
          onAnalyze={(force) => runAnalyze(selected, force)}
        />
      )}

      {pruneOpen && (
        <ConfirmModal
          title={t("news.feed.prune_title", "Hide unrelated news (auto-prune)")}
          confirmLabel={t("news.feed.prune_confirm", "Mark unrelated as IRRELEVANT")}
          busy={prune.isPending}
          onConfirm={runPrune}
          onCancel={() => setPruneOpen(false)}
        >
          <div>
            Marks low-importance / non-XAUUSD stories as <b className="inline-mono">IRRELEVANT</b> using importance + relevance — not a blunt
            "not gold =&gt; delete". Original articles are <b>preserved</b> and stay recoverable from the Irrelevant filter.
          </div>
        </ConfirmModal>
      )}
    </Panel>
  );
}

function impClass(pct: number | null): string {
  if (pct === null) return "news-imp";
  if (pct >= 70) return "news-imp high";
  if (pct >= 50) return "news-imp mid";
  if (pct >= 30) return "news-imp low";
  return "news-imp";
}

export const ArticleRow = memo(function ArticleRow({
  article: a,
  selected,
  busy,
  restoreBusy,
  onSelect,
  onAnalyze,
  onRestore,
}: {
  article: NewsFeedArticle;
  selected: boolean;
  busy: boolean;
  restoreBusy: boolean;
  onSelect: (articleId: string) => void;
  onAnalyze: (articleId: string, force: boolean) => void;
  onRestore: (articleId: string) => void;
}) {
  const t = useI18n((s) => s.t);
  const dir = directionOf(a);
  const imp = impactPct(a);
  const rel = xauusdRelPct(a);
  const status = (a.article_status ?? "ACTIVE").toUpperCase();
  const aiDone = !!(a.ai_analysis && (a.ai_analysis.summary || a.ai_analysis.analysis_status === "failed"));
  // keyboard parity for the clickable row: Enter/Space opens the same drawer the
  // pointer click opens (presentation only — no data, wording or fetch changes).
  const activate = (e: KeyboardEvent): void => {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    onSelect(a.article_id);
  };
  return (
    <article className={`news-item ${selected ? "selected" : ""}`} onClick={() => onSelect(a.article_id)} onKeyDown={activate} tabIndex={0} aria-current={selected || undefined}>
      <div className="news-item-head">
        <span className="title">{a.title}</span>
        <span className={`news-dir ${dir}`}>{dirWord(t, dir)}</span>
      </div>
      <div className="metarow">
        <span>{a.source_name || a.source_id || "—"}</span>
        <span className={impClass(imp)}>{imp === null ? t("news.feed.imp_dash", "imp —") : t("news.feed.imp", "imp {v}", { v: imp })}</span>
        <span className="tx-warn" >{t("news.feed.xau_rel", "XAU {v}", { v: rel === null ? "—" : `${rel}%` })}</span>
        {a.importance ? <span>{String(a.importance)}</span> : null}
        <span>{a.published_at ? formatDateTime(a.published_at) : "—"}</span>
        {status !== "ACTIVE" && <StatusBadge status={status} />}
        {a.is_duplicate && <span className="badge warn">{t("news.feed.duplicate", "DUPLICATE")}</span>}
      </div>
      {a.analysis?.market_mechanism && <div className="mech">{a.analysis.market_mechanism}</div>}
      {a.keyword_hits && a.keyword_hits.length > 0 && (
        <div className="metarow">
          {a.keyword_hits.slice(0, 6).map((k) => (
            <span className="news-kw" key={`${a.article_id}-${k.keyword}`}>
              {k.keyword}
            </span>
          ))}
        </div>
      )}
      <div className="news-actions" onClick={(e) => e.stopPropagation()}>
        {status === "IRRELEVANT" && (
          <button className="btn small" onClick={() => onRestore(a.article_id)} disabled={busy || restoreBusy}>
            {restoreBusy ? t("news.feed.restoring", "restoring…") : t("news.feed.restore", "Restore")}
          </button>
        )}
        {aiDone ? (
          <button className="btn small primary" onClick={() => onAnalyze(a.article_id, true)} disabled={busy || restoreBusy}>
            {busy ? t("news.feed.analyzing", "analyzing…") : t("news.feed.reanalyze", "Re-analyze (force)")}
          </button>
        ) : (
          <button className="btn small primary" onClick={() => onAnalyze(a.article_id, false)} disabled={busy || restoreBusy}>
            {busy ? t("news.feed.analyzing", "analyzing…") : t("news.feed.analyze_ai", "Analyze with AI")}
          </button>
        )}
        {a.ai_analysis?.analysis_status === "failed" && <span className="badge bad">{t("news.feed.ai_failed", "AI FAILED")}</span>}
        {a.ai_analysis?.insufficient_evidence && <span className="badge warn">{t("news.feed.insufficient_evidence", "INSUFFICIENT EVIDENCE")}</span>}
      </div>
    </article>
  );
});
