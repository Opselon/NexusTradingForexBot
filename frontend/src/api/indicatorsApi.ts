/**
 * indicatorsApi — technical-indicator read models.
 *
 * ALL v1 envelope (/api/v1/indicators*) — use getV1 (spec envelope rules).
 * Producer: web/api_v1/indicators.py. Query params: timeframe (validated
 * server-side; M1 default), limit (1..20000 M1 bars), symbol (optional).
 * The backend NEVER fabricates values: insufficient history -> value null +
 * NEUTRAL. Render what arrives; do not fill gaps client-side.
 */

import { getV1, toQuery } from "@/core/transport";
import type {
  IndicatorsGauges,
  IndicatorsMovingAverages,
  IndicatorsOscillators,
  IndicatorsPivots,
  IndicatorsSnapshot,
  IndicatorsSummary,
} from "@/types/features";

export interface IndicatorQuery {
  timeframe?: string;
  limit?: number;
  symbol?: string;
  signal?: AbortSignal;
}

function iq(params: IndicatorQuery): string {
  return toQuery({ timeframe: params.timeframe, limit: params.limit, symbol: params.symbol });
}

const BASE = "/api/v1/indicators";

export const indicatorsApi = {
  /** Full snapshot (oscillators + MAs + pivots + gauges + summary). */
  snapshot: (params: IndicatorQuery = {}): Promise<IndicatorsSnapshot> =>
    getV1<IndicatorsSnapshot>(`${BASE}${iq(params)}`, params.signal),

  oscillators: (params: IndicatorQuery = {}): Promise<IndicatorsOscillators> =>
    getV1<IndicatorsOscillators>(`${BASE}/oscillators${iq(params)}`, params.signal),

  movingAverages: (params: IndicatorQuery = {}): Promise<IndicatorsMovingAverages> =>
    getV1<IndicatorsMovingAverages>(`${BASE}/moving-averages${iq(params)}`, params.signal),

  pivots: (params: IndicatorQuery = {}): Promise<IndicatorsPivots> =>
    getV1<IndicatorsPivots>(`${BASE}/pivots${iq(params)}`, params.signal),

  gauges: (params: IndicatorQuery = {}): Promise<IndicatorsGauges> =>
    getV1<IndicatorsGauges>(`${BASE}/gauges${iq(params)}`, params.signal),

  summary: (params: IndicatorQuery = {}): Promise<IndicatorsSummary> =>
    getV1<IndicatorsSummary>(`${BASE}/summary${iq(params)}`, params.signal),
};
