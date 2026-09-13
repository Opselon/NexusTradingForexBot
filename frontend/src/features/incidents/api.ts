/**
 * Incidents: typed transport surface over @/api/client.
 *
 * Routes verified in src/nexus_scalp/web/diagnostics_state_routes.py:
 *   GET  /api/diagnostics/incidents (+filters)       list + counts
 *   GET  /api/diagnostics/incidents/{id}             detail
 *   GET  /api/diagnostics/incidents/{id}/report      JSON+MD export
 *   GET  /api/diagnostics/incidents/{id}/zip         evidence zip (path+size)
 *   POST /api/diagnostics/incidents/reconcile        forensic audit (guarded)
 *   GET  /api/diagnostics/health                     counts + worker + recurring
 *   GET  /api/diagnostics/search?q=                  bounded search
 *   GET  /api/diagnostics/trace?query=               one-click trace
 *   GET  /api/diagnostics/lineage?field=&ticket=     value lineage
 *   GET  /api/diagnostics/forensics?kind=&ticket=    forensic probes
 */

import { getLegacy, send } from "@/api/client";
import type {
  DiagnosticsForensicsDto,
  DiagnosticsHealthDto,
  DiagnosticsLineageDto,
  DiagnosticsReconcileDto,
  DiagnosticsSearchDto,
  DiagnosticsTraceDto,
  IncidentDetailDto,
  IncidentListDto,
  IncidentReportDto,
  IncidentZipDto,
} from "./model";

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export interface IncidentFilters {
  status?: string;
  severity?: string;
  category?: string;
  component?: string;
  limit?: number;
  offset?: number;
}

export const incidentsApi = {
  list: (f: IncidentFilters, signal?: AbortSignal): Promise<IncidentListDto> =>
    getLegacy<IncidentListDto>(`/api/diagnostics/incidents${qs({ ...f })}`, signal),

  detail: (id: string, signal?: AbortSignal): Promise<IncidentDetailDto> =>
    getLegacy<IncidentDetailDto>(`/api/diagnostics/incidents/${encodeURIComponent(id)}`, signal),

  report: (id: string, signal?: AbortSignal): Promise<IncidentReportDto> =>
    getLegacy<IncidentReportDto>(`/api/diagnostics/incidents/${encodeURIComponent(id)}/report`, signal),

  zip: (id: string, signal?: AbortSignal): Promise<IncidentZipDto> =>
    getLegacy<IncidentZipDto>(`/api/diagnostics/incidents/${encodeURIComponent(id)}/zip`, signal),

  health: (signal?: AbortSignal): Promise<DiagnosticsHealthDto> =>
    getLegacy<DiagnosticsHealthDto>("/api/diagnostics/health", signal),

  search: (query: string, signal?: AbortSignal): Promise<DiagnosticsSearchDto> =>
    getLegacy<DiagnosticsSearchDto>(`/api/diagnostics/search${qs({ query, limit: 50 })}`, signal),

  trace: (query: string, signal?: AbortSignal): Promise<DiagnosticsTraceDto> =>
    getLegacy<DiagnosticsTraceDto>(`/api/diagnostics/trace${qs({ query })}`, signal),

  lineage: (field: string, ticket: string, signal?: AbortSignal): Promise<DiagnosticsLineageDto> =>
    getLegacy<DiagnosticsLineageDto>(`/api/diagnostics/lineage${qs({ field, ticket })}`, signal),

  forensics: (kind: string, ticket: string, signal?: AbortSignal): Promise<DiagnosticsForensicsDto> =>
    getLegacy<DiagnosticsForensicsDto>(`/api/diagnostics/forensics${qs({ kind, ticket })}`, signal),

  reconcile: (): Promise<DiagnosticsReconcileDto> => send<DiagnosticsReconcileDto>("/api/diagnostics/incidents/reconcile", {}),
};

/** Evidence-zip download href (the zip route answers a path + size; a plain
 *  href is the legacy parity behaviour — the backend serves the bundle). */
export const incidentZipHref = (id: string): string => `/api/diagnostics/incidents/${encodeURIComponent(id)}/zip`;
