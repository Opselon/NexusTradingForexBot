/**
 * ConnectionIndicator + stale-aware status strip.
 *
 * Realtime states: connected / reconnecting / disconnected / failed.
 * When not connected, the last accepted snapshot's wall-clock age is shown so
 * stale data is never presented as current.
 */

import type { RealtimeStatus } from "@/types/realtime";
import { formatAgeMs } from "@/lib/format";

const LABELS: Record<RealtimeStatus["state"], string> = {
  connected: "LIVE FEED",
  reconnecting: "RECONNECTING…",
  disconnected: "DISCONNECTED",
  failed: "FEED FAILED",
};

export function ConnectionIndicator({
  status,
  nowMs,
  onRetry,
}: {
  status: RealtimeStatus;
  nowMs: number;
  onRetry?: () => void;
}) {
  const ageMs = status.lastMessageAt !== null ? Math.max(0, nowMs - status.lastMessageAt) : null;
  const stale = status.state !== "connected";
  return (
    <span className="conn-chip" title={`state_version=${status.lastVersion ?? "—"} · reconnects=${status.reconnectAttempts}`}>
      <span className={`conn-dot ${status.state}`} />
      <span>{LABELS[status.state]}</span>
      {ageMs !== null && <span className="faint">· data age {formatAgeMs(ageMs)}</span>}
      {stale && onRetry && (
        <button className="btn small" style={{ marginLeft: 6 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </span>
  );
}

/** Freshness meter — visualizes the age of one backend freshness stage against
 *  a 15s display budget. State words are the BACKEND's (FRESH/STALE/UNKNOWN);
 *  the bar length is derived from backend-reported age_ms only. */
export function FreshnessMeter({
  label,
  state,
  ageMs,
}: {
  label: string;
  state: string | null | undefined;
  ageMs: number | null | undefined;
}) {
  const s = (state ?? "UNKNOWN").toUpperCase();
  const level = s === "FRESH" ? "" : s === "STALE" ? "bad" : "warn";
  const pct =
    ageMs === null || ageMs === undefined || !Number.isFinite(ageMs)
      ? 0
      : Math.max(4, Math.min(100, 100 - (Math.max(0, ageMs) / 15_000) * 100));
  return (
    <span className={`freshness ${level}`} title={`${label}: ${s}${ageMs !== null && ageMs !== undefined ? ` · age ${formatAgeMs(ageMs)}` : ""}`}>
      <span className="lab">
        {label} {s === "UNKNOWN" ? "—" : s}
      </span>
      <span className="bar" aria-hidden="true">
        <i style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}

/** True when the connection is not currently delivering fresh frames. */
export function isFeedStale(status: RealtimeStatus, nowMs: number, maxAgeMs = 15_000): boolean {
  if (status.state !== "connected") return true;
  if (status.lastMessageAt === null) return true;
  return nowMs - status.lastMessageAt > maxAgeMs;
}
