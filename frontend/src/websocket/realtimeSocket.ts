/**
 * Realtime client for the NSE Alternative UI (hardened SSE state machine).
 *
 * TRANSPORT REALITY (verified in the repo, 2026-09-11):
 *  - The production launcher co-boots uvicorn with `ws="none"`
 *    (src/nexus_scalp/cli/engine_boot.py) - WebSocket support is disabled at
 *    the server boundary; the `/ws` route cannot upgrade in production.
 *  - The LIVE realtime mechanism is SSE: `GET /api/ticks/stream`
 *    (web/server.py sse_telemetry_stream), emitting
 *      event: state     -> full canonical snapshot (on connect, on the first
 *                          tick after an idle gap, and every 30 versions)
 *      event: tick      -> incremental sections (bars/features/predictions
 *                          dropped server-side; the client merges them back)
 *      event: heartbeat -> {} keepalive every ~5s
 *      event: error     -> SSE_SERIALIZATION_ERROR diagnostic: the payload for
 *                          that state_version never reached the wire
 *    Every payload carries `state_version`, monotonic FOR ONE SERVER LIFETIME
 *    (StateVersioner.bump, web/server.py:207/215) - it RESETS on a server
 *    restart, which is handled below as an explicit epoch re-base.
 *  - Auth: EventSource cannot send headers, so the token rides as ?token=
 *    (api/client.ts resolves it into sessionStorage on first load). PRESERVED.
 *
 * HARDENING CONTRACT (lane realtime-harden). Every rule lives in a pure helper
 * in ./rtMath.ts and is pinned by tests/js/pro_realtime.test.mjs:
 *  H1 BACKOFF - exponential with EQUAL jitter, hard-capped at 30s, exponent
 *     clamped so attempt growth can never overflow to Infinity
 *     (rtMath.backoffDelayMs / decideAfterFailedAttempt /
 *     RECONNECT_ATTEMPT_BUDGET). Beyond the budget the honest terminal state
 *     is `failed`, and only a manual retry() buys a new budget.
 *  H2 HEARTBEAT WATCHDOG - a half-open EventSource fires NO error event
 *     (BUG-212 lesson in the legacy dashboard). Only data frames and heartbeats
 *     refresh liveness; silence past STALE_AFTER_MS + HEARTBEAT_GRACE_MS
 *     (rtMath.isFeedSilent) force-closes the stream and drives `reconnecting`.
 *     The watchdog NEVER claims `connected`.
 *  H3 VERSIONING - out-of-order frames (v <= last) are dropped with no liveness
 *     credit (rtMath.classifyVersion). A jump (v > last+1) is an honest GAP:
 *     the newer truth is applied, `missing` versions accumulate into
 *     status.gapCount, and subscribeGap() listeners receive a throttled resync
 *     signal (leading edge + guaranteed trailing flush). A server restart (the
 *     first frame of a fresh stream being a full `state` snapshot with a LOWER
 *     version) re-bases the pointer instead of freezing the console forever on
 *     a dead epoch.
 *  H4 VISIBILITY - hidden tab: close the socket, cancel timers, truthful
 *     `disconnected` (no phantom reconnect storm against a frozen tab). Visible
 *  again: immediate reconnect attempt plus a `stream-paused` gap signal,
 *     because everything that happened while hidden is unproven (see H3).
 *     (rtMath.decideVisibilityAction).
 *  H5 TIMERS - three timers total (retry, watchdog, gap flush). Each is cleared
 *     when it fires or is superseded, and stop() clears all three plus the
 *     visibility listener. No leaks across StrictMode mount churn.
 *  H6 TRUTHFUL STATES - `connected` requires evidence on the wire: an accepted
 *     data frame or a heartbeat (onopen alone proves only the HTTP handshake,
 *     not that the engine is streaming). `reconnecting` covers every in-flight
 *     retry; `failed` only when the budget is spent; `disconnected` only when
 *     stopped or paused. No path invents a state.
 *  H7 NO DOUBLE-SUBSCRIBE - the module singleton stays; start() is idempotent
 *     (the original leak class: a second start() installed a second watchdog
 *     interval and stop() cleared only the newest handle), and connect() is
 *     single-flight - a second EventSource is never opened over a live one.
 */

import type { ConnectionState, RealtimeStatus, WsTickPayload } from "@/types/realtime";
import { isTickPayload } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";
import {
  RECONNECT_ATTEMPT_BUDGET,
  WATCHDOG_SILENCE_MS,
  WATCHDOG_TICK_MS,
  classifyVersion,
  decideAfterFailedAttempt,
  decideGapSignal,
  decideVisibilityAction,
  isFeedSilent,
  nextGapCount,
  nextGapCountForError,
  reconnectBudgetExhausted,
  type RealtimeGapInfo,
} from "./rtMath";

type Listener = (snapshot: EngineSnapshot) => void;
type StatusListener = (status: RealtimeStatus) => void;
type GapListener = (info: RealtimeGapInfo) => void;

function sseUrl(): string {
  let token: string | null = null;
  try {
    token = sessionStorage.getItem("nse.altui.token");
  } catch {
    token = null;
  }
  // AUTH TRANSPORT: EventSource cannot set headers, so ?token= is the only
  // channel that survives WEB-AUTH-P0. Do not "tidy" this into a header.
  return token ? `/api/ticks/stream?token=${encodeURIComponent(token)}` : "/api/ticks/stream";
}

function emptySnapshot(): EngineSnapshot {
  return {
    state_version: 0,
    snapshot_timestamp: "",
    generated_at: "",
    engine_running: false,
    symbol: null,
    execution_mode: null,
    runtime_mode: null,
    data_source: null,
    adapter_class: null,
    mode_source_mismatch: false,
    tick_stale: false,
    tick_freshness_ms: null,
    provenance: { price: "UNAVAILABLE", features: "UNAVAILABLE", model: "UNAVAILABLE", accounting: "UNAVAILABLE" },
    timestamps: { tick: null, features: null, inference: null, proposal: null },
    bid: null,
    ask: null,
    spread: null,
    price_digits: null,
    atr: null,
    regime: null,
    account: {
      available: false, source: null, login: null, server: null, company: null, currency: null,
      leverage: null, trade_mode: null, trade_allowed: null, balance: null, credit: null, equity: null,
      profit: null, margin: null, margin_free: null, margin_level: null, floating: null, drawdown: null,
      win_rate: null, open_positions: null, pending_orders: null,
    },
    positions: [],
    bars: [],
    features: [],
    probs: { available: false, no_trade: null, buy: null, sell: null },
    model: {
      available: false, model_id: null, model_version: null, architecture: null, artifact_path: null,
      feature_schema_id: null, feature_dimension: null, scaler_ready: null, latency_ms: null,
    },
    ai_decision: null,
    ai_confidence: null,
    ai_reason: null,
    predictions: [],
    radar: null,
    algo_config: {
      atr_sl_buffer_multiplier: 0, min_risk_reward_ratio: 0, ai_zone_confidence_threshold: 0,
      fvg_mitigation_sensitivity: 0, order_block_lookback_bars: 0,
    },
    liquidity: {},
    visual_overlays: {},
    health: { overall: "UNKNOWN", subsystems: {}, details: {}, checked_at: "" },
    live_freshness: null,
    is_stale: false,
    versioning: {},
    diagnostics: {
      state_age_sec: null, tick_age_sec: null, features_age_sec: null,
      inference_age_sec: null, proposal_age_sec: null, chart_age_sec: null,
    },
  };
}

export class NseRealtimeClient {
  private es: EventSource | null = null;
  /** Wall-clock of the CURRENT EventSource's construction (H2 watchdog anchor
   *  before the first frame ever lands). */
  private streamOpenedAt: number | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private gapListeners = new Set<GapListener>();
  private snapshot: EngineSnapshot | null = null;
  private state: ConnectionState = "disconnected";
  private lastVersion: number | null = null;
  private lastMessageAt: number | null = null;
  /**
   * H1: consecutive failed stream attempts since the last liveness evidence.
   * Reset by an accepted frame/heartbeat and by a manual retry().
   */
  private reconnectAttempts = 0;
  /**
   * H3: cumulative count of provably-missing `state_version`s since load.
   * Public (additive) so chrome can show it without parsing status twice.
   */
  gapCount = 0;
  // H5: exactly three timers, each individually clearable, all cleared on stop.
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private gapFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private lastGapSignalAt: number | null = null;
  private pendingGap: RealtimeGapInfo | null = null;
  /** stop() sets it, start() clears it. While true, NOTHING may reconnect. */
  private stoppedByUser = true;
  /** H7: start() ownership flag - makes start() idempotent. */
  private started = false;
  /** H4: hidden tab => closed socket, suppressed reconnects. */
  private paused = false;
  /**
   * H3: true until the first frame of the CURRENT EventSource, which is the
   * only frame allowed to re-base a restarted server's version counter.
   */
  private firstFrameOfStream = true;
  private visibilityBound = false;
  private readonly onVisibility = () => this.handleVisibility();
  /**
   * Jitter source seam: production uses Math.random; the deterministic test
   * harness replaces it to pin the exact scheduled delay. Never exposed on the
   * hook's public surface.
   */
  private jitter: () => number = Math.random;

  // -------------------------------------------------------------------------
  // Public surface - exactly what useRealtimeSnapshot consumes
  // (subscribe / subscribeStatus / currentStatus / start / stop / reconnectNow),
  // plus the ADDITIVE subscribeGap() + retry() + gapCount.
  // -------------------------------------------------------------------------

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    if (this.snapshot) listener(this.snapshot);
    return () => {
      this.listeners.delete(listener);
    };
  }

  subscribeStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.currentStatus());
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  /**
   * H3 (ADDITIVE): register a resync listener. Existing callers are untouched -
   * the hook may keep folding gaps from status transitions, or subscribe and
   * receive a throttled signal whenever the client can PROVE its view is
   * incomplete (version jump / backend error frame / resume after pause).
   * A signal means: refetch the REST snapshot.
   */
  subscribeGap(listener: GapListener): () => void {
    this.gapListeners.add(listener);
    return () => {
      this.gapListeners.delete(listener);
    };
  }

  currentStatus(): RealtimeStatus {
    return {
      state: this.state,
      lastVersion: this.lastVersion,
      lastMessageAt: this.lastMessageAt,
      reconnectAttempts: this.reconnectAttempts,
      // Additive optional field (types/realtime.ts): see `gapCount` above.
      gapCount: this.gapCount,
    };
  }

  /**
   * H7: idempotent. React StrictMode mounts twice; the original shape armed a
   * NEW watchdog interval on every call while stop() (never called by the hook,
   * which keeps the client app-scoped) cleared at most one - leaking a timer
   * that kept force-reconnecting the feed forever.
   */
  start(): void {
    this.stoppedByUser = false;
    if (this.started) {
      // Already owned: only make sure a stream exists (e.g. everything
      // re-mounted after a transient all-listeners-gone window). A pending
      // backoff timer already owns the next attempt -> never race it (H7).
      if (!this.es && !this.paused && this.retryTimer === null) this.connect();
      return;
    }
    this.started = true;
    this.paused = false;
    // H4: bind visibility FIRST, so a client that boots inside a hidden tab
    // never opens a socket it is about to close (bound once, unbound on stop).
    if (typeof document !== "undefined" && !this.visibilityBound) {
      document.addEventListener("visibilitychange", this.onVisibility);
      this.visibilityBound = true;
      if (document.hidden) this.paused = true;
    }
    if (this.paused) {
      this.setState("disconnected"); // truthful: we deliberately hold no stream
    } else if (!this.es && this.retryTimer === null) {
      // A pending backoff timer already owns the next attempt - do not open a
      // second stream beside it (that is the double-subscribe failure mode).
      this.connect();
    }
    // H2: heartbeat watchdog. Acts on SILENCE only - it can never conjure
    // `connected` (H6). The liveness anchor is the newer of the last frame /
    // heartbeat and THIS stream's open instant, so a freshly opened stream that
    // has not delivered yet is not killed prematurely, while a stream that
    // opened and says nothing forever does age out. When a retry timer owns the
    // next attempt (es === null) the watchdog is deliberately inert.
    this.watchdogTimer = setInterval(() => {
      if (this.stoppedByUser || this.paused) return;
      if (this.es === null) return; // connect()/retry owns the retry timer
      const anchor =
        this.lastMessageAt === null
          ? this.streamOpenedAt
          : this.streamOpenedAt === null
            ? this.lastMessageAt
            : Math.max(this.lastMessageAt, this.streamOpenedAt);
      if (isFeedSilent(Date.now(), anchor, WATCHDOG_SILENCE_MS)) {
        this.handleStreamDead("watchdog: no frame and no heartbeat in window");
      }
    }, WATCHDOG_TICK_MS);
  }

  stop(): void {
    this.stoppedByUser = true;
    this.paused = false;
    this.started = false;
    // H5: EVERY timer and the visibility listener. The original cleared only
    // retry + watchdog, and never removed its document listener.
    this.clearAllTimers();
    if (typeof document !== "undefined" && this.visibilityBound) {
      document.removeEventListener("visibilitychange", this.onVisibility);
      this.visibilityBound = false;
    }
    this.es?.close();
    this.es = null;
    this.streamOpenedAt = null;
    this.firstFrameOfStream = true;
    this.pendingGap = null; // a deferred gap signal must not outlive the client
    this.setState("disconnected");
  }

  /** Manual retry (banner button) - H1 budget reset + immediate attempt. */
  reconnectNow(): void {
    this.retry();
  }

  /**
   * TEST-ONLY seam (additive, unused by the app): pin the jitter source so the
   * deterministic harness can assert the EXACT scheduled backoff. Production
   * leaves it at Math.random.
   */
  setJitterSource(fn: () => number): void {
    this.jitter = fn;
  }

  /**
   * H1/H6 (ADDITIVE alias, explicit semantics): a human asked, so the
   * consecutive-failure budget resets and one attempt is made NOW. Any
   * in-flight backoff timer is cancelled first (H5) so it cannot double-connect
   * behind this attempt. A stopped client is NOT resurrected - the button lives
   * in the shell chrome, and reviving a deliberately stopped feed would be its
   * own lie.
   */
  retry(): void {
    this.reconnectAttempts = 0; // H1: fresh budget, attempts restart at 1s
    this.clearTimer("retry"); // H5: supersede the scheduled backoff
    if (this.stoppedByUser || this.paused) return;
    this.es?.close();
    this.es = null;
    this.streamOpenedAt = null;
    this.firstFrameOfStream = true;
    this.setState("reconnecting");
    this.connect();
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    const status = this.currentStatus();
    for (const l of this.statusListeners) l(status);
  }

  private clearTimer(which: "retry" | "watchdog" | "gapFlush"): void {
    if (which === "retry") {
      if (this.retryTimer !== null) {
        clearTimeout(this.retryTimer);
        this.retryTimer = null;
      }
    } else if (which === "watchdog") {
      if (this.watchdogTimer !== null) {
        clearInterval(this.watchdogTimer);
        this.watchdogTimer = null;
      }
    } else if (this.gapFlushTimer !== null) {
      clearTimeout(this.gapFlushTimer);
      this.gapFlushTimer = null;
    }
  }

  private clearAllTimers(): void {
    this.clearTimer("retry");
    this.clearTimer("watchdog");
    this.clearTimer("gapFlush");
  }

  /** H4: hidden -> close + stop timers; visible -> immediate attempt. */
  private handleVisibility(): void {
    if (this.stoppedByUser || !this.started) return;
    if (typeof document === "undefined") return;
    const action = decideVisibilityAction(document.hidden, this.started);
    if (action === "close-feed") {
      this.paused = true;
      this.clearTimer("retry"); // H5: no reconnect storm against a frozen tab
      this.clearTimer("gapFlush");
      this.pendingGap = null;
      this.es?.close();
      this.es = null;
      this.streamOpenedAt = null;
      this.firstFrameOfStream = true;
      // `disconnected` is truthful: the socket IS closed. Panels keep the last
      // snapshot and age-mark it themselves (status.lastMessageAt is untouched).
      this.setState("disconnected");
    } else if (action === "reconnect-now" && this.paused) {
      this.paused = false;
      // No budget reset here: this is automatic, not human. A dead backend must
      // keep degrading toward the honest `failed` across pause/resume cycles.
      // handleStreamDead() owns the single close+state+attempt+signal path
      // (H3: whatever happened while hidden is unseen -> a resync is warranted).
      this.handleStreamDead("visibility-resume", true);
    }
  }

  /** Single-flight open (H7): never a second EventSource over a live one. */
  private connect(): void {
    if (this.stoppedByUser || this.paused || this.es) return;
    if (reconnectBudgetExhausted(this.reconnectAttempts)) {
      // H6: budget spent => `failed`, reached WITHOUT pretending to try again.
      this.setState("failed");
      return;
    }
    let es: EventSource;
    try {
      es = new EventSource(sseUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.es = es;
    this.streamOpenedAt = Date.now(); // H2: fresh stream gets a fresh anchor
    this.firstFrameOfStream = true; // H3: epoch-reset detection is per stream

    es.onopen = () => {
      // H6: DELIBERATELY EMPTY. onopen proves the HTTP handshake completed, not
      // that the engine is streaming; `connected` waits for real evidence in
      // acceptFrame()/heartbeat (markLive()).
    };

    es.addEventListener("state", (ev) => this.acceptFrame((ev as MessageEvent).data, false));
    es.addEventListener("tick", (ev) => this.acceptFrame((ev as MessageEvent).data, true));
    es.addEventListener("heartbeat", () => {
      // H2: a keepalive IS transport evidence - it refreshes liveness - but it
      // never touches the data version, and it never reorders frames.
      this.lastMessageAt = Date.now();
      this.markLive();
    });
    es.addEventListener("error", (ev) => {
      const data = (ev as MessageEvent).data;
      if (typeof data === "string" && data.length > 0) {
        // The backend's OWN error frame (SSE_SERIALIZATION_ERROR): that
        // version's payload never reached the wire. The stream is still alive,
        // so this is NOT the dead-stream path - but the view IS incomplete, so
        // count it and signal for a resync. State is NOT upgraded to
        // `connected` on a corrupted payload (H6).
        this.lastMessageAt = Date.now();
        let version: number | null = null;
        try {
          const parsed = JSON.parse(data) as { correlation_id?: string };
          const tail = /-(\d+)$/.exec(parsed.correlation_id ?? "");
          if (tail) version = Number(tail[1]);
        } catch {
          /* unknown diagnostic shape - still a gap sighting */
        }
        this.gapCount = nextGapCountForError(this.gapCount); // loss is PROVEN
        this.signalGap({
          expected: this.lastVersion ?? 0,
          received: version ?? this.lastVersion ?? 0,
          missing: 1, // exactly the version whose serialization failed
          at: Date.now(),
          source: "error",
          reason: "server-error-frame",
        });
        return;
      }
      // Native transport error: close and drive the retry ourselves with H1
      // backoff. Letting EventSource's own auto-retry run beside our timer is
      // exactly the double-subscribe failure mode this lane is hardening.
      this.handleStreamDead("transport error");
    });
  }

  /**
   * The stream is provably unusable: close it, move to the honest state, and
   * (unless `immediate`) schedule the next attempt under the H1 backoff law.
   */
  private handleStreamDead(reason: string, immediate = false): void {
    this.clearTimer("retry"); // H5: any scheduled attempt is superseded
    this.es?.close();
    this.es = null;
    this.streamOpenedAt = null;
    this.firstFrameOfStream = true;
    if (this.stoppedByUser || this.paused) return;
    if (reconnectBudgetExhausted(this.reconnectAttempts)) {
      // H6: already spent => stay exactly at `failed`, never keep dialing and
      // never inflate the counter past the published budget.
      this.setState("failed");
      return;
    }
    const decision = decideAfterFailedAttempt(this.reconnectAttempts, this.jitter());
    this.reconnectAttempts = decision.attempts;
    if (decision.delayMs === null) {
      this.setState("failed"); // H6: budget spent - say so, and stop trying
      return;
    }
    this.setState("reconnecting"); // H6: the state lands BEFORE any signal
    if (immediate) {
      this.connect(); // H4: a human-facing resume skips the remaining backoff
    } else {
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        this.connect();
      }, decision.delayMs);
    }
    // H3: an interrupted stream cannot prove that nothing was missed, so ask
    // subscribers to resync. gapCount is deliberately NOT incremented here -
    // an interruption alone is not a PROVEN version loss (unlike a jump).
    this.signalGap({
      expected: this.lastVersion ?? 0,
      received: this.lastVersion ?? 0,
      missing: 0,
      at: Date.now(),
      source: reason,
      reason: "stream-paused",
    });
  }

  /** H6: the ONLY promotion path to `connected` (frame or heartbeat evidence). */
  private markLive(): void {
    if (this.reconnectAttempts !== 0) this.reconnectAttempts = 0;
    if (this.state !== "connected") this.setState("connected");
  }

  private acceptFrame(raw: string | null, isTick: boolean): void {
    if (!raw) return;
    let payload: EngineSnapshot | WsTickPayload;
    try {
      payload = JSON.parse(raw) as EngineSnapshot | WsTickPayload;
    } catch {
      return; // malformed frame - dropped, never fabricated (H6)
    }
    const wasFirst = this.firstFrameOfStream;
    this.firstFrameOfStream = false; // any frame retires the re-base privilege

    const outcome = classifyVersion(
      payload.state_version,
      this.lastVersion,
      !isTick, // a full `state` snapshot: the only frame allowed to re-base
      wasFirst, // first frame of THIS stream (server restart detection)
    );
    if (!outcome.live) {
      // H3: out-of-order/duplicate frames are dropped WITHOUT liveness credit.
      // A stream that only replays history must age out through the watchdog
      // (H2) instead of looking alive.
      return;
    }
    if (outcome.decision === "accept-gap") {
      // H3: apply the newer truth, but record what was provably never seen.
      // PROTOCOL CAVEAT (web/server.py): `state_version` is bumped by EVERY
      // get_system_state() call - shared across all SSE streams and REST
      // snapshot polls. With several consumers (two tabs, the legacy
      // dashboard, any /api/status poll) each stream samples a NON-contiguous
      // subset of the counter, so `missing` here means "versions this stream's
      // view skipped", not "engine data was lost". The signal is throttled to
      // one resync per window for exactly that reason: the cost of a false
      // positive is one harmless refetch; the cost of a missed TRUE gap is a
      // console showing stale truth.
      this.gapCount = nextGapCount(this.gapCount, outcome.missing);
      this.signalGap({
        expected: this.lastVersion ?? 0,
        received: outcome.lastVersion ?? 0,
        missing: outcome.missing,
        at: Date.now(),
        source: isTick ? "tick" : "state",
        reason: "version-gap",
      });
    }
    this.lastVersion = outcome.lastVersion;
    this.lastMessageAt = Date.now();

    // MERGE AUTHORITY = the SSE event name, not the payload's `event` field.
    // PROTOCOL REALITY (web/server.py sse_telemetry_stream): the `tick` frames
    // are built by popping bars/features/predictions off the snapshot and are
    // wrapped as `event: tick` on the WIRE only - the JSON body carries no
    // `event` key (only the WebSocket fan-out injects one). Keying the merge on
    // the payload field therefore used to DISCARD every SSE tick's data, leaving
    // the console to refresh solely on the every-30-versions `state` frames.
    // Accept either signal; a full `state` event stays authoritative replace.
    const incremental = isTick || isTickPayload(payload);
    if (incremental) {
      const { event: _event, ...sections } = payload as WsTickPayload;
      this.snapshot = this.snapshot ? { ...this.snapshot, ...sections } : { ...emptySnapshot(), ...sections };
    } else {
      this.snapshot = payload as EngineSnapshot;
    }
    const snap = this.snapshot;
    if (snap) for (const l of this.listeners) l(snap);
    this.markLive();
  }

  /** Backoff-scheduled retry for a stream that never even got constructed. */
  private scheduleReconnect(): void {
    if (this.stoppedByUser || this.paused || this.retryTimer !== null) return; // H5
    const decision = decideAfterFailedAttempt(this.reconnectAttempts, this.jitter());
    this.reconnectAttempts = decision.attempts;
    if (decision.delayMs === null) {
      this.setState("failed");
      return;
    }
    this.setState("reconnecting");
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, decision.delayMs);
  }

  /**
   * H3 gap signal - leading-edge throttled WITH a guaranteed trailing flush:
   * the first gap in a window notifies immediately; later gaps inside the
   * window are deferred (newest replaces the pending one - a resync refetch
   * sees the latest truth anyway) and flushed on a timer, so no detection is
   * ever silently swallowed. `gapCount` is NEVER throttled - understating the
   * fault would be the lie this whole lane exists to prevent.
   */
  private signalGap(info: Omit<RealtimeGapInfo, "gapCount">): void {
    const stamped: RealtimeGapInfo = { ...info, gapCount: this.gapCount };
    const decision = decideGapSignal(stamped.at, this.lastGapSignalAt);
    if (decision.action === "signal") {
      this.lastGapSignalAt = stamped.at;
      this.clearTimer("gapFlush");
      this.pendingGap = null;
      this.publishStatus(); // gapCount moved - status is the source of truth
      this.emitGap(stamped);
      return;
    }
    this.pendingGap = stamped;
    if (this.gapFlushTimer === null) {
      if (decision.waitMs <= 0) {
        // Rounding collapsed the rest of the window: flush now instead of
        // leaving a pending gap that no timer would ever deliver.
        this.pendingGap = null;
        if (!this.stoppedByUser && !this.paused) {
          this.lastGapSignalAt = Date.now();
          this.emitGap(stamped);
        }
        return;
      }
      this.gapFlushTimer = setTimeout(() => {
        this.gapFlushTimer = null;
        const pending = this.pendingGap;
        this.pendingGap = null;
        if (pending && !this.stoppedByUser && !this.paused) {
          this.lastGapSignalAt = Date.now();
          this.emitGap(pending);
        }
      }, decision.waitMs);
    }
  }

  private emitGap(info: RealtimeGapInfo): void {
    for (const l of this.gapListeners) l(info);
  }

  private publishStatus(): void {
    const status = this.currentStatus();
    for (const l of this.statusListeners) l(status);
  }
}

// H7: the module singleton is the foundation of the double-subscribe guard -
// the hook mounts and unmounts freely, exactly one client (and one stream)
// exists per tab.
export const realtimeClient = new NseRealtimeClient();

// Shared with tests and the hook lane so no module re-derives these numbers.
export { RECONNECT_ATTEMPT_BUDGET, WATCHDOG_SILENCE_MS };
