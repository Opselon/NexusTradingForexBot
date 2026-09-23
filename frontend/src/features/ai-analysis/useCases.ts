/**
 * AI-Analysis: application use cases. Read-only bounded context — every
 * number on the page comes from a v1 endpoint; the snapshot prop from the
 * shell is used only for the "live vs engine" cross-check caption, never to
 * fill a gap a backend call didn't answer.
 */

import { indicatorsApi } from "@/api/indicatorsApi";
import { aiAnalysisApi } from "./api";
import type { SignalDto } from "./model";

export const aiAnalysisQueries = {
  signalLatest: (signal?: AbortSignal) => aiAnalysisApi.signalLatest(signal),
  /** page_size 100: one fetch feeds BOTH the history table and the
   *  confidence-timeline chart; the UI captions the response's own page_size
   *  (the backend may clamp it lower — never assumed). */
  signalHistory: (page: number, hoursBack: number, signal?: AbortSignal) =>
    aiAnalysisApi.signalHistory({ page, page_size: 100, hours_back: hoursBack }, signal),
  decisionStats: (hoursBack: number, signal?: AbortSignal) => aiAnalysisApi.decisionStats(hoursBack, signal),
  noTradeReasons: (signal?: AbortSignal) => aiAnalysisApi.noTradeReasons(signal),
  decisionDetail: (id: string, signal?: AbortSignal) => aiAnalysisApi.decisionDetail(id, signal),
  decisionGates: (id: string, signal?: AbortSignal) => aiAnalysisApi.decisionGates(id, signal),
  decisionEvidence: (id: string, signal?: AbortSignal) => aiAnalysisApi.decisionEvidence(id, signal),
  decisionExplanation: (id: string, signal?: AbortSignal) => aiAnalysisApi.decisionExplanation(id, signal),
  /** Full snapshot (the indicators console's ONLY data source) via the
   *  dedicated read-model client @/api/indicatorsApi. Legacy widget parity:
   *  limit 20000 M1 bars resampled server-side. The gauges/summary/oscillators/
   *  moving-averages/pivots query wrappers were removed in wave 2 — zero
   *  importers anywhere (the console reads snapshot parts from the snapshot),
   *  dead fetch surface that misleads future edits. Re-add if a consumer lands. */
  indicatorsSnapshot: (timeframe: string, signal?: AbortSignal) =>
    indicatorsApi.snapshot({ timeframe, limit: 20_000, signal }),
  shadow70d: (signal?: AbortSignal) => aiAnalysisApi.shadow70d(signal),
};

/** Dedup + stable ordering for the history table (newest first by ledger id). */
export function orderHistory(items: SignalDto[]): SignalDto[] {
  const seen = new Set<string>();
  const out: SignalDto[] = [];
  for (const s of items) {
    const key = s.request_id ?? `${s.symbol}-${s.generated_at}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(s);
  }
  return out;
}
