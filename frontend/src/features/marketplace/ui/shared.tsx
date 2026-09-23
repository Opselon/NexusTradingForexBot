/**
 * Marketplace presentation helpers (feature-local; no cross-feature imports).
 * Same freshness contract as the other lane tabs: every section captions its
 * own data age, and an error shows the backend message + a retry.
 * i18n: t comes from the render-scope caller (or useI18n here), so captions
 * follow the active language — never a value captured at module scope.
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";
import { useI18n } from "@/stores/i18nStore";

type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/**
 * Backend error -> display text. ApiError code/message/request_id/details are
 * backend-provided (kept verbatim); only the non-Error fallback word is UI copy,
 * so callers pass their render-scope `t`.
 */
export function asErrorText(e: unknown, t?: Translate): string {
  if (e instanceof ApiError) {
    const detail = (e as ApiError & { details?: unknown }).details;
    return `${e.code}: ${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}${detail ? ` ${JSON.stringify(detail)}` : ""}`;
  }
  if (e instanceof Error) return e.message;
  return t ? t("marketplace.error.unknown", "unknown error") : "unknown error";
}

export function FreshnessNote({ updatedAtMs, label, staleAfterMs }: { updatedAtMs: number | null; label: string; staleAfterMs?: number }) {
  const t = useI18n((s) => s.t);
  if (!updatedAtMs) return <span className="timestamp-note">{t("marketplace.fresh.never", "{l}: never loaded", { l: label })}</span>;
  const age = Date.now() - updatedAtMs;
  const bad = staleAfterMs !== undefined && age > staleAfterMs;
  return (
    <span
      className="timestamp-note"
      style={bad ? { color: "var(--amber)" } : undefined}
      title={t("marketplace.fresh.cache_age", "cache age {s}s", { s: Math.round(age / 1000) })}
    >
      {t("marketplace.fresh.ago", "{l} · {a} ago", { l: label, a: formatAgeMs(age) })}
      {bad ? " ⚠" : ""}
    </span>
  );
}

/** Raw-JSON inspection text — byte-identical to `JSON.stringify(value)`. */
export function jsonInline(value: unknown): string {
  return JSON.stringify(value);
}

/** Pretty inspection text — byte-identical to `JSON.stringify(value, null, 2)`. */
export function jsonPretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
