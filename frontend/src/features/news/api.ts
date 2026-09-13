/**
 * features/news/api.ts — typed transport surface for the News tab.
 *
 * Lane rule: the shared src/api/newsApi.ts is being built concurrently by
 * Lane 1, so this feature keeps its OWN typed calls here over the STABLE
 * exports of @/api/client (getLegacy/send). Legacy /api/news/* routes return
 * raw JSON (available:false when the subsystem is off) — never v1 envelopes.
 */

import { getLegacy, send } from "@/api/client";
import type {
  NewsAiStatusResponse,
  NewsAnalyzeResult,
  NewsArticleAnalysisResponse,
  NewsArticleDetailResponse,
  NewsAutoAnalysisState,
  PruneResult,
  BatchAnalyzeResult,
  NewsFeedResponse,
  NewsFilter,
  NewsImpactResponse,
  NewsKeywordsResponse,
  NewsRefreshResult,
  NewsSelfHealResult,
  NewsSourcesResponse,
  NewsStateResponse,
  NewsSubsystemHealth,
  NewsTimelineResponse,
  NewsToggleState,
  NewsTradeLinksResponse,
} from "./types";

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const id = (v: string | number): string => encodeURIComponent(String(v));

export type { BatchAnalyzeResult, PruneResult };

export const newsApi = {
  /** GET /api/news — canonical feed with per-article analysis + AI + consensus. */
  feed: (opts: { limit?: number; status?: NewsFilter; includeDuplicates?: boolean }, signal?: AbortSignal): Promise<NewsFeedResponse> =>
    getLegacy<NewsFeedResponse>(`/api/news${qs({ limit: opts.limit ?? 50, status: opts.status, include_duplicates: opts.includeDuplicates })}`, signal),

  /** GET /api/news/latest — compact latest list. */
  latest: (limit = 10, signal?: AbortSignal) =>
    getLegacy<{ available: boolean; articles?: Array<Record<string, unknown>> }>(`/api/news/latest${qs({ limit })}`, signal),

  /** GET /api/news/{article_id} — full detail (article + analysis + impacts + links). */
  article: (articleId: string, signal?: AbortSignal) =>
    getLegacy<NewsArticleDetailResponse>(`/api/news/${id(articleId)}`, signal),

  /** GET /api/news/analysis/{article_id} — deterministic analysis + run. */
  analysis: (articleId: string, signal?: AbortSignal) =>
    getLegacy<NewsArticleAnalysisResponse>(`/api/news/analysis/${id(articleId)}`, signal),

  /** POST /api/news/analyze/{article_id} — enqueue AI analysis (?force=true to redo). */
  analyze: (articleId: string, force = false): Promise<NewsAnalyzeResult> =>
    send<NewsAnalyzeResult>(`/api/news/analyze/${id(articleId)}${force ? "?force=true" : ""}`, {}),

  /** POST /api/news/analyze/batch — bounded batch analysis of given ids. */
  analyzeBatch: (articleIds: string[]): Promise<BatchAnalyzeResult> =>
    send<BatchAnalyzeResult>("/api/news/analyze/batch", { article_ids: articleIds }),

  /** GET /api/news/state — derived subsystem state word + scores. */
  state: (signal?: AbortSignal) => getLegacy<NewsStateResponse>("/api/news/state", signal),

  /** GET /api/news/sources — registry + per-source health rows. */
  sources: (enabledOnly = false, signal?: AbortSignal) =>
    getLegacy<NewsSourcesResponse>(`/api/news/sources${qs({ enabled_only: enabledOnly })}`, signal),

  /** GET /api/news/health — subsystem health + worker + budget + calendar. */
  health: (signal?: AbortSignal) => getLegacy<NewsSubsystemHealth>("/api/news/health", signal),

  /** GET /api/news/impact — recent impact records for an asset. */
  impact: (asset = "XAUUSD", limit = 50, signal?: AbortSignal) =>
    getLegacy<NewsImpactResponse>(`/api/news/impact${qs({ asset, limit })}`, signal),

  /** GET /api/news/timeline — bucketed impact sums for the chart. */
  timeline: (bucketSec: number, hoursBack: number, asset = "XAUUSD", signal?: AbortSignal) =>
    getLegacy<NewsTimelineResponse>(`/api/news/timeline${qs({ bucket_sec: bucketSec, hours_back: hoursBack, asset })}`, signal),

  /** GET /api/news/keywords — dataset meta + corpus coverage + listing. */
  keywords: (opts: { topN?: number; category?: string; q?: string }, signal?: AbortSignal) =>
    getLegacy<NewsKeywordsResponse>(`/api/news/keywords${qs({ top_n: opts.topN ?? 25, category: opts.category ?? "", q: opts.q ?? "" })}`, signal),

  /** GET /api/news/trades/{trade_id} — news links for one trade. */
  tradeLinks: (tradeId: string | number, signal?: AbortSignal) =>
    getLegacy<NewsTradeLinksResponse>(`/api/news/trades/${id(tradeId)}`, signal),

  /** GET /api/news/ai-status — secret-free AI readiness. */
  aiStatus: (signal?: AbortSignal) => getLegacy<NewsAiStatusResponse>("/api/news/ai-status", signal),

  /** GET /api/news/toggle-state — news engine enabled (authoritative). */
  toggleState: (signal?: AbortSignal) => getLegacy<NewsToggleState>("/api/news/toggle-state", signal),

  /** POST /api/news/toggle — hot enable/disable the news engine. */
  setToggle: (enabled: boolean) => send<NewsToggleState>("/api/news/toggle", { enabled }),

  /** GET /api/news/auto-analysis — auto-analysis toggle (read side). */
  autoState: (signal?: AbortSignal) => getLegacy<NewsAutoAnalysisState>("/api/news/auto-analysis", signal),

  /** POST /api/news/auto-analysis — hot enable/disable auto analysis. */
  setAuto: (enabled: boolean) => send<NewsAutoAnalysisState>("/api/news/auto-analysis", { enabled }),

  /** POST /api/news/refresh — one bounded ingestion+analysis pass (60s server cooldown). */
  refresh: () => send<NewsRefreshResult>("/api/news/refresh", {}),

  /** POST /api/news/self-heal — rebuild derived state (confirm in UI before firing). */
  selfHeal: () => send<NewsSelfHealResult>("/api/news/self-heal", {}),

  /** POST /api/news/auto-prune — mark unrelated articles IRRELEVANT (recoverable). */
  autoPrune: (actor = "pro_user") => send<PruneResult>("/api/news/auto-prune", { actor }),

  /** POST /api/news/{id}/restore — IRRELEVANT -> ACTIVE. */
  restore: (articleId: string) => send<{ ok?: boolean; status?: string; error?: string }>(`/api/news/${id(articleId)}/restore`, {}),
};
