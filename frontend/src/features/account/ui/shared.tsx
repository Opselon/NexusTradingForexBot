/**
 * Account presentation helpers (feature-local; no cross-feature imports).
 */

import { formatAgeMs } from "@/lib/format";
import { ApiError } from "@/types/api";

export function asErrorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}`;
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
