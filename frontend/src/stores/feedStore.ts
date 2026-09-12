/**
 * feedStore — reactive shell of the derived feed-health view model.
 *
 * ALL logic (throttle decision, gap folding, tone/label selectors, the
 * never-regress snapshot merge) lives in lib/feedHealth.ts as pure,
 * dependency-free functions (unit-tested in tests/js/pro_feed.test.mjs).
 * This file only adds the zustand plumbing + a coalescing scheduler.
 *
 * Architecture rule: `RealtimeStatus` from the websocket layer stays the
 * single source of truth for the connection state machine. This store is a
 * ≤1Hz projection of it for cheap-render chrome (quality chip, meters, KPI
 * strips) — it never decides engine state, and per-tick SSE frames cannot
 * become a setState storm here.
 */

import { create } from "zustand";
import type { RealtimeStatus } from "@/types/realtime";
import {
  FEED_COMMIT_INTERVAL_MS,
  advanceFeedAges,
  computeFeedCommit,
  initialFeedHealth,
  type FeedHealth,
} from "@/lib/feedHealth";

export * from "@/lib/feedHealth";

interface FeedActions {
  /** Feed one RealtimeStatus (the source of truth). Coalesced to ≤1Hz. */
  ingest: (status: RealtimeStatus, nowMs?: number) => void;
  /** Commit a coalesced status and/or advance data age. Call at ~1Hz. */
  flush: (nowMs?: number) => void;
  /** Test/lifecycle helper: back to pristine. */
  resetFeedHealth: () => void;
}

export type FeedStore = FeedHealth & FeedActions;

let pendingStatus: RealtimeStatus | null = null;
let trailingTimer: ReturnType<typeof setTimeout> | null = null;

function clearTrailing(): void {
  if (trailingTimer !== null) {
    clearTimeout(trailingTimer);
    trailingTimer = null;
  }
}

export const useFeedStore = create<FeedStore>()((set, get) => ({
  ...initialFeedHealth,
  ingest: (status, nowMs = Date.now()) => {
    const res = computeFeedCommit(get(), status, nowMs);
    if (res.committed) {
      pendingStatus = null;
      clearTrailing();
      set(res.health);
      return;
    }
    // Coalesced: remember the freshest truth; the 1Hz flush (AppShell ticker
    // or the trailing timer below) commits it once the cadence window ends.
    pendingStatus = status;
    // Trailing flush only where a browser timer loop exists — node tests
    // drive `flush(nowMs)` explicitly for determinism.
    if (typeof window !== "undefined" && trailingTimer === null) {
      const wait = Math.max(0, FEED_COMMIT_INTERVAL_MS - (nowMs - (get().lastCommitAt ?? nowMs)));
      trailingTimer = window.setTimeout(() => {
        trailingTimer = null;
        get().flush();
      }, wait);
    }
  },
  flush: (nowMs = Date.now()) => {
    clearTrailing();
    if (pendingStatus !== null) {
      const status = pendingStatus;
      pendingStatus = null;
      const res = computeFeedCommit(get(), status, nowMs, true);
      if (res.health !== get()) set(res.health);
      return;
    }
    const advanced = advanceFeedAges(get(), nowMs);
    if (advanced !== get()) set(advanced);
  },
  resetFeedHealth: () => {
    pendingStatus = null;
    clearTrailing();
    set({ ...initialFeedHealth });
  },
}));

/**
 * Selector hook for chrome components: the derived feed-health view.
 *
 * The store state object is replaced only by `ingest`/`flush` (≤1Hz), so an
 * identity selector is stable between commits — no shallow-compare needed,
 * and no re-render storm from SSE tick cadence.
 */
export function useFeedHealth(): FeedHealth {
  return useFeedStore((s) => s);
}
