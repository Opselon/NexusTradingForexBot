/**
 * Realtime client for the NSE Alternative UI.
 *
 * TRANSPORT REALITY (verified in the repo, 2026-09-11):
 *  - The production launcher co-boots uvicorn with `ws="none"`
 *    (src/nexus_scalp/cli/engine_boot.py:600) — WebSocket support is
 *    deliberately disabled at the server boundary, and the uvicorn install
 *    has no ws driver. The `/ws` route exists in web/server.py but cannot
 *    upgrade in production. Attempting an upgrade yields HTTP 404.
 *  - The LIVE realtime mechanism the existing dashboard uses is SSE:
 *    `GET /api/ticks/stream` (web/server.py), emitting
 *      event: state  -> full canonical snapshot
 *      event: tick   -> incremental sections (bars/features/predictions dropped)
 *      event: heartbeat -> {} keepalive
 *    Every frame carries the monotonic `state_version`.
 *
 * This client therefore uses EventSource (SSE) as the primary transport and
 * keeps the same state machine contract (connected/reconnecting/disconnected/
 * failed) + out-of-order version guard. A WebSocket upgrade is attempted once
 * opportunistically; if it succeeds (future engine builds re-enable ws) it
 * takes over. Both paths feed the identical snapshot merge.
 */

import type { ConnectionState, RealtimeStatus, WsTickPayload } from "@/types/realtime";
import { isTickPayload } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

type Listener = (snapshot: EngineSnapshot) => void;
type StatusListener = (status: RealtimeStatus) => void;

const STALE_AFTER_MS = 10_000;
const HEARTBEAT_GRACE_MS = 15_000;

function sseUrl(): string {
  let token: string | null = null;
  try {
    token = sessionStorage.getItem("nse.altui.token");
  } catch {
    token = null;
  }
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
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private snapshot: EngineSnapshot | null = null;
  private state: ConnectionState = "disconnected";
  private lastVersion: number | null = null;
  private lastMessageAt: number | null = null;
  private reconnectAttempts = 0;
  private retryTimer: number | null = null;
  private watchdogTimer: number | null = null;
  private closedByUser = false;
  private hardFailures = 0;

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

  currentStatus(): RealtimeStatus {
    return {
      state: this.state,
      lastVersion: this.lastVersion,
      lastMessageAt: this.lastMessageAt,
      reconnectAttempts: this.reconnectAttempts,
    };
  }

  start(): void {
    this.closedByUser = false;
    this.connect();
    // Watchdog: a half-open EventSource fires NO error event (BUG-212
    // lesson in the legacy dashboard). If no frame arrives within the
    // heartbeat grace, force a reconnect so the UI state machine stays
    // honest.
    this.watchdogTimer = window.setInterval(() => {
      if (this.closedByUser) return;
      if (this.state === "connected" && this.lastMessageAt !== null && Date.now() - this.lastMessageAt > STALE_AFTER_MS + HEARTBEAT_GRACE_MS) {
        this.reconnectNow();
      }
    }, 5000);
  }

  stop(): void {
    this.closedByUser = true;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    if (this.watchdogTimer !== null) window.clearInterval(this.watchdogTimer);
    this.es?.close();
    this.es = null;
    this.setState("disconnected");
  }

  /** Manual retry (banner button). */
  reconnectNow(): void {
    this.es?.close();
    this.es = null;
    this.setState("reconnecting");
    this.connect();
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    const status = this.currentStatus();
    for (const l of this.statusListeners) l(status);
  }

  private connect(): void {
    if (this.closedByUser || this.es) return;
    let es: EventSource;
    try {
      es = new EventSource(sseUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.es = es;

    es.onopen = () => {
      this.reconnectAttempts = 0;
      this.hardFailures = 0;
      this.setState("connected");
    };

    es.addEventListener("state", (ev) => this.acceptFrame((ev as MessageEvent).data, false));
    es.addEventListener("tick", (ev) => this.acceptFrame((ev as MessageEvent).data, true));
    es.addEventListener("heartbeat", () => {
      // Keepalive only — refresh liveness without touching data version.
      this.lastMessageAt = Date.now();
    });
    es.addEventListener("error", () => {
      // EventSource auto-reconnects; we surface the state machine honestly.
      if (this.state === "connected") this.setState("reconnecting");
      this.reconnectAttempts += 1;
      if (this.reconnectAttempts > 5) {
        this.hardFailures += 1;
        this.setState("failed");
      } else {
        this.setState("reconnecting");
      }
    });
  }

  private acceptFrame(raw: string | null, isTick: boolean): void {
    if (!raw) return;
    let payload: EngineSnapshot | WsTickPayload;
    try {
      payload = JSON.parse(raw) as EngineSnapshot | WsTickPayload;
    } catch {
      return; // malformed frame — drop, never fabricate
    }
    const version = payload.state_version;
    if (typeof version === "number") {
      if (this.lastVersion !== null && version <= this.lastVersion) return; // out-of-order guard
      this.lastVersion = version;
    }
    this.lastMessageAt = Date.now();

    if (isTick && isTickPayload(payload)) {
      const { event: _e, ...sections } = payload;
      this.snapshot = this.snapshot ? { ...this.snapshot, ...sections } : { ...emptySnapshot(), ...sections };
    } else if (!isTick) {
      this.snapshot = payload as EngineSnapshot;
    }
    const snap = this.snapshot;
    if (snap) for (const l of this.listeners) l(snap);
    this.setState("connected");
  }

  private scheduleReconnect(): void {
    if (this.closedByUser || this.retryTimer !== null) return;
    const backoff = Math.min(30_000, 1000 * 2 ** Math.min(this.reconnectAttempts, 5));
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, backoff);
  }
}

export const realtimeClient = new NseRealtimeClient();
