/**
 * newsApi — news bounded context (legacy raw JSON, getLegacy/send).
 *
 * Routes per shared/ROUTES.txt (news_liquidity_mslie_routes.py +
 * news_intelligence_routes.py). intelligenceApi.ts keeps the older
 * /api/news/{state,health,latest} helpers for the existing pages — this
 * module is the complete surface lanes 2-5 build on.
 */

import { getLegacy, send } from "@/core/transport";
import { toQuery } from "@/core/config";
import type { NewsArticle } from "@/types/domain";
import type {
  NewsAiStatus,
  NewsDetailResponse,
  NewsImpactResponse,
  NewsKeywordsResponse,
  NewsListResponse,
  NewsMutationResult,
  NewsSourcesResponse,
  NewsTimelineResponse,
  NewsToggleState,
} from "@/types/features";

export const newsApi = {
  // ---------------------------------------------------------------- reads
  list: (
    params: { limit?: number; include_duplicates?: boolean; status?: string } = {},
    signal?: AbortSignal,
  ): Promise<NewsListResponse> =>
    getLegacy<NewsListResponse>(`/api/news${toQuery(params)}`, signal),

  latest: (limit = 10, signal?: AbortSignal): Promise<NewsListResponse> =>
    getLegacy<NewsListResponse>(`/api/news/latest${toQuery({ limit })}`, signal),

  detail: (articleId: string, signal?: AbortSignal): Promise<NewsDetailResponse> =>
    getLegacy<NewsDetailResponse>(`/api/news/${encodeURIComponent(articleId)}`, signal),

  analysis: (articleId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/news/analysis/${encodeURIComponent(articleId)}`, signal),

  tradeLinks: (tradeId: string, signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>(`/api/news/trades/${encodeURIComponent(tradeId)}`, signal),

  impact: (params: { asset?: string; limit?: number } = {}, signal?: AbortSignal): Promise<NewsImpactResponse> =>
    getLegacy<NewsImpactResponse>(`/api/news/impact${toQuery(params)}`, signal),

  timeline: (
    params: { from?: string; to?: string; asset?: string } = {},
    signal?: AbortSignal,
  ): Promise<NewsTimelineResponse> => getLegacy<NewsTimelineResponse>(`/api/news/timeline${toQuery(params)}`, signal),

  keywords: (params: { top_n?: number; category?: string; q?: string } = {}, signal?: AbortSignal): Promise<NewsKeywordsResponse> =>
    getLegacy<NewsKeywordsResponse>(`/api/news/keywords${toQuery(params)}`, signal),

  sources: (enabledOnly = false, signal?: AbortSignal): Promise<NewsSourcesResponse> =>
    getLegacy<NewsSourcesResponse>(`/api/news/sources${toQuery({ enabled_only: enabledOnly })}`, signal),

  health: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/health", signal),

  state: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/state", signal),

  toggleState: (signal?: AbortSignal): Promise<NewsToggleState> =>
    getLegacy<NewsToggleState>("/api/news/toggle-state", signal),

  autoAnalysisConfig: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/auto-analysis", signal),

  aiStatus: (signal?: AbortSignal): Promise<NewsAiStatus> =>
    getLegacy<NewsAiStatus>("/api/news/ai-status", signal),

  // ------------------------------------------------------------ pro console
  proStatus: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/pro/status", signal),

  proConsole: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/pro/console", signal),

  proLatestAnswers: (signal?: AbortSignal): Promise<Record<string, unknown>> =>
    getLegacy<Record<string, unknown>>("/api/news/pro/latest-answers", signal),

  // -------------------------------------------------------------- mutations
  refresh: (): Promise<NewsMutationResult> => send<NewsMutationResult>("/api/news/refresh"),

  toggle: (enabled: boolean): Promise<NewsMutationResult> =>
    send<NewsMutationResult>("/api/news/toggle", { enabled }),

  selfHeal: (): Promise<NewsMutationResult> => send<NewsMutationResult>("/api/news/self-heal"),

  analyze: (articleId: string): Promise<NewsMutationResult> =>
    send<NewsMutationResult>(`/api/news/analyze/${encodeURIComponent(articleId)}`),

  analyzeBatch: (payload: Record<string, unknown> = {}): Promise<NewsMutationResult> =>
    send<NewsMutationResult>("/api/news/analyze/batch", payload),

  setAutoAnalysis: (payload: Record<string, unknown>): Promise<NewsMutationResult> =>
    send<NewsMutationResult>("/api/news/auto-analysis", payload),

  autoPrune: (payload: Record<string, unknown> = {}): Promise<NewsMutationResult> =>
    send<NewsMutationResult>("/api/news/auto-prune", payload),

  proAnalyzeAll: (): Promise<NewsMutationResult> => send<NewsMutationResult>("/api/news/pro/analyze-all"),

  proPurge: (): Promise<NewsMutationResult> => send<NewsMutationResult>("/api/news/pro/purge"),

  restore: (articleId: string): Promise<NewsMutationResult> =>
    send<NewsMutationResult>(`/api/news/${encodeURIComponent(articleId)}/restore`),
};

export type NewsArticleRow = NewsArticle;
