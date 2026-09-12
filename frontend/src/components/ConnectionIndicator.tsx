/**
 * ConnectionIndicator + stale-aware status strip.
 *
 * Realtime states: connected / reconnecting / disconnected / failed.
 * When not connected, the last accepted snapshot's wall-clock age is shown so
 * stale data is never presented as current.
 *
 * i18n: the feed-state chips are UI words (not backend verdicts) and translate
 * via alt.conn.*; the FreshnessMeter's state word (FRESH/STALE/UNKNOWN) is the
 * BACKEND's and stays verbatim — only the "data age" affordance labels wrap.
 */

import type { RealtimeStatus } from "@/types/realtime";
import { formatAgeMs } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";

export function ConnectionIndicator({
  status,
  nowMs,
  onRetry,
}: {
  status: RealtimeStatus;
  nowMs: number;
  onRetry?: () => void;
}) {
  const t = useI18n((s) => s.t);
  const LABELS: Record<RealtimeStatus["state"], string> = {
    connected: t("alt.conn.feed_live", "LIVE FEED"),
    reconnecting: t("alt.conn.feed_reconnecting", "RECONNECTING…"),
    disconnected: t("alt.conn.feed_disconnected", "DISCONNECTED"),
    failed: t("alt.conn.feed_failed", "FEED FAILED"),
  };
  const ageMs = status.lastMessageAt !== null ? Math.max(0, nowMs - status.lastMessageAt) : null;
  const stale = status.state !== "connected";
  return (
    <span className="conn-chip" title={`state_version=${status.lastVersion ?? "—"} · reconnects=${status.reconnectAttempts}`}>
      <span className={`conn-dot ${status.state}`} />
      <span>{LABELS[status.state]}</span>
      {ageMs !== null && <span className="faint">· {t("alt.conn.data_age", "data age {age}", { age: formatAgeMs(ageMs) })}</span>}
      {stale && onRetry && (
        <button className="btn small" style={{ marginLeft: 6 }} onClick={onRetry}>
          {t("alt.common.retry", "Retry")}
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
  const t = useI18n((s) => s.t);
  const s = (state ?? "UNKNOWN").toUpperCase();
  const level = s === "FRESH" ? "" : s === "STALE" ? "bad" : "warn";
  const pct =
    ageMs === null || ageMs === undefined || !Number.isFinite(ageMs)
      ? 0
      : Math.max(4, Math.min(100, 100 - (Math.max(0, ageMs) / 15_000) * 100));
  return (
    <span
      className={`freshness ${level}`}
      title={`${label}: ${s}${ageMs !== null && ageMs !== undefined ? ` · ${t("alt.conn.age_word", "age")} ${formatAgeMs(ageMs)}` : ""}`}
    >
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
