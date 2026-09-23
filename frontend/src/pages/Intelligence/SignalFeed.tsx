/**
 * PURPOSE:  Signal feed for /api/news/latest — a pro filter-chip row with
 *           payload-derived counts and an animated active underline, a
 *           date-grouped timeline rail with sticky day headers and richer
 *           signal cards (source-initial avatar, verbatim importance badge,
 *           hover lift + staggered entry), honest loading/empty/error states,
 *           an endpoint provenance caption, and the existing CSV export.
 * OWNER:    ui/w2-lane-c  (future edits to this file belong to lane C)
 * CONSUMES: a TanStack query result for /api/news/latest (structurally typed
 *           via _shared/SectionState QueryLike), NewsArticle fields only,
 *           primitives (Panel, EmptyState), ./signalBits (WordBadge,
 *           importanceTone, ScoreBar, relTime), ./feed.css (`itl-*` classes).
 * PROVIDES: default SignalFeed — rendered inside IntelligencePage.
 * INVARIANTS: only NewsArticle fields are rendered (article_id, title,
 *           source_name, published_at, importance, importance_score — the
 *           endpoint's actual payload); the importance word is printed
 *           VERBATIM with a tone that only restates it; chips and their counts
 *           are derived strictly from payload rows and date groups only place
 *           articles by their own published_at (undated rows sink honestly);
 *           filter state persists only under `w5.intel.filter`; the CSV export
 *           stays byte-identical to the previous export (all payload rows,
 *           unfiltered); '/api/news/latest' is static provenance text; all
 *           motion is off under prefers-reduced-motion.
 * EXTEND:   a new filter dimension = derive its chips inside FeedBody from the
 *           loaded data; a new card figure = add it to SignalCard JSX.
 */

import { useMemo, useState, type KeyboardEvent } from "react";
import { Panel, EmptyState } from "@/components/primitives";
import { SectionState, type QueryLike } from "@/pages/_shared/SectionState";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime } from "@/lib/format";
import type { NewsArticle } from "@/types/domain";
import { ScoreBar, WordBadge, importanceTone, relTime } from "./signalBits";

type FeedPayload = { available: boolean; articles?: NewsArticle[] };

const FILTER_KEY = "w5.intel.filter";
const ALL = "*";
const UNDATED_KEY = "__undated__";

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

/** Chip/filter key: the backend importance word, normalized for matching only
 *  (badge text always shows the verbatim payload word, see SignalCard). */
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

/**
 * Deterministic avatar palette index from source_name — presentation only:
 * the letter shown is always the source's own first character, and the palette
 * draws from theme tokens (feed.css `.itl-avatar--N`), never from payload data.
 */
function avatarTone(source: string): number {
  let h = 0;
  for (let i = 0; i < source.length; i++) h = (h * 31 + source.charCodeAt(i)) >>> 0;
  return h % 4;
}

/** Day label restating each article's own published_at (client calendar day). */
function dayLabel(d: Date): string {
  const now = new Date();
  const same = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  if (same(d, now)) return `today · ${day}`;
  if (same(d, yesterday)) return `yesterday · ${day}`;
  return day;
}

interface RailEntry {
  article: NewsArticle;
  /** Position in the visible list — entry-animation stagger only. */
  index: number;
}

interface DayGroup {
  key: string;
  label: string;
  undated: boolean;
  entries: RailEntry[];
}

/** Group visible articles by the local calendar day of their published_at.
 *  Rows without a parseable published_at collect under an honest "undated"
 *  group (they keep no invented date); group order follows the sorted rail. */
function groupByDay(list: RailEntry[]): DayGroup[] {
  const out: DayGroup[] = [];
  const byKey = new Map<string, DayGroup>();
  const ensure = (key: string, label: string, undated: boolean): DayGroup => {
    const existing = byKey.get(key);
    if (existing) return existing;
    const g: DayGroup = { key, label, undated, entries: [] };
    byKey.set(key, g);
    out.push(g);
    return g;
  };
  for (const entry of list) {
    const iso = entry.article.published_at;
    const t = iso ? Date.parse(iso) : NaN;
    if (!Number.isFinite(t)) {
      // no (parseable) published_at — honest "undated" bucket, no invented date
      ensure(UNDATED_KEY, "undated", true).entries.push(entry);
      continue;
    }
    const d = new Date(t);
    ensure(d.toLocaleDateString("en-CA"), dayLabel(d), false).entries.push(entry);
  }
  return out;
}

/** Chip row arrow/Home/End roving focus — buttons stay individually tabbable. */
const CHIP_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End"]);

function onChipRowKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
  if (!CHIP_KEYS.has(e.key)) return;
  const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("button.itl-chip"));
  const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
  if (i < 0) return;
  e.preventDefault();
  const n = buttons.length;
  const next =
    e.key === "ArrowLeft"
      ? (i - 1 + n) % n
      : e.key === "ArrowRight"
        ? (i + 1) % n
        : e.key === "Home"
          ? 0
          : n - 1;
  buttons[next]?.focus();
}

function SignalCard({ article, newest, index }: { article: NewsArticle; newest: boolean; index: number }) {
  const score = typeof article.importance_score === "number" ? article.importance_score : null;
  const rel = relTime(article.published_at ?? null);
  const source = (article.source_name ?? "").trim();
  const rawTitle = article.title;
  return (
    <li className={`itl-item ${newest ? "itl-item--newest" : ""}`}>
      {/* index → entry-animation stagger (decorative; off under reduced motion) */}
      <article className="itl-card" style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}>
        <div className="itl-card__head">
          <span
            className={`itl-avatar itl-avatar--${avatarTone(source)}`}
            title={source ? `source_name: ${source}` : "backend sent no source_name"}
            aria-hidden="true"
          >
            {source ? source.slice(0, 1).toUpperCase() : "—"}
          </span>
          <span className="itl-type">news</span>
          <WordBadge
            word={article.importance}
            tone={importanceTone(article.importance)}
            title={
              article.importance !== null && article.importance !== undefined &&
              String(article.importance).trim() !== ""
                ? `importance (verbatim): ${String(article.importance).trim()}`
                : "backend sent no importance"
            }
          />
          <time
            className="itl-time"
            dateTime={article.published_at ?? undefined}
            title={article.published_at ? formatDateTime(article.published_at) : "backend sent no published_at"}
          >
            {rel ?? (article.published_at ? formatDateTime(article.published_at) : "—")}
          </time>
        </div>
        <h4 className="itl-card__title" title={rawTitle}>
          {rawTitle}
        </h4>
        <div className="itl-card__figures">
          <span
            className="itl-fig"
            title={score === null ? "importance_score missing in payload" : "importance_score (raw payload value)"}
          >
            <span className="k">score</span>
            <span className="v">{score === null ? "—" : String(score)}</span>
          </span>
          <span className="itl-fig" title={source ? "source_name (raw payload value)" : "source_name missing in payload"}>
            <span className="k">source</span>
            <span className="v">{source || "—"}</span>
          </span>
          <span className="itl-fig" title="article_id (raw payload value)">
            <span className="k">id</span>
            <span className="v">{article.article_id}</span>
          </span>
        </div>
        <ScoreBar value={score} label="importance score" />
      </article>
    </li>
  );
}

/** Filter chips + count + grouped rail. Owns the (persisted) filter selection. */
function FeedBody({ articles }: { articles: NewsArticle[] }) {
  const [filter, setFilter] = useState<string>(readStoredFilter);
  const sorted = useMemo(() => [...articles].sort(byPublishedDesc), [articles]);

  // Chips + counts are derived strictly from importance words in the payload.
  const chips = useMemo(() => {
    const counts = new Map<string, number>();
    for (const a of sorted) {
      const w = impWord(a);
      counts.set(w, (counts.get(w) ?? 0) + 1);
    }
    return Array.from(counts, ([word, count]) => ({ word, count }));
  }, [sorted]);

  const active = filter === ALL || chips.some((c) => c.word === filter) ? filter : ALL;
  const visible = active === ALL ? sorted : sorted.filter((a) => impWord(a) === active);

  const groups = useMemo(
    () => groupByDay(visible.map((article, index) => ({ article, index }))),
    [visible],
  );

  const pick = (value: string) => {
    setFilter(value);
    writeStoredFilter(value);
  };

  return (
    <>
      <div
        className="itl-chiprow"
        role="group"
        aria-label="Filter articles by importance"
        onKeyDown={onChipRowKeyDown}
      >
        <button
          className="itl-chip"
          aria-pressed={active === ALL}
          title={`show all ${sorted.length} loaded payload rows`}
          onClick={() => pick(ALL)}
        >
          <span className="itl-chip__label">all</span>
          <span className="itl-chip__n">{sorted.length}</span>
        </button>
        {chips.map((c) => (
          <button
            key={c.word}
            className="itl-chip"
            aria-pressed={active === c.word}
            title={`importance "${c.word}" — ${c.count} of ${sorted.length} payload rows`}
            onClick={() => pick(c.word)}
          >
            <span className="itl-chip__label">{c.word.toLowerCase()}</span>
            <span className="itl-chip__n">{c.count}</span>
          </button>
        ))}
        <span className="itl-count" aria-live="polite">
          {visible.length === sorted.length
            ? `${sorted.length} signals`
            : `${visible.length} of ${sorted.length} signals`}
        </span>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          message="No signals match the current filter."
          hint={`The payload changed since the filter was chosen — pick "all" to show ${sorted.length} loaded signals.`}
        />
      ) : (
        <div className="itl-rail">
          {groups.map((g) => (
            <section className="itl-group" key={g.key} aria-label={g.label}>
              <h3
                className="itl-grouphead"
                title={g.undated ? "backend sent no published_at for these rows" : `published_at day group: ${g.label}`}
              >
                <span className="itl-groupdate">{g.label}</span>
                <i className="itl-groupsep" aria-hidden="true" />
                <span className="itl-groupcount">
                  {g.entries.length} {g.entries.length === 1 ? "signal" : "signals"}
                </span>
              </h3>
              <ul className="itl-list">
                {g.entries.map(({ article, index }) => (
                  <SignalCard key={article.article_id} article={article} newest={index === 0} index={index} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

export default function SignalFeed({ query }: { query: QueryLike<FeedPayload> }) {
  return (
    <Panel
      title="Latest canonical articles"
      tight
      right={
        <>
          <span
            className="itl-epcap"
            title="static provenance — every figure below is loaded from this endpoint"
          >
            /api/news/latest
          </span>
          {query.data?.articles && query.data.articles.length > 0 ? (
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
              title="exports exactly the rows returned by /api/news/latest"
            >
              ⇩ CSV
            </button>
          ) : null}
        </>
      }
    >
      <SectionState
        query={query}
        skeletonRows={4}
        emptyWhen={(d) => !(d.available && (d.articles?.length ?? 0) > 0)}
        emptyMessage="No articles available."
        emptyHint="The feed endpoint answered with an empty list — the parser or the source may be warming up."
        errorFallback="News feed endpoint failed."
      >
        {(d) => <FeedBody articles={d.articles ?? []} />}
      </SectionState>
    </Panel>
  );
}
