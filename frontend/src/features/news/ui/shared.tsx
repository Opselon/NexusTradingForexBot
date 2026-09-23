/**
 * Shared presentation helpers for the lane's three premium tabs.
 *
 * Freshness is always shown: every section captions how old its backend data
 * is, and a section that never resolved says so instead of looking empty-and-calm.
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";

export function asErrorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}`;
  if (e instanceof Error) return e.message;
  return "unknown error";
}

/** "state · 12.0s ago" — client-captured cache age, explicitly labeled. */
export function FreshnessNote({ updatedAtMs, label, staleAfterMs }: { updatedAtMs: number | null; label: string; staleAfterMs?: number }) {
  if (!updatedAtMs) {
    return <span className="timestamp-note">{label}: never loaded</span>;
  }
  const age = Date.now() - updatedAtMs;
  const bad = staleAfterMs !== undefined && age > staleAfterMs;
  return (
    <span className="timestamp-note" style={bad ? { color: "var(--amber)" } : undefined} title={`cache age ${Math.round(age / 1000)}s`}>
      {label} · {formatAgeMs(age)} ago{bad ? " ⚠" : ""}
    </span>
  );
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

/** Raw-JSON inspection text — byte-identical to `JSON.stringify(value)`. */
export function jsonInline(value: unknown): string {
  return JSON.stringify(value);
}

/** Pretty inspection text — byte-identical to `JSON.stringify(value, null, 2)`. */
export function jsonPretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
