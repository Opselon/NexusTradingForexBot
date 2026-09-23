/**
 * Section-state kit shared by the seven operator pages (lane 4).
 *
 * Why it lives under pages/_shared and not components/: the shared
 * `components/` kit is another lane's build surface this wave, and the
 * quality bar demands EVERY section of EVERY page answer the same three
 * questions the same way — loading (skeleton, not spinner-only), failure
 * (backend error text + request id + Retry), empty (an honest statement that
 * nothing was returned, never a zero or a dash pretending to be data).
 *
 * Presentation only: no fetch, no derived NSE verdicts. The `query` prop is
 * structurally typed against a TanStack Query result so pages can hand in
 * any query without the kit importing the cache.
 */

import type { ReactNode } from "react";
import { ApiError } from "@/types/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";

/** Minimal structural view of a TanStack Query result (read + refetch). */
export interface QueryLike<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
  /** Optional: pages with a live feed pass a freshness note here. */
  dataUpdatedAt?: number;
}

/** One-line error text for any thrown value (never a silent fallback copy). */
export function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** request_id when the backend supplied one — audit trail, not decoration. */
export function errorRequestId(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null;
}

/**
 * SectionState — the per-section state machine in one place.
 *
 * Order matters: pending → error → empty → content. A page that has stale
 * data plus a fresh failed refetch keeps showing the data (TanStack keeps
 * `data` on error), which is the honest behaviour: the values were real, the
 * latest refresh did not land, and the caller renders its own stale caption.
 */
export function SectionState<T>({
  query,
  skeletonRows = 3,
  emptyWhen,
  emptyMessage,
  emptyHint,
  errorFallback,
  children,
}: {
  query: QueryLike<T>;
  skeletonRows?: number;
  /** Decides emptiness from the payload (arrays, records, {items} …). */
  emptyWhen?: (data: T) => boolean;
  emptyMessage: string;
  emptyHint?: string;
  errorFallback?: string;
  children: (data: T) => ReactNode;
}) {
  const t = useI18n((s) => s.t);
  if (query.isPending && query.data === undefined) {
    return (
      <div style={{ padding: "4px 2px" }}>
        <Skeleton count={skeletonRows} />
      </div>
    );
  }
  if (query.data === undefined) {
    return (
      <ErrorState
        message={errorText(query.error, errorFallback ?? t("shell.section.error_fallback", "Endpoint unavailable."))}
        requestId={errorRequestId(query.error)}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (emptyWhen ? emptyWhen(query.data) : false) {
    return <EmptyState message={emptyMessage} hint={emptyHint} />;
  }
  return <>{children(query.data)}</>;
}

/**
 * AgeNote — freshness caption for a panel header.
 *
 * Renders the backend's OWN age/timestamp value; a missing age renders
 * "age —" rather than 0s (an unproven zero reads as fresh).
 */
export function AgeNote({
  label,
  ageSec,
  suffix,
}: {
  label: string;
  ageSec: number | null | undefined;
  suffix?: string;
}) {
  return (
    <span className="timestamp-note">
      {label} {ageSec === null || ageSec === undefined || !Number.isFinite(ageSec) ? "—" : `${ageSec.toFixed(1)}s`}
      {suffix ? ` · ${suffix}` : ""}
    </span>
  );
}

/** Humanised seconds (shared by the pages that restate backend ages). */
export function fmtAge(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "—";
  if (sec < 90) return `${sec.toFixed(1)}s`;
  if (sec < 7200) return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

/** Boolean restatement with an explicit UNKNOWN third arm (never coerced). */
export function triWord(value: boolean | null | undefined, on: string, off: string): string {
  if (value === true) return on;
  if (value === false) return off;
  return "UNKNOWN";
}

/** Badge tone for a boolean-restated state (unknown reads neutral, not ok). */
export function triLevel(value: boolean | null | undefined): "good" | "bad" | "unknown" {
  if (value === true) return "good";
  if (value === false) return "bad";
  return "unknown";
}

export function TriBadge({ value, on, off }: { value: boolean | null | undefined; on: string; off: string }) {
  const t = useI18n((s) => s.t);
  const level = value === true ? "good" : value === false ? "bad" : "unknown";
  const word = value === true ? on : value === false ? off : t("shell.tri.unknown", "UNKNOWN");
  return <span className={`badge ${level}`}>{word}</span>;
}
