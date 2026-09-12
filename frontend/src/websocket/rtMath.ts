/**
 * rtMath - pure decision layer for the ALT-console realtime feed.
 *
 * WHY THIS FILE EXISTS: the reconnect / version-ordering / liveness rules are
 * the part of the SSE client that can silently lie to the operator (claiming
 * "LIVE" while a half-open socket streams nothing, or quietly merging frames
 * that skipped a `state_version`). They are therefore kept here as *total,
 * side-effect-free* functions with no DOM / EventSource / timer access, so
 * `tests/js/pro_realtime.test.mjs` can pin them under plain Node with type
 * stripping (erasable TypeScript only - no enums, no namespaces, no parameter
 * properties, and only `import type` edges, which are removed before module
 * resolution so the `@/` alias never has to resolve in Node).
 *
 * CONTRACT (each rule is also cited where it is applied in realtimeSocket.ts):
 *   1. Backoff is exponential, jittered, HARD-CAPPED at BACKOFF_CAP_MS, with a
 *      capped exponent so attempt growth cannot overflow into Infinity.
 *      Jitter is "equal jitter" - the result can never fall below half the
 *      ceiling, so a de-synchronised client can never hot-loop on a restarting
 *      engine.
 *   2. Version ordering has FOUR distinct outcomes (accept / accept-with-gap /
 *      drop-stale / drop-unversioned). A gap is *reported*, never repaired by
 *      inventing the missing frames, and never used as a reason to discard the
 *      newer truth that did arrive.
 *   3. Liveness is derived from evidence: only a data frame or a heartbeat
 *      counts. Absence of evidence inside the watchdog window means
 *      "not live" - the caller must move to `reconnecting`, never stay in
 *      `connected`.
 *   4. Nothing here fabricates a state. `retry` semantics (budget reset) are
 *      expressed as a decision the caller must carry out.
 */

// ---------------------------------------------------------------------------
// Tunables (exported so the socket client and the tests read the same numbers)
// ---------------------------------------------------------------------------

/** First reconnect delay ceiling (ms). Doubles per attempt up to the cap. */
export const BACKOFF_BASE_MS = 1_000;
/** Hard ceiling on any reconnect delay (ms) - jitter included. */
export const BACKOFF_CAP_MS = 30_000;
/**
 * Exponent clamp. base * 2^MAX_BACKOFF_EXPONENT = 32 000 ms > the cap, so the
 * ceiling saturates at the cap and `2 ** attempt` can never overflow toward
 * Infinity no matter how long the engine stays unreachable.
 */
export const BACKOFF_MAX_EXPONENT = 5;
/** Equal-jitter floor: a delay is never below ceil/2 (>= 500 ms at attempt 0). */
export const BACKOFF_JITTER_FLOOR_RATIO = 0.5;

/** Consecutive failed connect attempts before the feed goes honestly `failed`. */
export const RECONNECT_ATTEMPT_BUDGET = 8;

/** A snapshot whose newest activity is older than this is stale data. */
export const STALE_AFTER_MS = 10_000;
/** Extra grace on top of STALE_AFTER_MS for one lost heartbeat. */
export const HEARTBEAT_GRACE_MS = 15_000;
/** Total silence (no frame AND no heartbeat) that trips the watchdog. */
export const WATCHDOG_SILENCE_MS = STALE_AFTER_MS + HEARTBEAT_GRACE_MS;
/** Watchdog poll interval (ms). */
export const WATCHDOG_TICK_MS = 5_000;

// ---------------------------------------------------------------------------
// 1. Backoff
// ---------------------------------------------------------------------------

/** Non-negative integer coercion for hostile inputs (NaN/undefined/negatives). */
function safeAttempt(attempt: number): number {
  if (typeof attempt !== "number" || !Number.isFinite(attempt) || attempt < 0) return 0;
  return Math.floor(attempt);
}

/** Unit-interval coercion for a supplied random source (never trust the RNG). */
function safeRatio(rand: number): number {
  if (typeof rand !== "number" || !Number.isFinite(rand)) return 0.5;
  if (rand < 0) return 0;
  if (rand > 1) return 1;
  return rand;
}

/**
 * Un-jittered ceiling for a given 0-based attempt index.
 * Exponent is clamped at BACKOFF_MAX_EXPONENT and the result at BACKOFF_CAP_MS.
 */
export function backoffCeilingMs(attempt: number): number {
  const exp = Math.min(safeAttempt(attempt), BACKOFF_MAX_EXPONENT);
  return Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** exp);
}

/**
 * Jittered reconnect delay in ms: ceil/2 + rand * ceil/2   in  [ceil/2, ceil].
 *
 * Equal jitter (not full jitter) is deliberate: full jitter allows ~0 ms and a
 * stampede against an engine that is still booting. The result is always
 * finite, >= 0 and <= BACKOFF_CAP_MS for ANY attempt index and ANY rand input.
 */
export function backoffDelayMs(attempt: number, rand: number = Math.random()): number {
  const ceil = backoffCeilingMs(attempt);
  const floor = ceil * BACKOFF_JITTER_FLOOR_RATIO;
  return Math.round(floor + safeRatio(rand) * (ceil - floor));
}

/** True when the consecutive-failure budget is spent (caller => `failed`). */
export function reconnectBudgetExhausted(attempts: number): boolean {
  return safeAttempt(attempts) >= RECONNECT_ATTEMPT_BUDGET;
}

/**
 * Decision for "a connection attempt just failed": keep retrying with backoff,
 * or stop and tell the truth (`failed`) until a manual retry resets the budget.
 */
export interface ReconnectDecision {
  attempts: number;
  delayMs: number | null;
  state: "reconnecting" | "failed";
}

export function decideAfterFailedAttempt(attemptsSoFar: number, rand: number = Math.random()): ReconnectDecision {
  const prev = safeAttempt(attemptsSoFar);
  const attempts = prev + 1;
  if (reconnectBudgetExhausted(attempts)) {
    // Terminal: no delay, no further auto attempts. `null` means "caller must
    // not schedule a timer" - the state machine may not pretend otherwise.
    return { attempts, delayMs: null, state: "failed" };
  }
  // The delay is indexed by the failures ALREADY spent, so the very first
  // failure waits ~1s (not ~2s) and the growth reads as attempt n => base*2^n.
  return { attempts, delayMs: backoffDelayMs(prev, rand), state: "reconnecting" };
}

// ---------------------------------------------------------------------------
// 2. state_version ordering
// ---------------------------------------------------------------------------

export type VersionDecision =
  /** First versioned frame ever seen: establishes the baseline, no gap claim. */
  | "baseline"
  /** v === last + 1: in sequence. */
  | "accept"
  /** v > last + 1: newer truth, but `missing` frames were never delivered. */
  | "accept-gap"
  /** v === last: replayed/duplicate frame - drop. */
  | "drop-duplicate"
  /** v < last: out of order - drop (never rewind the console). */
  | "drop-stale"
  /**
   * v < last, but the frame is the FIRST full `state` snapshot of a freshly
   * opened stream: the server restarted its counter. Re-base instead of
   * freezing forever on a dead epoch (see `acceptsSnapshotEpochReset`).
   */
  | "reset-epoch"
  /** No usable numeric version in the payload - accepted unguarded (legacy). */
  | "accept-unversioned";

export interface VersionOutcome {
  decision: VersionDecision;
  /** Version the client should remember (unchanged when dropping). */
  lastVersion: number | null;
  /** How many intervening state_versions are provably missing (>= 0). */
  missing: number;
  /** True when the frame's payload may be merged into the snapshot. */
  apply: boolean;
  /**
   * True when the frame is fresh enough to be the new version pointer, i.e. the
   * caller should RE-BASE (a full-snapshot epoch reset after a server restart).
   */
  rebaseline: boolean;
  /**
   * True when the frame may refresh transport liveness (`lastMessageAt`).
   * Kept separate from `apply` so an epoch-reset frame still counts as activity.
   */
  live: boolean;
}

/**
 * Classify one incoming `state_version` against the last applied one.
 *
 * Truthfulness notes pinned by tests:
 *  - a dropped frame refreshes NOTHING (no liveness, no version, no merge) -
 *    a stream that only replays old frames must age out via the watchdog;
 *  - an accepted frame refreshes liveness;
 *  - `missing` is `v - last - 1`, so a single-version step reports 0 and is
 *    never a gap;
 *  - the first versioned frame is a BASELINE, never a huge gap: the server may
 *    restart its counter or the tab may have been paused, and claiming
 *    `123456` missing versions would be a fabricated diagnosis;
 *  - `isFullSnapshot` / `firstFrameOfStream` enable the ONE exception to the
 *    stale-drop rule (a server counter restart), and nothing else.
 */
export function classifyVersion(
  incoming: unknown,
  last: number | null,
  isFullSnapshot = false,
  firstFrameOfStream = false,
): VersionOutcome {
  if (typeof incoming !== "number" || !Number.isFinite(incoming)) {
    // Preserve the pre-hardening tolerance: a payload with no numeric
    // state_version cannot be ordered, so it is applied but never used to
    // prove sequencing or close a gap.
    return {
      decision: "accept-unversioned",
      lastVersion: last,
      missing: 0,
      apply: true,
      rebaseline: false,
      live: true,
    };
  }
  const v = Math.trunc(incoming);
  if (last === null) {
    return { decision: "baseline", lastVersion: v, missing: 0, apply: true, rebaseline: false, live: true };
  }
  if (v === last) {
    return {
      decision: "drop-duplicate",
      lastVersion: last,
      missing: 0,
      apply: false,
      rebaseline: false,
      live: false,
    };
  }
  if (v < last) {
    if (acceptsSnapshotEpochReset(v, last, isFullSnapshot, firstFrameOfStream)) {
      // Server restart: re-base the pointer onto the authoritative snapshot.
      // Not counted as a gap - nothing was "missed", the epoch changed.
      return {
        decision: "reset-epoch",
        lastVersion: v,
        missing: 0,
        apply: true,
        rebaseline: true,
        live: true,
      };
    }
    return { decision: "drop-stale", lastVersion: last, missing: 0, apply: false, rebaseline: false, live: false };
  }
  const missing = v - last - 1;
  if (missing > 0) {
    return { decision: "accept-gap", lastVersion: v, missing, apply: true, rebaseline: false, live: true };
  }
  return { decision: "accept", lastVersion: v, missing: 0, apply: true, rebaseline: false, live: true };
}

/**
 * Should a *lower* `state_version` override the out-of-order drop?
 *
 * Only when ALL of these hold: the frame is a full `state` snapshot (which the
 * server emits as authoritative on every stream open), it is the FIRST frame of
 * a freshly opened stream, and its version is strictly below the pointer. That
 * combination means the server restarted its counter - refusing it would freeze
 * the console on a dead epoch forever, a worse lie than re-basing. Ties and
 * incremental `tick` frames never qualify, so the ordinary guard is untouched.
 */
export function acceptsSnapshotEpochReset(
  incoming: unknown,
  last: number | null,
  isFullSnapshot: boolean,
  firstFrameOfStream: boolean,
): boolean {
  if (!isFullSnapshot || !firstFrameOfStream) return false;
  if (last === null) return false;
  if (typeof incoming !== "number" || !Number.isFinite(incoming)) return false;
  return Math.trunc(incoming) < last;
}

/** Minimum spacing between `onGap` signals (the resync is a REST call). */
export const GAP_SIGNAL_MIN_INTERVAL_MS = 2_000;

export type GapSignalAction = "signal" | "defer";

/**
 * Leading-edge throttle for the resync signal. The gap is always COUNTED
 * (gapCount is never throttled - that would understate the fault); only the
 * callback is rate-limited so a burst of version jumps cannot stampede REST.
 * `defer` carries the wait the caller must honour before flushing the newest
 * deferred gap, so no detection is ever silently swallowed.
 */
export function decideGapSignal(
  nowMs: number,
  lastSignalAtMs: number | null,
  windowMs: number = GAP_SIGNAL_MIN_INTERVAL_MS,
): { action: GapSignalAction; waitMs: number } {
  if (lastSignalAtMs === null || typeof lastSignalAtMs !== "number" || !Number.isFinite(lastSignalAtMs)) {
    return { action: "signal", waitMs: 0 };
  }
  const elapsed = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs - lastSignalAtMs : windowMs;
  if (elapsed < 0 || elapsed >= windowMs) return { action: "signal", waitMs: 0 };
  return { action: "defer", waitMs: Math.max(0, Math.round(windowMs - elapsed)) };
}

/** Cumulative gap accounting: never negative, never loses a sighting. */
export function nextGapCount(current: number, missing: number): number {
  const c = Number.isFinite(current) && current > 0 ? Math.trunc(current) : 0;
  const m = Number.isFinite(missing) && missing > 0 ? Math.trunc(missing) : 0;
  return c + m;
}

/** Why the client asks a subscriber to resynchronize. */
export type GapReason =
  /** `state_version` jumped: intermediate versions were never delivered. */
  | "version-gap"
  /**
   * The backend streamed its own `event: error` frame (SSE_SERIALIZATION_ERROR):
   * the payload for that version was never put on the wire, so the client's
   * view is provably incomplete even when the next version arrives in sequence.
   */
  | "server-error-frame"
  /**
   * The client dropped the stream for a while (tab hidden / watchdog trip) and
   * is re-subscribing: everything that happened in between is unseen.
   */
  | "stream-paused";

/**
 * Server-error frames lose exactly one version's payload. Counted into
 * `gapCount` like a version jump, because the loss is equally proven (the
 * backend said so) - leaving it out would understate the fault.
 */
export function nextGapCountForError(current: number): number {
  return nextGapCount(current, 1);
}

/** What the client tells a subscriber when its view is provably incomplete. */
export interface RealtimeGapInfo {
  /** Last version the client had applied (0 when it never had one). */
  expected: number;
  /** Version of the frame that triggered the signal (`expected` if unknown). */
  received: number;
  /** Provably missing intermediate versions (>= 0; 0 for non-version reasons). */
  missing: number;
  /** Cumulative missing versions since page load (monotonic). */
  gapCount: number;
  /** Wall-clock ms epoch of detection. */
  at: number;
  /** SSE event name that carried the frame ("state" | "tick" | "error"). */
  source: string;
  /** Why this is a gap - a subscriber may triage on it. */
  reason: GapReason;
}

// ---------------------------------------------------------------------------
// 3. Liveness / heartbeat watchdog
// ---------------------------------------------------------------------------

/**
 * Did the transport fall silent? `lastActivityAtMs` must be refreshed by data
 * frames AND heartbeats only - never by a state transition or an `onopen`.
 * Unknown liveness (null) counts as silent once we are past the first frame,
 * which the caller expresses by passing the connect instant as lastActivityAt.
 */
export function isFeedSilent(nowMs: number, lastActivityAtMs: number | null, windowMs: number = WATCHDOG_SILENCE_MS): boolean {
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs)) return true;
  if (typeof lastActivityAtMs !== "number" || !Number.isFinite(lastActivityAtMs)) return true;
  const age = nowMs - lastActivityAtMs;
  // A client clock that jumped backwards is not evidence of life.
  return age < 0 || age > windowMs;
}

/** Age of the newest transport activity (null when there never was any). */
export function activityAgeMs(nowMs: number, lastActivityAtMs: number | null): number | null {
  if (lastActivityAtMs === null || !Number.isFinite(nowMs) || !Number.isFinite(lastActivityAtMs)) return null;
  return Math.max(0, nowMs - lastActivityAtMs);
}

// ---------------------------------------------------------------------------
// 4. Tab visibility
// ---------------------------------------------------------------------------

export type VisibilityAction = "close-feed" | "reconnect-now";

/**
 * What to do on a `visibilitychange`: a hidden tab holds an SSE socket the
 * browser may freeze anyway, so we close it (no phantom reconnect timers, no
 * battery burn); a tab that becomes visible again deserves an immediate try
 * instead of sitting out the remaining backoff.
 */
export function decideVisibilityAction(hidden: boolean, wasStarted: boolean): VisibilityAction | null {
  if (!wasStarted) return null;
  return hidden ? "close-feed" : "reconnect-now";
}
