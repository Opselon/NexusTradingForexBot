/**
 * dbApi — database console + hygiene/manage surface (legacy raw JSON).
 *
 * Routes: db_console.py (prefix /api/db/console) and
 * diagnostics_state_routes.py (/api/db/hygiene, /api/db/manage/*,
 * /api/db/status via debug_research_routes.py).
 * SAFETY: every query runs server-side under the backend's read-guard +
 * audit; the UI never holds a DB handle. apikey create/revoke are mutations.
 */

import { getLegacy, send, drop, apiUrl } from "@/core/transport";
import { toQuery } from "@/core/config";
import type {
  DbApiKey,
  DbColumns,
  DbConsoleQueryPayload,
  DbConsoleQueryResult,
  DbDatabases,
  DbHygiene,
  DbManageProgress,
  DbQuick,
  DbRows,
  DbStatus,
  DbTables,
} from "@/types/features";

export interface DbRowsQuery {
  database?: string;
  table: string;
  page?: number;
  page_size?: number;
  order_by?: string;
  desc?: boolean;
  search?: string;
}

export const dbApi = {
  status: (signal?: AbortSignal): Promise<DbStatus> => getLegacy<DbStatus>("/api/db/status", signal),

  hygiene: (signal?: AbortSignal): Promise<DbHygiene> => getLegacy<DbHygiene>("/api/db/hygiene", signal),

  // ------------------------------------------------------------- console
  consoleDatabases: (signal?: AbortSignal): Promise<DbDatabases> =>
    getLegacy<DbDatabases>("/api/db/console/databases", signal),

  consoleTables: (database?: string, signal?: AbortSignal): Promise<DbTables> =>
    getLegacy<DbTables>(`/api/db/console/tables${toQuery({ database })}`, signal),

  consoleColumns: (params: { database?: string; table: string }, signal?: AbortSignal): Promise<DbColumns> =>
    getLegacy<DbColumns>(`/api/db/console/columns${toQuery(params)}`, signal),

  consoleRows: (params: DbRowsQuery, signal?: AbortSignal): Promise<DbRows> =>
    getLegacy<DbRows>(`/api/db/console/rows${toQuery({ ...params })}`, signal),

  consoleQuick: (signal?: AbortSignal): Promise<DbQuick> =>
    getLegacy<DbQuick>("/api/db/console/quick", signal),

  consoleQuery: (payload: DbConsoleQueryPayload): Promise<DbConsoleQueryResult> =>
    send<DbConsoleQueryResult>("/api/db/console/query", payload),

  consoleRefresh: (payload: Record<string, unknown> = {}): Promise<DbConsoleQueryResult> =>
    send<DbConsoleQueryResult>("/api/db/console/refresh", payload),

  consoleApiKeys: (signal?: AbortSignal): Promise<{ apikeys: DbApiKey[] }> =>
    getLegacy<{ apikeys: DbApiKey[] }>("/api/db/console/apikeys", signal),

  consoleCreateApiKey: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/db/console/apikey", payload),

  consoleRevokeApiKey: (name: string): Promise<Record<string, unknown>> =>
    drop<Record<string, unknown>>(`/api/db/console/apikey/${encodeURIComponent(name)}`),

  // --------------------------------------------------------------- manage
  manageProgress: (signal?: AbortSignal): Promise<DbManageProgress> =>
    getLegacy<DbManageProgress>("/api/db/manage/progress", signal),

  manageReport: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/db/manage/report", signal),

  manageValidate: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/db/manage/validate", signal),

  managePreview: (payload: Record<string, unknown>): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/db/manage/preview", payload),

  manageMigrate: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/db/manage/migrate", payload),

  manageBackup: (payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> =>
    send<Record<string, unknown>>("/api/db/manage/backup", payload),
};

/** Incident report download helper (zip endpoint, non-JSON). */
export function incidentZipUrl(incidentId: string): string {
  return apiUrl(`/api/diagnostics/incidents/${encodeURIComponent(incidentId)}/zip`);
}
