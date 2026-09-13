/**
 * accountingApi — account, equity, trades, live accounting (legacy raw JSON).
 *
 * Routes: diagnostics_state_routes.py (/api/account/summary, /api/account/trades,
 * /api/account/growth, /api/live/accounting, /api/live/state) and
 * debug_research_routes.py (/api/account/performance*, /api/account/equity-curve,
 * /api/account/drawdown, /api/account/trades/{ticket}, /api/account/strategies).
 * NOTE: legacy routes return {available:false} when a subsystem is offline —
 * render "unavailable", never zero-fill.
 */

import { getLegacy, toQuery } from "@/core/transport";
import type {
  AccountSummary,
  AccountTradesPage,
  DrawdownSeries,
  EquityCurvePoint,
  LiveAccounting,
  LiveState,
  PerformanceBundle,
} from "@/types/features";

export const accountingApi = {
  summary: (signal?: AbortSignal): Promise<AccountSummary> =>
    getLegacy<AccountSummary>("/api/account/summary", signal),

  trades: (
    params: { limit?: number; offset?: number; status?: string } = {},
    signal?: AbortSignal,
  ): Promise<AccountTradesPage> => getLegacy<AccountTradesPage>(`/api/account/trades${toQuery({ ...params })}`, signal),

  tradeDetail: (tradeId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/account/trades/${encodeURIComponent(tradeId)}`, signal),

  growth: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/account/growth", signal),

  equityCurve: (params: { days?: number } = {}, signal?: AbortSignal): Promise<{ points: EquityCurvePoint[] }> =>
    getLegacy<{ points: EquityCurvePoint[] }>(`/api/account/equity-curve${toQuery({ ...params })}`, signal),

  drawdown: (params: { days?: number } = {}, signal?: AbortSignal): Promise<DrawdownSeries> =>
    getLegacy<DrawdownSeries>(`/api/account/drawdown${toQuery({ ...params })}`, signal),

  strategies: (signal?: AbortSignal): Promise<{ strategies: Array<Record<string, unknown>> }> =>
    getLegacy<{ strategies: Array<Record<string, unknown>> }>("/api/account/strategies", signal),

  performance: (signal?: AbortSignal): Promise<PerformanceBundle> =>
    getLegacy<PerformanceBundle>("/api/account/performance", signal),

  performanceIntelligence: (signal?: AbortSignal): Promise<PerformanceBundle> =>
    getLegacy<PerformanceBundle>("/api/account/performance/intelligence", signal),

  performanceKind: (kind: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/account/performance/${encodeURIComponent(kind)}`, signal),

  performanceSeries: (kind: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/account/performance/${encodeURIComponent(kind)}/series`, signal),

  liveAccounting: (signal?: AbortSignal): Promise<LiveAccounting> =>
    getLegacy<LiveAccounting>("/api/live/accounting", signal),

  liveState: (signal?: AbortSignal): Promise<LiveState> =>
    getLegacy<LiveState>("/api/live/state", signal),
};
