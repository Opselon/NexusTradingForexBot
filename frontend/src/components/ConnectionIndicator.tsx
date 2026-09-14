/**
 * ConnectionIndicator + stale-aware status strip.
 *
 * Realtime truth source: useRealtimeStatus() (SSE client) — this component
 * never infers connectivity from anything else. The four legacy display words
 * keep their verbatim semantics (Web/ux_conn.js NXConn):
 *   CONNECTING — feed open but no frame accepted yet (never claim OK early)
 *   OK         — connected AND a fresh frame (legacy state UP)
 *   STALE      — reconnecting / connected but past the age budget (DEGRADED)
 *   ERROR      — disconnected / failed (DOWN)
 * When not OK, the last accepted snapshot's wall-clock age is shown so stale
 * data is never presented as current.
 */

import type { RealtimeStatus } from "@/types/realtime";
import { formatAgeMs } from "@/lib/format";
import "./shell.css";

export type ConnWord = "CONNECTING" | "OK" | "STALE" | "ERROR";

/** Pure mapping (unit-testable): RealtimeStatus -> legacy display word. */
export function connWord(status: RealtimeStatus, nowMs: number, maxAgeMs = 15_000): ConnWord {
  if (status.state === "disconnected" || status.state === "failed") return "ERROR";
  if (status.state === "reconnecting") return "STALE";
  if (status.lastMessageAt === null) return "CONNECTING";
  if (nowMs - status.lastMessageAt > maxAgeMs) return "STALE";
  return "OK";
}

const WORD_CLASS: Record<ConnWord, string> = {
  CONNECTING: "warn",
  OK: "ok",
  STALE: "warn",
  ERROR: "err",
};

/** Dot class on the existing .conn-dot palette (connected/reconnecting/...). */
function dotClass(status: RealtimeStatus, word: ConnWord): string {
  if (word === "OK") return "connected";
  if (word === "ERROR") return status.state === "failed" ? "failed" : "disconnected";
  return "reconnecting";
}

export function ConnectionIndicator({
  status,
  nowMs,
  onRetry,
}: {
  status: RealtimeStatus;
  nowMs: number;
  onRetry?: () => void;
}) {
  const word = connWord(status, nowMs);
  const ageMs = status.lastMessageAt !== null ? Math.max(0, nowMs - status.lastMessageAt) : null;
  return (
    <span
      className={`conn-chip ${WORD_CLASS[word]}`}
      title={`SSE state=${status.state} · state_version=${status.lastVersion ?? "—"} · reconnects=${status.reconnectAttempts}`}
      role="status"
      aria-live="polite"
    >
      <span className={`conn-dot ${dotClass(status, word)}`} aria-hidden="true" />
      <span>{word === "OK" ? "LIVE FEED" : word}</span>
      {ageMs !== null && <span className="faint">· data age {formatAgeMs(ageMs)}</span>}
      {word !== "OK" && onRetry && (
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
