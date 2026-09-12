import { useEffect, type ReactNode } from "react";
import { positionSide } from "@/lib/format";
import { useUiStore, type ToastItem } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";

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
  const t = useI18n((s) => s.t);
  const level = badgeLevel(status);
  // Backend status words render verbatim; only the client-side "no status"
  // placeholder is UI copy and translates.
  const text = status ? status.replace(/_/g, " ") : t("alt.common.badge_unknown", "UNKNOWN");
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
  accent,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  tight?: boolean;
  /** renders a small glowing dot before the title — visual grouping only */
  accent?: boolean;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        {accent && <span className="dot-accent" aria-hidden="true" />}
        <span>{title}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>{right}</span>
      </div>
      <div className={`panel-body ${tight ? "tight" : ""}`}>{children}</div>
    </section>
  );
}

/** Shared chrome labels translate here (alt.common.*) so every page gets them
 *  for free; caller-supplied `message`/`label` strings stay caller-owned. */
export function LoadingState({ label }: { label?: string }) {
  const t = useI18n((s) => s.t);
  const text = label ?? t("alt.common.loading_state", "Loading backend state…");
  return (
    <div className="state-block">
      <div className="spinner" />
      <div>{text}</div>
    </div>
  );
}

export function ErrorState({ message, requestId, onRetry }: { message: string; requestId?: string | null; onRetry?: () => void }) {
  const t = useI18n((s) => s.t);
  return (
    <div className="state-block error">
      <div className="glyph">⚠</div>
      <div>{message}</div>
      {requestId && <div className="hint inline-mono">request_id: {requestId}</div>}
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          {t("alt.common.retry", "Retry")}
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

/** Skeleton rows for loading states inside tables/panels — steadier than spinners. */
export function Skeleton({ count = 3, height = 14 }: { count?: number; height?: number }) {
  return (
    <div className="skeleton-line" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton" style={{ height, width: `${88 - (i % 3) * 14}%` }} />
      ))}
    </div>
  );
}

export function DataTable({ headers, children }: { headers: Array<{ label: string; num?: boolean }>; children: ReactNode }) {
  return (
    <div className="table-wrap">
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

/** Horizontal 0..1 probability meter rows (display of backend values only). */
export function ProbBar({ rows }: { rows: Array<{ label: string; value: number | null; tone: "buy" | "sell" | "flat" }> }) {
  return (
    <div className="probbar">
      {rows.map((r) => (
        <div className="row" key={r.label}>
          <span className="lab">{r.label}</span>
          <span className="track" role="img" aria-label={`${r.label} ${r.value ?? "unknown"}`}>
            <i className={r.tone} style={{ width: r.value === null ? 0 : `${Math.max(0, Math.min(1, r.value)) * 100}%` }} />
          </span>
          <span className="val">{r.value === null ? "—" : `${(r.value * 100).toFixed(1)}%`}</span>
        </div>
      ))}
    </div>
  );
}

/** Segmented control (tabs/filter) — visual selection only. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<{ id: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((o) => (
        <button
          key={o.id}
          role="tab"
          aria-selected={value === o.id}
          className={value === o.id ? "active" : ""}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Destructive-command confirmation modal. The ACTION semantics are unchanged:
 *  onConfirm still runs the backend call and the backend response decides the
 *  outcome — the modal only prevents mis-clicks (Esc = cancel). */
export function ConfirmModal({
  title,
  danger = true,
  confirmLabel,
  busy,
  children,
  onConfirm,
  onCancel,
}: {
  title: string;
  danger?: boolean;
  confirmLabel: string;
  busy?: boolean;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const t = useI18n((s) => s.t);

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">{title}</div>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            {t("alt.common.cancel", "Cancel")} <kbd>esc</kbd>
          </button>
          <button className={`btn ${danger ? "danger" : "primary"}`} disabled={busy} onClick={onConfirm}>
            {busy ? t("alt.common.sending", "sending…") : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Corner toast stack — visual mirror of command results (backend-decided). */
export function ToastHost() {
  const toasts = useUiStore((s) => s.toasts);
  const dismiss = useUiStore((s) => s.dismissToast);
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((t) => window.setTimeout(() => dismiss(t.id), 6000));
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [toasts, dismiss]);
  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack">
      {toasts.map((t: ToastItem) => (
        <div key={t.id} className={`toast ${t.kind}`} onClick={() => dismiss(t.id)} role="status">
          <span className="t-glyph">{t.kind === "ok" ? "✓" : t.kind === "fail" ? "✕" : "ℹ"}</span>
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}
