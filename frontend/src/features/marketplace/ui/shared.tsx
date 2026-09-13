/**
 * Marketplace presentation helpers (feature-local; no cross-feature imports).
 * Same freshness contract as the other lane tabs: every section captions its
 * own data age, and an error shows the backend message + a retry.
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";

export function asErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e as ApiError & { details?: unknown }).details;
    return `${e.code}: ${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}${detail ? ` ${JSON.stringify(detail)}` : ""}`;
  }
  if (e instanceof Error) return e.message;
  return "unknown error";
}

export function FreshnessNote({ updatedAtMs, label, staleAfterMs }: { updatedAtMs: number | null; label: string; staleAfterMs?: number }) {
  if (!updatedAtMs) return <span className="timestamp-note">{label}: never loaded</span>;
  const age = Date.now() - updatedAtMs;
  const bad = staleAfterMs !== undefined && age > staleAfterMs;
  return (
    <span className="timestamp-note" style={bad ? { color: "var(--amber)" } : undefined} title={`cache age ${Math.round(age / 1000)}s`}>
      {label} · {formatAgeMs(age)} ago{bad ? " ⚠" : ""}
    </span>
  );
}
