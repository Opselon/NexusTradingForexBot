/**
 * Observability (Log Center) — domain model.
 *
 * Mirrors the backend contracts exactly:
 * - rows come from src/nexus_scalp/database/log_store.py (DatabaseLogEntry's
 *   persisted projection),
 * - pages from web/api_v1/common.py build_page ({items,page,page_size,
 *   has_more} + observability's next_before_id/window),
 * - the envelope itself is unwrapped by getV1.
 */

export type Severity = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export const SEVERITIES: readonly Severity[] = [
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
] as const;

/** A persisted operational event, as returned by the log read plane. */
export interface LogEventDto {
  id: number;
  timestamp: string;
  level: Severity | string;
  provider?: string;
  domain?: string;
  logger_name?: string;
  operation?: string;
  event_name?: string;
  error_message?: string;
  error_code?: string;
  correlation_id?: string;
  full_trace?: string;
  context_json?: Record<string, unknown> | string | null;
  fingerprint?: string;
  first_seen_at?: string;
  last_seen_at?: string;
  repeat_count?: number;
}

/** build_page() + the observability extensions (cursor + window). */
export interface LogPageDto {
  items: LogEventDto[];
  page: number;
  page_size: number;
  has_more: boolean;
  next_before_id?: number | null;
  window?: { since: string; until: string };
}

/** Per-severity counts; `null` = the count itself could not be read. */
export interface LogSummaryDto {
  counts: Partial<Record<Severity, number | null>>;
  total: number | null;
  window: { since: string; until: string };
  generated_at: string;
}

export interface LogCorrelationDto {
  correlation_id: string;
  events: LogEventDto[];
  note?: string;
}

/** Active filters; empty string = filter off. */
export interface LogQuery {
  severity: string;
  component: string;
  event: string;
  correlationId: string;
  text: string;
  since: string;
  until: string;
}

export const EMPTY_QUERY: LogQuery = {
  severity: "",
  component: "",
  event: "",
  correlationId: "",
  text: "",
  since: "",
  until: "",
};

export type LogQueryKind =
  | "severity"
  | "component"
  | "event"
  | "correlationId"
  | "text"
  | "since"
  | "until";

export function updateQuery(q: LogQuery, kind: LogQueryKind, value: string): LogQuery {
  const next = { ...q, [kind]: value } as LogQuery;
  if (kind === "since" && next.until && next.since >= next.until) {
    // The backend rejects an inverted window; nudging `until` forward keeps
    // the UI out of a guaranteed-422 state while the user types.
    next.until = "";
  }
  return next;
}

export function isQueryActive(q: LogQuery): boolean {
  return (
    q.severity !== "" ||
    q.component !== "" ||
    q.event !== "" ||
    q.correlationId !== "" ||
    q.text !== "" ||
    q.since !== "" ||
    q.until !== ""
  );
}

/** Default window: the last 24h (matches the server-side default). */
export function defaultWindow(): LogQuery {
  const until = new Date();
  const since = new Date(until.getTime() - 24 * 60 * 60 * 1000);
  return { ...EMPTY_QUERY, since: since.toISOString(), until: until.toISOString() };
}

export type LoadState = "idle" | "loading" | "ready" | "error" | "denied";

/** UI affordances derived from a row (kept out of the component for tests). */
export function eventTone(level: string): "info" | "warn" | "error" | "critical" | "debug" {
  switch (String(level).toUpperCase()) {
    case "CRITICAL":
      return "critical";
    case "ERROR":
      return "error";
    case "WARNING":
      return "warn";
    case "DEBUG":
    case "TRACE":
      return "debug";
    default:
      return "info";
  }
}

export function parseContext(raw: LogEventDto["context_json"]): Record<string, unknown> | null {
  if (raw == null) return null;
  if (typeof raw === "object") return raw as Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function formatTimestamp(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
