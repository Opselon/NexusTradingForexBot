/**
 * News use cases — application services over features/news/api.
 *
 * Every read passes through the model guards (available=false => a real
 * NewsUnavailableError, never an empty-but-green list). Commands return the
 * backend's own verdict text; the UI never assumes success.
 */

import { newsApi } from "./api";
import {
  NewsUnavailableError,
  requireDetail,
  requireFeed,
  requireKeywords,
  requireProAnswers,
  requireProConsole,
  requireProStatus,
  requireState,
  requireTimeline,
  toSourceVM,
} from "./model";
import type { NewsFilter } from "./types";

export const newsQueries = {
  feed: (opts: { limit?: number; status?: NewsFilter }, signal?: AbortSignal) =>
    newsApi.feed(opts, signal).then(requireFeed),

  state: (signal?: AbortSignal) => newsApi.state(signal).then(requireState),

  sources: (signal?: AbortSignal) =>
    newsApi.sources(false, signal).then((res) => {
      if (!res.available) throw new NewsUnavailableError("SOURCES_UNAVAILABLE");
      return (res.sources ?? []).map(toSourceVM);
    }),

  health: (signal?: AbortSignal) => newsApi.health(signal),

  timeline: (bucketSec: number, hoursBack: number, signal?: AbortSignal) =>
    newsApi.timeline(bucketSec, hoursBack, "XAUUSD", signal).then((res) => {
      requireTimeline(res);
      return res.buckets ?? [];
    }),

  impact: (asset: string, limit: number, signal?: AbortSignal) =>
    newsApi.impact(asset, limit, signal).then((res) => {
      if (!res.available) throw new NewsUnavailableError("IMPACT_UNAVAILABLE");
      return res.impacts ?? [];
    }),

  keywords: (opts: { topN?: number; category?: string; q?: string }, signal?: AbortSignal) =>
    newsApi.keywords(opts, signal).then((res) => {
      requireKeywords(res);
      return res;
    }),

  article: (articleId: string, signal?: AbortSignal) =>
    newsApi.article(articleId, signal).then((res) => {
      requireDetail(res);
      return res;
    }),

  analysis: (articleId: string, signal?: AbortSignal) =>
    newsApi.analysis(articleId, signal).then((res) => {
      if (!res.available) throw new NewsUnavailableError("ANALYSIS_UNAVAILABLE");
      return res;
    }),

  tradeLinks: (tradeId: string | number, signal?: AbortSignal) =>
    newsApi.tradeLinks(tradeId, signal).then((res) => {
      if (!res.available) throw new NewsUnavailableError("TRADELINKS_UNAVAILABLE");
      return res.links ?? [];
    }),

  aiStatus: (signal?: AbortSignal) => newsApi.aiStatus(signal),

  toggleState: (signal?: AbortSignal) => newsApi.toggleState(signal),

  autoState: (signal?: AbortSignal) => newsApi.autoState(signal),

  /* ── PRO console reads (round 2). Refusals throw NewsProRefusedError from
   * the model guards — an available:false render is NEVER an empty list. ── */

  proStatus: (signal?: AbortSignal) => newsApi.proStatus(signal).then(requireProStatus),

  proConsole: (opts: { limit?: number; sinceSeq?: number }, signal?: AbortSignal) =>
    newsApi.proConsole(opts, signal).then(requireProConsole),

  proLatestAnswers: (limit = 8, signal?: AbortSignal) =>
    newsApi.proLatestAnswers(limit, signal).then(requireProAnswers),
};

export const newsCommands = {
  analyze: (articleId: string, force = false) => newsApi.analyze(articleId, force),
  analyzeBatch: (ids: string[]) => newsApi.analyzeBatch(ids),
  refresh: () => newsApi.refresh(),
  selfHeal: () => newsApi.selfHeal(),
  autoPrune: () => newsApi.autoPrune(),
  restore: (articleId: string) => newsApi.restore(articleId),
  setToggle: (enabled: boolean) => newsApi.setToggle(enabled),
  setAuto: (enabled: boolean) => newsApi.setAuto(enabled),

  /* ── PRO console commands (round 2). Commands DO NOT throw on a safe-envelope
   * refusal: they return it so the UI shows the backend's own code/message
   * (COOLDOWN, NEWS_UNAVAILABLE, ...) — progress/state stay backend-owned. ── */

  proAnalyzeAll: (opts: { limit?: number; force?: boolean } = {}) => newsApi.proAnalyzeAll(opts),
  proPurge: (opts: { hardDelete?: boolean; olderThanHours?: number | null; limit?: number } = {}) => newsApi.proPurge(opts),
  autoPruneSafe: (actor = "pro_user") => newsApi.autoPruneSafe(actor),
};
