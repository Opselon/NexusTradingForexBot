/**
 * Incidents: application use cases.
 *
 * Reconcile is the only mutation (read-only forensic audit that re-runs
 * probes and updates incident impact/evidence in place — still confirm-
 * guarded because it writes incident records). Every read is a raw legacy
 * envelope; `available:false` renders the backend reason verbatim.
 */

import { incidentsApi, type IncidentFilters } from "./api";
import { commandVerdict, toIncidentVo, type DiagnosticsReconcileDto, type IncidentDto } from "./model";

export const incidentsQueries = {
  list: (f: IncidentFilters, signal?: AbortSignal) => incidentsApi.list({ limit: 200, ...f }, signal),
  detail: (id: string, signal?: AbortSignal) => incidentsApi.detail(id, signal),
  report: (id: string, signal?: AbortSignal) => incidentsApi.report(id, signal),
  zip: (id: string, signal?: AbortSignal) => incidentsApi.zip(id, signal),
  health: (signal?: AbortSignal) => incidentsApi.health(signal),
  search: (q: string, signal?: AbortSignal) => incidentsApi.search(q, signal),
  trace: (q: string, signal?: AbortSignal) => incidentsApi.trace(q, signal),
  lineage: (field: string, ticket: string, signal?: AbortSignal) => incidentsApi.lineage(field, ticket, signal),
  forensics: (kind: string, ticket: string, signal?: AbortSignal) => incidentsApi.forensics(kind, ticket, signal),
};

export const incidentsUseCases = {
  voList: (rows: IncidentDto[]) => rows.map(toIncidentVo),
  reconcile: (): ReturnType<typeof incidentsApi.reconcile> => incidentsApi.reconcile(),
  reconcileVerdict: (res: DiagnosticsReconcileDto): { ok: boolean; message: string } => {
    const v = commandVerdict(res);
    if (!v.ok) return v;
    return {
      ok: true,
      message: `forensic audit complete — discovered ${String(res.incidents_discovered ?? 0)}, reconciled ${String(res.incidents_reconciled ?? 0)}`,
    };
  },
};
