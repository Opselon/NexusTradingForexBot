/**
 * lane5Kit — shared presentation helpers for lane-5 features.
 *
 * WHY HERE: lane 5 owns only its eight feature folders; cross-feature UI
 * helpers therefore live inside one owned folder (`features/research/ui/`)
 * and are imported by the sibling features via the `@/` alias. All lane-5
 * features use the exact same primitives + this kit; nothing here imports
 * core/transport directly (commands go through ../model -> ../api -> @/api/client).
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import "./lane5.css";

/** "updated HH:MM:SS · source" caption — freshness must always be visible. */
export function FreshnessCaption({
  timestamp,
  source,
  isFetching,
  error,
}: {
  timestamp?: string | number | null;
  source?: string;
  isFetching?: boolean;
  error?: boolean;
}) {
  return (
    <span className="timestamp-note tiny muted" role="status" style={{ marginInlineStart: "auto" }}>
      {error ? (
        <span className="tx-bad">stale — backend error</span>
      ) : (
        <>
          {timestamp ? `updated ${formatDateTime(timestamp)}` : "no timestamp returned"}
          {source ? ` · ${source}` : ""}
          {isFetching ? " · refreshing…" : ""}
        </>
      )}
    </span>
  );
}

/** label/value grid (right-to-left safe: uses the shared .kv token styles). */
export function InfoRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

/** Bounded pretty-printer for backend JSON blobs — never throws. */
export function JsonBlock({ value, maxChars = 4000 }: { value: unknown; maxChars?: number }) {
  if (value === null || value === undefined) {
    return <EmptyState message="Backend returned no payload for this block." />;
  }
  let text: string;
  try {
    text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  const clipped = text.length > maxChars ? `${text.slice(0, maxChars)}\n… (${text.length} chars, truncated)` : text;
  return (
    <pre tabIndex={0} className="inline-mono small" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0, maxHeight: 320, overflow: "auto" }}>
      {clipped}
    </pre>
  );
}

/** Query-state -> skeleton/loading/error mapping used by every lane-5 section. */
export function SectionState<T>({
  query,
  empty,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => unknown;
  };
  empty?: { when: (data: T) => boolean; message: string; hint?: string };
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <LoadingState />;
  if (query.isError) {
    return (
      <ErrorState
        message={query.error instanceof Error ? query.error.message : "Backend request failed."}
        requestId={
          query.error && typeof query.error === "object" && "requestId" in query.error
            ? String((query.error as { requestId?: string | null }).requestId ?? "") || null
            : null
        }
        onRetry={() => void query.refetch()}
      />
    );
  }
  const data = query.data as T;
  if (empty && empty.when(data)) return <EmptyState message={empty.message} hint={empty.hint} />;
  return <>{children(data)}</>;
}

/** Minimal right-side drawer (logical props, RTL-ready). Presentation only. */
export function Drawer({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);
  // Focus enters the drawer on open and returns to the trigger on close.
  useEffect(() => {
    prevFocusRef.current = document.activeElement as HTMLElement | null;
    panelRef.current?.focus({ preventScroll: true });
    return () => prevFocusRef.current?.focus?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = panelRef.current;
      // A stacked dialog (confirm modal) owns the keyboard while it holds
      // focus — only react when focus is inside this drawer.
      if (!root || !root.contains(document.activeElement)) return;
      if (e.key === "Escape") {
        onClose();
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
  }, [onClose]);
  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{
          position: "fixed",
          insetBlock: 0,
          insetInlineEnd: 0,
          width: "min(680px, 92vw)",
          background: "var(--bg-panel)",
          borderInlineStart: "1px solid var(--border-strong)",
          display: "flex",
          flexDirection: "column",
          zIndex: 60,
        }}
      >
        <div className="panel-header" style={{ flex: "0 0 auto" }}>
          <h2>{title}</h2>
          <button className="btn small ghost" style={{ marginInlineStart: "auto" }} onClick={onClose}>
            close <kbd>esc</kbd>
          </button>
        </div>
        <div tabIndex={0} className="panel-body" style={{ flex: "1 1 auto", overflow: "auto" }}>
          {children}
        </div>
        {footer && <div className="panel-body tight" style={{ flex: "0 0 auto", borderTop: "1px solid var(--border)" }}>{footer}</div>}
      </div>
    </div>
  );
}

/** Inline `cmd-result` line mirroring useMutationFeedback state. */
export function CommandResultLine({ state }: { state: { running: boolean; lastResult: boolean | null; lastMessage: string | null } }) {
  if (state.running) return <div className="cmd-result">…sending to backend</div>;
  if (state.lastResult === null || !state.lastMessage) return null;
  return (
    <div className={`cmd-result ${state.lastResult ? "ok" : "fail"}`}>
      {state.lastResult ? "✓" : "✕"} {state.lastMessage}
    </div>
  );
}

/** Small guarded action button — opens a caller-provided confirm flow. */
export function GuardButton({
  label,
  danger,
  busy,
  onClick,
}: {
  label: string;
  danger?: boolean;
  busy?: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`btn small ${danger ? "danger" : ""}`} disabled={busy} onClick={onClick}>
      {label}
    </button>
  );
}

/** Local wall-clock tick for age displays without re-fetching. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Gate stepper: pass/fail/pending with the backend's own reason text. */
export function GateStepper({
  gates,
}: {
  gates: Array<{ name: string; status: string; reason?: string | null; detail?: ReactNode }>;
}) {
  if (gates.length === 0) return <EmptyState message="No gates recorded by the backend for this item." />;
  return (
    <ol className="gate-stepper">
      {gates.map((g, i) => {
        const s = (g.status ?? "").toUpperCase();
        const passed = ["PASS", "PASSED", "OK", "COMPLETED", "YES"].includes(s);
        const failed = ["FAIL", "FAILED", "ERROR", "BLOCKED", "REJECTED", "NO"].includes(s);
        const tone = passed ? "var(--green)" : failed ? "var(--red)" : "var(--text-faint)";
        return (
          <li
            key={`${g.name}-${i}`}
            style={{
              display: "flex",
              gap: 8,
              alignItems: "baseline",
              borderInlineStart: `2px solid ${tone}`,
              paddingInlineStart: 8,
            }}
          >
            <span className="inline-mono small" style={{ minWidth: 18, color: tone }}>
              {passed ? "✓" : failed ? "✕" : "○"}
            </span>
            <span className="small" style={{ minWidth: 130 }}>{g.name}</span>
            <StatusPill status={g.status} />
            <span className="tiny muted">{g.reason ?? ""}</span>
            {g.detail}
          </li>
        );
      })}
    </ol>
  );
}

/** Badge fallback for arbitrary backend status strings (never guessed colors). */
export function StatusPill({ status }: { status: string | null | undefined }) {
  return <span className="badge">{(status ?? "—").replace(/_/g, " ")}</span>;
}

/** Count-distribution list (reason/stage histograms) with proportional bars. */
export function DistBars({
  rows,
  max,
  tone = "var(--accent)",
}: {
  rows: Array<{ label: string; count: number }>;
  max?: number;
  tone?: string;
}) {
  if (rows.length === 0) return <EmptyState message="No rows returned by the backend." />;
  const peak = max ?? Math.max(1, ...rows.map((r) => r.count));
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gridTemplateColumns: "minmax(90px, 26%) 1fr auto", gap: 8, alignItems: "center" }}>
          <span className="tiny inline-mono" title={r.label} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.label}
          </span>
          <span className="track" role="img" aria-label={`${r.label} ${r.count}`} style={{ display: "block", height: 8, background: "var(--bg-inset)", border: "1px solid var(--border)" }}>
            <i style={{ display: "block", height: "100%", width: `${(r.count / peak) * 100}%`, background: tone }} />
          </span>
          <span className="tiny num inline-mono">{r.count}</span>
        </div>
      ))}
    </div>
  );
}

/** Debounced mirror of a value (time-machine slider -> lazy frame fetch). */
export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
