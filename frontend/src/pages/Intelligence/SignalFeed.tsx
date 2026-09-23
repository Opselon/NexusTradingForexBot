/**
 * PURPOSE:  Signal feed for /api/news/latest — filter chip row (derived from
 *           the loaded articles), result-count label, and a newest-first
 *           timeline rail of per-article signal cards with honest
 *           loading/empty/error states plus the existing CSV export.
 * OWNER:    uiux-wave5-intel  (future edits to this file belong to this lane)
 * CONSUMES: a TanStack query result for /api/news/latest (structurally typed
 *           via _shared/SectionState QueryLike), NewsArticle fields only,
 *           primitives (Panel, SeverityBadge, EmptyState), ./signalBits.
 * PROVIDES: default SignalFeed — rendered inside IntelligencePage.
 * INVARIANTS: only NewsArticle fields are rendered (article_id, title,
 *           source_name, published_at, importance, importance_score — the
 *           endpoint's actual payload); chips are derived from importance
 *           words actually present; filter state persists only under
 *           `w5.intel.filter`; the CSV export stays byte-identical to the
 *           previous table export (all payload rows, unfiltered).
 * EXTEND:   a new filter dimension = derive its chips inside FeedBody from the
 *           loaded data; a new card figure = add it to SignalCard JSX.
 */

import { useMemo, useState } from "react";
import { Panel, SeverityBadge, EmptyState } from "@/components/primitives";
import { SectionState, type QueryLike } from "@/pages/_shared/SectionState";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime } from "@/lib/format";
import type { NewsArticle } from "@/types/domain";
import { ScoreBar, useRelTime } from "./signalBits";
import { useI18n } from "@/stores/i18nStore";

type FeedPayload = { available: boolean; articles?: NewsArticle[] };

const FILTER_KEY = "w5.intel.filter";
const ALL = "*";

/** Filter selection is display-only state — persisted under the lane key. */
function readStoredFilter(): string {
  try {
    return window.localStorage.getItem(FILTER_KEY) || ALL;
  } catch {
    return ALL;
  }
}

function writeStoredFilter(value: string): void {
  try {
    window.localStorage.setItem(FILTER_KEY, value);
  } catch {
    /* storage unavailable (private mode) — the in-memory filter still works */
  }
}

/** Importance word the backend sent; missing → "UNKNOWN" (the kit's own word). */
function impWord(a: NewsArticle): string {
  const w = String(a.importance ?? "").trim();
  return w ? w.toUpperCase() : "UNKNOWN";
}

/** Backend orders /api/news/latest by published_at DESC; re-sort defensively
 *  (stable) so the rail is newest-first even if the payload order changes.
 *  Undated articles sink to the end — they keep no invented age. */
function byPublishedDesc(a: NewsArticle, b: NewsArticle): number {
  const ta = a.published_at ? Date.parse(a.published_at) : NaN;
  const tb = b.published_at ? Date.parse(b.published_at) : NaN;
  const na = Number.isNaN(ta);
  const nb = Number.isNaN(tb);
  if (na && nb) return 0;
  if (na) return 1;
  if (nb) return -1;
  return tb - ta;
}

function SignalCard({ article, newest }: { article: NewsArticle; newest: boolean }) {
  const t = useI18n((s) => s.t);
  const score = typeof article.importance_score === "number" ? article.importance_score : null;
  const rel = useRelTime(article.published_at ?? null);
  return (
    <li className={`itl-item ${newest ? "itl-item--newest" : ""}`}>
      <article className="itl-card">
        <div className="itl-card__head">
          <span className="itl-type">{t("intelligence.card.news", "news")}</span>
          <SeverityBadge severity={impWord(article)} />
          <time
            className="itl-time"
            dateTime={article.published_at ?? undefined}
            title={article.published_at ? formatDateTime(article.published_at) : t("intelligence.feed.no_published", "backend sent no published_at")}
          >
            {rel ?? (article.published_at ? formatDateTime(article.published_at) : "—")}
          </time>
        </div>
        <h3 className="itl-card__title">{article.title}</h3>
        <div className="itl-card__figures">
          <span className="itl-fig">
            <span className="k">{t("intelligence.fig.score", "score")}</span>
            <span className="v">{score === null ? "—" : score.toFixed(3)}</span>
          </span>
          <span className="itl-fig">
            <span className="k">{t("intelligence.kv.source", "source")}</span>
            <span className="v">{article.source_name ?? "—"}</span>
          </span>
          <span className="itl-fig">
            <span className="k">{t("intelligence.fig.id", "id")}</span>
            <span className="v">{article.article_id}</span>
          </span>
        </div>
        <ScoreBar value={score} label={t("intelligence.card.importance_score", "importance score")} />
      </article>
    </li>
  );
}

/** Filter chips + count + rail. Owns the (persisted) filter selection. */
function FeedBody({ articles }: { articles: NewsArticle[] }) {
  const t = useI18n((s) => s.t);
  const [filter, setFilter] = useState<string>(readStoredFilter);
  const sorted = useMemo(() => [...articles].sort(byPublishedDesc), [articles]);
  // Chips are derived strictly from importance words present in the payload.
  const chips = useMemo(() => {
    const seen: string[] = [];
    for (const a of sorted) {
      const w = impWord(a);
      if (!seen.includes(w)) seen.push(w);
    }
    return seen;
  }, [sorted]);

  const active = filter === ALL || chips.includes(filter) ? filter : ALL;
  const visible = active === ALL ? sorted : sorted.filter((a) => impWord(a) === active);

  const pick = (value: string) => {
    setFilter(value);
    writeStoredFilter(value);
  };

  return (
    <>
      <div className="itl-chiprow" role="group" aria-label={t("intelligence.feed.filter_aria", "Filter articles by importance")}>
        <button className="itl-chip" aria-pressed={active === ALL} onClick={() => pick(ALL)}>
          {t("intelligence.feed.all", "all")}
        </button>
        {chips.map((w) => (
          <button key={w} className="itl-chip" aria-pressed={active === w} onClick={() => pick(w)}>
            {w.toLowerCase()}
          </button>
        ))}
        <span className="itl-count" aria-live="polite">
          {visible.length === sorted.length
            ? t("intelligence.feed.count", "{n} signals", { n: sorted.length })
            : t("intelligence.feed.count_filtered", "{a} of {b} signals", { a: visible.length, b: sorted.length })}
        </span>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          message={t("intelligence.feed.empty", "No signals match the current filter.")}
          hint={t("intelligence.feed.empty_hint", "The payload changed since the filter was chosen — pick \"all\" to show {n} loaded signals.", { n: sorted.length })}
        />
      ) : (
        <ul className="itl-rail">
          {visible.map((a, i) => (
            <SignalCard key={a.article_id} article={a} newest={i === 0} />
          ))}
        </ul>
      )}
    </>
  );
}

export default function SignalFeed({ query }: { query: QueryLike<FeedPayload> }) {
  const t = useI18n((s) => s.t);
  return (
    <Panel
      title={t("intelligence.panel.articles", "Latest canonical articles")}
      tight
      right={
        query.data?.articles && query.data.articles.length > 0 ? (
          <button
            className="btn small ghost"
            onClick={() =>
              downloadCsv({
                filename: `nse-news-${stampForFilename()}.csv`,
                headers: ["article_id", "published_at", "source", "importance", "status", "title"],
                rows: (query.data?.articles ?? []).map((a) => [
                  a.article_id,
                  a.published_at ?? "",
                  a.source_name ?? "",
                  String(a.importance ?? ""),
                  a.article_status ?? "",
                  a.title,
                ]),
              })
            }
            title={t("intelligence.csv.articles_title", "exports exactly the rows returned by /api/news/latest")}
          >
            ⇩ CSV
          </button>
        ) : undefined
      }
    >
      <SectionState
        query={query}
        skeletonRows={4}
        emptyWhen={(d) => !(d.available && (d.articles?.length ?? 0) > 0)}
        emptyMessage={t("intelligence.articles.empty", "No articles available.")}
        emptyHint={t("intelligence.articles.empty_hint", "The feed endpoint answered with an empty list — the parser or the source may be warming up.")}
        errorFallback={t("intelligence.error.news_feed", "News feed endpoint failed.")}
      >
        {(d) => <FeedBody articles={d.articles ?? []} />}
      </SectionState>
    </Panel>
  );
}
