import type { ReactNode } from "react";
import { positionSide } from "@/lib/format";

/** Health/status -> semantic badge level. Backend status strings are trusted;
 *  anything unrecognized renders UNKNOWN (never guessed). */
function badgeLevel(status: string | null | undefined): "good" | "warn" | "bad" | "neutral" | "unknown" {
  if (!status) return "unknown";
  const s = status.toUpperCase();
  if (["READY", "IDLE", "PASS", "CONNECTED", "ACTIVE", "OK", "RUNNING", "NORMAL", "DISABLED"].includes(s)) return "good";
  if (["WARMING_UP", "STALE", "DEGRADED", "CONNECTING", "ELEVATED", "WARNING", "WAITING_TICK", "PENDING"].includes(s)) return "warn";
  if (["ERROR", "DISCONNECTED", "UNAVAILABLE", "FAILED", "BLOCKED", "INVALID", "HALTED", "BREAKING", "HIGH_IMPACT", "CONFLICTED"].includes(s)) return "bad";
  if (["STOPPED", "UNKNOWN"].includes(s)) return "neutral";
  return "unknown";
}

export function StatusBadge({ status, label }: { status: string | null | undefined; label?: string }) {
  const level = badgeLevel(status);
  const text = status ? status.replace(/_/g, " ") : "UNKNOWN";
  return (
    <span className={`badge ${level}`} title={label ?? text}>
      {text}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const s = (severity ?? "unknown").toUpperCase();
  const level = s === "CRITICAL" || s === "HIGH" ? "bad" : s === "MEDIUM" ? "warn" : s === "LOW" ? "neutral" : "unknown";
  return <span className={`badge ${level}`}>{s}</span>;
}

export function PositionSideBadge({ type }: { type: number | string | null | undefined }) {
  const side = positionSide(type);
  return <span className={`badge ${side === "BUY" ? "good" : side === "SELL" ? "bad" : "unknown"}`}>{side}</span>;
}

export function MetricCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "pos" | "neg" | "dim";
}) {
  return (
    <div className="metric">
      <div className="k">{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}</div>
      {sub !== undefined && <div className="s">{sub}</div>}
    </div>
  );
}

export function Panel({
  title,
  right,
  children,
  tight,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  tight?: boolean;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <span>{title}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>{right}</span>
      </div>
      <div className={`panel-body ${tight ? "tight" : ""}`}>{children}</div>
    </section>
  );
}

export function LoadingState({ label = "Loading backend state…" }: { label?: string }) {
  return (
    <div className="state-block">
      <div className="spinner" />
      <div>{label}</div>
    </div>
  );
}

export function ErrorState({ message, requestId, onRetry }: { message: string; requestId?: string | null; onRetry?: () => void }) {
  return (
    <div className="state-block error">
      <div className="glyph">⚠</div>
      <div>{message}</div>
      {requestId && <div className="hint inline-mono">request_id: {requestId}</div>}
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="state-block">
      <div className="glyph">∅</div>
      <div>{message}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function DataTable({ headers, children }: { headers: Array<{ label: string; num?: boolean }>; children: ReactNode }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h.label} className={h.num ? "num" : undefined}>
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
