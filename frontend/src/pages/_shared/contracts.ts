/**
 * Lane-4 page-local contracts for backend payloads that have no module in
 * `src/api/*Api` yet (chart history, replay pipeline, liquidity/mSLIE state,
 * operator order flow, calibration, v1 shadow).
 *
 * Ownership rule this wave: `src/api/` and `src/types/` belong to other
 * lanes, so these mirrors live with the pages that consume them. Every
 * optional field is `| null`-friendly and defaults to UNKNOWN rendering at
 * the call site — the backend omits or nulls fields on purpose (never
 * fabricates), and the pages must not paper over that.
 *
 * Verified against src/nexus_scalp/web/ handlers (READ-ONLY):
 *  - /api/chart/history            -> web/server.py get_chart_history
 *  - /api/replay/{session,control,state,decision,report} -> web/replay_routes.py
 *  - /api/replay/toggle            -> web/server.py ToggleReplayRequest
 *  - /api/liquidity/state          -> features/liquidity_runtime.py report()
 *  - /api/mslie/status             -> mslie/engine.py get_debug_status()
 *  - /api/operator/orders          -> web/operator_routes.py operator_orders
 *  - /api/operator/calibration     -> web/calibration_monitor.py snapshot
 *  - /api/v1/shadow/status|runs|decisions|70d -> web/api_v1/shadow.py
 *  - /api/v1/market/regime         -> web/api_v1/market.py market_regime
 */

import type { Bar } from "@/types/domain";

// ---------------------------------------------------------------------------
// /api/chart/history
// ---------------------------------------------------------------------------

export interface ChartHistoryResponse {
  bars: Bar[];
  bars_available: boolean;
  /** BROKER_NATIVE | ENGINE_STATE | UNAVAILABLE — explicit provenance. */
  source: string;
  symbol: string | null;
  timeframe: string | null;
  requested: number;
  returned: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  generated_at: string;
  error: { code?: string; message?: string } | null;
  visual_overlays: VisualOverlays;
}

export interface OverlayRect {
  id?: string;
  type: string;
  price_low: number;
  price_high: number;
  time?: string | null;
  ai_confidence?: number | null;
  [key: string]: unknown;
}

export interface OverlayLine {
  id?: string;
  price: number;
  type?: string;
  time?: string | null;
  label?: string;
  time_start?: string | null;
  time_end?: string | null;
  [key: string]: unknown;
}

export interface OverlayOrderLines {
  entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  direction?: string | null;
  ticket?: number | string | null;
  [key: string]: unknown;
}

/** `visual_overlays` — SMC/ICT zone/BOS/sweep markers (backend-computed). */
export interface VisualOverlays {
  rectangles?: OverlayRect[];
  bos_lines?: OverlayLine[];
  midlines?: OverlayLine[];
  liq_markers?: OverlayLine[];
  order_lines?: OverlayOrderLines | null;
}

// ---------------------------------------------------------------------------
// /api/replay/* (REPLAY_API v1, CHG-0043)
// ---------------------------------------------------------------------------

export interface ReplaySessionIdentity {
  replay_id?: string;
  dataset_id?: string;
  dataset_fingerprint?: string;
  symbol?: string;
  timeframe?: string;
  replay_mode?: string;
  start_time?: string;
  end_time?: string;
  git_commit?: string;
  [key: string]: unknown;
}

export interface ReplaySessionResponse {
  ok: boolean;
  replay_id: string;
  identity?: ReplaySessionIdentity;
}

export interface ReplayControlResponse {
  ok: boolean;
  replay_id?: string;
  result?: { status?: string; [key: string]: unknown };
}

export interface ReplayCounts {
  bars?: number | null;
  ticks?: number | null;
  decisions?: number | null;
  trades?: number | null;
  [key: string]: unknown;
}

/** Cursor-bounded state — future data is a COUNT, never a payload. */
export interface ReplayState {
  phase?: string | null;
  status?: string | null;
  clock?: string | null;
  counts?: ReplayCounts;
  known_events?: number | null;
  unknown_events?: number | null;
  last_price?: { bid?: number | null; ask?: number | null; close?: number | null } | null;
  open_position?: { direction?: string; entry_price?: number | null; volume?: number | null } | null;
  equity?: number | null;
  regime?: { regime?: string | null; probability?: number | null } | null;
  regime_enabled?: boolean;
  regime_transitions?: Array<Record<string, unknown>>;
}

export interface ReplayDecision {
  decision_index?: number | null;
  ts?: string | null;
  action?: string | null;
  confidence?: number | null;
  regime?: string | null;
  reason_code?: string | null;
  blocked_by?: string | null;
  decision_stage?: string | null;
  entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  risk_accepted?: boolean | null;
  probs?: number[] | null;
  [key: string]: unknown;
}

export interface ReplayReport {
  decisions?: number;
  trades?: number;
  wins?: number;
  losses?: number;
  pnl_usd?: number;
  equity_end?: number;
  gate_distribution?: Record<string, number>;
  [key: string]: unknown;
}

/** /api/replay/toggle + /api/engine/toggle share the legacy mutation body. */
export interface ReplayToggleResponse {
  success: boolean;
  message?: string;
  replaying?: boolean;
}

// ---------------------------------------------------------------------------
// /api/liquidity/state (governor report) — READ-ONLY on the Intelligence page
// ---------------------------------------------------------------------------

export interface LiquidityPool {
  side?: string | null;
  source?: string | null;
  state?: string | null;
  price?: number | null;
  confirmed_at?: string | null;
}

export interface LiquidityState {
  success?: boolean;
  enabled?: boolean;
  available?: boolean;
  feature_availability?: string;
  status?: string;
  calculation_status?: string;
  source_status?: string;
  causal_state?: string;
  source?: string;
  algorithm_version?: string;
  last_update?: string | null;
  snapshot_timestamp?: string | null;
  age_sec?: number | null;
  latency_ms?: number | null;
  schema?: Record<string, unknown>;
  features?: Record<string, number>;
  feature_count?: number;
  feature_names?: string[];
  error?: string | null;
  error_at?: string | null;
  pools?: LiquidityPool[];
  model_compatibility?: Record<string, unknown>;
  liquidity_contract?: Record<string, unknown>;
  reason?: string;
}

// ---------------------------------------------------------------------------
// /api/mslie/status (MarketStructureEngine debug)
// ---------------------------------------------------------------------------

export interface MslieStatus {
  success?: boolean;
  available?: boolean;
  status?: string;
  reason?: string;
  engine_status?: Record<string, unknown>;
  market_context?: Record<string, unknown> | null;
  liquidity_map?: Array<Record<string, unknown>>;
  last_sweep?: Record<string, unknown> | null;
  feature_vector?: Record<string, unknown> | null;
  algorithm_version?: string;
}

// ---------------------------------------------------------------------------
// /api/operator/orders (dispatch evidence)
// ---------------------------------------------------------------------------

export interface OperatorOrderRow {
  id: number | string;
  ticket: number | null;
  order_id: string | null;
  symbol: string | null;
  action: string | null;
  price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  volume: number | null;
  reason: string | null;
  latency: number | null;
  execution_mode: string | null;
  timestamp: string | null;
  execution_id: string | null;
}

export interface OperatorOrdersResponse {
  available: boolean;
  count?: number;
  latency?: { n?: number; p50_ms?: number; p95_ms?: number; p99_ms?: number } | null;
  rows?: OperatorOrderRow[];
  reason?: string;
}

// ---------------------------------------------------------------------------
// /api/operator/calibration
// ---------------------------------------------------------------------------

export interface CalibrationResponse {
  available: boolean;
  serving_fingerprint?: string | null;
  serving_artifact_mtime?: string | null;
  artifact_status?: string;
  calibration_status?: string;
  matches_serving?: boolean | null;
  required_per_split?: number;
  calibration_split?: number;
  validation_split?: number;
  total_eligible?: number;
  deficit?: Record<string, number>;
  excluded?: Record<string, number>;
  oos_cutoff?: string;
  collector_status?: string;
  risk_multiplier?: number | null;
  min_risk_multiplier_floor?: number | null;
  ece?: number | null;
  brier?: number | null;
}

// ---------------------------------------------------------------------------
// /api/v1/shadow/* + /api/v1/market/regime
// ---------------------------------------------------------------------------

export interface ShadowStatus {
  shadow_60d: { available: boolean; runs?: Record<string, number>; decisions?: number; promotions?: number } | null;
  shadow_70d: { available: boolean; [key: string]: unknown } | null;
  generated_at: string;
}

export interface ShadowRunRow {
  run_id?: string;
  status?: string;
  champion_model?: string | null;
  shadow_model?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  [key: string]: unknown;
}

export interface Shadow70Block {
  summary?: Record<string, unknown> | null;
  disagreement_counts?: Record<string, number> | null;
  drift_alerts?: Array<Record<string, unknown>> | null;
  feature_health?: Record<string, unknown> | null;
  generated_at?: string;
}

export interface RegimePayload {
  regime: Record<string, unknown> | null;
  evidence: Record<string, unknown> | null;
  note?: string;
}

// ---------------------------------------------------------------------------
// /api/v1/runtime/mode (replay flag mirror for the dashboard controls)
// ---------------------------------------------------------------------------

export interface ReplayRuntimeState {
  mode: string | null;
  effective_mode: string | null;
  engine_attached: boolean;
  replaying: boolean | null;
}
