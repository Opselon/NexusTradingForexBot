/**
 * REALTIME-HARDEN (logic lane 1/5) - regression battery for the ALT console SSE
 * realtime client's decision layer.
 *
 * Run:  node tests/js/pro_realtime.test.mjs
 *   or: node --test tests/js/pro_realtime.test.mjs
 *
 * Imports the REAL frontend modules. `frontend/src/websocket/rtMath.ts` is
 * erasable TypeScript with no import edges at all, so Node's type stripping
 * loads it directly. `realtimeSocket.ts` keeps the project's `@/` alias, which
 * Node cannot resolve, so a tiny module.resolve hook maps the two bare edges
 * (`./rtMath`, `@/types/realtime`) onto their real .ts files - the file under
 * test is loaded byte-for-byte from disk, never a copy.
 *
 * WHAT THIS PINS (each item is a way the console could lie about its own
 * liveness or data completeness):
 *   1. Reconnect backoff: exponential, equal-jittered, hard-bounded in
 *      [ceil/2, min(30s)], never 0, never Infinity, for hostile inputs; a
 *      bounded attempt budget whose terminal state is an honest `failed`;
 *      manual retry() buys a fresh cycle.
 *   2. state_version ordering: v <= last is DROPPED with no liveness credit;
 *      v > last+1 applies the newer truth AND counts the missing versions as a
 *      gap; the first frame is a BASELINE (never a fabricated huge gap); only
 *      the first full `state` snapshot of a fresh stream may re-base a
 *      restarted server's counter.
 *   3. Gap accounting is cumulative/monotonic; the onGap resync signal is
 *      leading-edge throttled with a GUARANTEED trailing flush (nothing
 *      swallowed, no REST stampede).
 *   4. Heartbeat watchdog: silence (no frame AND no heartbeat) past the window
 *      force-closes into `reconnecting`; the watchdog can never produce
 *      `connected`; a stream that only replays old frames ages out.
 *   5. Wiring of the REAL client: truthful state machine (no `connected`
 *      without wire evidence), no double-subscribe under repeated start(),
 *      every timer cleared on stop(), tab-visibility pause/resume, listener
 *      unbinding.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

import {
  BACKOFF_CAP_MS,
  BACKOFF_MAX_EXPONENT,
  GAP_SIGNAL_MIN_INTERVAL_MS,
  RECONNECT_ATTEMPT_BUDGET,
  STALE_AFTER_MS,
  HEARTBEAT_GRACE_MS,
  WATCHDOG_SILENCE_MS,
  acceptsSnapshotEpochReset,
  activityAgeMs,
  backoffCeilingMs,
  backoffDelayMs,
  classifyVersion,
  decideAfterFailedAttempt,
  decideGapSignal,
  decideVisibilityAction,
  isFeedSilent,
  nextGapCount,
  reconnectBudgetExhausted,
} from "../../frontend/src/websocket/rtMath.ts";

// ---------------------------------------------------------------------------
// 1. Backoff - exponential, jittered, hard-capped
// ---------------------------------------------------------------------------

test("backoff ceiling: exponential growth capped at 30s, exponent clamped", () => {
  assert.equal(backoffCeilingMs(0), 1_000);
  assert.equal(backoffCeilingMs(1), 2_000);
  assert.equal(backoffCeilingMs(4), 16_000);
  assert.equal(backoffCeilingMs(BACKOFF_MAX_EXPONENT), BACKOFF_CAP_MS);
  // Beyond the exponent clamp the ceiling saturates: it cannot keep growing.
  for (const n of [6, 10, 32, 1_000, 1e9, Number.MAX_SAFE_INTEGER]) {
    assert.equal(backoffCeilingMs(n), BACKOFF_CAP_MS, `attempt ${n}`);
  }
  // The clamp must be high enough that the cap is reachable (no dead ceiling).
  assert.ok(2 ** BACKOFF_MAX_EXPONENT * 1_000 >= BACKOFF_CAP_MS);
});

test("backoff delay: HARD bounds for every attempt and every rand", () => {
  const rands = [0, 0.001, 0.25, 0.5, 0.75, 0.999, 1, NaN, -5, 42, Infinity, undefined];
  for (let attempt = -3; attempt < 45; attempt++) {
    for (const rand of rands) {
      const d = backoffDelayMs(attempt, rand);
      assert.equal(typeof d, "number");
      assert.ok(Number.isFinite(d), `finite: attempt=${attempt} rand=${rand}`);
      assert.ok(d >= 0, `>= 0: attempt=${attempt} rand=${rand} got ${d}`);
      assert.ok(d <= BACKOFF_CAP_MS, `<= cap: attempt=${attempt} rand=${rand} got ${d}`);
      if (typeof rand === "number" && Number.isFinite(rand) && rand >= 0 && rand <= 1) {
        // Equal jitter: never below half the ceiling (no 0ms stampede retry).
        const ceil = backoffCeilingMs(attempt);
        assert.ok(d >= Math.floor(ceil / 2) - 1, `>= half: ${d} vs ceil ${ceil}`);
        assert.ok(d <= ceil, `<= ceil: ${d} vs ${ceil}`);
        assert.equal(d, backoffDelayMs(attempt, rand), "deterministic in rand");
      }
    }
  }
  // Endpoints exact: no hidden off-by-one in the jitter band.
  assert.equal(backoffDelayMs(0, 0), 500);
  assert.equal(backoffDelayMs(0, 1), 1_000);
  assert.equal(backoffDelayMs(5, 0), BACKOFF_CAP_MS / 2);
  assert.equal(backoffDelayMs(5, 1), BACKOFF_CAP_MS);
  assert.equal(backoffDelayMs(99, 1), BACKOFF_CAP_MS);
});

test("SAFETY: backoff never collapses into a hot loop (delay > 0 always)", () => {
  for (let attempt = 0; attempt < 80; attempt++) {
    assert.ok(backoffDelayMs(attempt, 0) > 0, `attempt ${attempt}`);
    assert.ok(backoffDelayMs(attempt, 0.5) > 0, `attempt ${attempt} mid`);
  }
});

test("attempt budget: bounded, monotonic decision chain, honest terminal state", () => {
  assert.equal(reconnectBudgetExhausted(0), false);
  assert.equal(reconnectBudgetExhausted(RECONNECT_ATTEMPT_BUDGET - 1), false);
  assert.equal(reconnectBudgetExhausted(RECONNECT_ATTEMPT_BUDGET), true);
  assert.equal(reconnectBudgetExhausted(1e9), true);
  assert.equal(reconnectBudgetExhausted(NaN), false, "hostile input cannot force failure");

  let decision = decideAfterFailedAttempt(0);
  const delays = [];
  let prevAttempts = 0;
  for (let i = 0; i < RECONNECT_ATTEMPT_BUDGET + 6 && decision.state === "reconnecting"; i++) {
    assert.ok(decision.attempts > prevAttempts, "attempt counter strictly grows");
    assert.ok(decision.delayMs !== null, "non-terminal decision carries a delay");
    prevAttempts = decision.attempts;
    delays.push(decision.delayMs);
    decision = decideAfterFailedAttempt(decision.attempts);
  }
  assert.equal(decision.state, "failed");
  assert.equal(decision.delayMs, null, "terminal decision schedules NO timer");
  assert.ok(reconnectBudgetExhausted(decision.attempts));
  assert.ok(Math.max(...delays) <= BACKOFF_CAP_MS);
  assert.equal(decision.attempts, RECONNECT_ATTEMPT_BUDGET, "budget is exact, not drifting");
});

test("manual retry semantics: a reset attempt index restarts the short delays", () => {
  const exhausted = decideAfterFailedAttempt(RECONNECT_ATTEMPT_BUDGET - 1);
  assert.equal(exhausted.state, "failed");
  const afterReset = decideAfterFailedAttempt(0);
  assert.equal(afterReset.state, "reconnecting");
  assert.ok(afterReset.delayMs !== null && afterReset.delayMs <= backoffCeilingMs(0));
});

// ---------------------------------------------------------------------------
// 2. state_version ordering - baseline / accept / gap / drop / epoch re-base
// ---------------------------------------------------------------------------

test("classifyVersion: baseline and in-sequence", () => {
  const base = classifyVersion(10, null);
  assert.equal(base.decision, "baseline");
  assert.equal(base.missing, 0, "first frame must never claim a huge gap");
  assert.equal(base.apply, true);
  assert.equal(base.live, true);
  assert.equal(base.lastVersion, 10);
  assert.equal(classifyVersion(0, null).decision, "baseline", "v0 is a legal baseline");
  const ok = classifyVersion(11, 10);
  assert.equal(ok.decision, "accept");
  assert.equal(ok.missing, 0, "v = last+1 is NOT a gap");
  assert.equal(ok.apply, true);
  assert.equal(ok.live, true);
});

test("classifyVersion: out-of-order (v < last) stays DROPPED, with no liveness", () => {
  for (const v of [1, 9, 0, -5]) {
    const o = classifyVersion(v, 10);
    assert.equal(o.decision, "drop-stale", `v=${v}`);
    assert.equal(o.apply, false, "a stale frame never mutates the snapshot");
    assert.equal(o.live, false, "a stale frame must NOT refresh the watchdog");
    assert.equal(o.lastVersion, 10, "the version pointer never regresses here");
    assert.equal(o.missing, 0, "a drop is not a gap");
    assert.equal(o.rebaseline, false);
  }
});

test("classifyVersion: duplicate (v === last) is dropped, not applied twice", () => {
  const o = classifyVersion(10, 10);
  assert.equal(o.decision, "drop-duplicate");
  assert.equal(o.apply, false);
  assert.equal(o.live, false);
  assert.equal(o.lastVersion, 10);
});

test("classifyVersion: GAP (v > last+1) applies the truth and counts the loss", () => {
  const o = classifyVersion(15, 10);
  assert.equal(o.decision, "accept-gap");
  assert.equal(o.missing, 4, "11..14 were never delivered");
  assert.equal(o.apply, true, "the newer frame is real data - never discard it");
  assert.equal(o.live, true);
  assert.equal(o.lastVersion, 15);
  assert.equal(o.rebaseline, false);
  assert.equal(classifyVersion(11, 10).missing, 0);
  assert.equal(classifyVersion(12, 10).missing, 1);
  assert.equal(classifyVersion(1e6, 10).missing, 1e6 - 11, "a jump is reported honestly");
});

test("classifyVersion: a non-numeric version cannot be ordered and is never invented", () => {
  for (const v of [undefined, null, "11", {}, NaN, Infinity, -Infinity]) {
    const o = classifyVersion(v, 10);
    assert.equal(o.decision, "accept-unversioned", String(v));
    assert.equal(o.lastVersion, 10, "the pointer may not move on junk");
    assert.equal(o.missing, 0);
    assert.equal(o.rebaseline, false);
  }
  // No history at all + junk version: still no fabricated baseline pointer.
  const o = classifyVersion(undefined, null);
  assert.equal(o.decision, "accept-unversioned");
  assert.equal(o.lastVersion, null);
  assert.equal(o.apply, true, "legacy payload still renders, unguarded");
});

test("epoch re-base: ONLY the first full state snapshot of a new stream", () => {
  assert.equal(acceptsSnapshotEpochReset(3, 10, true, true), true);
  assert.equal(acceptsSnapshotEpochReset(3, 10, false, true), false, "tick frames never qualify");
  assert.equal(acceptsSnapshotEpochReset(3, 10, true, false), false, "mid-stream never qualifies");
  assert.equal(acceptsSnapshotEpochReset(10, 10, true, true), false, "a tie is not a reset");
  assert.equal(acceptsSnapshotEpochReset(11, 10, true, true), false, "newer is not a reset");
  assert.equal(acceptsSnapshotEpochReset(3, null, true, true), false, "nothing to re-base from");
  assert.equal(acceptsSnapshotEpochReset("3", 10, true, true), false, "string version rejected");

  const o = classifyVersion(3, 10, true, true);
  assert.equal(o.decision, "reset-epoch");
  assert.equal(o.apply, true, "refusing it would freeze the console on a dead epoch");
  assert.equal(o.lastVersion, 3, "pointer moves onto the authoritative snapshot");
  assert.equal(o.missing, 0, "an epoch change is not a lost-frame claim");
  assert.equal(o.rebaseline, true);
  // Ordinary out-of-order behaviour is untouched when the privilege is absent.
  assert.equal(classifyVersion(3, 10, true, false).decision, "drop-stale");
  assert.equal(classifyVersion(3, 10, false, true).decision, "drop-stale");
  assert.equal(classifyVersion(3, 10).decision, "drop-stale", "2-arg call keeps old semantics");
});

test("nextGapCount: cumulative, monotonic, hostile-input safe", () => {
  assert.equal(nextGapCount(0, 4), 4);
  assert.equal(nextGapCount(4, 1), 5);
  assert.equal(nextGapCount(5, 0), 5, "an accept must never decrement");
  assert.equal(nextGapCount(5, -99), 5, "negative missing cannot launder the count");
  assert.equal(nextGapCount(5, NaN), 5);
  assert.equal(nextGapCount(5, Infinity), 5, "an unprovable count is not added");
  assert.equal(nextGapCount(NaN, 3), 3);
  assert.equal(nextGapCount(-1, 2), 2, "counters are never negative");
  assert.equal(nextGapCount(2, 2.9), 4, "fractions truncate, never round up a fault");
});

test("gap signal: leading edge fires, deferral always reports a finite wait", () => {
  assert.deepEqual(decideGapSignal(1_000, null), { action: "signal", waitMs: 0 });
  assert.equal(decideGapSignal(1_000, 1_000).action, "defer");
  const d = decideGapSignal(1_500, 1_000);
  assert.equal(d.action, "defer");
  assert.ok(d.waitMs > 0 && d.waitMs <= GAP_SIGNAL_MIN_INTERVAL_MS, `bounded wait: ${d.waitMs}`);
  assert.equal(decideGapSignal(3_000, 1_000).action, "signal", "window elapsed");
  assert.equal(decideGapSignal(9_000, 1_000).action, "signal");
  // A backwards clock jump must not wedge the throttle forever.
  assert.equal(decideGapSignal(0, 9_000).action, "signal");
  // Hostile inputs fail toward signalling (never a swallowed resync request).
  assert.equal(decideGapSignal(NaN, 1_000).action, "signal");
  assert.equal(decideGapSignal(1_500, NaN).action, "signal");
});

// ---------------------------------------------------------------------------
// 3. Heartbeat watchdog - silence is the only input
// ---------------------------------------------------------------------------

test("isFeedSilent: a frame OR a heartbeat inside the window keeps the feed alive", () => {
  const now = 100_000;
  assert.equal(isFeedSilent(now, now - 1_000), false);
  assert.equal(isFeedSilent(now, now - WATCHDOG_SILENCE_MS), false, "exactly at the window is not yet silent");
  assert.equal(isFeedSilent(now, now - (WATCHDOG_SILENCE_MS + 1)), true);
  assert.equal(isFeedSilent(now, now - 60_000), true, "a minute of total silence is dead");
  assert.equal(WATCHDOG_SILENCE_MS, STALE_AFTER_MS + HEARTBEAT_GRACE_MS);
});

test("isFeedSilent: unknown or backwards evidence is never evidence of life", () => {
  assert.equal(isFeedSilent(100_000, null), true, "nothing heard yet => silent");
  assert.equal(isFeedSilent(100_000, 200_000), true, "clock jump => silent, never 'connected'");
  assert.equal(isFeedSilent(NaN, 100_000), true);
  assert.equal(isFeedSilent(100_000, NaN), true);
  assert.equal(isFeedSilent(100_000, "1000"), true);
  assert.equal(activityAgeMs(100_000, null), null);
  assert.equal(activityAgeMs(100_000, 95_000), 5_000);
  assert.equal(activityAgeMs(100_000, 105_000), 0, "age is never negative");
  assert.equal(activityAgeMs(NaN, 95_000), null);
});

test("visibility action map: hidden closes, visible retries, un-started does nothing", () => {
  assert.equal(decideVisibilityAction(true, true), "close-feed");
  assert.equal(decideVisibilityAction(false, true), "reconnect-now");
  assert.equal(decideVisibilityAction(true, false), null);
  assert.equal(decideVisibilityAction(false, false), null);
});

// ---------------------------------------------------------------------------
// 4. The REAL client wiring - fake EventSource + fake document + fake timers
// ---------------------------------------------------------------------------

class FakeEventSource {
  static instances = [];
  url;
  readyState = 0;
  closed = false;
  onopen = null;
  handlers = new Map();
  constructor(url) {
    FakeEventSource.instances.push(this);
    this.url = url;
  }
  addEventListener(name, fn) {
    if (!this.handlers.has(name)) this.handlers.set(name, []);
    this.handlers.get(name).push(fn);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  /** Test driver: dispatch a named SSE event (data === undefined => native error). */
  emit(name, data) {
    for (const fn of this.handlers.get(name) ?? []) fn({ data });
  }
  /** Test driver: the handshake completed (proves NOTHING about liveness). */
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

function frame(version, extra = {}) {
  return JSON.stringify({ state_version: version, engine_running: true, symbol: "XAUUSD", ...extra });
}

/**
 * Deterministic timer + clock harness. Installed ONLY around synchronous test
 * bodies (no awaits while patched) so the runner's own timers are never stolen.
 */
function installFakeTimers(startMs) {
  const saved = {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
    now: Date.now,
  };
  let clock = startMs;
  let seq = 0;
  /** @type {Map<number,{at:number,fn:Function,every:number|null}>} */
  const timers = new Map();
  globalThis.Date.now = () => clock;
  globalThis.setTimeout = (fn, ms) => {
    const id = ++seq;
    timers.set(id, { at: clock + Math.max(0, Number(ms) || 0), fn, every: null });
    return id;
  };
  globalThis.clearTimeout = (id) => timers.delete(id);
  globalThis.setInterval = (fn, ms) => {
    const id = ++seq;
    timers.set(id, { at: clock + Math.max(1, Number(ms) || 1), fn, every: Math.max(1, Number(ms) || 1) });
    return id;
  };
  globalThis.clearInterval = (id) => timers.delete(id);
  const api = {
    get now() {
      return clock;
    },
    /** Advance the clock, firing every due callback in chronological order. */
    advance(ms) {
      const target = clock + ms;
      let fired = 0;
      for (;;) {
        let dueId = null;
        for (const [id, t] of timers) {
          if (t.at <= target && (dueId === null || t.at < timers.get(dueId).at)) dueId = id;
        }
        if (dueId === null) break;
        const t = timers.get(dueId);
        clock = t.at;
        if (t.every === null) timers.delete(dueId);
        else t.at = clock + t.every;
        fired++;
        t.fn();
      }
      clock = target;
      return fired;
    },
    /** Number of live timers (retry / watchdog / gap flush) - leak assertion. */
    live() {
      return timers.size;
    },
    restore() {
      Object.assign(globalThis, saved);
    },
  };
  return api;
}

async function freshClient() {
  FakeEventSource.instances = [];
  const docHandlers = [];
  globalThis.EventSource = FakeEventSource;
  globalThis.sessionStorage = { getItem: () => null };
  globalThis.document = {
    hidden: false,
    addEventListener: (_name, h) => docHandlers.push(h),
    removeEventListener: (_name, h) => {
      const i = docHandlers.indexOf(h);
      if (i >= 0) docHandlers.splice(i, 1);
    },
  };
  const mod = await loadSocketModule();
  const client = new mod.NseRealtimeClient();
  const statuses = [];
  const gaps = [];
  const snaps = [];
  client.subscribeStatus((s) => statuses.push(s));
  client.subscribeGap((g) => gaps.push(g));
  client.subscribe((s) => snaps.push(s));
  return { client, mod, statuses, gaps, snaps, docHandlers, doc: globalThis.document };
}

function es() {
  return FakeEventSource.instances[FakeEventSource.instances.length - 1];
}

const socketModulePromise = (async () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const srcDir = resolve(here, "..", "..", "frontend", "src");
  registerHooks({
    resolve(spec, ctx, next) {
      if (spec === "./rtMath") {
        return { url: pathToFileURL(resolve(srcDir, "websocket", "rtMath.ts")).href, shortCircuit: true };
      }
      if (spec === "@/types/realtime") {
        return { url: pathToFileURL(resolve(srcDir, "types", "realtime.ts")).href, shortCircuit: true };
      }
      return next(spec, ctx);
    },
  });
  return import(pathToFileURL(resolve(srcDir, "websocket", "realtimeSocket.ts")).href);
});
let socketModule = null;
async function loadSocketModule() {
  socketModule ??= socketModulePromise();
  return socketModule;
}

test("H6 states stay truthful: no 'connected' before wire evidence", async () => {
  const timers = installFakeTimers(1_000_000);
  try {
    const { client, statuses } = await freshClient();
    client.start();
    assert.equal(client.currentStatus().state, "disconnected", "connecting is not connected");
    es().open();
    assert.equal(client.currentStatus().state, "disconnected", "onopen alone must NOT claim connected");
    es().emit("heartbeat", "{}");
    assert.equal(client.currentStatus().state, "connected", "a keepalive is real transport evidence");
    es().emit("state", frame(5));
    assert.equal(client.currentStatus().state, "connected");
    assert.equal(client.currentStatus().lastVersion, 5);
    assert.equal(statuses.filter((s) => s.state === "connected").length, 1, "no state churn per frame");
    assert.equal(typeof client.currentStatus().gapCount, "number", "additive gapCount is published");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("H3 dropped frames earn no liveness credit and no listener traffic", async () => {
  const timers = installFakeTimers(1_100_000);
  try {
    const { client, snaps } = await freshClient();
    client.start();
    es().emit("state", frame(20));
    const good = client.currentStatus();
    timers.advance(1_000);
    es().emit("tick", frame(19, { event: "tick" }));
    es().emit("tick", frame(20, { event: "tick" }));
    assert.equal(client.currentStatus().lastVersion, good.lastVersion, "pointer never regresses");
    assert.equal(client.currentStatus().lastMessageAt, good.lastMessageAt, "drops earn no liveness");
    assert.equal(snaps.length, 1, "no listener notification for a dropped frame");
    assert.equal(client.gapCount, 0, "a drop is not a gap");
    // A replay-only stream must age out through the watchdog (H2 + H3 joint).
    for (let i = 0; i < 3; i++) es().emit("tick", frame(19, { event: "tick" }));
    assert.equal(client.currentStatus().state, "connected", "not yet past the window");
    timers.advance(WATCHDOG_SILENCE_MS + 5_001);
    assert.equal(client.currentStatus().state, "reconnecting", "history-only stream is dead evidence");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("H1 reconnect delay is bounded by the published backoff law", async () => {
  const timers = installFakeTimers(1_200_000);
  try {
    const { client } = await freshClient();
    client.setJitterSource(() => 0.5); // deterministic: delay == 3/4 of the ceiling
    client.start();
    es().emit("error", undefined); // transport error => scheduled retry
    assert.equal(client.currentStatus().state, "reconnecting");
    const before = FakeEventSource.instances.length;
    const delay = backoffDelayMs(0, 0.5);
    assert.equal(delay, 750, "first failure waits under a second, in [ceil/2, ceil]");
    timers.advance(delay - 1);
    assert.equal(FakeEventSource.instances.length, before, "retry not early");
    timers.advance(2);
    assert.equal(FakeEventSource.instances.length, before + 1, "retry fired at the jittered delay");
    // Second failure doubles the ceiling (exponential, same pinned jitter).
    es().emit("error", undefined);
    const delay2 = backoffDelayMs(1, 0.5);
    assert.equal(delay2, 1_500);
    timers.advance(delay2 + 1);
    assert.equal(FakeEventSource.instances.length, before + 2);
    client.stop();
  } finally {
    timers.restore();
  }
});

test("H3 gap: applied, counted, signaled once then flushed (never swallowed)", async () => {
  const timers = installFakeTimers(2_000_000);
  try {
    const { client, gaps, statuses } = await freshClient();
    client.start();
    es().emit("state", frame(10));
    es().emit("tick", frame(15, { event: "tick" })); // 11..14 lost
    assert.equal(client.currentStatus().gapCount, 4);
    assert.equal(client.currentStatus().lastVersion, 15, "the newer truth IS applied");
    assert.equal(gaps.length, 1, "leading edge fires immediately");
    assert.equal(gaps[0].reason, "version-gap");
    assert.equal(gaps[0].missing, 4);
    assert.equal(gaps[0].expected, 10);
    assert.equal(gaps[0].received, 15);
    assert.equal(gaps[0].source, "tick");
    assert.equal(gaps[0].gapCount, 4, "the signal carries the cumulative count");
    assert.equal(statuses[statuses.length - 1].gapCount, 4, "status publishes it too");

    es().emit("tick", frame(100, { event: "tick" })); // 84 more lost, same window
    assert.equal(client.currentStatus().gapCount, 88, "counting is NEVER throttled");
    assert.equal(gaps.length, 1, "signalling IS rate-limited (no REST stampede)");

    timers.advance(GAP_SIGNAL_MIN_INTERVAL_MS + 10); // trailing flush must land
    assert.equal(gaps.length, 2, `deferred gap flushed (got ${gaps.length})`);
    assert.equal(gaps[1].received, 100, "the newest deferred gap wins the flush");
    assert.equal(gaps[1].gapCount, 88, "flush reports the cumulative count");

    // Signal count can never exceed sightings, and never loses the last one.
    assert.ok(gaps.length <= 2, "bursts coalesce instead of replaying per sighting");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("H2 watchdog: total silence force-closes without ever claiming connected", async () => {
  const timers = installFakeTimers(3_000_000);
  try {
    const { client, statuses } = await freshClient();
    client.start();
    es().emit("state", frame(1));
    assert.equal(client.currentStatus().state, "connected");
    const sock = es();
    // Heartbeats keep it alive even with no data at all (engine paused).
    timers.advance(WATCHDOG_SILENCE_MS - 1);
    sock.emit("heartbeat", "{}");
    assert.equal(client.currentStatus().state, "connected", "keepalive is evidence, no data needed");
    timers.advance(WATCHDOG_SILENCE_MS + 5_001); // now truly silent
    assert.equal(sock.closed, true, "watchdog force-closes the half-open stream");
    assert.equal(client.currentStatus().state, "reconnecting", "never 'connected' on silence");
    assert.equal(
      statuses.filter((s) => s.state === "connected").length,
      1,
      "the watchdog never re-promotes the feed to connected",
    );
    client.stop();
  } finally {
    timers.restore();
  }
});

test("H4 visibility: hidden closes, visible retries at once and signals a resync", async () => {
  const timers = installFakeTimers(4_000_000);
  try {
    const { client, gaps, docHandlers, doc } = await freshClient();
    client.start();
    es().emit("state", frame(42));
    assert.equal(client.currentStatus().state, "connected");
    const before = FakeEventSource.instances.length;
    doc.hidden = true;
    for (const h of [...docHandlers]) h();
    assert.equal(es().closed, true, "hidden tab must not hold a frozen socket");
    assert.equal(client.currentStatus().state, "disconnected", "truthful: the feed IS closed");
    const whileHidden = FakeEventSource.instances.length;
    timers.advance(120_000);
    assert.equal(FakeEventSource.instances.length, whileHidden, "no reconnect storm behind a hidden tab");
    doc.hidden = false;
    for (const h of [...docHandlers]) h();
    assert.equal(FakeEventSource.instances.length, before + 1, "visible => immediate attempt, no backoff wait");
    assert.equal(client.currentStatus().state, "reconnecting", "an attempt is not a connection");
    assert.ok(gaps.some((g) => g.reason === "stream-paused"), "unseen-while-hidden asks for a resync");
    client.stop();
    assert.equal(docHandlers.length, 0, "H5: visibility listener is unbound on stop");
  } finally {
    timers.restore();
  }
});

test("H7 no double-subscribe: repeated start() keeps ONE stream; stop/start re-opens one", async () => {
  const timers = installFakeTimers(5_000_000);
  try {
    const { client } = await freshClient();
    client.start();
    client.start();
    client.start();
    assert.equal(FakeEventSource.instances.length, 1, "StrictMode churn must not stack sockets");
    assert.equal(timers.live(), 1, "exactly one watchdog interval (the original leak)");
    es().emit("state", frame(7));
    es().emit("error", undefined);
    assert.equal(client.currentStatus().state, "reconnecting");
    assert.equal(FakeEventSource.instances.length, 1, "the error path must not open a second socket at once");
    client.start();
    assert.equal(FakeEventSource.instances.length, 1, "start() while a stream exists is a no-op");
    client.stop();
    client.start();
    assert.equal(FakeEventSource.instances.length, 2, "start after stop reconnects once");
    client.stop();
    assert.equal(timers.live(), 0, "H5: no timer survives stop()");
  } finally {
    timers.restore();
  }
});

test("H1/H6 attempt budget ends in failed; retry() buys a fresh cycle", async () => {
  const timers = installFakeTimers(6_000_000);
  try {
    const { client } = await freshClient();
    client.start();
    es().emit("state", frame(1));
    const openedAt = FakeEventSource.instances.length;
    for (let i = 0; i < RECONNECT_ATTEMPT_BUDGET + 3; i++) {
      es().emit("error", undefined);
      timers.advance(BACKOFF_CAP_MS + 1_000); // outlive any scheduled backoff
    }
    assert.equal(
      client.currentStatus().state,
      "failed",
      `after ${RECONNECT_ATTEMPT_BUDGET} consecutive failures the only truth is failed`,
    );
    assert.equal(client.currentStatus().reconnectAttempts, RECONNECT_ATTEMPT_BUDGET);
    const afterFail = FakeEventSource.instances.length;
    timers.advance(BACKOFF_CAP_MS * 4);
    assert.equal(FakeEventSource.instances.length, afterFail, "`failed` must not keep dialing");
    client.retry();
    assert.equal(client.currentStatus().state, "reconnecting", "manual retry resets the budget");
    assert.equal(FakeEventSource.instances.length, afterFail + 1, "and attempts once, now");
    assert.equal(client.currentStatus().reconnectAttempts, 0, "budget reset is observable");
    es().emit("state", frame(2));
    assert.equal(client.currentStatus().state, "connected", "evidence, not promises");
    client.stop();
    assert.equal(timers.live(), 0);
    assert.ok(openedAt >= 1);
  } finally {
    timers.restore();
  }
});

test("H5 stop() clears every timer; nothing reconnects after stop", async () => {
  const timers = installFakeTimers(7_000_000);
  try {
    const { client } = await freshClient();
    client.start();
    es().emit("state", frame(1));
    es().emit("error", undefined); // schedules a retry timer
    assert.equal(client.currentStatus().state, "reconnecting");
    assert.ok(timers.live() >= 2, "watchdog + retry pending");
    const opened = FakeEventSource.instances.length;
    client.stop();
    assert.equal(timers.live(), 0, "H5: all timers cleared");
    timers.advance(120_000);
    assert.equal(FakeEventSource.instances.length, opened, "the retry timer is gone");
    assert.equal(es().closed, true, "the socket is closed");
    assert.equal(client.currentStatus().state, "disconnected");
    // A stray post-stop error is inert.
    es().emit("error", undefined);
    assert.equal(client.currentStatus().state, "disconnected");
    assert.equal(FakeEventSource.instances.length, opened, "stop() wins over everything");
    timers.restore();
  } catch (e) {
    timers.restore();
    throw e;
  }
});

test("backend error frame (SSE_SERIALIZATION_ERROR) is a gap, not a live claim", async () => {
  const timers = installFakeTimers(8_000_000);
  try {
    const { client, gaps } = await freshClient();
    client.start();
    es().emit("state", frame(10));
    assert.equal(client.currentStatus().state, "connected");
    es().emit("error", JSON.stringify({ event: "SSE_SERIALIZATION_ERROR", correlation_id: "sse-11" }));
    assert.equal(client.currentStatus().state, "connected", "the stream itself is still alive");
    assert.equal(client.gapCount, 1, "one version's payload was provably lost");
    assert.equal(gaps.length, 1);
    assert.equal(gaps[0].reason, "server-error-frame");
    assert.equal(gaps[0].received, 11, "the lost version is recovered from the correlation id");
    assert.equal(gaps[0].source, "error");
    assert.equal(client.currentStatus().lastVersion, 10, "a corrupted payload never advances data");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("server restart: first full snapshot of a new stream re-bases the pointer", async () => {
  const timers = installFakeTimers(9_000_000);
  try {
    const { client, snaps } = await freshClient();
    client.start();
    es().emit("state", frame(500));
    assert.equal(client.currentStatus().lastVersion, 500);
    // Stream dies, a NEW server (counter restarted) answers on reconnect.
    es().emit("error", undefined);
    timers.advance(BACKOFF_CAP_MS + 1_000);
    assert.equal(FakeEventSource.instances.length, 2, "a new EventSource was opened");
    es().emit("state", frame(3));
    assert.equal(client.currentStatus().lastVersion, 3, "pointer re-based, not frozen on a dead epoch");
    assert.equal(client.gapCount, 0, "an epoch change is not counted as lost frames");
    assert.equal(snaps.length, 2, "the fresh snapshot reached listeners");
    assert.equal(client.currentStatus().state, "connected");
    // Mid-stream a lower version is still refused (no infinite rewind).
    es().emit("state", frame(2));
    assert.equal(client.currentStatus().lastVersion, 3, "only the FIRST frame may re-base");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("gap flush survives a fractional clock (waitMs rounds to 0 => immediate flush)", async () => {
  const timers = installFakeTimers(10_500_000);
  try {
    const { client, gaps } = await freshClient();
    client.start();
    es().emit("state", frame(10)); // baseline
    es().emit("tick", frame(20, { event: "tick" })); // first gap -> leading signal
    assert.equal(gaps.length, 1);
    // Land 0.2ms short of the throttle window: the remaining wait rounds to 0.
    timers.advance(GAP_SIGNAL_MIN_INTERVAL_MS - 0.2);
    es().emit("tick", frame(30, { event: "tick" })); // deferred sighting
    assert.equal(gaps.length, 2, "collapsed window flushes immediately, never swallows");
    assert.equal(client.currentStatus().gapCount, 18, "11..19 + 21..29 missing");
    client.stop();
    assert.equal(timers.live(), 0, "no orphan flush timer");
  } finally {
    timers.restore();
  }
});

test("SSE tick frames (no `event` key in the body) still merge their data", async () => {
  const timers = installFakeTimers(9_800_000);
  try {
    const { client, snaps } = await freshClient();
    client.start();
    es().emit("state", frame(10, { bid: 1.11 }));
    // PROTOCOL PIN: web/server.py builds `tick` frames by POPPING the
    // heavyweight lists; the JSON body carries NO `event` field (only the WS
    // fan-out injects one). The merge authority is the SSE event name.
    const tickBody = JSON.stringify({ state_version: 11, bid: 2.22, ask: 2.25 });
    assert.equal(JSON.parse(tickBody).event, undefined, "real SSE tick shape");
    es().emit("tick", tickBody);
    const last = snaps[snaps.length - 1];
    assert.equal(last.bid, 2.22, "tick data must land in the snapshot, not be discarded");
    assert.equal(last.ask, 2.25);
    assert.equal(last.state_version, 11);
    assert.equal(client.currentStatus().lastVersion, 11);
    // A full `state` frame replaces wholesale, including lists a tick omitted.
    es().emit("state", frame(12, { bid: 3.33, bars: [1, 2, 3] }));
    assert.equal(snaps[snaps.length - 1].bars.length, 3, "state replaces");
    // WS-style body (event key present) keeps working through the same path.
    es().emit("tick", JSON.stringify({ event: "tick", state_version: 13, bid: 4.44 }));
    assert.equal(snaps[snaps.length - 1].bid, 4.44);
    assert.equal(snaps[snaps.length - 1].bars.length, 3, "lists survive a tick merge");
    client.stop();
  } finally {
    timers.restore();
  }
});

test("token rides the query string (EventSource cannot send headers)", async () => {
  const timers = installFakeTimers(9_500_000);
  try {
    FakeEventSource.instances = [];
    globalThis.EventSource = FakeEventSource;
    globalThis.sessionStorage = { getItem: () => "s3cr3t/+" };
    globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
    const mod = await loadSocketModule();
    const client = new mod.NseRealtimeClient();
    client.start();
    assert.equal(es().url, "/api/ticks/stream?token=s3cr3t%2F%2B", "token must be URL-encoded");
    client.stop();
    globalThis.sessionStorage = { getItem: () => null };
    client.start();
    assert.equal(es().url, "/api/ticks/stream", "no token => no query param");
    client.stop();
  } finally {
    timers.restore();
  }
});
