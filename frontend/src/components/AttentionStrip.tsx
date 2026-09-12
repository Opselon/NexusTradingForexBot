/**
 * Attention strip + connectivity truth — port of the main dashboard's
 * NXAttention + NXConn (Web/ux_attention.js / ux_conn.js, CHG-0048).
 *
 * Answers the HOME question: "Do I need to do anything right now?" using
 * data the client ALREADY receives (canonical snapshot + realtime feed
 * state). No new backend calls, no new polls.
 *
 * Priority classes (identical semantics to the legacy layer):
 *   CRITICAL — connection lost / runtime BLOCKED|DISCONNECTED / model unavailable
 *   ACTION   — stale data, degraded subsystems
 *   CALM     — everything fine: explicit "all good", never silence.
 *
 * Truth rule: never invents a problem, never hides a real one — every row
 * comes from the payload.
 */

import type { EngineSnapshot } from "@/types/domain";
import type { RealtimeStatus } from "@/types/realtime";
import { useI18n } from "@/stores/i18nStore";

export interface AttentionRow {
  kind: "critical" | "action";
  text: string;
}

/** Pure derivation (unit-testable): snapshot + feed state -> rows. */
export function computeAttention(
  snapshot: EngineSnapshot | undefined,
  feed: RealtimeStatus,
  nowMs: number,
  t: (key: string, fallback: string, vars?: Record<string, string | number>) => string,
): AttentionRow[] | null {
  if (!snapshot) return null;
  const items: AttentionRow[] = [];

  // Connection: the realtime layer is this app's truth source (SSE client).
  const connDown = feed.state === "disconnected" || feed.state === "failed";
  const ageMs = feed.lastMessageAt !== null ? Math.max(0, nowMs - feed.lastMessageAt) : null;
  if (connDown) {
    const last =
      feed.lastMessageAt !== null
        ? ` · ${t("ux.conn.last", "Last update: {t} ({s}s ago)", {
            t: new Date(feed.lastMessageAt).toLocaleTimeString("en-GB", { hour12: false }),
            s: Math.round((ageMs ?? 0) / 1000),
          })}`
        : "";
    items.push({ kind: "critical", text: `${t("ux.conn.title", "Connection lost — live updates stopped.")}${last}` });
  }

  const runtime = String(snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
  if (runtime.includes("BLOCKED") || runtime.includes("DISCONNECTED")) {
    items.push({ kind: "critical", text: t("ux.attention.runtime_blocked", "Runtime is {m}. Trading is not possible until it recovers.", { m: runtime }) });
  }
  if (snapshot.model && snapshot.model.available === false) {
    items.push({ kind: "critical", text: t("ux.attention.model_unavailable", "Model unavailable — decisions cannot be produced.") });
  }

  if (snapshot.is_stale) {
    items.push({ kind: "action", text: t("ux.attention.stale", "Data is stale — the shown values are the last known.") });
  }
  for (const k of ["engine", "mt5", "database", "news", "workers"] as const) {
    const st = String(snapshot.health.subsystems[k] ?? "").toUpperCase();
    if (st && st !== "READY" && st !== "FRESH" && st !== "DISABLED") {
      items.push({ kind: "action", text: t("ux.attention.subsystem", "{s}: {v}", { s: k.toUpperCase(), v: st }) });
    }
  }
  if (!connDown && feed.state === "reconnecting" && !snapshot.is_stale) {
    items.push({ kind: "action", text: t("ux.conn.stale_title", "Data may be stale — no live updates recently.") });
  }
  return items;
}

export function AttentionStrip({
  snapshot,
  feed,
  nowMs,
}: {
  snapshot: EngineSnapshot | undefined;
  feed: RealtimeStatus;
  nowMs: number;
}) {
  const t = useI18n((s) => s.t);
  const rows = computeAttention(snapshot, feed, nowMs, t);
  if (rows === null) return null;

  if (rows.length === 0) {
    return (
      <div className="banner calm" role="status">
        <span>✓ {t("ux.attention.allgood", "All systems normal. No action needed.")}</span>
        {feed.lastMessageAt !== null && (
          <span className="tiny faint inline-mono" style={{ marginLeft: "auto" }}>
            {t("ux.conn.last", "Last update: {t} ({s}s ago)", {
              t: new Date(feed.lastMessageAt).toLocaleTimeString("en-GB", { hour12: false }),
              s: Math.round((nowMs - feed.lastMessageAt) / 1000),
            })}
          </span>
        )}
      </div>
    );
  }
  const critical = rows.some((r) => r.kind === "critical");
  return (
    <div className={`banner ${critical ? "down" : "stale"}`} role="region" aria-label="Attention summary">
      <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
        {rows.map((r, i) => (
          <span key={i} style={{ whiteSpace: "normal" }}>
            {r.kind === "critical" ? "⛔" : "⚠"} {r.text}
          </span>
        ))}
      </div>
      {critical && (
        <span className="tiny" style={{ marginLeft: "auto", letterSpacing: "0.1em" }}>
          {t("ux.attention.critical", "ATTENTION REQUIRED")}
        </span>
      )}
    </div>
  );
}
