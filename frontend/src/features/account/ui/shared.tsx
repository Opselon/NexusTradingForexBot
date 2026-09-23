/**
 * Account presentation helpers (feature-local; no cross-feature imports).
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";
import { useI18n } from "@/stores/i18nStore";

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

export function asErrorText(e: unknown, t?: Translator): string {
  const tr = t ?? useI18n.getState().t;
  if (e instanceof ApiError) return `${e.message}${e.requestId ? ` ${tr("account.error.request_id", "(request_id: {id})", { id: e.requestId })}` : ""}`;
  if (e instanceof Error) return e.message;
  return tr("account.error.unknown", "unknown error");
}

export function FreshnessNote({ updatedAtMs, label, staleAfterMs }: { updatedAtMs: number | null; label: string; staleAfterMs?: number }) {
  const t = useI18n((s) => s.t);
  if (!updatedAtMs) return <span className="timestamp-note">{t("account.freshness.never", "{label}: never loaded", { label })}</span>;
  const age = Date.now() - updatedAtMs;
  const bad = staleAfterMs !== undefined && age > staleAfterMs;
  return (
    <span className="timestamp-note" style={bad ? { color: "var(--amber)" } : undefined} title={t("account.freshness.cache_age", "cache age {seconds}s", { seconds: String(Math.round(age / 1000)) })}>
      {t("account.freshness.ago", "{label} · {age} ago", { label, age: formatAgeMs(age) })}{bad ? " ⚠" : ""}
    </span>
  );
}

/** Money with explicit no-data handling (never a fabricated $0.00). */
export function moneyOrDash(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (v < 0) return `-$${s}`;
  return signed ? `+$${s}` : `$${s}`;
}

export function numOrDash(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

export function pctOrDash(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}%`;
}

export const DASH = "—";
