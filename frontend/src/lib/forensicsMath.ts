/**
 * forensicsMath — pure audit/forensics math for the pro console tools.
 *
 * Hard rules for this module:
 *  - Pure: no fetching, no DOM, no React, no module state, no Date.now() reads
 *    that could make a render non-deterministic between passes.
 *  - Parse-safe: a timestamp that cannot be understood is SKIPPED and counted,
 *    never guessed, never coerced to 0, never silently reordered.
 *  - Hostile-input tolerant: null/undefined/NaN/circular/huge payloads all
 *    produce a bounded, deterministic answer instead of throwing.
 *  - Erasable TypeScript only (interfaces + type aliases), so the module can be
 *    imported directly by the node:test suite without a build step.
 */

/** Bucket used when a severity/status field is missing, empty or untypeable. */
export const UNKNOWN = "unknown";

/** Canonical row order for severities; anything else is appended alphabetically. */
const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;

/** Canonical column order for incident statuses; unknown codes sort alphabetically. */
const STATUS_ORDER = [
  "OPEN",
  "INVESTIGATING",
  "ACKNOWLEDGED",
  "MITIGATED",
  "RESOLVED",
  "CLOSED",
  "IGNORED",
] as const;

/** How many keys/items a single summary container renders before collapsing. */
const SUMMARY_CHILD_LIMIT = 50;

/** Longest string rendered inline by payloadSummary before an ellipsis. */
const SUMMARY_STRING_CAP = 64;

// ---------------------------------------------------------------------------
// Structural input types — deliberately loose so backend rows
// (AuditEventRow / IncidentRow) satisfy them without importing page code.
// ---------------------------------------------------------------------------

export interface EventLike {
  created_at?: string | number | null;
  event_type?: string | null;
}

export interface IncidentLike {
  severity?: string | null;
  status?: string | null;
  title?: string | null;
}

// ---------------------------------------------------------------------------
// Timestamp parsing
// ---------------------------------------------------------------------------

/**
 * Parse a backend timestamp into epoch milliseconds.
 *
 * Accepted: ISO/HTTP strings (Date.parse), epoch seconds and epoch
 * milliseconds (numbers or numeric strings; the 1e12 split distinguishes them
 * the same way lib/format.ts does). Rejected — returns null — anything NaN,
 * Infinity, boolean, object, or blank. Epoch 0 is a VALID result, so callers
 * must compare with `=== null`, never with truthiness.
 *
 * Note: a timezone-less string is interpreted in the runtime's local zone
 * (Date.parse semantics); gaps and offsets are deltas, so the zone cancels.
 */
export function parseTimestampMs(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    const ms = Math.abs(value) > 1e12 ? value : value * 1000;
    return Number.isFinite(ms) ? ms : null;
  }
  if (typeof value === "bigint") {
    const asNumber = Number(value);
    return Number.isFinite(asNumber) ? parseTimestampMs(asNumber) : null;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return parseTimestampMs(Number(trimmed));
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

// ---------------------------------------------------------------------------
// Severity x status matrix
// ---------------------------------------------------------------------------

export interface SeverityMatrixResult {
  /** Row labels (normalized severities, "unknown" last when present). */
  severities: string[];
  /** Column labels (normalized statuses, "unknown" last when present). */
  statuses: string[];
  /** counts[severity][status] — every cell exists, zero-filled. */
  counts: Record<string, Record<string, number>>;
  severityTotals: Record<string, number>;
  statusTotals: Record<string, number>;
  /** Rows actually counted (incidents with neither field still count once). */
  total: number;
  /** Rows dropped because the element itself was not an object. */
  skipped: number;
}

/** Normalize a categorical value into a comparable bucket label. */
export function normalizeBucket(value: unknown): string {
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : UNKNOWN;
  }
  if (typeof value !== "string") return UNKNOWN;
  const s = value.trim().toUpperCase();
  return s === "" ? UNKNOWN : s;
}

function orderBuckets(observed: ReadonlySet<string>, canonical: readonly string[]): string[] {
  const head = canonical.filter((c) => observed.has(c));
  const rest = [...observed]
    .filter((o) => o !== UNKNOWN && !head.includes(o))
    .sort();
  return observed.has(UNKNOWN) ? [...head, ...rest, UNKNOWN] : [...head, ...rest];
}

/**
 * Count incidents by severity x status. Missing / empty / non-string fields
 * land in the "unknown" bucket rather than being guessed or dropped, so the
 * matrix total always equals the number of incidents examined.
 */
export function severityMatrix(
  incidents: readonly IncidentLike[] | null | undefined,
): SeverityMatrixResult {
  const sevSeen = new Set<string>();
  const statSeen = new Set<string>();
  const raw = new Map<string, Map<string, number>>();
  let total = 0;
  let skipped = 0;

  const list = Array.isArray(incidents) ? incidents : [];
  for (const incident of list) {
    if (incident === null || typeof incident !== "object" || Array.isArray(incident)) {
      skipped += 1;
      continue;
    }
    const severity = normalizeBucket((incident as IncidentLike).severity);
    const status = normalizeBucket((incident as IncidentLike).status);
    sevSeen.add(severity);
    statSeen.add(status);
    let row = raw.get(severity);
    if (!row) {
      row = new Map<string, number>();
      raw.set(severity, row);
    }
    row.set(status, (row.get(status) ?? 0) + 1);
    total += 1;
  }

  const severities = orderBuckets(sevSeen, SEVERITY_ORDER);
  const statuses = orderBuckets(statSeen, STATUS_ORDER);
  const counts: Record<string, Record<string, number>> = {};
  const severityTotals: Record<string, number> = {};
  const statusTotals: Record<string, number> = {};
  for (const status of statuses) statusTotals[status] = 0;

  for (const severity of severities) {
    const rowMap = raw.get(severity);
    const cells: Record<string, number> = {};
    let rowTotal = 0;
    for (const status of statuses) {
      const n = rowMap?.get(status) ?? 0;
      cells[status] = n;
      rowTotal += n;
      statusTotals[status] = (statusTotals[status] ?? 0) + n;
    }
    counts[severity] = cells;
    severityTotals[severity] = rowTotal;
  }

  return { severities, statuses, counts, severityTotals, statusTotals, total, skipped };
}

// ---------------------------------------------------------------------------
// Event gaps
// ---------------------------------------------------------------------------

export interface EventGap {
  /** Index in the input array of the row that ENDS the quiet period. */
  index: number;
  /** Index of the previous row whose timestamp parsed (the gap's other end). */
  previousIndex: number;
  /** Absolute delta in seconds (ordering-agnostic: backend tails are newest-first). */
  deltaSec: number;
  previousCreatedAt: unknown;
  createdAt: unknown;
}

/**
 * Indices where the wall-clock delta between consecutive parseable events
 * exceeds `thresholdSec`. Rows with unparseable timestamps are skipped (never
 * guessed); the delta is then measured across the skip and the gap is still
 * reported between the two good neighbours. Returns [] for an invalid
 * threshold so a bad filter value can never crash a render.
 */
export function eventGaps(
  rows: readonly EventLike[] | null | undefined,
  thresholdSec: number,
): EventGap[] {
  const gaps: EventGap[] = [];
  if (!Number.isFinite(thresholdSec) || thresholdSec <= 0) return gaps;
  const list = Array.isArray(rows) ? rows : [];

  let prevIndex = -1;
  let prevMs = 0;
  for (let i = 0; i < list.length; i += 1) {
    const row = list[i];
    const createdAt = row && typeof row === "object" ? (row as EventLike).created_at : undefined;
    const ms = parseTimestampMs(createdAt);
    if (ms === null) continue;
    if (prevIndex >= 0) {
      const deltaSec = Math.abs(ms - prevMs) / 1000;
      if (deltaSec > thresholdSec) {
        gaps.push({
          index: i,
          previousIndex: prevIndex,
          deltaSec,
          previousCreatedAt: list[prevIndex]?.created_at,
          createdAt,
        });
      }
    }
    prevIndex = i;
    prevMs = ms;
  }
  return gaps;
}

// ---------------------------------------------------------------------------
// Payload summary
// ---------------------------------------------------------------------------

function renderSummaryString(value: string): string {
  const cleaned = value.replace(/[\u0000-\u001F\u007F]/g, " ");
  const clipped = cleaned.length > SUMMARY_STRING_CAP ? `${cleaned.slice(0, SUMMARY_STRING_CAP - 1)}…` : cleaned;
  return `"${clipped.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function renderSummaryValue(value: unknown, depth: number, maxDepth: number, seen: WeakSet<object>): string {
  if (value === null) return "null";
  switch (typeof value) {
    case "undefined":
      return "undefined";
    case "string":
      return renderSummaryString(value);
    case "number":
      return Number.isFinite(value) ? String(value) : String(value); // "NaN" / "Infinity" stay visible
    case "bigint":
      return `${value.toString()}n`;
    case "boolean":
      return value ? "true" : "false";
    case "function":
      return "[fn]";
    case "symbol":
      return String(value);
    default:
      break;
  }

  const obj = value as object;
  if (seen.has(obj)) return "[circular]";
  const isArray = Array.isArray(value);
  const size = isArray
    ? (value as unknown[]).length
    : Object.keys(value as Record<string, unknown>).length;

  if (depth >= maxDepth) {
    return isArray ? `[…${size} items]` : `{…${size} keys}`;
  }

  seen.add(obj);
  const parts: string[] = [];
  if (isArray) {
    const items = value as unknown[];
    for (let i = 0; i < Math.min(items.length, SUMMARY_CHILD_LIMIT); i += 1) {
      parts.push(renderSummaryValue(items[i], depth + 1, maxDepth, seen));
    }
    if (items.length > SUMMARY_CHILD_LIMIT) parts.push(`…${items.length - SUMMARY_CHILD_LIMIT} more`);
    seen.delete(obj);
    return `[${parts.join(", ")}]`;
  }

  const entries = Object.entries(value as Record<string, unknown>);
  for (let i = 0; i < Math.min(entries.length, SUMMARY_CHILD_LIMIT); i += 1) {
    const [k, v] = entries[i] as [string, unknown];
    parts.push(`${k}: ${renderSummaryValue(v, depth + 1, maxDepth, seen)}`);
  }
  if (entries.length > SUMMARY_CHILD_LIMIT) parts.push(`…${entries.length - SUMMARY_CHILD_LIMIT} more`);
  seen.delete(obj);
  return `{${parts.join(", ")}}`;
}

/**
 * Depth-limited, single-line summary of an arbitrary payload.
 *
 * Control characters are flattened to spaces, long strings and deep containers
 * collapse to bracketed markers, cycles become "[circular]", and the result is
 * hard-clamped to `maxLen` characters (last char becomes the "…" marker). The
 * output is plain text for a React child — nothing here is HTML-ready.
 */
export function payloadSummary(payload: unknown, maxLen = 160, maxDepth = 3): string {
  const limit = Number.isFinite(maxLen) ? Math.max(1, Math.floor(maxLen)) : 1;
  const depth = Number.isFinite(maxDepth) ? Math.max(1, Math.floor(maxDepth)) : 1;
  let text: string;
  try {
    text = renderSummaryValue(payload, 0, depth, new WeakSet<object>());
  } catch {
    text = "[unsummarizable]";
  }
  text = text.replace(/[\u0000-\u001F\u007F]/g, " ");
  if (text.length <= limit) return text;
  if (limit === 1) return "…";
  return `${text.slice(0, limit - 1)}…`;
}

// ---------------------------------------------------------------------------
// Timeline points
// ---------------------------------------------------------------------------

export interface TimelinePoint {
  /** Index into the input array (stable identity for keys/tooltips). */
  index: number;
  /** Milliseconds after the earliest parseable timestamp (>= 0). */
  offsetMs: number;
  /** Absolute epoch milliseconds. */
  timestampMs: number;
}

export interface TimelineResult {
  points: TimelinePoint[];
  /** spanMs = last-minus-first of parseable timestamps (0 when < 2 points). */
  spanMs: number;
  /** Largest delta between two consecutive parseable points (input order). */
  maxGapMs: number;
  /** [previousIndex, index] bracketing maxGapMs, or null when undefined. */
  maxGapPair: [number, number] | null;
  /** Rows whose created_at could not be parsed (skipped, not guessed). */
  skipped: number;
  /** Raw array length examined (includes non-object entries). */
  examined: number;
}

/**
 * Project event rows onto a relative-millisecond axis.
 *
 * Unparseable timestamps are counted in `skipped` and excluded from `points`,
 * so the strip never invents positions for data the backend did not supply.
 */
export function timelinePoints(
  rows: readonly EventLike[] | null | undefined,
): TimelineResult {
  const list = Array.isArray(rows) ? rows : [];
  const stamps: { index: number; ms: number }[] = [];
  let skipped = 0;

  for (let i = 0; i < list.length; i += 1) {
    const row = list[i];
    const createdAt = row && typeof row === "object" ? (row as EventLike).created_at : undefined;
    const ms = parseTimestampMs(createdAt);
    if (ms === null) {
      skipped += 1;
      continue;
    }
    stamps.push({ index: i, ms });
  }

  if (stamps.length === 0) {
    return {
      points: [],
      spanMs: 0,
      maxGapMs: 0,
      maxGapPair: null,
      skipped,
      examined: list.length,
    };
  }

  let minMs = stamps[0]!.ms;
  let maxMs = stamps[0]!.ms;
  for (const s of stamps) {
    if (s.ms < minMs) minMs = s.ms;
    if (s.ms > maxMs) maxMs = s.ms;
  }

  let maxGapMs = 0;
  let maxGapPair: [number, number] | null = null;
  for (let i = 1; i < stamps.length; i += 1) {
    const prev = stamps[i - 1]!;
    const cur = stamps[i]!;
    const delta = Math.abs(cur.ms - prev.ms);
    if (delta > maxGapMs) {
      maxGapMs = delta;
      maxGapPair = [prev.index, cur.index];
    }
  }

  return {
    points: stamps.map((s) => ({
      index: s.index,
      offsetMs: s.ms - minMs,
      timestampMs: s.ms,
    })),
    spanMs: maxMs - minMs,
    maxGapMs,
    maxGapPair,
    skipped,
    examined: list.length,
  };
}

// ---------------------------------------------------------------------------
// CSV (RFC-4180 quoting + formula-injection neutralization)
// ---------------------------------------------------------------------------

const FORMULA_LEADERS = ["=", "+", "-", "@", "\t", "\r"];

function stringifyCellInput(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "[unserializable]";
  }
}

/**
 * Render ONE CSV field, RFC-4180 quoted and safe to open in a spreadsheet.
 *
 *  - every field is wrapped in double quotes, internal quotes are doubled
 *  - CR/LF (and the other C0 controls) are stripped, so a value can never
 *    forge a row break inside or outside the quotes
 *  - a value whose first char is a formula trigger (= + - @, or the tab/CR that
 *    Excel honours after stripping) is prefixed with ' so it stays text
 *  - null/undefined/NaN become empty cells rather than the strings "null"/"NaN"
 */
export function escapeCsvCell(value: unknown): string {
  let s = stringifyCellInput(value);
  s = s.replace(/[\u0000-\u001F\u007F\u2028\u2029]/g, "");
  const lead = s.charAt(0);
  if (lead !== "" && FORMULA_LEADERS.includes(lead)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

export interface BuildCsvOptions {
  /** Row/record separator. RFC-4180 says CRLF; keep it configurable for diffs. */
  eol?: string;
  /** Trailing eol after the last record (RFC-4180 recommends it). Default true. */
  trailingNewline?: boolean;
}

/**
 * Build a CSV document from already-rendered header labels and row cells.
 * Cells may be any type — each goes through escapeCsvCell, so nothing that
 * reaches the file bypasses the neutralization path.
 */
export function buildCsv(
  headers: readonly string[],
  rows: ReadonlyArray<readonly unknown[]>,
  options: BuildCsvOptions = {},
): string {
  const eol = options.eol ?? "\r\n";
  const trailing = options.trailingNewline ?? true;
  const headerCells = Array.isArray(headers) ? headers : [];
  const lines: string[] = [headerCells.map((h) => escapeCsvCell(h)).join(",")];
  const list = Array.isArray(rows) ? rows : [];
  for (const row of list) {
    const cells = Array.isArray(row) ? row : [];
    lines.push(cells.map((cell) => escapeCsvCell(cell)).join(","));
  }
  return trailing ? `${lines.join(eol)}${eol}` : lines.join(eol);
}
