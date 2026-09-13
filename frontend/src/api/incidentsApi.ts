/**
 * incidentsApi — diagnostics / incident inventory (legacy raw JSON).
 *
 * Routes (diagnostics_state_routes.py):
 *  GET  /api/diagnostics/health
 *  GET  /api/diagnostics/incidents  (+ /{id}, /{id}/report, /{id}/zip download)
 *  GET  /api/diagnostics/forensics | /lineage | /search | /trace
 *  POST /api/diagnostics/incidents/reconcile
 * Plus release status + generic search. `zip` is a binary download — use
 * dbApi.incidentZipUrl() (or incidentDownloadUrl here) for an <a href>.
 */

import { getLegacy, send } from "@/core/transport";
import { apiUrl, toQuery } from "@/core/config";
import type {
  DiagnosticsHealth,
  IncidentDetail,
  IncidentListResponse,
  LineageResponse,
  SearchResponse,
} from "@/types/features";

const D = "/api/diagnostics";

export const incidentsApi = {
  health: (signal?: AbortSignal): Promise<DiagnosticsHealth> =>
    getLegacy<DiagnosticsHealth>(`${D}/health`, signal),

  incidents: (
    params: { severity?: string; status?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<IncidentListResponse> =>
    getLegacy<IncidentListResponse>(`${D}/incidents${toQuery({ ...params })}`, signal),

  incidentDetail: (incidentId: string, signal?: AbortSignal): Promise<IncidentDetail> =>
    getLegacy<IncidentDetail>(`${D}/incidents/${encodeURIComponent(incidentId)}`, signal),

  incidentReport: (incidentId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${D}/incidents/${encodeURIComponent(incidentId)}/report`, signal),

  /** Absolute URL for a zip download anchor (binary; not fetched as JSON). */
  incidentDownloadUrl: (incidentId: string): string =>
    apiUrl(`${D}/incidents/${encodeURIComponent(incidentId)}/zip`),

  forensics: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`${D}/forensics`, signal),

  lineage: (params: { entity?: string; id?: string } = {}, signal?: AbortSignal): Promise<LineageResponse> =>
    getLegacy<LineageResponse>(`${D}/lineage${toQuery({ ...params })}`, signal),

  search: (q: string, signal?: AbortSignal): Promise<SearchResponse> =>
    getLegacy<SearchResponse>(`${D}/search${toQuery({ q })}`, signal),

  reconcile: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>(`${D}/incidents/reconcile`, payload),

  releaseStatus: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/release/status", signal),
};
