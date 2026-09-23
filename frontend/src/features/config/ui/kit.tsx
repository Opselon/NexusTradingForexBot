/**
 * Lane-3 shared presentation kit (config / rules / debug / health / database).
 *
 * WHY HERE: `features/config/` is the platform-settings surface and already
 * owns the lane's shared logic layer (`validation.ts`). These are pure
 * presentation helpers — no fetch, no business rules — so every lane-3 page
 * can keep skeleton/error/empty + freshness discipline identical without
 * five divergent copies. Anything with a decision in it belongs in
 * `validation.ts` or a feature's `model.ts`, never here.
 *
 * Styling: feature-scoped classes only, colors exclusively from theme tokens
 * (`--accent`, `--green`, `--red`, `--amber`, `--mono`…), logical CSS props so
 * the RTL switch (`<html dir=fa>`) mirrors correctly.
 */

import { useCallback, useRef, useState, type ReactNode } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import { useUiStore } from "@/stores/uiStore";
import { formatAgeMs } from "@/lib/format";
import { Panel, EmptyState, ErrorState, Skeleton } from "@/components/primitives";

/* ------------------------------------------------------------------ */
/* Freshness caption                                                   */
/* ------------------------------------------------------------------ */

/** "updated 3.2s ago · every 10s" — the age is measured from a client-captured
 *  wall clock of the LAST SUCCESSFUL response, never from the payload. */
export function FreshnessCaption({
  fetchedAtMs,
  nowMs,
  intervalMs,
  note,
  stale,
}: {
  fetchedAtMs: number | null;
  nowMs?: number;
  intervalMs?: number;
  note?: string;
  stale?: boolean;
}) {
  if (fetchedAtMs === null) {
    return <span className="timestamp-note l3-fresh none">never fetched</span>;
  }
  const age = Math.max(0, (nowMs ?? Date.now()) - fetchedAtMs);
  const words = [
    `updated ${formatAgeMs(age)} ago`,
    intervalMs ? `every ${Math.round(intervalMs / 1000)}s` : null,
    note ?? null,
  ].filter(Boolean);
  return (
    <span className={`timestamp-note l3-fresh ${stale || age > 3 * (intervalMs ?? 15_000) ? "bad" : ""}`}>
      {words.join(" · ")}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Pause / resume polling                                              */
/* ------------------------------------------------------------------ */

/** Owns the "polling" boolean and exposes the `refetchInterval` value to hand
 *  to React Query (0 disables polling without unmounting the query). */
export function usePolling(defaultIntervalMs = 10_000): {
  paused: boolean;
  togglePaused: () => void;
  intervalMs: number;
  setPaused: (p: boolean) => void;
} {
  const [paused, setPaused] = useState(false);
  const togglePaused = useCallback(() => setPaused((p) => !p), []);
  return { paused, togglePaused, intervalMs: defaultIntervalMs, setPaused };
}

export function PollControl({
  paused,
  onToggle,
  intervalMs,
  busy,
}: {
  paused: boolean;
  onToggle: () => void;
  intervalMs: number;
  busy?: boolean;
}) {
  return (
    <span className="l3-poll">
      {busy && <span className="spinner l3-mini-spinner" aria-hidden="true" />}
      <button className="btn small ghost" onClick={onToggle} aria-pressed={!paused}>
        {paused ? "Resume polling" : "Pause polling"}
      </button>
      <span className="timestamp-note">{paused ? "manual refresh only" : `every ${Math.round(intervalMs / 1000)}s`}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Query section wrapper: skeleton / error+retry / empty / content     */
/* ------------------------------------------------------------------ */

export function QuerySection<T>({
  title,
  accent,
  right,
  query,
  skeletonRows = 3,
  emptyMessage = "Backend returned no rows.",
  emptyHint,
  children,
  tight,
}: {
  title: string;
  accent?: boolean;
  right?: ReactNode;
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => unknown;
  };
  skeletonRows?: number;
  emptyMessage?: string;
  emptyHint?: string;
  children: (data: T) => ReactNode;
  tight?: boolean;
}) {
  const body = (() => {
    if (query.isPending) return <Skeleton count={skeletonRows} />;
    if (query.isError) {
      return (
        <ErrorState
          message={query.error instanceof Error ? query.error.message : "Backend request failed."}
          requestId={(query.error as { requestId?: string } | null)?.requestId ?? null}
          onRetry={() => void query.refetch()}
        />
      );
    }
    if (query.data === undefined || query.data === null) {
      return <EmptyState message={emptyMessage} hint={emptyHint} />;
    }
    return children(query.data);
  })();
  return (
    <Panel title={title} accent={accent} right={right} tight={tight}>
      {body}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* JSON / key-value viewers                                            */
/* ------------------------------------------------------------------ */

function preview(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value.length > 120 ? `${value.slice(0, 117)}…` : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

/** Collapsible raw viewer with a per-node depth cap: the debug snapshot is
 *  large and must never lock the tab. Values are rendered verbatim, strings
 *  are never quoted away or reinterpreted. */
export function JsonView({ value, name, depth = 0 }: { value: unknown; name?: string; depth?: number }) {
  const [open, setOpen] = useState(depth < 1);
  const pad = { paddingInlineStart: 10 + depth * 12 };

  if (Array.isArray(value)) {
    return (
      <div className="l3-json-node" style={pad}>
        <button className="l3-json-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "▾" : "▸"} <span className="l3-json-key">{name ?? "array"}</span>
          <span className="l3-json-meta">[{value.length}]</span>
        </button>
        {open &&
          (value.length === 0 ? (
            <div className="l3-json-empty" style={{ paddingInlineStart: 12 }}>
              empty
            </div>
          ) : (
            value.slice(0, 200).map((v, i) => <JsonView key={i} value={v} name={`${i}`} depth={depth + 1} />)
          ))}
        {value.length > 200 && (
          <div className="l3-json-empty" style={{ paddingInlineStart: 12 }}>
            … {value.length - 200} more (capped for rendering)
          </div>
        )}
      </div>
    );
  }

  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <div className="l3-json-node" style={pad}>
        <button className="l3-json-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "▾" : "▸"} <span className="l3-json-key">{name ?? "object"}</span>
          <span className="l3-json-meta">{`{${entries.length}}`}</span>
        </button>
        {open &&
          entries.map(([k, v]) => <JsonView key={k} value={v} name={k} depth={depth + 1} />)}
      </div>
    );
  }

  return (
    <div className="l3-json-leaf" style={pad}>
      <span className="l3-json-key">{name}</span>
      <span className="l3-json-val">{preview(value)}</span>
    </div>
  );
}

/** Definition list of scalar entries — used everywhere a backend object needs
 *  a readable, non-guessing table (unknown keys render too, in insertion order). */
export function KeyValueList({ rows }: { rows: Array<[string, ReactNode]> }) {
  if (rows.length === 0) return <EmptyState message="No fields in this payload." />;
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <div key={`${k}-${i}`} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Rows built from a payload object are cached per OBJECT IDENTITY (WeakMap):
 * `scalarRows` runs in the render path (StateTab feeds KeyValueList from it),
 * and a nested object value would otherwise be JSON.stringify'd on every
 * render of a polling panel. React Query hands back a stable object identity
 * until the next fetch lands, so one serialization per distinct payload is
 * enough. INVARIANT: the returned array and its elements are treated as
 * read-only (callers may `.filter()` — which allocates — but never mutate);
 * the rendered bytes are exactly `preview(v) || JSON.stringify(v)` either way.
 */
const scalarRowsByIdentity = new WeakMap<Record<string, unknown>, Array<[string, ReactNode]>>();

export function scalarRows(obj: Record<string, unknown> | null | undefined): Array<[string, ReactNode]> {
  if (!obj) return [];
  const cached = scalarRowsByIdentity.get(obj);
  if (cached) return cached;
  const rows = Object.entries(obj).map(([k, v]) => [k, <span key={k}>{preview(v) || JSON.stringify(v)}</span>] as [string, ReactNode]);
  scalarRowsByIdentity.set(obj, rows);
  return rows;
}

/* ------------------------------------------------------------------ */
/* Form controls with inline errors                                    */
/* ------------------------------------------------------------------ */

export function FieldRow({
  label,
  hint,
  error,
  dirty,
  mutability,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  dirty?: boolean;
  mutability?: string | null;
  children: ReactNode;
}) {
  return (
    <div className={`l3-field ${error ? "invalid" : ""}`}>
      <div className="l3-field-head">
        <span className="lab">{label}</span>
        {dirty && <span className="l3-dirty" title="changed locally, not yet applied">edited</span>}
        {mutability && <span className={`l3-mut ${mutabilityLevel(mutability)}`}>{mutability}</span>}
      </div>
      <div className="l3-field-control">{children}</div>
      {error ? (
        <div className="l3-field-error" role="alert">
          {error}
        </div>
      ) : hint ? (
        <div className="l3-field-hint">{hint}</div>
      ) : null}
    </div>
  );
}

function mutabilityLevel(m: string): "good" | "warn" | "bad" | "neutral" {
  switch (m.toUpperCase()) {
    case "HOT":
      return "good";
    case "HOT_RESTRICTED":
      return "warn";
    case "RESTART_REQUIRED":
      return "warn";
    case "SECRET":
      return "bad";
    case "READ_ONLY":
      return "neutral";
    default:
      return "neutral";
  }
}

export function TextField({
  spec,
  value,
  onChange,
  error,
  placeholder,
}: {
  spec?: string;
  value: string;
  onChange: (v: string) => void;
  error?: string | null;
  placeholder?: string;
}) {
  return (
    <input
      className="input l3-input"
      type="text"
      value={value}
      spellCheck={false}
      autoComplete="off"
      aria-label={spec}
      aria-invalid={error ? true : undefined}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function NumberField({
  value,
  onChange,
  error,
  step,
  placeholder,
  spec,
}: {
  value: string;
  onChange: (v: string) => void;
  error?: string | null;
  step?: string;
  placeholder?: string;
  /** TASK-CFGUI-001: accessible name (screen readers get the field label). */
  spec?: string;
}) {
  return (
    <input
      className="input l3-input num"
      type="number"
      value={value}
      step={step}
      aria-label={spec ?? placeholder}
      aria-invalid={error ? true : undefined}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function SelectField({
  value,
  onChange,
  options,
  error,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
  error?: string | null;
  label?: string;
}) {
  return (
    <select
      className="select l3-input"
      value={value}
      aria-label={label}
      aria-invalid={error ? true : undefined}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

export function CheckField({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      className="l3-check"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      title={label}
    >
      <span className={`switch ${checked ? "on" : ""}`} aria-hidden="true" />
      <span className="l3-check-lab">{checked ? "ON" : "OFF"}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Typed confirmation (destructive ops)                                */
/* ------------------------------------------------------------------ */

/**
 * Destructive-action modal that requires the operator to TYPE a word. Used by
 * engine mode (LIVE) and database backup/migrate. Esc / backdrop cancel; the
 * confirm button stays disabled until the phrase matches exactly.
 */
export function TypedConfirmModal({
  title,
  body,
  word,
  confirmLabel,
  busy,
  busyLabel,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: ReactNode;
  word: string;
  confirmLabel: string;
  busy?: boolean;
  /** TASK-CFGUI-001: busy button text (default "sending…" — the preview gate
   *  passes "validating…" while the server matrix check is in flight). */
  busyLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === word;
  const boxRef = useRef<HTMLDivElement | null>(null);
  const typedInputRef = useRef<HTMLInputElement | null>(null);
  // Typed confirm: land focus in the token input so typing works immediately.
  useDialogA11y(boxRef, onCancel, { initialFocusRef: typedInputRef });
  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}
    >
      <div ref={boxRef} className="modal danger" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">{title}</div>
        <div className="modal-body">
          <div className="confirm-box">
            <div className="note">{body}</div>
            <div className="row">
              <label className="l3-typed-label" htmlFor={`typed-${word}`}>
                Type <span className="inline-mono">{word}</span> to confirm
              </label>
              <input
                id={`typed-${word}`}
                ref={typedInputRef}
                className="input l3-input"
                value={typed}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onCancel}>
            Cancel <kbd>esc</kbd>
          </button>
          <button className="btn danger" disabled={!matches || busy} onClick={onConfirm}>
            {busy ? (busyLabel ?? "sending…") : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Command-result strip (backend verdict, verbatim)                    */
/* ------------------------------------------------------------------ */

export function ResultStrip({
  result,
}: {
  result: { lastResult: boolean | null; lastMessage: string | null; running: boolean } | null;
}) {
  if (!result || (result.lastResult === null && !result.running)) return null;
  const kind = result.running ? "run" : result.lastResult ? "ok" : "fail";
  return (
    <div className={`l3-result ${kind}`} role="status">
      {result.running ? "⏳ sending…" : result.lastResult ? "✓" : "✕"}{" "}
      <span>{result.lastMessage ?? "no message from backend"}</span>
    </div>
  );
}

/** Toast mirror for raw (non-LegacyMutationResult) backend answers. */
export function pushBackendToast(ok: boolean, text: string) {
  useUiStore.getState().pushToast(ok ? "ok" : "fail", text);
}

/* ------------------------------------------------------------------ */
/* Small display helpers                                               */
/* ------------------------------------------------------------------ */

export function Dot({ status }: { status: string | null | undefined }) {
  const s = (status ?? "UNKNOWN").toUpperCase();
  const level =
    s === "HEALTHY" || s === "PASS" || s === "FRESH" || s === "READY" || s === "OK" || s === "RUNNING"
      ? "good"
      : s === "DEGRADED" || s === "WARNING" || s === "STALE" || s === "WARMING_UP" || s === "PENDING"
        ? "warn"
        : s === "UNHEALTHY" || s === "FAIL" || s === "ERROR" || s === "NOT READY" || s === "DISCONNECTED"
          ? "bad"
          : "neutral";
  return <span className={`l3-dot ${level}`} title={s} />;
}

export function MonoValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="faint">—</span>;
  if (typeof value === "boolean") return <span>{value ? "true" : "false"}</span>;
  if (typeof value === "number")
    return <span>{Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(4)}</span>;
  const s = String(value);
  return (
    <span className="inline-mono" title={s.length > 40 ? s : undefined}>
      {s.length > 40 ? `…${s.slice(-37)}` : s}
    </span>
  );
}

export function SectionGrid({ children, cols = 2 }: { children: ReactNode; cols?: 2 | 3 | 4 }) {
  return <div className={`grid cols-${cols}`}>{children}</div>;
}
