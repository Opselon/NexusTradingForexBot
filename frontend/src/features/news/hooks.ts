/**
 * News hooks — TanStack Query bindings over the news use cases.
 *
 * Query keys are namespaced ["news", …] (lane contract). Polling mirrors the
 * legacy cadence (state/feed 60s, health 30s) but through the cache, and every
 * command invalidates the exact keys it can affect — the backend stays
 * authoritative, the UI refetches instead of assuming.
 *
 * Round 2 adds the PRO console bindings: `useProConsoleLog` reproduces the
 * legacy since_seq cursor poll (1.5s, Web/news_intelligence.js) appending ONLY
 * real entries returned by the backend — progress is never simulated.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { newsCommands, newsQueries } from "./useCases";
import { maxConsoleSeq } from "./model";
import type { ProConsoleEntry } from "./proTypes";
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
  proStatus: () => ["news", "pro", "status"] as const,
  proConsole: (limit: number, sinceSeq: number) => ["news", "pro", "console", limit, sinceSeq] as const,
  proAnswers: (limit: number) => ["news", "pro", "answers", limit] as const,
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

/* ══════════════════════════════════════════════════════════════════════════
 * PRO CONSOLE (round 2 — legacy Web/news_intelligence.js parity)
 * ══════════════════════════════════════════════════════════════════════════ */

/** GET /api/news/pro/status — worker counts + provider + latest AI row (15s poll). */
export function useNewsProStatus() {
  return useQuery({
    queryKey: newsKeys.proStatus(),
    queryFn: ({ signal }) => newsQueries.proStatus(signal),
    refetchInterval: 15_000,
    retry: 1,
  });
}

/** GET /api/news/pro/latest-answers — verbatim news_ai_analysis rows (30s poll). */
export function useNewsProAnswers(limit = 8) {
  return useQuery({
    queryKey: newsKeys.proAnswers(limit),
    queryFn: ({ signal }) => newsQueries.proLatestAnswers(limit, signal),
    refetchInterval: 30_000,
    retry: 1,
  });
}

/** Legacy parity caps: ring is 400 rows on screen, 500 server-side. */
export const PRO_LOG_MAX = 400;
export const PRO_POLL_MS = 1_500;

/**
 * Cursor-polled live console log — the React form of the legacy
 * `_pollProConsole` loop. It appends ONLY entries the backend returned with
 * seq > cursor; on failure the log just stops growing (honest, no fake rows).
 * `error` carries the last refusal so the panel can show it once, not spin.
 */
export function useProConsoleLog(opts: { limit?: number; pollMs?: number } = {}) {
  const limit = opts.limit ?? 200;
  const pollMs = opts.pollMs ?? PRO_POLL_MS;
  const [entries, setEntries] = useState<ProConsoleEntry[]>([]);
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const seqRef = useRef(0);
  const inflight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const poll = useCallback(async (): Promise<void> => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const prevCursor = seqRef.current;
      const fresh = await newsQueries.proConsole({ limit, sinceSeq: prevCursor });
      if (!mounted.current) return;
      const top = maxConsoleSeq(fresh);
      if (fresh.length > 0) {
        // Filter with the pre-update cursor: the setEntries updater may run
        // after seqRef advanced, and must not drop the batch it came for.
        const freshOnly = fresh.filter((e) => Number(e.seq ?? 0) > prevCursor);
        setEntries((prev) => {
          const merged = [...prev, ...freshOnly];
          return merged.length > PRO_LOG_MAX ? merged.slice(merged.length - PRO_LOG_MAX) : merged;
        });
      }
      if (top !== null) {
        seqRef.current = top;
        setCursor(top);
      }
      setError(null);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "console poll failed");
    } finally {
      inflight.current = false;
    }
  }, [limit]);

  useEffect(() => {
    if (!polling) return;
    void poll();
    const t = setInterval(() => void poll(), pollMs);
    return () => clearInterval(t);
  }, [polling, poll, pollMs]);

  const start = useCallback(() => setPolling(true), []);
  const stop = useCallback(() => setPolling(false), []);
  /** Clear == legacy clearProConsole: reset the local view + cursor (server ring untouched). */
  const clear = useCallback(() => {
    setEntries([]);
    seqRef.current = 0;
    setCursor(0);
  }, []);

  return { entries, cursor, error, polling, start, stop, clear, pollNow: poll };
}

/** POST /api/news/pro/analyze-all — returns the safe envelope (no throw on refusal). */
export function useProAnalyzeAll() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { limit?: number; force?: boolean } = {}) => newsCommands.proAnalyzeAll(args),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["news", "pro"] }),
  });
}

/** POST /api/news/pro/purge — soft count (default) or bounded hard delete. */
export function useProPurge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { hardDelete?: boolean; olderThanHours?: number | null; limit?: number } = {}) => newsCommands.proPurge(args),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}

/** POST /api/news/auto-prune (safe-envelope variant used by the pro console). */
export function useAutoPruneSafe() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (actor: string | undefined) => newsCommands.autoPruneSafe(actor ?? "pro_user"),
    onSuccess: () => void client.invalidateQueries({ queryKey: newsKeys.all }),
  });
}
