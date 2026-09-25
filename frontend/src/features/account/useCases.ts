/**
 * Account use cases — application services over features/account/api.
 *
 * `available:false` on the accounting endpoints means the accounting core is
 * not mounted — that is raised as a typed error so sections render an honest
 * unavailable state (with retry) instead of zeros (BUG-020 invariant).
 */

import { accountApi, type RiskPlanParams } from "./api";
import { useI18n } from "@/stores/i18nStore";
import { toGrowthPoints, toTradeVM, type TradeVM } from "./model";
import type { GrowthPoint } from "./types";
import type {
  AccountEquityCurve,
  AccountPerformance,
  AccountStrategiesResponse,
  LiveAccountingResponse,
  PeriodKind,
} from "./types";

export class AccountingUnavailableError extends Error {
  constructor(reason: string) {
    super(useI18n.getState().t("account.usecase.unavailable", "Accounting core unavailable ({reason}) — no numbers are shown because none exist server-side.", { reason }));
    this.name = "AccountingUnavailableError";
  }
}

export interface TradesPage {
  trades: TradeVM[];
  limit: number;
  offset: number;
}

export const accountQueries = {
  performance: (signal?: AbortSignal): Promise<AccountPerformance> =>
    accountApi.performance(signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError(res.reason ?? "ENGINE_UNAVAILABLE");
      return res;
    }),

  period: (kind: PeriodKind, signal?: AbortSignal) =>
    accountApi.period(kind, signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError(res.reason ?? "ENGINE_UNAVAILABLE");
      return res;
    }),

  periodSeries: (kind: PeriodKind, count: number, signal?: AbortSignal) =>
    accountApi.periodSeries(kind, count, signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError("SERIES_UNAVAILABLE");
      return res.periods ?? [];
    }),

  intelligence: (kind: PeriodKind, signal?: AbortSignal) =>
    accountApi.intelligence(kind, signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError(res.reason ?? "INTELLIGENCE_UNAVAILABLE");
      return res;
    }),

  equityCurve: (lookbackDays: number | undefined, signal?: AbortSignal): Promise<AccountEquityCurve> =>
    accountApi.equityCurve(lookbackDays, signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError(res.reason ?? "CURVE_UNAVAILABLE");
      return res;
    }),

  growth: (signal?: AbortSignal): Promise<Array<{ timestamp: string; balance: number | null; equity: number | null }>> =>
    accountApi.growth(signal).then((rows: GrowthPoint[]) => toGrowthPoints(rows)),

  strategies: (signal?: AbortSignal): Promise<AccountStrategiesResponse> =>
    accountApi.strategies(50, signal).then((res) => {
      if (!res.available) throw new AccountingUnavailableError(res.reason ?? "STRATEGIES_UNAVAILABLE");
      return res;
    }),

  trades: (opts: { limit?: number; offset?: number }, signal?: AbortSignal): Promise<TradesPage> =>
    accountApi.trades(opts, signal).then((rows) => ({
      trades: (rows ?? []).map(toTradeVM),
      limit: opts.limit ?? 50,
      offset: opts.offset ?? 0,
    })),

  tradeForensics: (ticket: number | string | null, signal?: AbortSignal) =>
    accountApi.tradeForensics(ticket as number | string, signal).then((res) => {
      if (!res.found) {
        const note = (res.notes ?? []).join(", ");
        throw new Error(note ? useI18n.getState().t("account.usecase.trace_notes", "Trace not found — backend notes: {notes}", { notes: note }) : useI18n.getState().t("account.usecase.trace_missing", "Trace not found in the accounting ledger."));
      }
      return res;
    }),

  liveAccounting: (params: RiskPlanParams, signal?: AbortSignal): Promise<LiveAccountingResponse> =>
    accountApi.liveAccounting(params, signal),
};
