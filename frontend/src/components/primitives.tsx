import { useEffect, type ReactNode } from "react";
import { positionSide } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useUiStore, type ToastItem } from "@/stores/uiStore";

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

type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** Known status CODES whose display word may be localized at the render site
 *  (requested by lanes P2/F1 so badges sit in the page language). The level
 *  mapping above still compares the RAW code, and anything not listed here —
 *  incl. compound codes like HIGH_IMPACT / BREAKING — is a backend enum value
 *  and renders VERBATIM (underscore -> space, as before). */
function statusText(t: Translate, status: string | null | undefined): string {
  if (!status) return t("ui.word.unknown", "UNKNOWN");
  switch (status) {
    case "READY": return t("ui.status.ready", "READY");
    case "IDLE": return t("ui.status.idle", "IDLE");
    case "PASS": return t("ui.word.pass", "PASS");
    case "CONNECTED": return t("ui.status.connected", "CONNECTED");
    case "ACTIVE": return t("ui.word.active", "ACTIVE");
    case "OK": return t("ui.status.ok", "OK");
    case "RUNNING": return t("ui.status.running", "RUNNING");
    case "NORMAL": return t("ui.status.normal", "NORMAL");
    case "DISABLED": return t("ui.status.disabled", "DISABLED");
    case "WARMING_UP": return t("ui.status.warming_up", "WARMING_UP");
    case "STALE": return t("ux.data.stale", "STALE");
    case "DEGRADED": return t("ui.status.degraded", "DEGRADED");
    case "CONNECTING": return t("ui.conn.connecting", "CONNECTING");
    case "ELEVATED": return t("ui.status.elevated", "ELEVATED");
    case "WARNING": return t("ui.status.warning", "WARNING");
    case "WAITING_TICK": return t("ui.status.waiting_tick", "WAITING_TICK");
    case "PENDING": return t("ui.status.pending", "PENDING");
    case "ERROR": return t("ui.conn.error_word", "ERROR");
    case "DISCONNECTED": return t("ui.status.disconnected", "DISCONNECTED");
    case "UNAVAILABLE": return t("ui.status.unavailable", "UNAVAILABLE");
    case "FAILED": return t("ui.status.failed", "FAILED");
    case "BLOCKED": return t("ui.status.blocked", "BLOCKED");
    case "INVALID": return t("ui.status.invalid", "INVALID");
    case "HALTED": return t("ui.status.halted", "HALTED");
    case "CONFLICTED": return t("ui.status.conflicted", "CONFLICTED");
    case "STOPPED": return t("ui.status.stopped", "STOPPED");
    case "UNKNOWN": return t("ui.word.unknown", "UNKNOWN");
    default: return status.replace(/_/g, " ");
  }
}

export function StatusBadge({ status, label }: { status: string | null | undefined; label?: string }) {
  const t = useI18n((s) => s.t);
  const level = badgeLevel(status);
  const text = statusText(t, status);
  const glyph = level === "good" ? "✓" : level === "warn" ? "⚠" : level === "bad" ? "✕" : level === "neutral" ? "●" : "–";
  return (
    <span className={`badge ${level}`} title={label ?? text}>
      <span aria-hidden="true">{glyph}</span>
      {text}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const t = useI18n((s) => s.t);
  const s = (severity ?? "unknown").toUpperCase();
  const level = s === "CRITICAL" || s === "HIGH" ? "bad" : s === "MEDIUM" ? "warn" : s === "LOW" ? "neutral" : "unknown";
  // display localized for the known severity words; level compare stays on `s`
  const text =
    s === "CRITICAL" ? t("ui.severity.critical", "CRITICAL")
    : s === "HIGH" ? t("ui.severity.high", "HIGH")
    : s === "MEDIUM" ? t("ui.severity.medium", "MEDIUM")
    : s === "LOW" ? t("ui.severity.low", "LOW")
    : s === "UNKNOWN" ? t("ui.word.unknown", "UNKNOWN")
    : s;
  return <span className={`badge ${level}`}>{text}</span>;
}

export function PositionSideBadge({ type }: { type: number | string | null | undefined }) {
  const t = useI18n((s) => s.t);
  const side = positionSide(type);
  const text = side === "BUY" ? t("ui.side.buy", "BUY") : side === "SELL" ? t("ui.side.sell", "SELL") : t("ui.word.unknown", "UNKNOWN");
  return <span className={`badge ${side === "BUY" ? "good" : side === "SELL" ? "bad" : "unknown"}`}>{text}</span>;
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
  subtitle,
  right,
  children,
  tight,
  accent,
}: {
  title: string;
  /** Optional second line under the panel title (feature stubs use it). */
  subtitle?: ReactNode;
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
        <span>
          {title}
          {subtitle !== undefined && subtitle !== null && subtitle !== "" && (
            <span className="panel-subtitle muted">
              {subtitle}
            </span>
          )}
        </span>
        <span style={{ marginInlineStart: "auto", display: "flex", gap: 8, alignItems: "center" }}>{right}</span>
      </div>
      <div className={`panel-body ${tight ? "tight" : ""}`}>{children}</div>
    </section>
  );
}

export function LoadingState({ label }: { label?: string }) {
  const t = useI18n((s) => s.t);
  return (
    <div className="state-block">
      <div className="spinner" />
      <div>{label ?? t("ui.state.loading", "Loading backend state…")}</div>
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
          {t("common.retry", "Retry")}
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
  const t = useI18n((s) => s.t);
  return (
    <div className="probbar">
      {rows.map((r) => (
        <div className="row" key={r.label}>
          <span className="lab">{r.label}</span>
          <span className="track" role="img" aria-label={`${r.label} ${r.value ?? t("ui.word.unknown", "UNKNOWN")}`}>
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
  const t = useI18n((s) => s.t);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">{title}</div>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            {t("ux.confirm.cancel", "Cancel")} <kbd>esc</kbd>
          </button>
          <button className={`btn ${danger ? "danger" : "primary"}`} disabled={busy} onClick={onConfirm}>
            {busy ? t("ui.confirm.sending", "sending…") : confirmLabel}
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
