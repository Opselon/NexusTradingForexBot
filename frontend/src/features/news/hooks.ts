/**
 * News hooks — TanStack Query bindings over the news use cases.
 *
 * Query keys are namespaced ["news", …] (lane contract). Polling mirrors the
 * legacy cadence (state/feed 60s, health 30s) but through the cache, and every
 * command invalidates the exact keys it can affect — the backend stays
 * authoritative, the UI refetches instead of assuming.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { newsCommands, newsQueries } from "./useCases";
import type { NewsFilter } from "./types";

export const newsKeys = {
  all: ["news"] as const,
  feed: (status: NewsFilter, limit: number) => ["news", "feed", status, limit] as const,
  state: () => ["news", "state"] as const,
  sources: () => ["news", "sources"] as const,
  health: () => ["news", "health"] as const,
  timeline: (bucket: number, hours: number) => ["news", "timeline", bucket, hours] as const,
  impact: (asset: string) => ["news", "impact", asset] as const,
  keywords: (category: string, q: string, topN: number) => ["news", "keywords", category, q, topN] as const,
  article: (id: string) => ["news", "article", id] as const,
  analysis: (id: string) => ["news", "analysis", id] as const,
  tradeLinks: (ticket: string | number) => ["news", "trade-links", ticket] as const,
  aiStatus: () => ["news", "ai-status"] as const,
  toggleState: () => ["news", "toggle-state"] as const,
  autoState: () => ["news", "auto-state"] as const,
};

export function useNewsFeed(status: NewsFilter, limit = 50) {
  return useQuery({
    queryKey: newsKeys.feed(status, limit),
    queryFn: ({ signal }) => newsQueries.feed({ status, limit }, signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useNewsState() {
  return useQuery({
    queryKey: newsKeys.state(),
    queryFn: ({ signal }) => newsQueries.state(signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useNewsSources() {
  return useQuery({
    queryKey: newsKeys.sources(),
    queryFn: ({ signal }) => newsQueries.sources(signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useNewsHealth() {
  return useQuery({
    queryKey: newsKeys.health(),
    queryFn: ({ signal }) => newsQueries.health(signal),
    refetchInterval: 30_000,
    retry: 1,
  });
}

export function useNewsTimeline(bucketSec: number, hoursBack: number) {
  return useQuery({
    queryKey: newsKeys.timeline(bucketSec, hoursBack),
    queryFn: ({ signal }) => newsQueries.timeline(bucketSec, hoursBack, signal),
    refetchInterval: 120_000,
    retry: 1,
  });
}

export function useNewsImpact(asset: string, limit = 40) {
  return useQuery({
    queryKey: newsKeys.impact(asset),
    queryFn: ({ signal }) => newsQueries.impact(asset, limit, signal),
    refetchInterval: 120_000,
    retry: 1,
  });
}

export function useNewsKeywords(opts: { category: string; q: string; topN: number }) {
  return useQuery({
    queryKey: newsKeys.keywords(opts.category, opts.q, opts.topN),
    queryFn: ({ signal }) => newsQueries.keywords(opts, signal),
    staleTime: 30_000,
    retry: 1,
  });
}

export function useNewsArticle(articleId: string | null) {
  return useQuery({
    queryKey: newsKeys.article(articleId ?? "-"),
    queryFn: ({ signal }) => newsQueries.article(articleId as string, signal),
    enabled: articleId !== null,
    retry: 1,
  });
}

export function useNewsAnalysis(articleId: string | null) {
  return useQuery({
    queryKey: newsKeys.analysis(articleId ?? "-"),
    queryFn: ({ signal }) => newsQueries.analysis(articleId as string, signal),
    enabled: articleId !== null,
    retry: 1,
  });
}

export function useNewsTradeLinks(ticket: string | number | null) {
  return useQuery({
    queryKey: newsKeys.tradeLinks(ticket ?? "-"),
    queryFn: ({ signal }) => newsQueries.tradeLinks(ticket as string | number, signal),
    enabled: ticket !== null,
    retry: 1,
  });
}

export function useNewsAiStatus() {
  return useQuery({
    queryKey: newsKeys.aiStatus(),
    queryFn: ({ signal }) => newsQueries.aiStatus(signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useNewsToggleState() {
  return useQuery({
    queryKey: newsKeys.toggleState(),
    queryFn: ({ signal }) => newsQueries.toggleState(signal),
    refetchInterval: 90_000,
    retry: 1,
  });
}

export function useNewsAutoState() {
  return useQuery({
    queryKey: newsKeys.autoState(),
    queryFn: ({ signal }) => newsQueries.autoState(signal),
    refetchInterval: 90_000,
    retry: 1,
  });
}

/** Command helper: run, then refresh exactly the affected keys, backend-verdict first. */
export function useNewsCommand<TArgs extends unknown[], TRes>(
  fn: (...args: TArgs) => Promise<TRes>,
  invalidate: (keys: Array<readonly string[]>) => void,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => fn(...args),
    onSuccess: () => {
      invalidate([newsKeys.all]);
      void client.refetchQueries({ queryKey: newsKeys.all, type: "active", exact: false });
    },
  });
}

export function useAnalyzeArticle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { articleId: string; force?: boolean }) => newsCommands.analyze(args.articleId, args.force ?? false),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: newsKeys.all });
    },
  });
}

export function useRestoreArticle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (articleId: string) => newsCommands.restore(articleId),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useNewsRefresh() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => newsCommands.refresh(),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useNewsSelfHeal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => newsCommands.selfHeal(),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useAutoPrune() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => newsCommands.autoPrune(),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useBatchAnalyze() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => newsCommands.analyzeBatch(ids),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useNewsToggle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => newsCommands.setToggle(enabled),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

export function useNewsAutoToggle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => newsCommands.setAuto(enabled),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}
