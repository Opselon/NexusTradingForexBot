/**
 * intelligenceApi — news / market-context / trade intelligence.
 *
 *  GET /api/news/state      — current news state (NORMAL/ELEVATED/.../STALE)
 *  GET /api/news/health     — subsystem health + worker + calendar gate
 *  GET /api/news/latest     — latest canonical articles
 *  GET /api/intelligence/summary — trade-intelligence telemetry + worker
 *  GET /api/intelligence/autopsies — bounded trade autopsies
 */

import { getLegacy } from "./client";
import type { NewsState, NewsHealth, NewsArticle, IntelligenceSummary, AutopsyRow } from "@/types/domain";

export const intelligenceApi = {
  newsState: (signal?: AbortSignal): Promise<NewsState> => getLegacy<NewsState>("/api/news/state", signal),

  newsHealth: (signal?: AbortSignal): Promise<NewsHealth> => getLegacy<NewsHealth>("/api/news/health", signal),

  newsLatest: (limit = 15, signal?: AbortSignal): Promise<{ available: boolean; articles?: NewsArticle[] }> =>
    getLegacy<{ available: boolean; articles?: NewsArticle[] }>(`/api/news/latest?limit=${limit}`, signal),

  intelligenceSummary: (signal?: AbortSignal): Promise<IntelligenceSummary> =>
    getLegacy<IntelligenceSummary>("/api/intelligence/summary", signal),

  autopsies: (limit = 25, signal?: AbortSignal): Promise<{ available: boolean; autopsies?: AutopsyRow[] }> =>
    getLegacy<{ available: boolean; autopsies?: AutopsyRow[] }>(`/api/intelligence/autopsies?limit=${limit}`, signal),
};
