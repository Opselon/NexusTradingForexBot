/**
 * News feed — headlines with impact/sentiment badges + the article drawer.
 *
 * Badge words are the backend's own (`analysis.direction`, `article_status`,
 * `ai_analysis.analysis_status`); an unanalyzed article is PENDING, never
 * silently NEUTRAL. AI actions call the analyze endpoints and then let the
 * refetched feed decide what to show (server owns the state).
 */

import { useMemo, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, EmptyState, ErrorState, Panel, Segmented, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useAnalyzeArticle, useAutoPrune, useBatchAnalyze, useNewsAiStatus, useNewsFeed, useRestoreArticle } from "../hooks";
import { batchVerdict, directionOf, errorText, impactPct, NEWS_FILTERS, xauusdRelPct } from "../model";
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

  const ai = aiStatus.data?.ai_status;

  const runAnalyze = (articleId: string, force: boolean): void => {
    analyze.mutate(
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
  };

  const runBatch = (): void => {
    if (ids.length === 0) {
      setNote({ err: false, text: "No visible articles to analyze." });
      return;
    }
    batch.mutate(ids, {
      onSuccess: (res) => {
        const v = batchVerdict(res);
        setNote({ err: !v.ok, text: v.message(t) });
      },
      onError: (e) => setNote({ err: true, text: `Batch failed: ${asErrorText(e)}` }),
    });
  };

  const runPrune = (): void => {
    prune.mutate(undefined, {
      onSuccess: (res) => {
        setPruneOpen(false);
        if (res.available === false || res.error) {
          setNote({ err: true, text: `Auto-prune refused: ${errorText(res.error)}` });
          return;
        }
        setNote({
          err: false,
          text: `Pruning complete — ${res.marked_irrelevant ?? 0} marked irrelevant, ${res.preserved ?? 0} preserved (${res.already_irrelevant ?? 0} already irrelevant, ${res.failed ?? 0} failed). Recoverable via the Irrelevant filter.`,
        });
      },
      onError: (e) => {
        setPruneOpen(false);
        setNote({ err: true, text: `Auto-prune failed: ${asErrorText(e)}` });
      },
    });
  };

  const runRestore = (articleId: string): void => {
    restore.mutate(articleId, {
      onSuccess: (res) => setNote({ err: !!res.error, text: res.error ? `Restore refused: ${res.error}` : "Article restored to ACTIVE (backend-confirmed)." }),
      onError: (e) => setNote({ err: true, text: `Restore failed: ${asErrorText(e)}` }),
    });
  };

  return (
    <Panel
      title={`Headlines (${articles.length})`}
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
            {batch.isPending ? "analyzing…" : "AI-analyze visible"}
          </button>
          <button className="btn small" onClick={() => setPruneOpen(true)} disabled={prune.isPending}>
            {prune.isPending ? "pruning…" : "Hide unrelated"}
          </button>
        </>
      }
    >
      <div className="news-toolbar">
        <span className="badge unknown" title="AI readiness from /api/news/ai-status (secret-free)">
          AI {ai?.state ?? "UNKNOWN"}
          {ai?.provider ? ` · ${ai.provider}` : ""}
          {ai?.model ? ` ${ai.model}` : ""}
        </span>
        {counts && (
          <span className="timestamp-note">
            active {counts.ACTIVE ?? "—"} · irrelevant {counts.IRRELEVANT ?? "—"}
          </span>
        )}
        <span className="spacer" />
        <FreshnessNote updatedAtMs={feed.dataUpdatedAt ?? null} label="feed" staleAfterMs={150_000} />
      </div>
      {note && <div className={`news-status-line ${note.err ? "err" : ""}`}>{note.text}</div>}

      {feed.isPending ? (
        <Skeleton count={6} height={44} />
      ) : feed.isError ? (
        <ErrorState message={asErrorText(feed.error)} onRetry={() => feed.refetch()} />
      ) : articles.length === 0 ? (
        <EmptyState
          message={`No ${filter.toLowerCase()} articles.`}
          hint={filter === "IRRELEVANT" ? "Nothing was pruned — the auto-prune pass marks unrelated stories here." : 'Use "Fetch news" above, or wait for the ingestion worker.'}
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
          analyzeNote={note?.text ?? null}
          onClose={() => setSelected(null)}
          onAnalyze={(force) => runAnalyze(selected, force)}
        />
      )}

      {pruneOpen && (
        <ConfirmModal
          title="Hide unrelated news (auto-prune)"
          confirmLabel="Mark unrelated as IRRELEVANT"
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

export function ArticleRow({
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
  onSelect: () => void;
  onAnalyze: (force: boolean) => void;
  onRestore: () => void;
}) {
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
        <span className={`news-dir ${dir}`}>{dir}</span>
      </div>
      <div className="metarow">
        <span>{a.source_name || a.source_id || "—"}</span>
        <span className={impClass(imp)}>{imp === null ? "imp —" : `imp ${imp}`}</span>
        <span className="tx-warn" >XAU {rel === null ? "—" : `${rel}%`}</span>
        {a.importance ? <span>{String(a.importance)}</span> : null}
        <span>{a.published_at ? formatDateTime(a.published_at) : "—"}</span>
        {status !== "ACTIVE" && <StatusBadge status={status} />}
        {a.is_duplicate && <span className="badge warn">DUPLICATE</span>}
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
          <button className="btn small" onClick={onRestore} disabled={busy || restoreBusy}>
            {restoreBusy ? "restoring…" : "Restore"}
          </button>
        )}
        {aiDone ? (
          <button className="btn small primary" onClick={() => onAnalyze(true)} disabled={busy || restoreBusy}>
            {busy ? "analyzing…" : "Re-analyze (force)"}
          </button>
        ) : (
          <button className="btn small primary" onClick={() => onAnalyze(false)} disabled={busy || restoreBusy}>
            {busy ? "analyzing…" : "Analyze with AI"}
          </button>
        )}
        {a.ai_analysis?.analysis_status === "failed" && <span className="badge bad">AI FAILED</span>}
        {a.ai_analysis?.insufficient_evidence && <span className="badge warn">INSUFFICIENT EVIDENCE</span>}
      </div>
    </article>
  );
}
