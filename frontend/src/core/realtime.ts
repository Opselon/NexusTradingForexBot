/**
 * core/realtime — the SSE realtime client, the single live-data producer.
 *
 * TRANSPORT REALITY (verified in repo; UI_WAVE_SPEC): the production launcher
 * boots uvicorn with ws="none" (src/nexus_scalp/cli/engine_boot.py) — the
 * live mechanism is Server-Sent Events at GET /api/ticks/stream, emitting:
 *   event: state     -> full canonical EngineSnapshot
 *   event: tick      -> incremental sections (bars/features/predictions dropped)
 *   event: heartbeat -> {} keepalive (liveness only)
 * Every frame carries the monotonic `state_version`.
 *
 * Moved here from src/websocket/realtimeSocket.ts (which is now a back-compat
 * re-export shim). Preserved invariants from that file:
 *  - state_version out-of-order guard: a frame whose version is <= the last
 *    accepted version is DROPPED (never rewind the UI).
 *  - malformed frames are dropped, never fabricated into a snapshot.
 *  - half-open EventSource watchdog (BUG-212 lesson): a stalled stream fires
 *    NO error event, so a timer forces a reconnect when no frame arrives
 *    within stale + heartbeat-grace windows.
 *  - honest state machine: connected/reconnecting/disconnected/failed.
 *
 * Consumption contract: subscribe via hooks (useRealtime/useRealtimeSnapshot)
 * or coreEvents topics — never by importing this module from a component.
 */

import type { ConnectionState, RealtimeStatus, WsTickPayload } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";
import { runtimeConfig, ENDPOINTS, STORAGE_KEYS } from "./config";
import { resolveToken } from "./auth";
import { coreEvents } from "./events";

type Listener = (snapshot: EngineSnapshot) => void;
type StatusListener = (status: RealtimeStatus) => void;

/**
 * Sections the backend deliberately omits from incremental `tick` frames
 * (web/server.py `sse_telemetry_stream`): only the full `state` event carries
 * them. Listed here so the merge can expire them instead of inheriting the
 * last full snapshot's copies as if they were current.
 */
const TICK_DROPPED_SECTIONS = ["bars", "features", "predictions"] as const;

function sseUrl(): string {
  // EventSource cannot send headers — the backend also accepts ?token=.
  // Token comes from core/auth (the single resolution point), falling back
  // to the raw sessionStorage read only if auth is unavailable (non-browser).
  let token: string | null = null;
  try {
    token = resolveToken();
    if (!token) token = sessionStorage.getItem(STORAGE_KEYS.token);
  } catch {
    token = null;
  }
  const base = `${runtimeConfig.apiBase}${ENDPOINTS.ticksStream}`;
  return token ? `${base}?${runtimeConfig.tokenQueryParam}=${encodeURIComponent(token)}` : base;
}

export function emptySnapshot(): EngineSnapshot {
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
  /** Wall-clock of the last stream open — the CONNECTING watchdog anchor. */
  private connectedAt: number | null = null;
  /** Names of the sections carried by the most recent accepted tick frame. */
  private lastTickSections: string[] = [];

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

  /** Latest merged snapshot (or null before the first frame). */
  currentSnapshot(): EngineSnapshot | null {
    return this.snapshot;
  }

  /** Sections of the last accepted `tick` frame (freshness UIs use this). */
  currentTickSections(): string[] {
    return [...this.lastTickSections];
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
      if (this.state !== "connected") return;
      const reference =
        this.lastMessageAt !== null
          ? this.lastMessageAt
          : // A connection that opened but never delivered a frame has no
            // lastMessageAt — anchor to the open time, otherwise a silently
            // dead stream would hold "CONNECTING" forever with no retry.
            this.connectedAt;
      if (reference === null) return;
      if (
        Date.now() - reference >
        runtimeConfig.timeouts.realtimeStaleMs + runtimeConfig.timeouts.realtimeHeartbeatGraceMs
      ) {
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
    coreEvents.publish("realtime:status", status);
    coreEvents.publish("realtime:connection", state);
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
      this.connectedAt = Date.now();
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
      // state_version out-of-order guard (preserved from the legacy client).
      if (this.lastVersion !== null && version <= this.lastVersion) return;
      if (this.lastVersion !== null && version > this.lastVersion + 1) {
        coreEvents.publish("realtime:version-jump", { from: this.lastVersion, to: version });
      }
      this.lastVersion = version;
    }
    this.lastMessageAt = Date.now();

    if (isTick) {
      // Transport-level discrimination, not a payload field: the SSE route
      // (web/server.py) labels frames with the SSE `event:` line only — the
      // JSON body carries no `event` key, so isTickPayload() never matched
      // and every incremental frame was silently dropped. Sections from a
      // tick merge into the last full state; a tick arriving before any
      // full state is merged onto an empty snapshot.
      const sections = { ...payload };
      // The backend OMITS bars/features/predictions from tick frames (only
      // the full `state` event carries them). A shallow merge would keep the
      // last full snapshot's lists forever, presenting data from a prior
      // state as if it belonged to this version. Drop the sections this
      // transport is known not to carry so the UI can fall back to its
      // staleness presentation instead of showing stale lists as current.
      for (const dropped of TICK_DROPPED_SECTIONS) delete (sections as Record<string, unknown>)[dropped];
      this.snapshot = this.snapshot
        ? { ...this.snapshot, ...sections }
        : { ...emptySnapshot(), ...sections };
      this.lastTickSections = Object.keys(sections);
      coreEvents.publish("realtime:tick", { sections: this.lastTickSections, state_version: version ?? null });
    } else {
      this.snapshot = payload as EngineSnapshot;
    }
    const snap = this.snapshot;
    if (snap) {
      for (const l of this.listeners) l(snap);
      coreEvents.publish("realtime:snapshot", snap);
    }
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

/** App-scoped singleton — start() is idempotent across route changes. */
export const realtimeClient = new NseRealtimeClient();
