/**
 * News model — DTO -> view-model mapping + invariant checks (pure, unit-testable).
 *
 * EDD rules this file enforces:
 *  - `available:false` is a REAL state (subsystem off / engine offline): it maps
 *    to NewsUnavailable, never to an empty list that looks like "no news".
 *  - An article with no analysis is PENDING — the direction word is never
 *    defaulted to NEUTRAL (that would fabricate a backend verdict).
 *  - Ratios are clamped only for rendering width; a missing ratio stays null.
 */

import type {
  BatchAnalyzeResult,
  NewsFeedArticle,
  NewsFeedResponse,
  NewsFilter,
  NewsRefreshResult,
  NewsSelfHealResult,
  NewsSourceRow,
  NewsStateResponse,
  NewsTimelineBucket,
} from "./types";

/** Thrown when a legacy news route honestly reports the subsystem as off. */
export class NewsUnavailableError extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(
      reason === "ARTICLE_NOT_FOUND"
        ? "Article not found in the news database."
        : "News subsystem unavailable — the news engine is disabled or the backend reported available=false.",
    );
    this.name = "NewsUnavailableError";
    this.reason = reason;
  }
}

/** Feed guard: available=false is an unavailable state, not an empty feed. */
export function requireFeed(res: NewsFeedResponse): { articles: NewsFeedArticle[]; statusCounts: Record<string, number> } {
  if (!res.available) throw new NewsUnavailableError("FEED_UNAVAILABLE");
  return { articles: res.articles ?? [], statusCounts: res.status_counts ?? {} };
}

export function requireState(res: NewsStateResponse): NewsStateResponse {
  if (!res.available) throw new NewsUnavailableError(res.reason ?? "STATE_UNAVAILABLE");
  return res;
}

export function requireTimeline(res: { available: boolean }): void {
  if (!res.available) throw new NewsUnavailableError("TIMELINE_UNAVAILABLE");
}

export function requireKeywords(res: { available: boolean }): void {
  if (!res.available) throw new NewsUnavailableError("KEYWORDS_UNAVAILABLE");
}

export function requireDetail(res: { available: boolean; error?: string }): void {
  if (!res.available) throw new NewsUnavailableError(res.error ?? "DETAIL_UNAVAILABLE");
}

/** PENDING (never analyzed) is distinct from NEUTRAL (analyzed, no lean). */
export type DirectionWord = "BULLISH" | "BEARISH" | "NEUTRAL" | "PENDING" | string;

export function directionOf(a: NewsFeedArticle): DirectionWord {
  const d = a.analysis?.direction;
  if (a.analysis && typeof d === "string" && d.trim() !== "") return d.toUpperCase();
  return "PENDING";
}

/** Impact chip: percentage of the backend importance_score, null when unscored. */
export function impactPct(a: NewsFeedArticle): number | null {
  const s = a.analysis?.importance_score ?? a.importance_score;
  if (s === null || s === undefined || Number.isNaN(Number(s))) return null;
  return Math.round(Number(s) * 100);
}

export function xauusdRelPct(a: NewsFeedArticle): number | null {
  const r = a.analysis?.relevance_to_xauusd;
  if (r === null || r === undefined || Number.isNaN(Number(r))) return null;
  return Math.round(Number(r) * 100);
}

/** Filter chips -> API status param mapping (backend owns the semantics). */
export const NEWS_FILTERS: Array<{ id: NewsFilter; label: string }> = [
  { id: "ACTIVE", label: "Active" },
  { id: "ALL", label: "All" },
  { id: "IRRELEVANT", label: "Irrelevant" },
];

export function statusCount(counts: Record<string, number> | undefined, key: string): number | null {
  const v = counts?.[key];
  return typeof v === "number" ? v : null;
}

export interface SourceVM {
  source: NewsSourceRow;
  enabled: boolean;
  healthy: boolean | null;
  failStreak: number | null;
  successRatio: number | null;
  lastSuccessAt: string | null;
  lastFailureAt: string | null;
  backoffUntil: string | null;
  rateLimited: boolean;
  /** One-word health label — derived ONLY from the backend health columns. */
  label: "OK" | "FAILING" | "BACKOFF" | "RATE_LIMITED" | "DISABLED" | "NO_HEALTH";
}

const truthy = (v: unknown): boolean => v === true || v === 1 || v === "1";

/**
 * Source health view model. `successRatio` is a rendering aid over the backend's
 * own counters (consecutive_failures against a bounded 5-failure backoff window)
 * and stays null when there is no health row at all — no invented 100%.
 */
export function toSourceVM(s: NewsSourceRow): SourceVM {
  const h = s.health ?? null;
  const enabled = truthy(s.enabled ?? 1);
  const failStreak = typeof h?.consecutive_failures === "number" ? h.consecutive_failures : null;
  const healthy = h ? (h.healthy === undefined || h.healthy === null ? null : truthy(h.healthy)) : null;
  let label: SourceVM["label"] = "NO_HEALTH";
  if (!enabled) label = "DISABLED";
  else if (h) {
    if (truthy(h.rate_limited)) label = "RATE_LIMITED";
    else if (truthy(h.healthy) && (failStreak ?? 0) === 0) label = "OK";
    else if ((failStreak ?? 0) > 0) label = "FAILING";
    else label = "BACKOFF";
  }
  const backoff = h?.backoff_until && String(h.backoff_until).trim() !== "";
  if (enabled && backoff && label !== "RATE_LIMITED") label = "BACKOFF";
  const successRatio =
    !enabled || failStreak === null ? null : Math.max(0, Math.min(1, 1 - failStreak / 5));
  return {
    source: s,
    enabled,
    healthy,
    failStreak,
    successRatio,
    lastSuccessAt: h?.last_success_at ? String(h.last_success_at) : null,
    lastFailureAt: h?.last_failure_at ? String(h.last_failure_at) : null,
    backoffUntil: h?.backoff_until ? String(h.backoff_until) : null,
    rateLimited: truthy(h?.rate_limited),
    label,
  };
}

/** News state -> badge level; unknown words render UNKNOWN (never guessed). */
export function newsStateTone(state: string | null | undefined): "good" | "warn" | "bad" | "neutral" | "unknown" {
  switch ((state ?? "").toUpperCase()) {
    case "NORMAL":
      return "good";
    case "ELEVATED":
    case "STALE":
    case "CONFLICTED":
      return "warn";
    case "BREAKING":
    case "HIGH_IMPACT":
      return "bad";
    case "OFF":
    case "UNAVAILABLE":
      return "neutral";
    default:
      return "unknown";
  }
}

/** Refresh verdict sentence — built from the backend's own numbers only. */
export function refreshVerdict(res: NewsRefreshResult): { ok: boolean; message: string } {
  if (!res.available) return { ok: false, message: "News engine unavailable (available=false) — nothing was fetched." };
  if (res.cooldown && res.cooldown > 0) {
    return { ok: true, message: `Refresh skipped by the bandwidth guard: retry in ${res.cooldown}s (${res.skipped ?? "cooldown"}).` };
  }
  const ing = res.ingested ?? {};
  return {
    ok: true,
    message: `Fetch complete — sources_polled ${ing.sources_polled ?? 0}, new ${ing.new ?? 0}, duplicate ${ing.duplicate ?? 0}, merged ${ing.merged ?? 0}, analyzed ${res.analyzed_count ?? 0}.`,
  };
}

export function selfHealVerdict(res: NewsSelfHealResult): { ok: boolean; message: string } {
  if (!res.available) return { ok: false, message: "News engine unavailable — nothing rebuilt." };
  const rebuilt = res.rebuilt ?? {};
  const parts = Object.entries(rebuilt).map(([k, v]) => `${k}=${v}`);
  return { ok: res.status === "SUCCESS", message: `Self-heal ${res.status ?? "UNKNOWN"}${parts.length ? ` · ${parts.join(" · ")}` : ""}` };
}

export function batchVerdict(res: BatchAnalyzeResult): { ok: boolean; message: string } {
  if (res.error) return { ok: false, message: `Batch analysis refused: ${res.error}` };
  return {
    ok: true,
    message: `Batch AI analysis — completed ${res.completed ?? 0}, failed ${res.failed ?? 0}, skipped ${res.skipped ?? 0}.`,
  };
}

/** Timeline buckets arrive oldest->newest from the backend; keep the order. */
export function timelineWindow(buckets: NewsTimelineBucket[]): { from: string | null; to: string | null; articles: number } {
  const first = buckets[0]?.bucket_start ?? null;
  const last = buckets[buckets.length - 1]?.bucket_start ?? null;
  const articles = buckets.reduce((acc, b) => acc + (b.article_count ?? 0), 0);
  return { from: first, to: last, articles };
}
