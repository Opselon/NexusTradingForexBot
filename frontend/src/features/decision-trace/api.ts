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
 *  GET    /api/trace/events?last_seq=    batched event tail + resume
 *  GET    /api/trace/bundle/{key}        forensic bundle (trace OR EXEC id)
 *  GET    /api/trace/stream?last_seq=    SSE live stream
 *
 * Read-only w.r.t. trading: the observer is a passive in-memory store; the
 * only mutation endpoints are the observer lifecycle (never touch engine
 * behavior — §08 invariant, pinned by the backend integration suite).
 */

import { getLegacy, send } from "@/api/client";
import type {
  DecisionRow,
  DecisionsList,
  EventsSince,
  IntegrityReport,
  LatencyStats,
  ObserverSnapshot,
  ObserverSession,
  TopologySnapshot,
  TraceBundle,
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

  events: (lastSeq = 0, limit = 1000, signal?: AbortSignal) =>
    getLegacy<EventsSince>(`${BASE}/events?last_seq=${lastSeq}&limit=${limit}`, signal),

  bundle: (key: string, signal?: AbortSignal) =>
    getLegacy<TraceBundle>(`${BASE}/bundle/${encodeURIComponent(key)}`, signal),
};

export type { DecisionRow };
