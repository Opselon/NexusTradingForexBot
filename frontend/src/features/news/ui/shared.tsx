/**
 * Shared presentation helpers for the lane's three premium tabs.
 *
 * Freshness is always shown: every section captions how old its backend data
 * is, and a section that never resolved says so instead of looking empty-and-calm.
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";

// perf: memoize the two inspection texts per VALUE identity — callers pass
// backend payload slices whose object identity is stable across renders
// (TanStack structural sharing), so a repeated render reuses the string
// instead of re-serializing it. The returned text is byte-identical to
// JSON.stringify(value) / JSON.stringify(value, null, 2) — the helpers'
// documented contract is unchanged, only HOW OFTEN they serialize.
const inlineCache = new WeakMap<object, string>();
const prettyCache = new WeakMap<object, string>();

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
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  const hit = inlineCache.get(value);
  if (hit !== undefined) return hit;
  const text = JSON.stringify(value);
  inlineCache.set(value, text);
  return text;
}

/** Pretty inspection text — byte-identical to `JSON.stringify(value, null, 2)`. */
export function jsonPretty(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value, null, 2);
  const hit = prettyCache.get(value);
  if (hit !== undefined) return hit;
  const text = JSON.stringify(value, null, 2);
  prettyCache.set(value, text);
  return text;
}
