/**
 * Shared presentation helpers for the lane's three premium tabs.
 *
 * Freshness is always shown: every section captions how old its backend data
 * is, and a section that never resolved says so instead of looking empty-and-calm.
 */

import { formatAgeMs } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { ApiError } from "@/types/api";
import { NewsUnavailableError } from "../model";

/** The t() signature from stores/i18nStore — lets non-component helpers translate. */
export type TFunc = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/**
 * Flatten any thrown value to display text. `t` (when given) translates the
 * client-owned parts — NewsUnavailableError reasons and the unknown-error
 * fallback. Backend messages, ApiError text and request ids pass through
 * verbatim (never translated).
 */
export function asErrorText(e: unknown, t?: TFunc): string {
  if (e instanceof NewsUnavailableError) return newsUnavailableText(e.reason, t);
  if (e instanceof ApiError) return `${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}`;
  if (e instanceof Error) return e.message;
  if (t) return t("news.err.unknown", "unknown error");
  return "unknown error";
}

/** Guard reason -> translated sentence; unknown/backend reasons use the generic line. */
export function newsUnavailableText(reason: string, t?: TFunc): string {
  if (!t) {
    return reason === "ARTICLE_NOT_FOUND"
      ? "Article not found in the news database."
      : "News subsystem unavailable — the news engine is disabled or the backend reported available=false.";
  }
  switch (reason) {
    case "ARTICLE_NOT_FOUND":
      return t("news.err.article_not_found", "Article not found in the news database.");
    case "SOURCES_UNAVAILABLE":
      return t("news.err.sources_unavailable", "News source registry unavailable (news engine off).");
    case "IMPACT_UNAVAILABLE":
      return t("news.err.impact_unavailable", "News impact records unavailable.");
    case "ANALYSIS_UNAVAILABLE":
      return t("news.err.analysis_unavailable", "Article analysis unavailable.");
    case "TRADELINKS_UNAVAILABLE":
      return t("news.err.tradelinks_unavailable", "News trade links unavailable.");
    default:
      return t(
        "news.err.unavailable",
        "News subsystem unavailable — the news engine is disabled or the backend reported available=false.",
      );
  }
}

/** "state · 12.0s ago" — client-captured cache age, explicitly labeled. */
export function FreshnessNote({ updatedAtMs, label, staleAfterMs }: { updatedAtMs: number | null; label: string; staleAfterMs?: number }) {
  const t = useI18n((s) => s.t);
  if (!updatedAtMs) {
    return <span className="timestamp-note">{t("news.fresh.never", "{label}: never loaded", { label })}</span>;
  }
  const age = Date.now() - updatedAtMs;
  const bad = staleAfterMs !== undefined && age > staleAfterMs;
  return (
    <span
      className="timestamp-note"
      style={bad ? { color: "var(--amber)" } : undefined}
      title={t("news.fresh.cache_age", "cache age {s}s", { s: Math.round(age / 1000) })}
    >
      {t("news.fresh.ago", "{label} · {age} ago", { label, age: formatAgeMs(age) })}
      {bad ? " ⚠" : ""}
    </span>
  );
}

/**
 * Note text: either a plain string built with the `t` in scope at the call
 * site, or a lazy `(t) => string` closure resolved on every render — so an
 * already-visible note re-translates on a language switch.
 */
export type NoteText = string | ((t: TFunc) => string);

/** Resolve a note for display in the ACTIVE language. */
export function noteText(text: NoteText | null | undefined, t: TFunc): string | null {
  return typeof text === "function" ? text(t) : text ?? null;
}

/** Section shell: pending -> skeleton, error -> retry, empty -> message, else children. */
export type QueryLike<T> = {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
  refetch: () => void;
};

export function sectionState(query: QueryLike<unknown>): "loading" | "error" | "ready" {
  if (query.isPending) return "loading";
  if (query.isError) return "error";
  return "ready";
}
