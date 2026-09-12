/**
 * feedHealth — PURE realtime feed-health view model + realtime merge rules.
 *
 * Zero runtime dependencies (type-only imports) so the exact same functions
 * run in the app and in `node --experimental-strip-types` tests
 * (tests/js/pro_feed.test.mjs). The zustand shell lives in
 * stores/feedStore.ts and simply schedules these functions ≤1×/second.
 *
 * Truth rule: `RealtimeStatus` (websocket layer) stays the source of truth.
 * Nothing here decides engine state — it derives *feed quality chrome*
 * (tone, age, gap count) and enforces the version-monotonic snapshot merge.
 */

import type { ConnectionState, RealtimeStatus } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

/** Commit cadence: at most one store update per second (coalesced). */
export const FEED_COMMIT_INTERVAL_MS = 1000;
/** Connected but no frame within this window → the view model is STALE. */
export const FEED_STALE_AFTER_MS = 10_000;

export interface FeedHealth {
  /** Mirror of RealtimeStatus.state (never an independent judgement). */
  connectionState: ConnectionState;
  /** Server state_version of the last accepted frame (null = never received). */
  lastVersion: number | null;
  /** Age of the last frame at commit time (ms). null = no frame yet. */
  dataAgeMs: number | null;
  /** Monotonic count of detected realtime gaps (reconnects, lost frames). */
  gapCount: number;
  /** Client wall-clock (epoch ms) of the last accepted frame. */
  lastEventAt: number | null;
  /** Reconnect attempts since last successful connect (mirror). */
  reconnectAttempts: number;
  /** Wall-clock of the last committed store update (throttle anchor). */
  lastCommitAt: number | null;
}

export const initialFeedHealth: FeedHealth = {
  connectionState: "disconnected",
  lastVersion: null,
  dataAgeMs: null,
  gapCount: 0,
  lastEventAt: null,
  reconnectAttempts: 0,
  lastCommitAt: null,
};

/** Minimal comparable slice of the feed — decouples gap math from shapes. */
export interface FeedProbe {
  state: ConnectionState;
  lastVersion: number | null;
  reconnectAttempts: number;
  lastEventAt: number | null;
  /**
   * Cumulative provable-missing versions as counted by the realtime client,
   * when it exposes one (REALTIME-HARDEN additive `gapCount` field). Absent
   * on the committed pre-harden shape → callers fall back to folding.
   */
  clientGapCount?: number | null;
}

export function probeFromStatus(status: RealtimeStatus): FeedProbe {
  const clientGap = (status as RealtimeStatus & { gapCount?: number }).gapCount;
  return {
    state: status.state,
    lastVersion: status.lastVersion,
    reconnectAttempts: status.reconnectAttempts,
    lastEventAt: status.lastMessageAt,
    clientGapCount: typeof clientGap === "number" && Number.isFinite(clientGap) ? clientGap : null,
  };
}

export function probeFromHealth(h: FeedHealth): FeedProbe {
  return {
    state: h.connectionState,
    lastVersion: h.lastVersion,
    reconnectAttempts: h.reconnectAttempts,
    lastEventAt: h.lastEventAt,
    // The store's gapCount IS the merged truth (client count folded in), so
    // the round-trip probe carries it as the client-visible count.
    clientGapCount: h.gapCount,
  };
}

/**
 * A realtime "gap" = a window in which live frames were certainly lost:
 *  - resumed to `connected` after an interruption while holding data,
 *  - the reconnect-attempt counter increased (each SSE error = lost run),
 *  - version went BACKWARDS (server restart / versioner reset — defensive:
 *    the SSE client drops stale versions locally, so this only fires when
 *    the transport itself re-seeded).
 * Deliberately NOT a gap: version jumps >1 while connected — the SSE loop
 * samples `get_system_state()` on its own cadence and re-emits full `state`
 * events periodically, so jumps are lossless by protocol.
 */
export function isFeedGap(prev: FeedProbe | null, next: FeedProbe): boolean {
  if (!prev) return false;
  const resumed = prev.state !== "connected" && next.state === "connected" && prev.lastVersion !== null;
  const attemptBump = next.reconnectAttempts > prev.reconnectAttempts;
  const versionRegression =
    prev.lastVersion !== null && next.lastVersion !== null && next.lastVersion < prev.lastVersion;
  return resumed || attemptBump || versionRegression;
}

export function deriveDataAgeMs(lastEventAt: number | null, nowMs: number): number | null {
  if (lastEventAt === null || !Number.isFinite(lastEventAt)) return null;
  return Math.max(0, nowMs - lastEventAt);
}

/** Pure fold: probe + clock -> the next committed health (gapCount monotonic). */
export function buildFeedHealth(prev: FeedHealth, probe: FeedProbe, nowMs: number, gap: boolean): FeedHealth {
  const lastEventAt = probe.lastEventAt ?? prev.lastEventAt;
  // Truth precedence: the realtime client's own cumulative gap count (when
  // the hardened client exposes it) is authoritative; otherwise fold the
  // locally-detected gaps. Never regress below what was already shown.
  const folded = prev.gapCount + (gap ? 1 : 0);
  const gapCount =
    probe.clientGapCount !== null && probe.clientGapCount !== undefined
      ? Math.max(probe.clientGapCount, folded)
      : folded;
  return {
    connectionState: probe.state,
    lastVersion: probe.lastVersion ?? prev.lastVersion,
    dataAgeMs: deriveDataAgeMs(lastEventAt, nowMs),
    gapCount,
    lastEventAt,
    reconnectAttempts: probe.reconnectAttempts,
    lastCommitAt: nowMs,
  };
}

export interface FeedCommitResult {
  health: FeedHealth;
  /** false = coalesced (caller keeps the pending status for a later flush). */
  committed: boolean;
  gap: boolean;
}

/**
 * Throttled commit decision (pure). Commits when: first frame, cadence due
 * (≥1s since last commit), a state-machine change, or a gap. Everything else
 * — per-tick version/age churn at SSE cadence — is coalesced away.
 */
export function computeFeedCommit(
  prev: FeedHealth,
  status: RealtimeStatus,
  nowMs: number,
  force = false,
): FeedCommitResult {
  const probe = probeFromStatus(status);
  const gap = isFeedGap(probeFromHealth(prev), probe);
  const stateChanged = probe.state !== prev.connectionState;
  const first = prev.lastCommitAt === null;
  const due = nowMs - (prev.lastCommitAt ?? 0) >= FEED_COMMIT_INTERVAL_MS;
  const committed = force || first || due || stateChanged || gap;
  if (!committed) return { health: prev, committed: false, gap };
  return { health: buildFeedHealth(prev, probe, nowMs, gap), committed: true, gap };
}

/** Age-only advance used by the 1s flush tick when nothing new arrived. */
export function advanceFeedAges(prev: FeedHealth, nowMs: number): FeedHealth {
  const age = deriveDataAgeMs(prev.lastEventAt, nowMs);
  if (age === null || age === prev.dataAgeMs) return prev;
  return { ...prev, dataAgeMs: age, lastCommitAt: nowMs };
}

export type FeedTone = "live" | "reconnecting" | "stale" | "down";

export const FEED_TONE_LABEL: Record<FeedTone, string> = {
  live: "LIVE",
  reconnecting: "RECONNECTING",
  stale: "STALE",
  down: "OFFLINE",
};

/** Chip tone selector (pure): derived from the mirrored state machine only. */
export function selectFeedTone(
  h: Pick<FeedHealth, "connectionState" | "lastEventAt">,
  nowMs: number,
): FeedTone {
  if (h.connectionState === "disconnected" || h.connectionState === "failed") return "down";
  if (h.connectionState === "reconnecting") return "reconnecting";
  const age = deriveDataAgeMs(h.lastEventAt, nowMs);
  if (age === null || age > FEED_STALE_AFTER_MS) return "stale";
  return "live";
}

export function selectFeedAgeMs(h: Pick<FeedHealth, "lastEventAt">, nowMs: number): number | null {
  return deriveDataAgeMs(h.lastEventAt, nowMs);
}

/** Compact chip text, e.g. `LIVE · v812 · 0.4s · 1 gap`. */
export function formatFeedLabel(h: FeedHealth, nowMs: number): string {
  const parts: string[] = [FEED_TONE_LABEL[selectFeedTone(h, nowMs)]];
  if (h.lastVersion !== null) parts.push(`v${h.lastVersion}`);
  const age = deriveDataAgeMs(h.lastEventAt, nowMs);
  if (age !== null) parts.push(age < 10_000 ? `${(age / 1000).toFixed(1)}s` : `${Math.round(age / 1000)}s`);
  if (h.gapCount > 0) parts.push(`${h.gapCount} gap${h.gapCount === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// REALTIME ⇄ REST merge rules (never regress state_version)
// ---------------------------------------------------------------------------

/**
 * Merge rule (pure):
 *  - incoming version < current  → keep current (stale REST poll / mirror
 *    reorder can never overwrite fresher live data),
 *  - incoming version > current  → take incoming,
 *  - equal version               → take incoming but carry over the
 *    heavyweight lists it omitted (`tick` frames drop bars/features/
 *    predictions — they only ride full `state` frames).
 */
export function mergeSnapshotByRule(
  current: EngineSnapshot | undefined,
  incoming: EngineSnapshot | undefined,
): EngineSnapshot | undefined {
  if (!incoming) return current;
  if (!current) return incoming;
  const curV = current.state_version ?? 0;
  const incV = incoming.state_version ?? 0;
  if (incV < curV) return current;
  if (incV > curV) return incoming;
  const merged: EngineSnapshot = { ...incoming };
  if (incoming.bars === undefined && current.bars !== undefined) merged.bars = current.bars;
  if (incoming.features === undefined && current.features !== undefined) merged.features = current.features;
  if (incoming.predictions === undefined && current.predictions !== undefined) {
    merged.predictions = current.predictions;
  }
  return merged;
}

// ---------------------------------------------------------------------------
// react-query error → toast bridge rules (pure decisions; queryBridge wires)
// ---------------------------------------------------------------------------

/** Is this error something the toast layer should surface? */
export function shouldToastError(err: unknown): boolean {
  if (err && typeof err === "object" && err instanceof Error) {
    const e = err as Error & { isAuthError?: boolean; name?: string };
    if (e.isAuthError === true) return false; // the auth banner owns this case
    if (e.name === "AbortError") return false; // unmount/invalidation race
    return true;
  }
  return false;
}

/** Toast text: backend words + request_id trail. Never invented. */
export function toastTextForError(err: unknown, queryKey?: readonly unknown[]): string {
  const where = queryKey && queryKey.length > 0 ? `[${String(queryKey[0])}] ` : "";
  const e = err as (Error & { message?: string; requestId?: string | null }) | null;
  const message = e && typeof e.message === "string" && e.message ? e.message : "Request failed — backend response unavailable.";
  const rid = e && e.requestId ? ` (request_id: ${e.requestId})` : "";
  return `${where}${message}${rid}`;
}
