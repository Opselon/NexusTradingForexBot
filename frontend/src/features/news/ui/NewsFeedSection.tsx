/**
 * News feed — headlines with impact/sentiment badges + the article drawer.
 *
 * Badge words are the backend's own (`analysis.direction`, `article_status`,
 * `ai_analysis.analysis_status`); an unanalyzed article is PENDING, never
 * silently NEUTRAL. AI actions call the analyze endpoints and then let the
 * refetched feed decide what to show (server owns the state).
 *
 * Status notes are (t) => string closures resolved during render, so a
 * language switch re-translates an already-visible note.
 */

import { useMemo, useState } from "react";
import { ConfirmModal, EmptyState, ErrorState, Panel, Segmented, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useAnalyzeArticle, useAutoPrune, useBatchAnalyze, useNewsAiStatus, useNewsFeed, useRestoreArticle } from "../hooks";
import { batchVerdict, directionOf, dirWord, errorText, impactPct, newsFilterLabel, NEWS_FILTERS, xauusdRelPct } from "../model";
import type { NewsFeedArticle, NewsFilter } from "../types";
import { ArticleDrawer } from "./ArticleDrawer";
import { FreshnessNote, asErrorText, type TFunc } from "./shared";
import "./news.css";

/** One feed note: rendered with the ACTIVE language every time. */
type FeedNote = { err: boolean; text: (t: TFunc) => string };

export function NewsFeedSection() {
  const t = useI18n((s) => s.t);
  const [filter, setFilter] = useState<NewsFilter>("ACTIVE");
  const [limit] = useState(50);
  const [selected, setSelected] = useState<string | null>(null);
  const [pruneOpen, setPruneOpen] = useState(false);
  const [note, setNote] = useState<FeedNote | null>(null);

  const feed = useNewsFeed(filter, limit);
  const aiStatus = useNewsAiStatus();
  const analyze = useAnalyzeArticle();
  const batch = useBatchAnalyze();
  const prune = useAutoPrune();
  const restore = useRestoreArticle();

  const articles = feed.data?.articles ?? [];
  const counts = feed.data?.statusCounts;

  const ids = useMemo(() => articles.map((a) => a.article_id), [articles]);

  const ai = aiStatus.data?.ai_status;

  const filterOptions = NEWS_FILTERS.map((f) => ({ ...f, label: newsFilterLabel(t, f.id) }));

  const runAnalyze = (articleId: string, force: boolean): void => {
    analyze.mutate(
      { articleId, force },
      {
        onSuccess: (res) =>
          setNote({
            err: res.ok === false || !!res.error,
            text: (t) =>
              res.error
                ? t("news.feed.analyze_refused", "Analyze refused: {e}", { e: res.error })
                : res.status === "SKIPPED_ALREADY_ANALYZED"
                  ? t("news.feed.analyze_skipped", "Already analyzed ({reason}) — use Re-analyze to force.", {
                      reason: res.reason ?? "dedup",
                    })
                  : t(
                      "news.feed.analyze_queued",
                      "Analysis {status} — the feed refreshes with the backend result.",
                      { status: res.status ?? "QUEUED" },
                    ),
          }),
        onError: (e) =>
          setNote({
            err: true,
            text: (t) => t("news.feed.analyze_failed", "Analyze failed: {e}", { e: asErrorText(e, t) }),
          }),
      },
    );
  };

  const runBatch = (): void => {
    if (ids.length === 0) {
      setNote({ err: false, text: (t) => t("news.feed.batch_empty", "No visible articles to analyze.") });
      return;
    }
    batch.mutate(ids, {
      onSuccess: (res) => {
        const v = batchVerdict(res);
        setNote({ err: !v.ok, text: v.message });
      },
      onError: (e) =>
        setNote({
          err: true,
          text: (t) => t("news.feed.batch_failed", "Batch failed: {e}", { e: asErrorText(e, t) }),
        }),
    });
  };

  const runPrune = (): void => {
    prune.mutate(undefined, {
      onSuccess: (res) => {
        setPruneOpen(false);
        if (res.available === false || res.error) {
          setNote({
            err: true,
            text: (t) => t("news.feed.prune_refused", "Auto-prune refused: {e}", { e: errorText(res.error) }),
          });
          return;
        }
        setNote({
          err: false,
          text: (t) =>
            t(
              "news.feed.prune_done",
              "Pruning complete — {marked} marked irrelevant, {preserved} preserved ({already} already irrelevant, {failed} failed). Recoverable via the {f} filter.",
              {
                marked: res.marked_irrelevant ?? 0,
                preserved: res.preserved ?? 0,
                already: res.already_irrelevant ?? 0,
                failed: res.failed ?? 0,
                f: t("news.filter.irrelevant", "Irrelevant"),
              },
            ),
        });
      },
      onError: (e) => {
        setPruneOpen(false);
        setNote({
          err: true,
          text: (t) => t("news.feed.prune_failed", "Auto-prune failed: {e}", { e: asErrorText(e, t) }),
        });
      },
    });
  };

  const runRestore = (articleId: string): void => {
    restore.mutate(articleId, {
      onSuccess: (res) =>
        setNote({
          err: !!res.error,
          text: (t) =>
            res.error
              ? t("news.feed.restore_refused", "Restore refused: {e}", { e: res.error })
              : t("news.feed.restore_done", "Article restored to ACTIVE (backend-confirmed)."),
        }),
      onError: (e) =>
        setNote({
          err: true,
          text: (t) => t("news.feed.restore_failed", "Restore failed: {e}", { e: asErrorText(e, t) }),
        }),
    });
  };

  return (
    <Panel
      title={t("news.feed.headlines", "Headlines ({n})", { n: articles.length })}
      tight={false}
      right={
        <>
          <Segmented
            options={filterOptions}
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
        <span
          className="badge unknown"
          title={t("news.feed.ai_badge_title", "AI readiness from /api/news/ai-status (secret-free)")}
        >
          {t("news.feed.ai_badge", "AI {state}", { state: ai?.state ?? "UNKNOWN" })}
          {ai?.provider ? ` · ${ai.provider}` : ""}
          {ai?.model ? ` ${ai.model}` : ""}
        </span>
        {counts && (
          <span className="timestamp-note">
            {t("news.feed.counts", "active {active} · irrelevant {irrelevant}", {
              active: counts.ACTIVE ?? "—",
              irrelevant: counts.IRRELEVANT ?? "—",
            })}
          </span>
        )}
        <span className="spacer" />
        <FreshnessNote updatedAtMs={feed.dataUpdatedAt ?? null} label="feed" staleAfterMs={150_000} />
      </div>
      {note && <div className={`news-status-line ${note.err ? "err" : ""}`}>{note.text(t)}</div>}

      {feed.isPending ? (
        <Skeleton count={6} height={44} />
      ) : feed.isError ? (
        <ErrorState message={asErrorText(feed.error, t)} onRetry={() => feed.refetch()} />
      ) : articles.length === 0 ? (
        <EmptyState
          message={t("news.feed.empty_any", "No {filter} articles.", {
            filter: newsFilterLabel(t, filter).toLowerCase(),
          })}
          hint={
            filter === "IRRELEVANT"
              ? t("news.feed.empty_hint_irrelevant", "Nothing was pruned — the auto-prune pass marks unrelated stories here.")
              : t("news.feed.empty_hint_fetch", "Use \"Fetch news\" above, or wait for the ingestion worker.")
          }
        />
      ) : (
        <div className="news-list">
          {articles.map((a) => (
            <ArticleRow
              key={a.article_id}
              article={a}
              selected={selected === a.article_id}
              busy={analyze.isPending}
              onSelect={() => setSelected(a.article_id)}
              onAnalyze={(force) => runAnalyze(a.article_id, force)}
              onRestore={() => runRestore(a.article_id)}
            />
          ))}
        </div>
      )}

      {selected && (
        <ArticleDrawer
          articleId={selected}
          fallback={articles.find((a) => a.article_id === selected)}
          busy={analyze.isPending}
          analyzeNote={note?.text(t) ?? null}
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
            {t("news.feed.prune_body_1", "Marks low-importance / non-XAUUSD stories as ")}
            <b className="inline-mono">IRRELEVANT</b>
            {t(
              "news.feed.prune_body_2",
              " using importance + relevance — not a blunt \"not gold => delete\". Original articles are ",
            )}
            <b>{t("news.feed.prune_preserved", "preserved")}</b>
            {t("news.feed.prune_body_3", " and stay recoverable from the Irrelevant filter.")}
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

export function ArticleRow({
  article: a,
  selected,
  busy,
  onSelect,
  onAnalyze,
  onRestore,
}: {
  article: NewsFeedArticle;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onAnalyze: (force: boolean) => void;
  onRestore: () => void;
}) {
  const t = useI18n((s) => s.t);
  const dir = directionOf(a);
  const imp = impactPct(a);
  const rel = xauusdRelPct(a);
  const status = (a.article_status ?? "ACTIVE").toUpperCase();
  const aiDone = !!(a.ai_analysis && (a.ai_analysis.summary || a.ai_analysis.analysis_status === "failed"));
  return (
    <article className={`news-item ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <span className="title" style={{ flex: 1, minWidth: 0 }}>
          {a.title}
        </span>
        <span className={`news-dir ${dir}`}>{dirWord(t, dir)}</span>
      </div>
      <div className="metarow">
        <span>{a.source_name || a.source_id || "—"}</span>
        <span className={impClass(imp)}>{t("news.feed.imp", "imp {v}", { v: imp === null ? "—" : imp })}</span>
        <span style={{ color: "var(--amber)" }}>{t("news.feed.xau_rel", "XAU {v}", { v: rel === null ? "—" : `${rel}%` })}</span>
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
          <button className="btn small" onClick={onRestore} disabled={busy}>
            {t("news.feed.restore", "Restore")}
          </button>
        )}
        {aiDone ? (
          <button className="btn small primary" onClick={() => onAnalyze(true)} disabled={busy}>
            {busy ? t("news.feed.analyzing", "analyzing…") : t("news.feed.reanalyze", "Re-analyze (force)")}
          </button>
        ) : (
          <button className="btn small primary" onClick={() => onAnalyze(false)} disabled={busy}>
            {busy ? t("news.feed.analyzing", "analyzing…") : t("news.feed.analyze_ai", "Analyze with AI")}
          </button>
        )}
        {a.ai_analysis?.analysis_status === "failed" && (
          <span className="badge bad">{t("news.feed.ai_failed", "AI FAILED")}</span>
        )}
        {a.ai_analysis?.insufficient_evidence && (
          <span className="badge warn">{t("news.feed.insufficient_evidence", "INSUFFICIENT EVIDENCE")}</span>
        )}
      </div>
    </article>
  );
}
