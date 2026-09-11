/**
 * auditApi — audit viewer data. All reads go through the backend audit layer
 * (AuditRepository / IncidentStore) — React NEVER touches SQLite.
 *
 *  GET /api/v1/audit/events          — audit_ledger tail (status filter, page)
 *  GET /api/v1/observability/events  — audit_events tail (type filter, page)
 *  GET /api/v1/incidents             — paginated incident inventory
 *  GET /api/v1/database/status       — db presence/size/table count (metadata)
 *  GET /api/v1/database/integrity    — quick_check + bounded row counts
 */

import { getV1 } from "./client";
import type {
  AuditEventRow,
  AuditLedgerRow,
  IncidentRow,
  V1Page,
} from "@/types/domain";

export const auditApi = {
  ledgerEvents: (
    params: { status?: string; page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<AuditLedgerRow>> => {
    const q = new URLSearchParams();
    q.set("page", String(params.page ?? 1));
    q.set("page_size", String(params.page_size ?? 50));
    if (params.status) q.set("status", params.status);
    return getV1<V1Page<AuditLedgerRow>>(`/api/v1/audit/events?${q.toString()}`, signal);
  },

  systemEvents: (
    params: { eventType?: string; page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<AuditEventRow>> => {
    const q = new URLSearchParams();
    q.set("page", String(params.page ?? 1));
    q.set("page_size", String(params.page_size ?? 50));
    if (params.eventType) q.set("event_type", params.eventType);
    return getV1<V1Page<AuditEventRow>>(`/api/v1/observability/events?${q.toString()}`, signal);
  },

  incidents: (
    params: { severity?: string; status?: string; page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<IncidentRow>> => {
    const q = new URLSearchParams();
    q.set("page", String(params.page ?? 1));
    q.set("page_size", String(params.page_size ?? 50));
    if (params.severity) q.set("severity", params.severity);
    if (params.status) q.set("status", params.status);
    return getV1<V1Page<IncidentRow>>(`/api/v1/incidents?${q.toString()}`, signal);
  },

  databaseStatus: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getV1<Record<string, unknown>>("/api/v1/database/status", signal),

  databaseIntegrity: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getV1<Record<string, unknown>>("/api/v1/database/integrity", signal),
};
