/**
 * Observability (Log Center) — typed transport over the v1 log read plane.
 *
 * Routes (src/nexus_scalp/web/api_v1/observability.py):
 *   GET /api/v1/observability/logs                 search (filters + keyset)
 *   GET /api/v1/observability/logs/summary         per-severity counts
 *   GET /api/v1/observability/logs/export          bounded JSON/CSV export
 *   GET /api/v1/observability/logs/{id}            detail (trace + context)
 *   GET /api/v1/observability/logs/{id}/correlate  correlation drill-down
 *
 * All v1 routes answer in the `{data, meta}` envelope; `getV1` unwraps it.
 */

import { getV1 } from "@/api/client";
import type {
  LogCorrelationDto,
  LogEventDto,
  LogPageDto,
  LogQuery,
  LogSummaryDto,
} from "./model";

const qs = (params: Record<string, string | number | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
};

/** Build the shared filter querystring so search/export cannot diverge. */
export function logQueryString(q: LogQuery): string {
  return qs({
    severity: q.severity,
    component: q.component,
    event: q.event,
    correlation_id: q.correlationId,
    q: q.text,
    since: q.since,
    until: q.until,
  });
}

export const logsApi = {
  search: (q: LogQuery, pageSize = 50, beforeId?: number, signal?: AbortSignal) =>
    getV1<LogPageDto>(
      `/api/v1/observability/logs${qs({
        severity: q.severity,
        component: q.component,
        event: q.event,
        correlation_id: q.correlationId,
        q: q.text,
        since: q.since,
        until: q.until,
        page_size: pageSize,
        before_id: beforeId,
      })}`,
      signal,
    ),

  summary: (q: Pick<LogQuery, "since" | "until">, signal?: AbortSignal) =>
    getV1<LogSummaryDto>(
      `/api/v1/observability/logs/summary${qs({ since: q.since, until: q.until })}`,
      signal,
    ),

  detail: (id: number, signal?: AbortSignal) =>
    getV1<LogEventDto>(`/api/v1/observability/logs/${id}`, signal),

  correlate: (id: number, signal?: AbortSignal) =>
    getV1<LogCorrelationDto>(`/api/v1/observability/logs/${id}/correlate`, signal),

  /**
   * Absolute export URL. The export endpoint streams a file, so the browser
   * must fetch it directly (a fetch() would have to buffer the whole body and
   * loses the Content-Disposition filename).
   */
  exportUrl: (q: LogQuery, format: "json" | "csv"): string =>
    `/api/v1/observability/logs/export${qs({
      format,
      severity: q.severity,
      component: q.component,
      q: q.text,
      since: q.since,
      until: q.until,
    })}`,
};
