/**
 * marketApi — market data + market state (/api/v1/market, /api/v1/runtime).
 */

import { getV1 } from "./client";

export interface MarketQuote {
  symbol?: string | null;
  bid?: number | null;
  ask?: number | null;
  last?: number | null;
  time_utc?: string | null;
  freshness_ms?: number | null;
  stale?: boolean;
  spread_points?: number | null;
  available?: boolean;
}

export interface RuntimeMode {
  mode: string | null;
  effective_mode: string | null;
  engine_attached: boolean;
  replaying: boolean | null;
}

export const marketApi = {
  quote: (symbol?: string, signal?: AbortSignal): Promise<MarketQuote> =>
    getV1<MarketQuote>(`/api/v1/market/quote${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`, signal),

  runtimeMode: (signal?: AbortSignal): Promise<RuntimeMode> => getV1<RuntimeMode>("/api/v1/runtime/mode", signal),

  freshness: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getV1<Record<string, unknown>>("/api/v1/runtime/freshness", signal),
};
