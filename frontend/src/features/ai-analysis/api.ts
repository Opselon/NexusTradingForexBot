/**
 * AI-Analysis: typed transport surface over @/api/client (v1 envelope only).
 *
 * Routes verified in src/nexus_scalp/web/api_v1/{signals,decisions,indicators,shadow}.py.
 * getV1 unwraps {data,meta}; RESOURCE_NOT_FOUND (no signals yet) surfaces as an
 * ApiError the UI renders as an empty state, never as a fake zero.
 */

import { getV1 } from "@/api/client";
import type { V1Page } from "@/types/domain";
import type {
  DecisionDetailDto,
  DecisionGatesDto,
  DecisionEvidenceDto,
  DecisionExplanationDto,
  DecisionStatsDto,
  IndicatorSummaryDto,
  NoTradeReasonsDto,
  Shadow70dDto,
  SignalDto,
} from "./model";

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export interface IndicatorParams {
  timeframe?: string;
  limit?: number;
  symbol?: string;
  [key: string]: string | number | boolean | undefined;
}

export const aiAnalysisApi = {
  signalLatest: (signal?: AbortSignal): Promise<SignalDto> => getV1<SignalDto>("/api/v1/signals/latest", signal),

  signalHistory: (
    opts: { page?: number; page_size?: number; symbol?: string; action?: string; hours_back?: number },
    signal?: AbortSignal,
  ): Promise<V1Page<SignalDto>> => getV1<V1Page<SignalDto>>(`/api/v1/signals/history${qs(opts)}`, signal),

  decisionStats: (hoursBack: number, signal?: AbortSignal): Promise<DecisionStatsDto> =>
    getV1<DecisionStatsDto>(`/api/v1/decisions/stats${qs({ hours_back: hoursBack })}`, signal),

  noTradeReasons: (signal?: AbortSignal): Promise<NoTradeReasonsDto> =>
    getV1<NoTradeReasonsDto>("/api/v1/decisions/no-trade/reasons", signal),

  decisionDetail: (decisionId: string, signal?: AbortSignal): Promise<DecisionDetailDto> =>
    getV1<DecisionDetailDto>(`/api/v1/decisions/${encodeURIComponent(decisionId)}`, signal),

  decisionGates: (decisionId: string, signal?: AbortSignal): Promise<DecisionGatesDto> =>
    getV1<DecisionGatesDto>(`/api/v1/decisions/${encodeURIComponent(decisionId)}/gates`, signal),

  decisionEvidence: (decisionId: string, signal?: AbortSignal): Promise<DecisionEvidenceDto> =>
    getV1<DecisionEvidenceDto>(`/api/v1/decisions/${encodeURIComponent(decisionId)}/evidence`, signal),

  decisionExplanation: (decisionId: string, signal?: AbortSignal): Promise<DecisionExplanationDto> =>
    getV1<DecisionExplanationDto>(`/api/v1/decisions/${encodeURIComponent(decisionId)}/explanation`, signal),

  indicatorsSummary: (p: IndicatorParams, signal?: AbortSignal): Promise<IndicatorSummaryDto> =>
    getV1<IndicatorSummaryDto>(`/api/v1/indicators/summary${qs(p)}`, signal),

  shadow70d: (signal?: AbortSignal): Promise<Shadow70dDto> => getV1<Shadow70dDto>("/api/v1/shadow/70d", signal),
};
