/**
 * REALTIME lane — feed-health view model + merge rules regression suite.
 *
 * Run:  node --test tests/js/pro_feed.test.mjs
 *   or: node tests/js/pro_feed.test.mjs
 *
 * Imports the REAL frontend module (Node 24 strips the TS types; the only
 * non-erasable imports in feedHealth.ts are `import type`, removed before
 * resolution, so the `@/` alias never has to resolve here).
 *
 * Pinned contracts:
 *  - the derived store commits AT MOST once per second while SSE ticks fly
 *    (no setState storm), but never hides a state-machine change or a gap;
 *  - gapCount is monotonic and prefers the client's own count when present;
 *  - tone/label selectors never claim LIVE without fresh evidence;
 *  - mergeSnapshotByRule can never regress state_version;
 *  - the query→toast bridge rules skip auth (banner owns it) and aborts, and
 *    never invent error text.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  FEED_COMMIT_INTERVAL_MS,
  FEED_STALE_AFTER_MS,
  FEED_TONE_LABEL,
  advanceFeedAges,
  buildFeedHealth,
  computeFeedCommit,
  deriveDataAgeMs,
  formatFeedLabel,
  initialFeedHealth,
  isFeedGap,
  mergeSnapshotByRule,
  probeFromHealth,
  probeFromStatus,
  selectFeedAgeMs,
  selectFeedTone,
  shouldToastError,
  toastTextForError,
} from "../../frontend/src/lib/feedHealth.ts";

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

const T0 = 1_700_000_000_000;

function status(over = {}) {
  return {
    state: "connected",
    lastVersion: 100,
    lastMessageAt: T0,
    reconnectAttempts: 0,
    ...over,
  };
}

/** A duck-typed ApiError (the real class lives behind the `@/` alias). */
function apiError(over = {}) {
  const e = new Error(over.message ?? "Backend unreachable.");
  e.name = "ApiError";
  Object.assign(e, {
    status: 502,
    code: "NETWORK_ERROR",
    requestId: "altui_abc_1",
    retryable: true,
    isAuthError: false,
    ...over,
  });
  return e;
}

// ---------------------------------------------------------------------------
// throttling / coalescing
// ---------------------------------------------------------------------------

test("first status always commits (chrome must not start empty)", () => {
  const res = computeFeedCommit(initialFeedHealth, status(), T0);
  assert.equal(res.committed, true);
  assert.equal(res.health.connectionState, "connected");
  assert.equal(res.health.lastVersion, 100);
  assert.equal(res.health.lastEventAt, T0);
  assert.equal(res.health.dataAgeMs, 0);
});

test("SAFETY: tick-storm at SSE cadence coalesces to <=1 commit/second", () => {
  let h = computeFeedCommit(initialFeedHealth, status({ lastVersion: 1 }), T0).health;
  let commits = 0;
  // 50 frames across ~900ms (18ms apart) — the real `state_version` bump rate.
  for (let i = 2; i <= 51; i++) {
    const res = computeFeedCommit(h, status({ lastVersion: i, lastMessageAt: T0 + i * 18 }), T0 + i * 18);
    if (res.committed) {
      commits += 1;
      h = res.health;
    }
  }
  assert.equal(commits, 0, "coalesced window must not commit per-tick churn");
  // ...and once the cadence window is due, it commits exactly once with the
  // freshest version of the burst.
  const due = computeFeedCommit(h, status({ lastVersion: 52, lastMessageAt: T0 + 1100 }), T0 + 1100);
  assert.equal(due.committed, true);
  assert.equal(due.health.lastVersion, 52);
  assert.ok(FEED_COMMIT_INTERVAL_MS === 1000);
});

test("state-machine changes and gaps bypass the throttle", () => {
  const h = computeFeedCommit(initialFeedHealth, status({ lastVersion: 5 }), T0).health;
  // drop to reconnecting 10ms later — must commit now, not in 990ms
  const drop = computeFeedCommit(h, status({ state: "reconnecting", lastVersion: 5 }), T0 + 10);
  assert.equal(drop.committed, true);
  assert.equal(drop.health.connectionState, "reconnecting");
  // resume with data = gap -> commit + count
  const back = computeFeedCommit(drop.health, status({ lastVersion: 9, lastMessageAt: T0 + 20 }), T0 + 20);
  assert.equal(back.committed, true);
  assert.equal(back.gap, true);
  assert.equal(back.health.gapCount, 1);
});

test("force commit (1Hz flush of a coalesced status) still folds gaps", () => {
  const h = computeFeedCommit(initialFeedHealth, status({ lastVersion: 7 }), T0).health;
  const coalesced = computeFeedCommit(h, status({ lastVersion: 8, lastMessageAt: T0 + 100 }), T0 + 100);
  assert.equal(coalesced.committed, false);
  const forced = computeFeedCommit(h, status({ lastVersion: 8, lastMessageAt: T0 + 100 }), T0 + 500, true);
  assert.equal(forced.committed, true);
  assert.equal(forced.health.lastVersion, 8);
  assert.equal(forced.health.lastCommitAt, T0 + 500);
});

test("advanceFeedAges grows dataAgeMs without touching versions", () => {
  const h = computeFeedCommit(initialFeedHealth, status({ lastVersion: 3, lastMessageAt: T0 }), T0).health;
  const later = advanceFeedAges(h, T0 + 4000);
  assert.equal(later.dataAgeMs, 4000);
  assert.equal(later.lastVersion, 3);
  assert.equal(later.gapCount, 0);
  // identical age -> same object identity (cheap-render: no spurious notify)
  assert.equal(advanceFeedAges(later, T0 + 4000), later);
  // no frame ever -> nothing to advance
  assert.equal(advanceFeedAges(initialFeedHealth, T0 + 9000), initialFeedHealth);
});

test("deriveDataAgeMs is clock-safe: null stays null, negative clamps to 0", () => {
  assert.equal(deriveDataAgeMs(null, T0), null);
  assert.equal(deriveDataAgeMs(Number.NaN, T0), null);
  assert.equal(deriveDataAgeMs(T0 + 5000, T0), 0);
  assert.equal(deriveDataAgeMs(T0, T0 + 1500), 1500);
});

// ---------------------------------------------------------------------------
// gap detection
// ---------------------------------------------------------------------------

test("gap: resume after interruption WITH data, reconnect-attempt bump, version regression", () => {
  const live = probeFromStatus(status({ lastVersion: 40, lastMessageAt: T0 }));
  const down = probeFromStatus({ state: "disconnected", lastVersion: 40, lastMessageAt: T0, reconnectAttempts: 0 });
  assert.equal(isFeedGap(live, down), false, "going down is a state change, not a gap yet");
  assert.equal(isFeedGap(down, live), true, "resume after loss = gap");
  assert.equal(
    isFeedGap(live, probeFromStatus({ ...status(), reconnectAttempts: 1 })),
    true,
    "each failed attempt is an interrupted run",
  );
  assert.equal(
    isFeedGap(live, probeFromStatus({ ...status(), lastVersion: 12 })),
    true,
    "version regression (server restart re-seed) is a gap",
  );
});

test("NOT a gap: version jump while connected, first-ever frame, fresh-session resume", () => {
  const a = probeFromStatus(status({ lastVersion: 10 }));
  assert.equal(isFeedGap(a, probeFromStatus(status({ lastVersion: 41 }))), false, "protocol re-emits full state on jumps");
  assert.equal(isFeedGap(null, a), false, "no baseline -> nothing lost yet");
  const noData = probeFromStatus({ state: "disconnected", lastVersion: null, lastMessageAt: null, reconnectAttempts: 0 });
  assert.equal(isFeedGap(noData, a), false, "resume with no prior data lost nothing");
});

test("gapCount never regresses; client gapCount wins when exposed", () => {
  const start = { ...initialFeedHealth, gapCount: 4, lastVersion: 50, connectionState: "connected", lastCommitAt: T0 };
  const noClient = buildFeedHealth(start, probeFromStatus(status({ lastVersion: 50 })), T0 + 1000, true);
  assert.equal(noClient.gapCount, 5);
  // HARDEN client reports 12 provable missing versions -> the higher truth wins
  const withClient = buildFeedHealth(
    start,
    probeFromStatus(status({ lastVersion: 50, gapCount: 12 })),
    T0 + 1000,
    false,
  );
  assert.equal(withClient.gapCount, 12);
  // client reports LOWER than shown -> never understate (monotonic chrome)
  const lower = buildFeedHealth(start, probeFromStatus(status({ gapCount: 2 })), T0 + 1000, false);
  assert.equal(lower.gapCount, 4);
});

test("probeFromStatus/probeFromHealth round-trip the comparable slice", () => {
  const s = status({ state: "failed", lastVersion: 9, lastMessageAt: T0 + 1, reconnectAttempts: 7, gapCount: 3 });
  const p = probeFromStatus(s);
  assert.deepEqual(p, { state: "failed", lastVersion: 9, reconnectAttempts: 7, lastEventAt: T0 + 1, clientGapCount: 3 });
  const h = buildFeedHealth(initialFeedHealth, p, T0 + 5000, false);
  assert.deepEqual(probeFromHealth(h), p);
});

// ---------------------------------------------------------------------------
// tone / label selectors
// ---------------------------------------------------------------------------

test("tone: LIVE needs connection AND fresh evidence", () => {
  const base = { connectionState: "connected", lastEventAt: T0 };
  assert.equal(selectFeedTone(base, T0 + 900), "live");
  assert.equal(selectFeedTone(base, T0 + FEED_STALE_AFTER_MS + 1), "stale", "silent 10s+ is NOT live");
  assert.equal(selectFeedTone({ connectionState: "connected", lastEventAt: null }, T0), "stale", "never any frame");
  assert.equal(selectFeedTone({ connectionState: "reconnecting", lastEventAt: T0 }, T0 + 100), "reconnecting");
  assert.equal(selectFeedTone({ connectionState: "disconnected", lastEventAt: T0 }, T0 + 100), "down");
  assert.equal(selectFeedTone({ connectionState: "failed", lastEventAt: T0 }, T0 + 100), "down");
  assert.equal(selectFeedAgeMs(base, T0 + 250), 250);
});

test("formatFeedLabel shows tone, version, age and gaps — never invented fields", () => {
  const h = { ...initialFeedHealth, connectionState: "connected", lastVersion: 812, lastEventAt: T0 - 400, gapCount: 1, lastCommitAt: T0 };
  assert.equal(formatFeedLabel(h, T0), "LIVE · v812 · 0.4s · 1 gap");
  assert.equal(
    formatFeedLabel({ ...h, gapCount: 3 }, T0),
    "LIVE · v812 · 0.4s · 3 gaps",
  );
  assert.equal(
    formatFeedLabel({ ...initialFeedHealth, connectionState: "disconnected" }, T0),
    "OFFLINE",
    "no data -> tone only, no fake version/age",
  );
  assert.equal(
    formatFeedLabel({ ...h, lastEventAt: T0 - 25_000 }, T0),
    "STALE · v812 · 25s · 1 gap",
  );
});

// ---------------------------------------------------------------------------
// realtime ⇄ REST merge rules (never regress state_version)
// ---------------------------------------------------------------------------

test("merge: an older REST poll never overwrites fresher live state", () => {
  const live = { state_version: 220, bid: 33.21, bars: [1, 2, 3] };
  const staleRest = { state_version: 219, bid: 30.0, bars: [] };
  assert.equal(mergeSnapshotByRule(live, staleRest), live);
  // and direction matters: fresher incoming wins outright
  assert.equal(mergeSnapshotByRule(staleRest, live), live);
});

test("merge: equal version carries over the lists tick frames omit", () => {
  const full = { state_version: 5, bars: [{ time: "x" }], features: [{ n: 1 }], predictions: [{ a: 1 }], bid: 1 };
  const tick = { state_version: 5, bid: 2, bars: undefined, features: undefined, predictions: undefined };
  const merged = mergeSnapshotByRule(full, tick);
  assert.equal(merged.bid, 2, "scalar live values DO update at the same version");
  assert.deepEqual(merged.bars, full.bars);
  assert.deepEqual(merged.features, full.features);
  assert.deepEqual(merged.predictions, full.predictions);
});

test("merge: a newer version replaces wholesale (fresh heavyweight lists ride state frames)", () => {
  const cur = { state_version: 5, bars: ["old"], bid: 1 };
  const next = { state_version: 6, bars: ["new"], bid: 2 };
  assert.deepEqual(mergeSnapshotByRule(cur, next), next);
});

test("merge: undefined on either side is not an error (boot ordering)", () => {
  const s = { state_version: 1 };
  assert.equal(mergeSnapshotByRule(undefined, s), s);
  assert.equal(mergeSnapshotByRule(s, undefined), s);
  assert.equal(mergeSnapshotByRule(undefined, undefined), undefined);
});

test("merge: repeated folding is idempotent (no version drift over 100 renders)", () => {
  let cur = mergeSnapshotByRule(undefined, { state_version: 10, bars: ["a"] });
  for (let i = 0; i < 100; i++) {
    cur = mergeSnapshotByRule(cur, { state_version: 9, bars: ["older"] });
    cur = mergeSnapshotByRule(cur, { state_version: 10, bars: undefined });
  }
  assert.equal(cur.state_version, 10);
  assert.deepEqual(cur.bars, ["a"]);
});

// ---------------------------------------------------------------------------
// react-query → toast bridge rules
// ---------------------------------------------------------------------------

test("auth errors never toast (AppShell banner owns them)", () => {
  assert.equal(shouldToastError(apiError({ status: 401, code: "UNAUTHORIZED", isAuthError: true })), false);
  assert.equal(shouldToastError(apiError({ status: 403, code: "FORBIDDEN", isAuthError: true })), false);
  assert.equal(shouldToastError(apiError({ code: "AUTH_CONFIG_ERROR", isAuthError: true })), false);
});

test("aborts and non-errors never toast; real failures do", () => {
  const abort = new Error("The user aborted a request.");
  abort.name = "AbortError";
  assert.equal(shouldToastError(abort), false);
  assert.equal(shouldToastError(new Error("boom")), true);
  assert.equal(shouldToastError(apiError()), true);
  assert.equal(shouldToastError(null), false);
  assert.equal(shouldToastError(undefined), false);
  assert.equal(shouldToastError("string failure"), false, "strings are not surfaced blindly");
});

test("toast text keeps backend words + request_id + query label", () => {
  assert.equal(
    toastTextForError(apiError({ message: "ENGINE_UNAVAILABLE: engine detached", requestId: "altui_x_9" }), ["risk-status"]),
    "[risk-status] ENGINE_UNAVAILABLE: engine detached (request_id: altui_x_9)",
  );
  assert.equal(toastTextForError(new Error("plain failure"), ["audit-events"]), "[audit-events] plain failure");
  assert.equal(
    toastTextForError(new Error(""), ["mt5-status"]),
    "[mt5-status] Request failed — backend response unavailable.",
  );
  assert.equal(toastTextForError(apiError({ message: "no id", requestId: null })), "no id");
});

// ---------------------------------------------------------------------------
// structural guard: the chrome must not fabricate verdict words
// ---------------------------------------------------------------------------

test("SAFETY: feed chrome vocabulary is closed (LIVE/RECONNECTING/STALE/OFFLINE only)", () => {
  assert.deepEqual(Object.keys(FEED_TONE_LABEL).sort(), ["down", "live", "reconnecting", "stale"]);
  assert.deepEqual(Object.values(FEED_TONE_LABEL).sort(), ["LIVE", "OFFLINE", "RECONNECTING", "STALE"]);
});
