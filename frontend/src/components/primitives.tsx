import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { positionSide } from "@/lib/format";
import { useUiStore, type ToastItem } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";

/** t signature shared by the local label maps below. */
type T = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** Backend status token -> localized display word. DATA stays verbatim (the
 *  level is computed from the raw token, never from the translation); only the
 *  word a user reads is mapped. A status the dictionary does not know renders
 *  verbatim, so a new health code can never be mistranslated. */
function statusWord(t: T, status: string): string {
  switch (status.toUpperCase()) {
    case "READY": return t("ui.status.ready", "READY");
    case "IDLE": return t("ui.status.idle", "IDLE");
    case "CONNECTED": return t("ui.status.connected", "CONNECTED");
    case "OK": return t("ui.status.ok", "OK");
    case "RUNNING": return t("ui.status.running", "RUNNING");
    case "NORMAL": return t("ui.status.normal", "NORMAL");
    case "DISABLED": return t("ui.status.disabled", "DISABLED");
    case "WARMING_UP": return t("ui.status.warming_up", "WARMING UP");
    case "DEGRADED": return t("ui.status.degraded", "DEGRADED");
    case "ELEVATED": return t("ui.status.elevated", "ELEVATED");
    case "WARNING": return t("ui.status.warning", "WARNING");
    case "WAITING_TICK": return t("ui.status.waiting_tick", "WAITING TICK");
    case "PENDING": return t("ui.status.pending", "PENDING");
    case "DISCONNECTED": return t("ui.status.disconnected", "DISCONNECTED");
    case "UNAVAILABLE": return t("ui.status.unavailable", "UNAVAILABLE");
    case "FAILED": return t("ui.status.failed", "FAILED");
    case "BLOCKED": return t("ui.status.blocked", "BLOCKED");
    case "INVALID": return t("ui.status.invalid", "INVALID");
    case "HALTED": return t("ui.status.halted", "HALTED");
    case "CONFLICTED": return t("ui.status.conflicted", "CONFLICTED");
    case "STOPPED": return t("ui.status.stopped", "STOPPED");
    default: return status.replace(/_/g, " ");
  }
}

/** Severity token -> localized display word (unknown values pass through). */
function severityWord(t: T, severity: string): string {
  switch (severity.toUpperCase()) {
    case "CRITICAL": return t("ui.severity.critical", "CRITICAL");
    case "HIGH": return t("ui.severity.high", "HIGH");
    case "MEDIUM": return t("ui.severity.medium", "MEDIUM");
    case "LOW": return t("ui.severity.low", "LOW");
    default: return severity.toUpperCase();
  }
}

/** Side token -> localized display word (unknown values pass through). */
function sideWord(t: T, side: string): string {
  switch (side.toUpperCase()) {
    case "BUY": return t("ui.side.buy", "BUY");
    case "SELL": return t("ui.side.sell", "SELL");
    default: return side;
  }
}

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
  // The badge renders by the hundred in tables and re-renders with its parent;
  // the only non-trivial internal derivation is badgeLevel (uppercase + four
  // category scans), memoized on the exact value it reads. Same status in,
  // same level out — the badge markup is unchanged.
  const t = useI18n((s) => s.t);
  const level = useMemo(() => badgeLevel(status), [status]);
  const text = status ? statusWord(t, status) : "UNKNOWN";
  const glyph = level === "good" ? "\u2713" : level === "warn" ? "\u26a0" : level === "bad" ? "\u2715" : level === "neutral" ? "\u25cf" : "\u2013";
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
  return <span className={`badge ${level}`}>{severityWord(t, s)}</span>;
}

export function PositionSideBadge({ type }: { type: number | string | null | undefined }) {
  const t = useI18n((s) => s.t);
  const side = positionSide(type);
  return <span className={`badge ${side === "BUY" ? "good" : side === "SELL" ? "bad" : "unknown"}`}>{sideWord(t, side)}</span>;
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
        <h2>
          {title}
          {subtitle !== undefined && subtitle !== null && subtitle !== "" && (
            <span className="panel-subtitle muted">
              {subtitle}
            </span>
          )}
        </h2>
        <span className="panel-tools">{right}</span>
      </div>
      <div className={`panel-body ${tight ? "tight" : ""}`}>{children}</div>
    </section>
  );
}

export function LoadingState({ label }: { label?: string }) {
  const t = useI18n((s) => s.t);
  const text = label ?? t("ui.state.loading", "Loading backend state…");
  return (
    <div className="state-block" role="status">
      <div className="spinner" aria-hidden="true" />
      <div>{text}</div>
    </div>
  );
}

export function ErrorState({ message, requestId, onRetry }: { message: string; requestId?: string | null; onRetry?: () => void }) {
  const t = useI18n((s) => s.t);
  return (
    <div className="state-block" role="alert">
      {/* Decorative: the message below carries the meaning for AT. */}
      <div className="glyph" aria-hidden="true">⚠</div>
      <div>{message}</div>
      {requestId && <div className="hint inline-mono">request_id: {requestId}</div>}
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          {t("ui.state.retry", "Retry")}
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="state-block" role="status">
      {/* Decorative: the message below carries the meaning for AT. */}
      <div className="glyph" aria-hidden="true">∅</div>
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
    <div tabIndex={0} className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th scope="col" key={h.label} className={h.num ? "num" : undefined}>
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
  onPrefetch,
}: {
  options: Array<{ id: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
  /** Optional (wave 2b): fires on hover/focus of an option so the caller can
   *  warm that section's chunk+query before the click. Inert when omitted. */
  onPrefetch?: (v: T) => void;
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
          onPointerEnter={onPrefetch ? () => onPrefetch(o.id) : undefined}
          onFocus={onPrefetch ? () => onPrefetch(o.id) : undefined}
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
  const modalRef = useRef<HTMLDivElement>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);
  // Focus enters the dialog on open and returns to the trigger on close.
  useEffect(() => {
    prevFocusRef.current = document.activeElement as HTMLElement | null;
    modalRef.current?.focus({ preventScroll: true });
    return () => prevFocusRef.current?.focus?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = modalRef.current;
      // A stacked dialog (drawer beneath / modal above) owns the keyboard
      // while it holds focus — only react when focus is inside this dialog.
      if (!root || !root.contains(document.activeElement)) return;
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key === "Tab") {
        const focusables = Array.from(
          root.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
        ).filter((el) => !el.hasAttribute("disabled"));
        if (focusables.length === 0) return;
        const first = focusables[0] as HTMLElement;
        const last = focusables[focusables.length - 1] as HTMLElement;
        const active = document.activeElement;
        if (e.shiftKey && (active === first || !root.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && (active === last || !root.contains(active))) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div ref={modalRef} tabIndex={-1} className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">{title}</div>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            {t("ui.state.cancel", "Cancel")} <kbd>esc</kbd>
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
