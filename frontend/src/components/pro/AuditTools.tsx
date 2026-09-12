/**
 * AuditTools — pro forensics widgets for the audit lane.
 *
 * Contract this file must never break:
 *  - NO raw-HTML prop anywhere (the static scan in
 *    tests/js/pro_forensics.test.mjs greps for that escape hatch, comments
 *    included). Every key, string and number coming from the backend is
 *    passed to React as a text CHILD, so React escapes it.
 *    Payloads are hostile-by-default (operator-typed notes, broker strings,
 *    vendor news) and are treated as data, never as markup.
 *  - No fetch, no react-query, no stores, no routing. Components take plain
 *    arrays/objects as props and are pure presentation + local UI state.
 *  - Missing data renders as an explicit "—" / UNKNOWN marker; nothing is
 *    inferred or back-filled (backend-authoritative presentation).
 */

import { useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import {
  buildCsv,
  eventGaps,
  payloadSummary,
  severityMatrix,
  timelinePoints,
  UNKNOWN,
} from "@/lib/forensicsMath";
import type { IncidentLike, EventLike } from "@/lib/forensicsMath";
import "./pro-audit.css";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Deterministic epoch-ms -> "YYYY-MM-DD HH:MM:SS.mmmZ" (UTC, no locale drift). */
export function formatUtc(timestampMs: number | null | undefined): string {
  if (timestampMs === null || timestampMs === undefined || !Number.isFinite(timestampMs)) return "—";
  const d = new Date(timestampMs);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number, w = 2): string => String(n).padStart(w, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}.${p(d.getUTCMilliseconds(), 3)}Z`
  );
}

/** Compact human duration from milliseconds ("412ms" / "18.4s" / "4m12s" / "2h03m"). */
function durationLabel(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m${String(Math.floor(sec % 60)).padStart(2, "0")}s`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h${String(min % 60).padStart(2, "0")}m`;
  return `${Math.floor(hr / 24)}d${String(hr % 24).padStart(2, "0")}h`;
}

const emDash = "—";

/** Stable empty fallback: keeps useMemo deps referentially stable. */
const NO_ROWS: readonly never[] = [];

// ---------------------------------------------------------------------------
// 1. JsonTreeViewer
// ---------------------------------------------------------------------------

/** Discriminate a value the way a JSON inspector shows it (null before object). */
function kindOf(value: unknown): "null" | "array" | "object" | "string" | "number" | "boolean" | "other" {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  switch (typeof value) {
    case "object":
      return "object";
    case "string":
      return "string";
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    default:
      return "other";
  }
}

interface JsonNodeProps {
  /** Property name or array index, rendered as a text node (never HTML). */
  name: string;
  value: unknown;
  depth: number;
  /** Nodes deeper than this render collapsed and cannot be expanded. */
  maxDepth: number;
  /** Longest string rendered inline before an ellipsis. */
  valueMaxLen: number;
  /** Cycle guard along the current path only (siblings stay independent). */
  seen: ReadonlySet<object>;
  defaultExpandDepth: number;
}

function leafText(value: unknown, maxLen: number): string {
  switch (typeof value) {
    case "string":
      return value.length > maxLen ? `${value.slice(0, maxLen)}…` : value;
    case "number":
      return Number.isFinite(value) ? String(value) : String(value);
    case "boolean":
      return value ? "true" : "false";
    default:
      return emDash;
  }
}

const RENDER_CHILD_CAP = 200;

/** Longest list of children rendered at once — protects against 10k-item arrays. */
function capChildren(entries: Array<[string, unknown]>): { shown: Array<[string, unknown]>; hidden: number } {
  if (entries.length <= RENDER_CHILD_CAP) return { shown: entries, hidden: 0 };
  return { shown: entries.slice(0, RENDER_CHILD_CAP), hidden: entries.length - RENDER_CHILD_CAP };
}

function JsonNode(props: JsonNodeProps): JSX.Element {
  const { name, value, depth, maxDepth, valueMaxLen, seen, defaultExpandDepth } = props;
  const [open, setOpen] = useState(depth < defaultExpandDepth);
  const kind = kindOf(value);
  const pad = { paddingLeft: `${Math.min(depth, 12) * 12}px` } as const;

  if (kind === "null" || kind === "other") {
    const literal =
      value === null ? "null" : value === undefined ? "undefined" : typeof value === "bigint" ? `${value.toString()}n` : String(value);
    return (
      <div className="pat-json-row" style={pad}>
        <span className="pat-json-key">{name}</span>
        <span className="pat-json-sep">: </span>
        <span className="pat-json-null">{literal}</span>
      </div>
    );
  }

  if (kind === "string" || kind === "number" || kind === "boolean") {
    const text = leafText(value, valueMaxLen);
    const clipped = kind === "string" && typeof value === "string" && value.length > valueMaxLen;
    return (
      <div className="pat-json-row" style={pad}>
        <span className="pat-json-key">{name}</span>
        <span className="pat-json-sep">: </span>
        <span className={`pat-json-val pat-json-${kind}`} title={clipped ? text : undefined}>
          {kind === "string" ? `"${text}"` : text}
        </span>
      </div>
    );
  }

  const container = value as object;
  const entries: Array<[string, unknown]> =
    kind === "array"
      ? (value as unknown[]).map((item, i) => [String(i), item] as [string, unknown])
      : Object.entries(value as Record<string, unknown>);
  const circular = seen.has(container);
  const overDepth = depth >= maxDepth;
  const bracketOpen = kind === "array" ? "[" : "{";
  const bracketClose = kind === "array" ? "]" : "}";
  const nextSeen = circular ? seen : new Set<object>([...seen, container]);
  const capped = capChildren(entries);

  if (circular || overDepth || entries.length === 0) {
    const reason = circular ? "circular" : overDepth ? `depth>${maxDepth}` : "empty";
    return (
      <div className="pat-json-row" style={pad}>
        <span className="pat-json-key">{name}</span>
        <span className="pat-json-sep">: </span>
        <span className="pat-json-empty" title={reason}>
          {bracketOpen}
          {entries.length}
          {bracketClose}
          <em className="pat-json-note"> {reason}</em>
        </span>
      </div>
    );
  }

  return (
    <div className="pat-json-node">
      <div className="pat-json-row" style={pad}>
        <button
          type="button"
          className="pat-json-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          title={open ? "collapse" : "expand"}
        >
          {open ? "▾" : "▸"}
        </button>
        <span className="pat-json-key">{name}</span>
        <span className="pat-json-sep">: </span>
        <span className="pat-json-meta">
          {bracketOpen}
          {entries.length}
          {bracketClose}
        </span>
      </div>
      {open &&
        capped.shown.map(([childName, childValue]) => (
          <JsonNode
            key={`${depth}:${childName}`}
            name={childName}
            value={childValue}
            depth={depth + 1}
            maxDepth={maxDepth}
            valueMaxLen={valueMaxLen}
            seen={nextSeen}
            defaultExpandDepth={defaultExpandDepth}
          />
        ))}
      {open && capped.hidden > 0 && (
        <div className="pat-json-row" style={{ paddingLeft: `${Math.min(depth + 1, 12) * 12}px` }}>
          <span className="pat-json-note">{`+${capped.hidden} more ${kind === "array" ? "items" : "keys"} (render cap ${RENDER_CHILD_CAP})`}</span>
        </div>
      )}
    </div>
  );
}

export interface JsonTreeViewerProps {
  /** Payload value: object/array, JSON string, or null/undefined. */
  payload: unknown;
  /** Default 1: the top-level container is open, its children start collapsed. */
  defaultExpandDepth?: number;
  /** Hard recursion cap for very deep documents. Default 8. */
  maxDepth?: number;
  /** Longest inline string before ellipsis. Default 120. */
  valueMaxLen?: number;
  /** Shown instead of the empty-state line when there is nothing to render. */
  emptyLabel?: string;
}

/**
 * Recursive JSON tree over untrusted backend payloads. All rendering goes
 * through React text children — an escaped `<script>` or `<img onerror>`
 * string inside a payload displays as literal text.
 */
export function JsonTreeViewer({
  payload,
  defaultExpandDepth = 1,
  maxDepth = 8,
  valueMaxLen = 120,
  emptyLabel = "no payload",
}: JsonTreeViewerProps): JSX.Element {
  const normalized = useMemo(() => {
    if (typeof payload === "string") {
      const trimmed = payload.trim();
      if (trimmed === "") return { ok: false as const, reason: "empty payload" };
      try {
        const parsed: unknown = JSON.parse(trimmed);
        if (parsed === null || typeof parsed !== "object") {
          return { ok: true as const, value: { value: parsed } };
        }
        return { ok: true as const, value: parsed };
      } catch {
        return { ok: false as const, reason: "not valid JSON" };
      }
    }
    if (payload === null || payload === undefined) return { ok: false as const, reason: emptyLabel };
    if (typeof payload !== "object") return { ok: true as const, value: { value: payload } };
    return { ok: true as const, value: payload };
  }, [payload, emptyLabel]);

  if (!normalized.ok) {
    return (
      <div className="pat-json pat-json-empty-state">
        <span className="pat-json-key">root</span>
        <span className="pat-json-sep">: </span>
        <span className="pat-json-note">{normalized.reason}</span>
        {typeof payload === "string" && (
          <div className="pat-json-raw" title="raw text, shown escaped">
            {payload.slice(0, 2000)}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="pat-json">
      <JsonNode
        name="root"
        value={normalized.value}
        depth={0}
        maxDepth={maxDepth}
        valueMaxLen={valueMaxLen}
        seen={new Set<object>()}
        defaultExpandDepth={defaultExpandDepth}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. TimelineStrip
// ---------------------------------------------------------------------------

export interface TimelineStripProps {
  /** Event rows in backend order (newest-first tails are handled). */
  rows: readonly EventLike[];
  /** Consecutive delta that counts as a visible gap. Default 60s. */
  gapThresholdSec?: number;
  width?: number;
  height?: number;
  /** Left/right inset inside the SVG viewBox, in user units. */
  paddingX?: number;
  /** Optional tooltip prefix per dot (e.g. event_type from the same index). */
  labelFor?: (index: number) => string | null | undefined;
  emptyLabel?: string;
}

const GAP_HL = 0.03; // gap wider than 3% of the span gets a visible break band

/**
 * SVG dot strip over the rendered page's timestamps, with explicit break bands
 * where the quiet period exceeds `gapThresholdSec`. Unparseable timestamps are
 * excluded from the plot and reported in the footer as "N skipped" — never
 * placed at a guessed position.
 */
export function TimelineStrip({
  rows,
  gapThresholdSec = 60,
  width = 900,
  height = 74,
  paddingX = 14,
  labelFor,
  emptyLabel = "no plottable timestamps on this page",
}: TimelineStripProps): JSX.Element {
  const list = Array.isArray(rows) ? rows : NO_ROWS;
  const timeline = useMemo(() => timelinePoints(list), [list]);
  const gaps = useMemo(() => eventGaps(list, gapThresholdSec), [list, gapThresholdSec]);

  const baseline = height - 18;
  const span = timeline.spanMs > 0 ? timeline.spanMs : 0;
  const xFor = (offsetMs: number): number =>
    span > 0 ? paddingX + (offsetMs / span) * (width - paddingX * 2) : (width - paddingX * 2) / 2 + paddingX;

  const gapBands = gaps
    .filter((g) => span > 0 && g.deltaSec * 1000 > span * GAP_HL)
    .map((g) => {
      const prev = timeline.points.find((p) => p.index === g.previousIndex);
      const cur = timeline.points.find((p) => p.index === g.index);
      if (!prev || !cur) return null;
      const a = xFor(prev.offsetMs);
      const b = xFor(cur.offsetMs);
      return {
        x: Math.min(a, b),
        w: Math.abs(b - a),
        deltaSec: g.deltaSec,
        from: formatUtc(prev.timestampMs),
        to: formatUtc(cur.timestampMs),
      };
    })
    .filter((band): band is NonNullable<typeof band> => band !== null);

  return (
    <div className="pat-timeline">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Event timeline: ${timeline.points.length} plotted points, ${gaps.length} gaps over ${gapThresholdSec}s`}
      >
        <title>{`span ${span > 0 ? durationLabel(span) : emDash} · max gap ${durationLabel(timeline.maxGapMs)}`}</title>
        <line x1={paddingX} y1={baseline} x2={width - paddingX} y2={baseline} className="pat-tl-axis" />
        {gapBands.map((band) => (
          <rect
            key={`band:${band.x.toFixed(1)}:${band.deltaSec.toFixed(0)}`}
            x={band.x}
            y={8}
            width={Math.max(band.w, 2)}
            height={baseline - 8}
            className="pat-tl-gap"
          >
            <title>{`gap ${durationLabel(band.deltaSec * 1000)}\n${band.from}\n→ ${band.to}`}</title>
          </rect>
        ))}
        {timeline.points.map((p) => {
          const cx = xFor(p.offsetMs);
          const label = labelFor ? labelFor(p.index) : null;
          const stamp = formatUtc(p.timestampMs);
          return (
            <circle
              key={`pt:${p.index}`}
              cx={cx}
              cy={baseline}
              r={3.2}
              className="pat-tl-dot"
              tabIndex={-1}
            >
              <title>{`${stamp}${label ? ` · ${label}` : ""}`}</title>
            </circle>
          );
        })}
        {gaps.slice(0, 40).map((g) => {
          const prev = timeline.points.find((p) => p.index === g.previousIndex);
          const cur = timeline.points.find((p) => p.index === g.index);
          if (!prev || !cur) return null;
          const mid = (xFor(prev.offsetMs) + xFor(cur.offsetMs)) / 2;
          return (
            <text key={`lbl:${g.index}`} x={mid} y={16} className="pat-tl-gap-label" textAnchor="middle">
              {`⚠ ${durationLabel(g.deltaSec * 1000)}`}
            </text>
          );
        })}
      </svg>
      <div className="pat-tl-footer">
        <span className="pat-chip">{`${timeline.points.length}/${list.length} plotted`}</span>
        <span className="pat-chip">{`span ${span > 0 ? durationLabel(span) : emDash}`}</span>
        <span className="pat-chip">{`max gap ${durationLabel(timeline.maxGapMs)}`}</span>
        <span className={`pat-chip ${gaps.length > 0 ? "pat-chip-warn" : ""}`}>
          {`${gaps.length} gap${gaps.length === 1 ? "" : "s"} > ${gapThresholdSec}s`}
        </span>
        {timeline.skipped > 0 && (
          <span className="pat-chip pat-chip-bad" title="rows whose created_at could not be parsed — skipped, never guessed">
            {`${timeline.skipped} skipped (bad timestamp)`}
          </span>
        )}
      </div>
      {timeline.points.length === 0 && <div className="pat-empty">{emptyLabel}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. SeverityMatrix
// ---------------------------------------------------------------------------

export interface SeverityMatrixProps {
  /** Incidents from the backend inventory (any subset of fields may be absent). */
  incidents: readonly IncidentLike[];
  /** Cell click handler: drill into the (severity, status) slice upstream. */
  onSelect?: (severity: string, status: string) => void;
  caption?: string;
}

/** Severity x status cross-tab of the rendered incidents, with totals. */
export function SeverityMatrix({ incidents, onSelect, caption }: SeverityMatrixProps): JSX.Element {
  const list = Array.isArray(incidents) ? incidents : NO_ROWS;
  const matrix = useMemo(() => severityMatrix(list), [list]);

  if (matrix.total === 0) {
    return <div className="pat-empty">no incidents to cross-tabulate{matrix.skipped > 0 ? ` (${matrix.skipped} non-object rows skipped)` : ""}</div>;
  }

  const handleCell = (severity: string, status: string) => (event: ReactMouseEvent<HTMLTableCellElement>) => {
    void event;
    onSelect?.(severity, status);
  };

  return (
    <div className="pat-matrix-wrap">
      <table className="pat-matrix">
        {caption ? <caption className="pat-matrix-caption">{caption}</caption> : null}
        <thead>
          <tr>
            <th scope="col" className="pat-matrix-corner">
              severity \ status
            </th>
            {matrix.statuses.map((status) => (
              <th key={status} scope="col" className={status === UNKNOWN ? "pat-matrix-unknown" : undefined}>
                {status}
              </th>
            ))}
            <th scope="col" className="pat-matrix-total">
              total
            </th>
          </tr>
        </thead>
        <tbody>
          {matrix.severities.map((severity) => (
            <tr key={severity}>
              <th scope="row" className={severity === UNKNOWN ? "pat-matrix-unknown" : undefined}>
                {severity}
              </th>
              {matrix.statuses.map((status) => {
                const n = matrix.counts[severity]?.[status] ?? 0;
                return (
                  <td
                    key={`${severity}:${status}`}
                    className={`pat-matrix-cell ${n > 0 ? "pat-matrix-hit" : "pat-matrix-zero"}${onSelect ? " pat-matrix-click" : ""}`}
                    onClick={onSelect ? handleCell(severity, status) : undefined}
                    title={onSelect ? `filter ${severity} × ${status}` : undefined}
                  >
                    {n}
                  </td>
                );
              })}
              <td className="pat-matrix-cell pat-matrix-total">{matrix.severityTotals[severity] ?? 0}</td>
            </tr>
          ))}
          <tr>
            <th scope="row" className="pat-matrix-total">
              total
            </th>
            {matrix.statuses.map((status) => (
              <td key={`sum:${status}`} className="pat-matrix-cell pat-matrix-total">
                {matrix.statusTotals[status] ?? 0}
              </td>
            ))}
            <td className="pat-matrix-cell pat-matrix-total pat-matrix-grand">{matrix.total}</td>
          </tr>
        </tbody>
      </table>
      <div className="pat-matrix-note">
        {`missing/blank fields land in ${UNKNOWN} (never guessed)`}
        {matrix.skipped > 0 ? ` · ${matrix.skipped} non-object rows skipped` : ""}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4. CsvExportButton
// ---------------------------------------------------------------------------

export interface CsvExportButtonProps {
  /** Download name; `.csv` is appended when missing. Path separators are stripped. */
  filename: string;
  headers: readonly string[];
  /** The rows as rendered by the page (backend values, client-projected). */
  rows: ReadonlyArray<readonly unknown[]>;
  label?: string;
  /** BOM so Excel opens UTF-8 (Persian/German news titles) correctly. Default true. */
  bom?: boolean;
  /** Non-destructive preview: show the first CSV lines instead of downloading. */
  preview?: boolean;
  className?: string;
}

const PREVIEW_LINES = 4;

function safeFilename(name: string): string {
  const cleaned = String(name ?? "")
    .replace(/[\\/]/g, "_")
    .replace(/[\u0000-\u001F\u007F]/g, "")
    .trim();
  const base = cleaned === "" ? "export" : cleaned;
  return /\.csv$/i.test(base) ? base : `${base}.csv`;
}

/**
 * Export the CURRENTLY RENDERED rows as RFC-4180 CSV.
 *
 * Every cell passes through escapeCsvCell, so quotes, embedded commas, newlines
 * and formula-leading values (=HYPERLINK, +…, -…, @…) are neutralized before the
 * file is built. Blob download only — no fetch, no server round-trip.
 */
export function CsvExportButton({
  filename,
  headers,
  rows,
  label = "export CSV",
  bom = true,
  preview = false,
  className,
}: CsvExportButtonProps): JSX.Element {
  const headerList = Array.isArray(headers) ? headers : NO_ROWS;
  const rowList = Array.isArray(rows) ? rows : NO_ROWS;
  const empty = rowList.length === 0;
  const doc = useMemo(() => buildCsv(headerList, rowList), [headerList, rowList]);

  const onClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    void event;
    if (empty) return;
    const url = URL.createObjectURL(new Blob([bom ? "\uFEFF" : "", doc], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = safeFilename(filename);
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const firstLines = doc.split("\r\n").slice(0, PREVIEW_LINES).join("\n");

  return (
    <span className="pat-csv">
      <button
        type="button"
        className={`btn small pat-csv-btn ${className ?? ""}`.trim()}
        disabled={empty}
        aria-disabled={empty}
        aria-label={empty ? `${label}: nothing rendered to export` : `${label} (${rowList.length} rows)`}
        onClick={onClick}
        title={
          empty
            ? "No rows rendered on this page — nothing to export (export never fetches a different page)"
            : `${rowList.length} rendered row${rowList.length === 1 ? "" : "s"} · RFC-4180 quoted, formula-prefixed cells neutralized`
        }
      >
        {label}
      </button>
      {!empty && (
        <span className="pat-chip">{`${rowList.length} rendered row${rowList.length === 1 ? "" : "s"}`}</span>
      )}
      {preview && (
        <pre className="pat-csv-preview" aria-label="CSV preview (escaped text)">
          {firstLines}
          {doc.split("\r\n").length > PREVIEW_LINES ? "\n…" : ""}
        </pre>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 5. PayloadInspector — summary line + tree, the shape both tabs use
// ---------------------------------------------------------------------------

export interface PayloadInspectorProps {
  /** Raw payload (string | object | null) exactly as the backend sent it. */
  payload: unknown;
  /** Row identity shown in the header line, as text only. */
  title?: string | number | null;
  /** Single-line depth-limited summary length. Default 180. */
  summaryMaxLen?: number;
  defaultOpen?: boolean;
  valueMaxLen?: number;
  /** Trailing slot for row actions (e.g. a per-row CSV export). */
  right?: ReactNode;
}

/** Collapsible payload row: `payloadSummary()` line, expanded = JsonTreeViewer. */
export function PayloadInspector({
  payload,
  title,
  summaryMaxLen = 180,
  defaultOpen = false,
  valueMaxLen = 120,
  right,
}: PayloadInspectorProps): JSX.Element {
  const [open, setOpen] = useState(defaultOpen);
  const summary = useMemo(() => payloadSummary(payload, summaryMaxLen, 3), [payload, summaryMaxLen]);
  const isEmpty = payload === null || payload === undefined || payload === "";

  return (
    <div className={`pat-inspector ${open ? "pat-inspector-open" : ""}`}>
      <div className="pat-inspector-head">
        <button
          type="button"
          className="pat-json-toggle pat-inspector-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          title={open ? "collapse payload tree" : "expand payload tree"}
        >
          {open ? "▾" : "▸"}
        </button>
        {title !== null && title !== undefined && <span className="pat-inspector-id">{String(title)}</span>}
        <code className="pat-inspector-summary" title={summary}>
          {isEmpty ? emDash : summary}
        </code>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>{right}</span>
      </div>
      {open && <JsonTreeViewer payload={payload} valueMaxLen={valueMaxLen} defaultExpandDepth={1} />}
    </div>
  );
}
