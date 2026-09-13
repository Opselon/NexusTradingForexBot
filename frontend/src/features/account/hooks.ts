/**
 * Account hooks — TanStack Query bindings over the account use cases.
 *
 * Keys are namespaced ["account", …]. All reads are GETs (accounting is
 * read-only from the console — the close/modify commands live in positions).
 * Polling mirrors the legacy refresh-on-tick behaviour at a cache level.
 */

import { useQuery } from "@tanstack/react-query";
import { accountQueries } from "./useCases";
import type { PeriodKind } from "./types";

export const accountKeys = {
  all: ["account"] as const,
  performance: () => ["account", "performance"] as const,
  period: (kind: PeriodKind) => ["account", "period", kind] as const,
  periodSeries: (kind: PeriodKind, count: number) => ["account", "series", kind, count] as const,
  intelligence: (kind: PeriodKind) => ["account", "intelligence", kind] as const,
  equityCurve: (lookback: number | null) => ["account", "equity-curve", lookback ?? "all"] as const,
  growth: () => ["account", "growth"] as const,
  strategies: () => ["account", "strategies"] as const,
  trades: (limit: number, offset: number) => ["account", "trades", limit, offset] as const,
  forensics: (ticket: string | number) => ["account", "forensics", ticket] as const,
  liveAccounting: (key: string) => ["account", "live", key] as const,
};

export function useAccountPerformance() {
  return useQuery({ queryKey: accountKeys.performance(), queryFn: ({ signal }) => accountQueries.performance(signal), refetchInterval: 30_000, retry: 1 });
}

export function useAccountPeriod(kind: PeriodKind) {
  return useQuery({ queryKey: accountKeys.period(kind), queryFn: ({ signal }) => accountQueries.period(kind, signal), refetchInterval: 60_000, retry: 1 });
}

export function useAccountSeries(kind: PeriodKind, count = 12) {
  return useQuery({
    queryKey: accountKeys.periodSeries(kind, count),
    queryFn: ({ signal }) => accountQueries.periodSeries(kind, count, signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useAccountIntelligence(kind: PeriodKind) {
  return useQuery({ queryKey: accountKeys.intelligence(kind), queryFn: ({ signal }) => accountQueries.intelligence(kind, signal), retry: 1 });
}

export function useEquityCurve(lookbackDays: number | null) {
  return useQuery({
    queryKey: accountKeys.equityCurve(lookbackDays),
    queryFn: ({ signal }) => accountQueries.equityCurve(lookbackDays ?? undefined, signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useAccountGrowth() {
  return useQuery({ queryKey: accountKeys.growth(), queryFn: ({ signal }) => accountQueries.growth(signal), refetchInterval: 90_000, retry: 1 });
}

export function useAccountStrategies() {
  return useQuery({ queryKey: accountKeys.strategies(), queryFn: ({ signal }) => accountQueries.strategies(signal), retry: 1 });
}

export function useAccountTrades(limit: number, offset: number) {
  return useQuery({
    queryKey: accountKeys.trades(limit, offset),
    queryFn: ({ signal }) => accountQueries.trades({ limit, offset }, signal),
    refetchInterval: 45_000,
    retry: 1,
  });
}

export function useTradeForensics(ticket: string | number | null) {
  return useQuery({
    queryKey: accountKeys.forensics(ticket ?? "-"),
    queryFn: ({ signal }) => accountQueries.tradeForensics(ticket, signal),
    enabled: ticket !== null,
    retry: 1,
  });
}

export function useLiveAccounting(params: { equity?: number; entry?: number; stopLoss?: number; riskPct?: number }, enabled = true) {
  const key = JSON.stringify(params);
  return useQuery({
    queryKey: accountKeys.liveAccounting(key),
    queryFn: ({ signal }) => accountQueries.liveAccounting(params, signal),
    refetchInterval: enabled ? 30_000 : false,
    retry: 1,
  });
}
