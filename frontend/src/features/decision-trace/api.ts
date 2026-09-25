/**
 * Decision Trace — typed transport for the observer surface.
 *
 * Endpoints verified in src/nexus_scalp/web/trace_routes.py:
 *  GET    /api/trace/observer            lifecycle + counters
 *  POST   /api/trace/observer/start      activate detailed tracing (UI open)
 *  POST   /api/trace/observer/stop       return to low-overhead path
 *  GET    /api/trace/topology            discovered runtime topology
 *  GET    /api/trace/latency             per-stage p50/p95/p99
 *  GET    /api/trace/integrity           integrity warnings
 *  GET    /api/trace/decisions?limit=    bounded decision feed (newest first)
 *  GET    /api/trace/events              batched event tail + resume; OPTIONAL
 *                                         AND-filters trace_id/stage/status/
 *                                         mode/position_id (trace_routes.py:344)
 *  GET    /api/trace/why/{event_id}      WHY/NEXT forensics (trace_routes.py:407)
 *  GET    /api/trace/bundle/{key}        forensic bundle (trace OR EXEC id)
 *  GET    /api/trace/stream?last_seq=    SSE live stream
 *
 * The query string for /events is built by buildEventsQuery (types.ts) so the
 * filter -> query-param contract is regression-pinned by
 * tests/js/decision_trace_inspectors.test.js.
 *
 * Read-only w.r.t. trading: the observer is a passive in-memory store; the
 * only mutation endpoints are the observer lifecycle (never touch engine
 * behavior — §08 invariant, pinned by the backend integration suite).
 */

import { getLegacy, send } from "@/api/client";
import {
  buildEventsQuery,
  type DecisionRow,
  type DecisionsList,
  type EventFilters,
  type EventsSince,
  type IntegrityReport,
  type LatencyStats,
  type ObserverSnapshot,
  type ObserverSession,
  type TopologySnapshot,
  type TraceBundle,
  type WhyResponse,
} from "./types";

const BASE = "/api/trace";

export const traceApi = {
  observer: (signal?: AbortSignal) =>
    getLegacy<ObserverSnapshot>(`${BASE}/observer`, signal),

  start: () => send<ObserverSession>(`${BASE}/observer/start`),

  stop: () => send<{ status: string }>(`${BASE}/observer/stop`),

  topology: (signal?: AbortSignal) =>
    getLegacy<TopologySnapshot>(`${BASE}/topology`, signal),

  latency: (signal?: AbortSignal) => getLegacy<LatencyStats>(`${BASE}/latency`, signal),

  integrity: (signal?: AbortSignal) =>
    getLegacy<IntegrityReport>(`${BASE}/integrity`, signal),

  decisions: (limit = 50, offset = 0, signal?: AbortSignal) =>
    getLegacy<DecisionsList>(
      `${BASE}/decisions?limit=${limit}&offset=${offset}`,
      signal,
    ),

  /**
   * Batched event tail with explicit resume point. When `filters` carries any
   * non-empty word the backend AND-narrows the page and echoes the active
   * filters back (`filters` key in EventsSince) — an empty result is the
   * honest "no observed events match" answer, never a fabricated page.
   */
  events: (
    lastSeq = 0,
    limit = 1000,
    filters?: EventFilters | null,
    signal?: AbortSignal,
  ) =>
    getLegacy<EventsSince>(
      `${BASE}/events${buildEventsQuery({
        last_seq: lastSeq,
        limit,
        trace_id: filters?.trace_id ?? null,
        stage: filters?.stage ?? null,
        status: filters?.status ?? null,
        mode: filters?.mode ?? null,
        position_id: filters?.position_id ?? null,
      })}`,
      signal,
    ),

  /** WHY + NEXT DESTINATION for one stored event (§37/§38). 404 => ApiError.status 404. */
  why: (eventId: string, signal?: AbortSignal) =>
    getLegacy<WhyResponse>(`${BASE}/why/${encodeURIComponent(eventId)}`, signal),

  bundle: (key: string, signal?: AbortSignal) =>
    getLegacy<TraceBundle>(`${BASE}/bundle/${encodeURIComponent(key)}`, signal),
};

export type { DecisionRow };
