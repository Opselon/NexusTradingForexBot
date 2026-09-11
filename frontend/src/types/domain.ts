/**
 * Backend-authoritative domain types (Alternative UI).
 *
 * Every interface here mirrors a real payload produced by the NSE backend
 * (src/nexus_scalp/web/*). Optional fields are `| null` — the backend sends
 * explicit `null` / `available: false` markers instead of fabricating data,
 * and the UI must render UNKNOWN, never invent values.
 *
 * Sources (verified 2026-09-11):
 *  - web/server.py `get_system_state()`          -> EngineSnapshot
 *  - web/server.py `_build_health_section()`     -> HealthSection
 *  - web/diagnostics_state_routes.py live/state  -> LiveUiState
 *  - web/api_v1/*.py                             -> v1 domains
 *  - web/model_governance_routes.py shadow70     -> Shadow70State
 *  - web/news_liquidity_mslie_routes.py          -> NewsState
 *  - web/intelligence_routes.py                  -> IntelligenceSummary
 *  - adapters/mt5/providers.py snapshots         -> Position / OrderRow
 */

// ---------------------------------------------------------------------------
// Core engine / market
// ---------------------------------------------------------------------------

export type ExecutionModeValue = "LIVE" | "PAPER" | "SHADOW" | "REPLAY" | "BACKTEST";

export type HealthStatus =
  | "READY"
  | "IDLE"
  | "WARMING_UP"
  | "STALE"
  | "DEGRADED"
  | "DISCONNECTED"
  | "ERROR"
  | "UNAVAILABLE"
  | "STOPPED"
  | "DISABLED"
  | "UNKNOWN";

export interface Provenance {
  price: string;
  features: string;
  model: string;
  accounting: string;
}

export interface EngineSnapshotTimestamps {
  tick: string | null;
  features: string | null;
  inference: string | null;
  proposal: string | null;
}

export interface DiagnosticsSection {
  state_age_sec: number | null;
  tick_age_sec: number | null;
  features_age_sec: number | null;
  inference_age_sec: number | null;
  proposal_age_sec: number | null;
  chart_age_sec: number | null;
}

export interface AccountState {
  available: boolean;
  source: string | null;
  login: number | string | null;
  server: string | null;
  company: string | null;
  currency: string | null;
  leverage: number | null;
  trade_mode: number | string | null;
  trade_allowed: boolean | null;
  balance: number | null;
  credit: number | null;
  equity: number | null;
  profit: number | null;
  margin: number | null;
  margin_free: number | null;
  margin_level: number | null;
  floating: number | null;
  drawdown: number | null;
  win_rate: number | null;
  open_positions: number | null;
  pending_orders: number | null;
}

export interface Position {
  ticket: number | null;
  symbol: string | null;
  /** MT5 POSITION_TYPE: 0 = BUY, 1 = SELL (also string form from some routes). */
  type: number | string | null;
  volume: number | null;
  price_open: number | null;
  price_current: number | null;
  sl: number | null;
  tp: number | null;
  profit: number | null;
  swap: number | null;
  commission: number | null;
  magic: number | null;
  time: number | string | null;
}

export interface OrderRow {
  ticket: number | null;
  symbol: string | null;
  type: number | string | null;
  magic: number | null;
  volume_current: number | null;
  volume_initial?: number | null;
  price_open: number | null;
  sl: number | null;
  tp: number | null;
  state: number | string | null;
  time_setup: number | string | null;
}

export interface Bar {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume?: number | null;
  tick_volume?: number | null;
  is_complete?: boolean;
}

export interface PredictionRow {
  request_id: string | null;
  time: string | null;
  action: string | null;
  confidence: number | null;
  regime: string | null;
  reason: string | null;
  probabilities: {
    no_trade: number | null;
    buy: number | null;
    sell: number | null;
  };
}

export interface Probabilities {
  available: boolean;
  no_trade: number | null;
  buy: number | null;
  sell: number | null;
  inference_timestamp?: string | null;
}

export interface ModelMeta {
  available: boolean;
  model_id: string | null;
  model_version: string | null;
  architecture: string | null;
  artifact_path: string | null;
  feature_schema_id: string | null;
  feature_dimension: number | null;
  scaler_ready: boolean | null;
  inference_timestamp?: string | null;
  latency_ms: number | null;
  latency_breakdown?: Record<string, number | null> | null;
  model_forward_ms?: number | null;
  feature_ms?: number | null;
  e2e_ms?: number | null;
}

export interface RiskCheckValue {
  passed?: boolean;
  allowed?: boolean;
  value?: number | string | boolean | null;
  limit?: number | null;
  reason?: string | null;
}

/** `proposal.risk_checks` — keys vary by gate; shape stays string-keyed. */
export type RiskChecks = Record<string, unknown>;

export interface HealthSection {
  overall: HealthStatus;
  subsystems: Partial<Record<"engine" | "mt5" | "database" | "model" | "inference_freshness" | "news" | "workers", HealthStatus>>;
  details: Record<string, unknown>;
  checked_at: string;
}

export interface FreshnessStage {
  state?: "FRESH" | "STALE" | "UNKNOWN" | string;
  age_ms?: number | null;
}

export interface LiveFreshness {
  overall?: string;
  market?: FreshnessStage;
  features?: FreshnessStage;
  inference?: FreshnessStage;
  decision?: FreshnessStage;
}

export interface LiquiditySection {
  enabled?: boolean;
  available?: boolean;
  status?: string;
  causal_state?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface VersioningSection {
  [key: string]: unknown;
}

/** Canonical `get_system_state()` snapshot (SSE `state` event + /api/status). */
export interface EngineSnapshot {
  state_version: number;
  snapshot_timestamp: string;
  generated_at: string;
  engine_running: boolean;
  symbol: string | null;
  execution_mode: ExecutionModeValue | null;
  runtime_mode: string | null;
  data_source: string | null;
  adapter_class: string | null;
  mode_source_mismatch: boolean;
  tick_stale: boolean;
  tick_freshness_ms: number | null;
  provenance: Provenance;
  timestamps: EngineSnapshotTimestamps;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  price_digits: number | null;
  atr: number | null;
  regime: string | null;
  account: AccountState;
  positions: Position[];
  bars: Bar[];
  features: Array<{ index: number; name: string; value: number | null; status: string }>;
  probs: Probabilities;
  model: ModelMeta;
  ai_decision: string | null;
  ai_confidence: number | null;
  ai_reason: string | null;
  predictions: PredictionRow[];
  radar: unknown;
  algo_config: {
    atr_sl_buffer_multiplier: number;
    min_risk_reward_ratio: number;
    ai_zone_confidence_threshold: number;
    fvg_mitigation_sensitivity: number;
    order_block_lookback_bars: number;
  };
  liquidity: LiquiditySection;
  visual_overlays: Record<string, unknown>;
  health: HealthSection;
  live_freshness: LiveFreshness | null;
  is_stale: boolean;
  versioning: VersioningSection;
  diagnostics: DiagnosticsSection;
}

// ---------------------------------------------------------------------------
// MT5 status (/api/mt5/status)
// ---------------------------------------------------------------------------

export interface MT5Status {
  available: boolean;
  reason?: string;
  symbol?: string | null;
  connection?: Record<string, unknown>;
  account?: Partial<AccountState> & { available?: boolean; captured_at?: string | null; error_state?: string | null };
  symbol_spec?: { available?: boolean; specification?: Record<string, unknown>; current_tick?: Record<string, unknown>; spread_points?: number | null; tick_stale?: boolean; error_state?: string | null };
  positions?: Position[];
  orders?: OrderRow[];
  history?: Record<string, unknown>;
  calculations?: Record<string, unknown>;
  terminal?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Risk (v1 + runtime halt state)
// ---------------------------------------------------------------------------

export interface V1RiskStatus {
  last_proposal_present: boolean;
  risk_checks: RiskChecks | null;
  risk_config: {
    max_account_drawdown_pct: number | null;
    risk_per_trade_pct: number | null;
    max_concurrent_positions: number | null;
    max_spread_points: number | null;
    max_margin_usage_pct: number | null;
    max_allowed_lots: number | null;
    enforce_stop_loss: boolean | null;
  } | null;
  probed_at: string;
}

export interface V1RiskSummary {
  exposure: {
    available: boolean;
    open_positions?: number;
    total_volume?: number;
    total_floating_profit?: number;
    by_symbol?: Record<string, { volume: number; profit: number; positions: number }>;
    account?: {
      equity: number | null;
      balance: number | null;
      margin: number | null;
      margin_free: number | null;
      margin_level: number | null;
    };
    reason?: string;
  };
  probed_at: string;
}

export interface RuntimeRiskState {
  available: boolean;
  kill_switch_active: boolean;
  runtime_risk_state: string;
  runtime_risk_state_effective: string;
  halt_reason: string;
  halt_triggered_at: string;
  survival_mode: boolean;
  account_freshness: string;
  consecutive_losses: number;
  audit_batch_failures: number;
  audit_dead_letter_rows: number;
  telemetry_dropped: number;
  financial_queue_backpressure: number;
  financial_events_overflowed: number;
  financial_events_failed: number;
  hard_max_lots: number;
  config_error?: string;
}

// ---------------------------------------------------------------------------
// ML / model / shadow70
// ---------------------------------------------------------------------------

export interface V1ModelStatus {
  bundle_loaded: boolean;
  inference_enabled: boolean;
  warmup_state: string | null;
  runtime_mode: string | null;
  probed_at: string;
}

export interface V1ModelIdentity {
  available: boolean;
  reason?: string;
  artifact_id?: string;
  model_id?: string;
  schema_id?: string;
  version?: string;
  created_at?: string;
  feature_schema_hash?: string | null;
  scaler?: Record<string, unknown>;
}

export interface V1FeaturesStatus {
  warmup_state: string | null;
  inference_enabled: boolean;
  last_vector_available: boolean;
  missing_features: string[];
  probed_at: string;
}

export interface Shadow70State {
  available: boolean;
  runtime: (Record<string, unknown> & { state?: string; load_result?: Record<string, unknown> | null }) | null;
  store: (Record<string, unknown> & { disagreement_counts?: Record<string, number>; recent_observations?: Shadow70Observation[] }) | null;
  worker: Record<string, unknown> | null;
}

export interface Shadow70Observation {
  observation_id: string;
  timestamp: string;
  champion_action: string;
  shadow_action: string;
  champion_confidence: number;
  shadow_confidence: number;
  disagreement: string;
  regime: string;
  news_state: string;
  liquidity_state: string;
  outcome: string;
}

export interface ModelIntegrity {
  available: boolean;
  state?: string;
  model_id?: string;
  model_version?: string;
  schema_id?: string;
  feature_dimension?: number;
  actual_input_dimension?: number;
  actual_output_classes?: number;
  scaler_dimension?: number;
  compatibility?: string;
  integrity?: string;
  active?: boolean;
  reason?: string;
}

// ---------------------------------------------------------------------------
// News / intelligence
// ---------------------------------------------------------------------------

export interface NewsState {
  available: boolean;
  state?: string | null;
  timestamp?: string | null;
  bullish_score?: number | null;
  bearish_score?: number | null;
  confidence?: number | null;
  conflict_score?: number | null;
  freshness?: number | null;
  xauusd_relevance?: number | null;
  usd_relevance?: number | null;
  active_event_count?: number | null;
  stale?: boolean;
  news_adjustment?: number | null;
  active_high_impact?: unknown;
  reason?: string;
}

export interface NewsHealth {
  available: boolean;
  enabled?: boolean;
  health?: Record<string, unknown>;
  worker?: Record<string, unknown> | null;
  llm_budget?: Record<string, unknown>;
  calendar?: Record<string, unknown>;
  event_gate?: Record<string, unknown>;
}

export interface NewsArticle {
  article_id: string;
  title: string;
  summary?: string;
  source_name?: string;
  published_at?: string;
  importance?: string | number;
  importance_score?: number | null;
  article_status?: string;
}

export interface IntelligenceSummary {
  available: boolean;
  reasons?: string;
  lifecycle_events?: number;
  autopsies?: number;
  worker?: Record<string, unknown>;
  fetch_time?: string;
  last_suitability?: Record<string, unknown>;
}

export interface AutopsyRow {
  ticket?: string | number;
  strategy_id?: string | null;
  outcome?: string | null;
  realized_r?: number | null;
  exit_reason?: string | null;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditEventRow {
  id: number | string;
  event_type: string | null;
  created_at: string | null;
  payload: string | Record<string, unknown> | null;
}

export interface AuditLedgerRow {
  ticket: number | null;
  symbol: string | null;
  direction: string | null;
  volume: number | null;
  entry_price: number | null;
  status: string | null;
  timestamp: string | null;
  pnl: number | null;
}

export interface ExecutionHistoryRow {
  id?: number | string;
  order_id?: string | null;
  symbol?: string | null;
  order_type?: string | null;
  volume?: number | null;
  price?: number | null;
  status?: string | null;
  executed_at?: string | null;
}

export interface V1Page<T> {
  items: T[];
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface IncidentRow {
  incident_id?: string;
  id?: string;
  title?: string;
  severity?: string;
  status?: string;
  category?: string;
  component?: string;
  created_at?: string;
  impact?: Record<string, unknown>;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Intelligence page (live/state intelligence + market context)
// ---------------------------------------------------------------------------

export interface CalendarEventGate {
  [key: string]: unknown;
}
