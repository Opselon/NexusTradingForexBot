/**
 * positionsApi — open positions, pending orders, ledger history.
 *
 *  GET /api/v1/positions            — real adapter snapshot (PositionSnapshot)
 *  GET /api/v1/positions/history    — ledger trades (broker-first, ledger fallback)
 *  GET /api/v1/execution/history    — paginated audit_executions
 *  GET /api/mt5/status              — includes pending orders snapshot
 */

import { getV1, getLegacy } from "./client";
import type { Position, OrderRow, AuditLedgerRow, ExecutionHistoryRow, V1Page } from "@/types/domain";

interface RawPositionEnvelope {
  positions: Position[];
  count: number;
}

export const positionsApi = {
  openPositions: (signal?: AbortSignal): Promise<RawPositionEnvelope> =>
    getV1<RawPositionEnvelope>("/api/v1/positions", signal),

  ledgerHistory: (
    params: { limit?: number; offset?: number; status?: string } = {},
    signal?: AbortSignal,
  ): Promise<AuditLedgerRow[]> => {
    const q = new URLSearchParams();
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    if (params.status) q.set("status", params.status);
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return getLegacy<AuditLedgerRow[]>(`/api/account/trades${suffix}`, signal);
  },

  executionHistory: (
    params: { page?: number; page_size?: number; status?: string } = {},
    signal?: AbortSignal,
  ): Promise<V1Page<ExecutionHistoryRow>> => {
    const q = new URLSearchParams();
    q.set("page", String(params.page ?? 1));
    q.set("page_size", String(params.page_size ?? 50));
    if (params.status) q.set("status", params.status);
    return getV1<V1Page<ExecutionHistoryRow>>(`/api/v1/execution/history?${q.toString()}`, signal);
  },

  /** Pending orders come bundled with the MT5 status payload. */
  pendingOrders: (signal?: AbortSignal): Promise<OrderRow[]> =>
    getLegacy<{ orders?: OrderRow[] }>("/api/mt5/status", signal).then((r) => r.orders ?? []),
};
