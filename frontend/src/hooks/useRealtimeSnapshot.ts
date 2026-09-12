/**
 * useRealtimeSnapshot — React binding for the realtime feed (SSE-first).
 *
 * Server state comes from the backend's canonical snapshot; the REST snapshot
 * query seeds initial data and the SSE/WS client keeps it live. When the feed
 * is down, consumers see `connectionState !== "connected"` plus `stale=true`
 * and must render stale indicators — the last-good data KEEPS rendering
 * (explicitly age-marked), it is never silently dropped.
 *
 * REALTIME-FIRST MERGE RULES (all fields after `realtimeStatus` are additive
 * — the original {snapshot, connectionState, realtimeStatus} shape is kept):
 *  1. The visible snapshot is merged by monotonic `state_version`
 *     (`mergeSnapshotByRule`, pure + tested): a REST poll OLDER than the live
 *     feed never overwrites fresher state; equal-version frames carry over
 *     the heavyweight lists tick events omit (bars/features/predictions).
 *  2. The last coherent snapshot is cached with the wall-clock of the newest
 *     server version it holds, so `dataAgeMs` is the data's true age — not a
 *     restated age from a redundant poll.
 *  3. On a realtime gap the engine-snapshot query is resynced, rate-limited
 *     to one resync per RESYNC_MIN_INTERVAL_MS so a flapping feed cannot storm
 *     the backend. Gap detection prefers the client's own `subscribeGap`
 *     channel (REALTIME-HARDEN, additive); when the client build predates it,
 *     gaps are folded from status transitions instead. Both paths are guarded
 *     by the same rate limiter.
 *  4. Every status frame also feeds the throttled derived feedStore (chrome
 *     view model, ≤1Hz). `RealtimeStatus` stays the source of truth; this
 *     hook drives the 1Hz `flush()` tick because AppShell is out of lane.
 *  5. This hook is app-scoped (mounted once by AppShell), so it also mounts
 *     the global react-query error→toast bridge here rather than editing a
 *     file owned by another lane.
 */

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { realtimeClient } from "@/websocket/realtimeSocket";
import { mergeSnapshotByRule, useFeedStore } from "@/stores/feedStore";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import type { ConnectionState, RealtimeStatus } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

/** Resync storm guard: at most one gap-triggered refetch per 5s. */
export const RESYNC_MIN_INTERVAL_MS = 5_000;
/** Chrome clock: how often the throttled feed store is flushed. */
export const FEED_FLUSH_INTERVAL_MS = 1_000;

/** True when a feed transition lost frames (fallback folding path). */
function gapBetween(prev: RealtimeStatus | null, next: RealtimeStatus): boolean {
  if (!prev) return false;
  const resumed = prev.state !== "connected" && next.state === "connected";
  const attemptsBump = next.reconnectAttempts > prev.reconnectAttempts;
  return resumed || attemptsBump;
}

/**
 * Optional seam: the hardened realtime client exposes `subscribeGap(cb)` with
 * `{missing, gapCount, ...}`. Coded defensively against the committed shape —
 * if the method is absent (pre-harden build) or throws, the status-transition
 * fallback above covers resync and the store folds its own gaps.
 */
function trySubscribeGap(client: unknown, cb: (info: { gapCount?: unknown }) => void): (() => void) | null {
  const maybe = client as { subscribeGap?: (l: (info: { gapCount?: unknown }) => void) => unknown };
  if (typeof maybe.subscribeGap !== "function") return null;
  try {
    const unsub = maybe.subscribeGap(cb);
    return typeof unsub === "function" ? (unsub as () => void) : null;
  } catch {
    return null;
  }
}

export interface RealtimeSnapshotView {
  snapshot: EngineSnapshot | undefined;
  connectionState: ConnectionState;
  realtimeStatus: RealtimeStatus;
  /** --- additive fields (existing consumers ignore them) --- */
  /** Client wall-clock when the visible snapshot's newest version arrived. */
  snapshotAtMs: number | null;
  /** Age of the visible snapshot's newest server version at render time (ms). */
  dataAgeMs: number | null;
  /** True when live delivery is interrupted or the backend marks data stale. */
  stale: boolean;
  /** Realtime gaps observed since mount (each fired a rate-limited resync). */
  gapCount: number;
  /** Monotonic server version of the visible snapshot. */
  stateVersion: number | null;
}

export function useRealtimeSnapshot(
  restSnapshot: EngineSnapshot | undefined,
): RealtimeSnapshotView {
  const [socketSnapshot, setSocketSnapshot] = useState<EngineSnapshot | undefined>(undefined);
  const [status, setStatus] = useState<RealtimeStatus>(() => realtimeClient.currentStatus());
  const queryClient = useQueryClient();
  // Global react-query failures → the ONE toast system (uiStore), auth skipped.
  useQueryErrorToast();

  // Last-good cache: only ever holds a coherent snapshot, so panels keep
  // showing real data (marked stale) while the feed is interrupted.
  const lastGoodRef = useRef<{ snapshot: EngineSnapshot; atMs: number } | null>(null);
  const prevStatusRef = useRef<RealtimeStatus | null>(null);
  const lastResyncRef = useRef(0);
  const [gapCount, setGapCount] = useState(0);

  useEffect(() => {
    /** Shared 5s rate limiter for both gap sources. */
    const resync = (): void => {
      const now = Date.now();
      if (now - lastResyncRef.current < RESYNC_MIN_INTERVAL_MS) return;
      lastResyncRef.current = now;
      void queryClient.refetchQueries({ queryKey: ["engine-snapshot"] });
    };

    const unsubData = realtimeClient.subscribe((snap) => {
      lastGoodRef.current = { snapshot: snap, atMs: Date.now() };
      setSocketSnapshot(snap);
    });
    const unsubStatus = realtimeClient.subscribeStatus((next) => {
      const prev = prevStatusRef.current;
      prevStatusRef.current = next;
      setStatus(next);
      // Derived feed-health chrome (throttled ≤1Hz inside the store shell).
      useFeedStore.getState().ingest(next);
      // Fallback gap detector for client builds without subscribeGap().
      if (gapBetween(prev, next)) {
        setGapCount((n) => n + 1);
        resync();
      }
    });
    const unsubGap = trySubscribeGap(realtimeClient, (info) => {
      // Authoritative gap signal (REALTIME-HARDEN): counts a local gap only
      // when the client could not already be counted higher.
      const clientCount = typeof info.gapCount === "number" ? info.gapCount : null;
      setGapCount((n) => (clientCount !== null ? Math.max(clientCount, n + 1) : n + 1));
      resync();
    });
    realtimeClient.start();

    // 1Hz chrome clock: advances dataAgeMs and commits coalesced statuses.
    const flushTimer = window.setInterval(() => {
      useFeedStore.getState().flush();
    }, FEED_FLUSH_INTERVAL_MS);

    return () => {
      unsubData();
      unsubStatus();
      if (unsubGap) unsubGap();
      window.clearInterval(flushTimer);
      // Keep the client alive across route changes; it is app-scoped.
    };
  }, [queryClient]);

  // Derived view: merge REST seed and live feed under the never-regress rule.
  // The cache identity carries the arrival time of the NEWEST version it
  // holds — a redundant equal-version poll must not reset the data's age.
  const cached = lastGoodRef.current;
  const candidate = mergeSnapshotByRule(
    mergeSnapshotByRule(restSnapshot, socketSnapshot),
    cached?.snapshot,
  );
  let visible: EngineSnapshot | undefined;
  let atMs: number | null = null;
  if (candidate) {
    const cachedV = cached ? (cached.snapshot.state_version ?? 0) : -1;
    if (cached && (candidate.state_version ?? 0) <= cachedV) {
      // Nothing newer than the cache (older frames were already rejected by
      // the merge rule): keep the cached identity + its arrival time.
      visible = cached.snapshot;
      atMs = cached.atMs;
    } else {
      visible = candidate;
      atMs = Date.now();
      lastGoodRef.current = { snapshot: candidate, atMs };
    }
  } else {
    visible = cached?.snapshot;
    atMs = cached?.atMs ?? null;
  }
  const stale =
    status.state !== "connected" ||
    (visible?.is_stale ?? false) ||
    (visible?.tick_stale ?? false);
  const dataAgeMs = visible && atMs !== null ? Math.max(0, Date.now() - atMs) : null;

  return {
    snapshot: visible,
    connectionState: status.state,
    realtimeStatus: status,
    snapshotAtMs: atMs,
    dataAgeMs,
    stale,
    gapCount,
    stateVersion: visible?.state_version ?? status.lastVersion ?? null,
  };
}
