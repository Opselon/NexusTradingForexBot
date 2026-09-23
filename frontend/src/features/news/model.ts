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
import type {
  NewsAutoPruneResponse,
  NewsProAnalyzeAllResponse,
  NewsProAnswersResponse,
  NewsProConsoleResponse,
  NewsProPurgeResponse,
  NewsProStatusResponse,
  ProConsoleEntry,
} from "./proTypes";

/** The t() signature from stores/i18nStore — verdict closures translate at render. */
export type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** A backend verdict: `ok` decides the tone, `message(t)` renders in the active language. */
export interface Verdict {
  ok: boolean;
  /** Resolved during RENDER (never at event time) so language switches re-translate it. */
  message: (t: Translate) => string;
}

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

/** Chip display label — key stays the raw NewsFilter, the label translates at render. */
export function newsFilterLabel(t: Translate, id: NewsFilter): string {
  switch (id) {
    case "ACTIVE":
      return t("news.filter.active", "Active");
    case "ALL":
      return t("news.filter.all", "All");
    case "IRRELEVANT":
      return t("news.filter.irrelevant", "Irrelevant");
    default:
      return id;
  }
}

/**
 * Direction/severity word shown to a human (badge/chip/kv text). Known words
 * translate; any other backend value passes through verbatim (never a guess).
 */
export function dirWord(t: Translate, v: string): string {
  switch (String(v).toUpperCase()) {
    case "BULLISH":
      return t("news.dir.bullish", "BULLISH");
    case "BEARISH":
      return t("news.dir.bearish", "BEARISH");
    case "NEUTRAL":
      return t("news.dir.neutral", "NEUTRAL");
    case "PENDING":
      return t("news.dir.pending", "PENDING");
    default:
      return v;
  }
}

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
export function refreshVerdict(res: NewsRefreshResult): Verdict {
  if (!res.available) {
    return {
      ok: false,
      message: (t) => t("news.refresh.unavailable", "News engine unavailable (available=false) — nothing was fetched."),
    };
  }
  if (res.cooldown && res.cooldown > 0) {
    return {
      ok: true,
      message: (t) =>
        t("news.refresh.cooldown", "Refresh skipped by the bandwidth guard: retry in {s}s ({reason}).", {
          s: res.cooldown ?? 0,
          reason: res.skipped ?? "cooldown",
        }),
    };
  }
  const ing = res.ingested ?? {};
  return {
    ok: true,
    message: (t) =>
      t("news.refresh.done", "Fetch complete — sources_polled {polled}, new {neu}, duplicate {dup}, merged {merged}, analyzed {analyzed}.", {
        polled: ing.sources_polled ?? 0,
        neu: ing.new ?? 0,
        dup: ing.duplicate ?? 0,
        merged: ing.merged ?? 0,
        analyzed: res.analyzed_count ?? 0,
      }),
  };
}

export function selfHealVerdict(res: NewsSelfHealResult): Verdict {
  if (!res.available) {
    return { ok: false, message: (t) => t("news.selfheal.unavailable", "News engine unavailable — nothing rebuilt.") };
  }
  const rebuilt = res.rebuilt ?? {};
  const parts = Object.entries(rebuilt).map(([k, v]) => `${k}=${v}`);
  const suffix = parts.length ? ` · ${parts.join(" · ")}` : "";
  return {
    ok: res.status === "SUCCESS",
    message: (t) =>
      t("news.selfheal.done", "Self-heal {status}{parts}", {
        status: res.status ?? "UNKNOWN",
        parts: suffix,
      }),
  };
}

/** Flatten either error shape (safe-envelope object or legacy string) to text. */
export function errorText(e: { code?: string; message?: string; request_id?: string } | string | undefined): string {
  if (!e) return "unknown";
  if (typeof e === "string") return e;
  return [e.code, e.message].filter(Boolean).join(" — ") || "unknown";
}

export function batchVerdict(res: BatchAnalyzeResult): Verdict {
  if (res.error) {
    return {
      ok: false,
      message: (t) => t("news.batch.refused", "Batch analysis refused: {e}", { e: errorText(res.error) }),
    };
  }
  if (res.available === false) {
    return {
      ok: false,
      message: (t) => t("news.batch.unavailable", "Batch analysis refused: news subsystem unavailable (available=false)."),
    };
  }
  return {
    ok: true,
    message: (t) =>
      t("news.batch.done", "Batch AI analysis — completed {done}, failed {fail}, skipped {skip}.", {
        done: res.completed ?? 0,
        fail: res.failed ?? 0,
        skip: res.skipped ?? 0,
      }),
  };
}

/**
 * Timeline buckets arrive oldest->newest from the backend; keep the order.
 */
export function timelineWindow(buckets: NewsTimelineBucket[]): { from: string | null; to: string | null; articles: number } {
  const first = buckets[0]?.bucket_start ?? null;
  const last = buckets[buckets.length - 1]?.bucket_start ?? null;
  const articles = buckets.reduce((acc, b) => acc + (b.article_count ?? 0), 0);
  return { from: first, to: last, articles };
}

/**
 * news_ai_analysis.key_facts / .uncertainties arrive as JSON array STRINGS
 * from the raw SQLite rows (db_analysis.insert_ai_analysis json.dumps; verified
 * in db_analysis.py L359-377) but as real arrays inside analyze responses.
 * Decode JSON only — a non-array or unparsable value yields [] (never a guess).
 */
export function decodeStringList(v: string[] | string | null | undefined): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x));
  if (typeof v === "string" && v.trim() !== "") {
    try {
      const parsed: unknown = JSON.parse(v);
      return Array.isArray(parsed) ? parsed.map((x) => String(x)) : [];
    } catch {
      return [];
    }
  }
  return [];
}

/* ──────────────────────────────────────────────────────────────────────────
 * PRO CONSOLE view models (round 2) — legacy Web/news_intelligence.js parity.
 * The safe error envelope arrives at HTTP 200 (web/errors.py
 * safe_error_payload), so the transport does NOT throw for refusals: every
 * guard below converts available:false into a real error the UI renders
 * honestly (NEWS_UNAVAILABLE / COOLDOWN / INTERNAL_ERROR codes included).
 * ────────────────────────────────────────────────────────────────────────── */

/** One safe-envelope refusal: code + message + request_id, backend-text only. */
export class NewsProRefusedError extends Error {
  readonly code: string;
  readonly requestId: string | null;
  constructor(code: string, message: string | undefined, requestId: string | null) {
    super(message ? `${code} — ${message}` : code);
    this.name = "NewsProRefusedError";
    this.code = code;
    this.requestId = requestId;
  }
}

type ProEnvelope = {
  available?: boolean;
  success?: boolean;
  error?: { code?: string; message?: string; request_id?: string } | string;
};

/** Pull a human-readable code/message/request_id out of either error shape. */
function describeRefusal(res: ProEnvelope): NewsProRefusedError {
  const e = res.error;
  if (e && typeof e === "object") {
    return new NewsProRefusedError(e.code ?? "UNAVAILABLE", e.message, e.request_id ?? null);
  }
  return new NewsProRefusedError(typeof e === "string" ? e : "UNAVAILABLE", undefined, null);
}

function requirePro<T extends ProEnvelope>(res: T): T {
  if (res.available === false || res.success === false) throw describeRefusal(res);
  return res;
}

/** GET /api/news/pro/status — throws on the safe-envelope refusal shape. */
export function requireProStatus(res: NewsProStatusResponse): NewsProStatusResponse {
  return requirePro(res);
}

/** GET /api/news/pro/console — keeps backend ring order (ascending seq). */
export function requireProConsole(res: NewsProConsoleResponse): ProConsoleEntry[] {
  return requirePro(res).entries ?? [];
}

/** GET /api/news/pro/latest-answers — verbatim rows, no normalization. */
export function requireProAnswers(res: NewsProAnswersResponse): NonNullable<NewsProAnswersResponse["answers"]> {
  return requirePro(res).answers ?? [];
}

/** Purge / analyze-all result text — built ONLY from the backend's numbers. */
export function purgeVerdict(res: NewsProPurgeResponse): Verdict {
  if (!res || res.available === false) {
    const r = describeRefusal(res || {});
    return { ok: false, message: (t) => t("news.purge.refused", "Purge refused: {e}", { e: r.message }) };
  }
  if (res.hard_delete) {
    return {
      ok: true,
      message: (t) =>
        t("news.purge.hard", "Hard purge: deleted {deleted} of {candidates} candidates ({total} IRRELEVANT total).", {
          deleted: res.deleted ?? 0,
          candidates: res.candidates ?? 0,
          total: res.total_irrelevant ?? 0,
        }),
    };
  }
  return {
    ok: true,
    message: (t) =>
      t("news.purge.soft", "IRRELEVANT: {total} total, {candidates} candidates in this limit window (soft count — nothing deleted).", {
        total: res.total_irrelevant ?? 0,
        candidates: res.candidates ?? 0,
      }),
  };
}

export function analyzeAllVerdict(res: NewsProAnalyzeAllResponse): Verdict {
  if (!res || res.available === false) {
    const r = describeRefusal(res || {});
    return { ok: false, message: (t) => t("news.analyze_all.refused", "Analyze ALL refused: {e}", { e: r.message }) };
  }
  const s = res.summary ?? {};
  return {
    ok: true,
    message: (t) =>
      t(
        "news.analyze_all.done",
        "PRO drain — pending {pending}, analyzed {analyzed}, skipped {skipped}, failed {failed} (llm {llm} / local {local}), junk marked {marked}, console seq {seq}.",
        {
          pending: s.total_pending ?? 0,
          analyzed: s.analyzed ?? 0,
          skipped: s.skipped ?? 0,
          failed: s.failed ?? 0,
          llm: s.via_llm ?? 0,
          local: s.via_local ?? 0,
          marked: s.junk?.marked_irrelevant ?? 0,
          seq: s.console_seq ?? "—",
        },
      ),
  };
}

export function autoPruneSafeVerdict(res: NewsAutoPruneResponse): Verdict {
  if (!res || res.available === false) {
    const r = describeRefusal(res || {});
    return { ok: false, message: (t) => t("news.auto_prune.refused", "Auto-prune refused: {e}", { e: r.message }) };
  }
  return {
    ok: true,
    message: (t) =>
      t(
        "news.auto_prune.done",
        "Pruning complete — {marked} marked irrelevant, {preserved} preserved ({already}, {failed} failed, rule {rule}).",
        {
          marked: res.marked_irrelevant ?? 0,
          preserved: res.preserved ?? 0,
          already: res.already_irrelevant ?? 0,
          failed: res.failed ?? 0,
          rule: res.rule_version ?? "—",
        },
      ),
  };
}

export function proKindLabel(t: Translate, kind: string | undefined): string {
  switch (kind) {
    case "cycle_start":
      return t("news.pro.kind.cycle", "CYCLE");
    case "cycle_done":
      return t("news.pro.kind.done", "DONE");
    case "analysis_ok":
      return t("news.pro.kind.analysis", "ANALYSIS");
    case "analysis_failed":
      return t("news.pro.kind.fail", "FAIL");
    case "ai_ok":
      return t("news.pro.kind.llm_answer", "LLM ANSWER");
    case "ai_failed":
      return t("news.pro.kind.llm_fail", "LLM FAIL");
    case "ai_retry_ok":
      return t("news.pro.kind.llm_retry", "LLM RETRY OK");
    case "ai_persist_failed":
      return t("news.pro.kind.llm_persist", "LLM PERSIST FAIL");
    case "fallback":
      return t("news.pro.kind.fallback", "FALLBACK");
    case "skip":
      return t("news.pro.kind.skip", "SKIP");
    case "error":
      return t("news.pro.kind.error", "ERROR");
    case "deterministic_skip":
      return t("news.pro.kind.det_skip", "DET SKIP");
    case "budget_exhausted":
      return t("news.pro.kind.budget", "BUDGET");
    case "junk_prune":
      return t("news.pro.kind.junk", "JUNK");
    case "junk_failed":
      return t("news.pro.kind.junk_fail", "JUNK FAIL");
    case "llm_purge":
      return t("news.pro.kind.llm_purge", "LLM PURGE");
    case "llm_purge_failed":
      return t("news.pro.kind.llm_purge_fail", "LLM PURGE FAIL");
    case "llm_mark_irrelevant":
      return t("news.pro.kind.llm_mark", "LLM MARK");
    case "purge":
      return t("news.pro.kind.purge", "PURGE");
    default:
      return String(kind ?? "log").toUpperCase();
  }
}

/** Row tone class from the kind ONLY (legacy color map translated to tokens). */
export function proKindTone(kind: string | undefined): "ok" | "info" | "bad" | "warn" | "muted" {
  switch (kind) {
    case "ai_ok":
    case "ai_retry_ok":
    case "analysis_ok":
      return "ok";
    case "cycle_start":
    case "cycle_done":
      return "info";
    case "error":
    case "analysis_failed":
    case "ai_failed":
    case "ai_persist_failed":
    case "junk_failed":
    case "llm_purge_failed":
    case "budget_exhausted":
      return "bad";
    case "junk_prune":
    case "purge":
    case "llm_mark_irrelevant":
    case "deterministic_skip":
      return "warn";
    default:
      return "muted";
  }
}

/** Highest seq in a batch (poll cursor) — never synthesised when absent. */
export function maxConsoleSeq(entries: ProConsoleEntry[]): number | null {
  let best: number | null = null;
  for (const e of entries) {
    const s = Number(e.seq ?? NaN);
    if (Number.isFinite(s) && (best === null || s > best)) best = s;
  }
  return best;
}
