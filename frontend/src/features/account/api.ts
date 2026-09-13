/**
 * features/account/api.ts — typed calls for the accounting context.
 *
 * Lane rule: the shared src/api/accountingApi.ts is being built concurrently
 * by Lane 1, so this feature keeps its own surface over the STABLE exports of
 * @/api/client (getLegacy — /api/account/* and /api/live/accounting are legacy
 * raw-JSON routes, not v1 envelopes).
 */

import { getLegacy } from "@/api/client";
import type {
  AccountEquityCurve,
  AccountIntelResponse,
  AccountPerformance,
  AccountPeriodResponse,
  AccountSeriesResponse,
  AccountStrategiesResponse,
  AccountTradeRow,
  GrowthPoint,
  LiveAccountingResponse,
  PeriodKind,
  TradeForensics,
} from "./types";

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export interface RiskPlanParams {
  equity?: number;
  entry?: number;
  stopLoss?: number;
  riskPct?: number;
}

export const accountApi = {
  /** GET /api/account/performance — canonical live + periods + drawdown + advanced. */
  performance: (signal?: AbortSignal) => getLegacy<AccountPerformance>("/api/account/performance", signal),

  /** GET /api/account/performance/{kind} — one period report + market context. */
  period: (kind: PeriodKind, signal?: AbortSignal) => getLegacy<AccountPeriodResponse>(`/api/account/performance/${kind}`, signal),

  /** GET /api/account/performance/{kind}/series?count — consecutive periods. */
  periodSeries: (kind: PeriodKind, count = 12, signal?: AbortSignal) =>
    getLegacy<AccountSeriesResponse>(`/api/account/performance/${kind}/series${qs({ count })}`, signal),

  /** GET /api/account/performance/intelligence?kind — Performance Intelligence report. */
  intelligence: (kind: PeriodKind = "DAY", signal?: AbortSignal) =>
    getLegacy<AccountIntelResponse>(`/api/account/performance/intelligence${qs({ kind })}`, signal),

  /** GET /api/account/equity-curve — balance/equity/drawdown + cumulative PnL. */
  equityCurve: (lookbackDays?: number, signal?: AbortSignal) =>
    getLegacy<AccountEquityCurve>(`/api/account/equity-curve${qs({ lookback_days: lookbackDays })}`, signal),

  /** GET /api/account/drawdown — canonical drawdown report (one methodology). */
  drawdown: (lookbackDays?: number, signal?: AbortSignal) =>
    getLegacy<Record<string, unknown>>(`/api/account/drawdown${qs({ lookback_days: lookbackDays })}`, signal),

  /** GET /api/account/growth — audit-snapshot growth rows. */
  growth: (signal?: AbortSignal) => getLegacy<GrowthPoint[]>("/api/account/growth", signal),

  /** GET /api/account/strategies — per-strategy contributions joined to intelligence. */
  strategies: (limit = 50, signal?: AbortSignal) => getLegacy<AccountStrategiesResponse>(`/api/account/strategies${qs({ limit })}`, signal),

  /** GET /api/account/trades — closed-trade history (array body). */
  trades: (opts: { limit?: number; offset?: number; status?: string }, signal?: AbortSignal) =>
    getLegacy<AccountTradeRow[]>(`/api/account/trades${qs({ limit: opts.limit ?? 50, offset: opts.offset ?? 0, status: opts.status })}`, signal),

  /** GET /api/account/trades/{ticket} — forensic trace of one closed trade. */
  tradeForensics: (ticket: number | string, signal?: AbortSignal) =>
    getLegacy<TradeForensics>(`/api/account/trades/${encodeURIComponent(String(ticket))}`, signal),

  /** GET /api/live/accounting — RiskEngine single-source live state + risk plan. */
  liveAccounting: (params: RiskPlanParams = {}, signal?: AbortSignal) =>
    getLegacy<LiveAccountingResponse>(
      `/api/live/accounting${qs({ equity: params.equity, entry: params.entry, stop_loss: params.stopLoss, risk_pct: params.riskPct })}`,
      signal,
    ),
};
