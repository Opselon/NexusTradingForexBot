/**
 * Feature DTO contracts for the parity wave (lane 1 owns this file).
 *
 * Rules (UI_WAVE_SPEC): types are DTOs — raw backend JSON shapes, no client
 * defaults, no invented fields. Optional `?` mirrors "backend may omit".
 * Free-form backend dicts are `Record<string, unknown>` with a doc comment
 * pointing at the Python producer, so lanes can narrow in model.ts.
 */

// ------------------------------------------------------------------ v1 shared

/** v1 list endpoints that page through meta; payload is a plain array. */
export interface V1List<T> {
  items: T[];
  count?: number;
}

// ------------------------------------------------------------ indicators v1
// Producer: src/nexus_scalp/web/api_v1/indicators.py + indicators/service.py
// to_api_dict(). GET /api/v1/indicators* (v1 envelope).

/**
 * One computed indicator. Backend truth: ports.py `IndicatorResult.as_api_dict`
 * emits EXACTLY {name, value, action} — no signal/kind/label/params fields
 * ever arrive, so the phantom fields are removed (D5-wave fix, verified
 * read-only against src/nexus_scalp/indicators/ports.py:42-43).
 * action ∈ "Buy" | "Sell" | "Neutral" | "Strong buy" | "Strong sell";
 * value is null when history is insufficient (never fabricated).
 */
export interface IndicatorReading {
  name: string;
  value: number | null;
  action: string;
}

/**
 * Pivot MATRIX (D5 fix). Producer: indicators/service.py `PivotMatrix` —
 * `levels` is an ORDERED list of level names (["R3","R2","R1","P","S1","S2","S3"]),
 * `columns` the family names the backend actually sends
 * (["Classic","Fibonacci","Camarilla","Woodie","DM"]), and `rows` a dict
 * keyed by LEVEL: rows[level][family] = number|null (not a flat levels map,
 * not an array of row objects — the old DTO was wrong on both counts).
 */
export interface IndicatorPivots {
  levels: string[];
  columns: string[];
  rows: Record<string, Record<string, number | null>>;
}

export interface IndicatorGauge {
  label: string;
  sell: number;
  neutral: number;
  buy: number;
  angle_deg: number;
}

export interface IndicatorsSnapshot {
  symbol: string;
  timeframe: string;
  bar_count: number;
  last_close: number | null;
  oscillators: IndicatorReading[];
  moving_averages: IndicatorReading[];
  pivots: IndicatorPivots;
  gauges: Record<string, IndicatorGauge>;
  summary: Record<string, number>;
  source_bar_count?: number;
}

export interface IndicatorsOscillators {
  oscillators: IndicatorReading[];
}

export interface IndicatorsMovingAverages {
  moving_averages: IndicatorReading[];
}

export interface IndicatorsPivots {
  pivots: IndicatorPivots;
}

export interface IndicatorsGauges {
  gauges: Record<string, IndicatorGauge>;
  summary: Record<string, number>;
}

export interface IndicatorsSummary {
  summary: Record<string, number>;
  gauges?: Record<string, IndicatorGauge>;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------- news (legacy)
// Producer: src/nexus_scalp/web/news_liquidity_mslie_routes.py (raw JSON).
// Existing NewsArticle/NewsState/NewsHealth live in types/domain.ts.

export interface NewsListResponse {
  articles: import("./domain").NewsArticle[];
  count?: number;
  available?: boolean;
}

export interface NewsImpactResponse {
  impacts: Array<Record<string, unknown>>;
  asset?: string;
  [extra: string]: unknown;
}

export interface NewsTimelineResponse {
  events: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

export interface NewsSourcesResponse {
  sources: Array<Record<string, unknown>>;
  count?: number;
}

export interface NewsKeywordsResponse {
  keywords: Array<{ keyword?: string; term?: string; count?: number; [extra: string]: unknown }>;
  [extra: string]: unknown;
}

export interface NewsDetailResponse {
  article?: import("./domain").NewsArticle;
  related?: import("./domain").NewsArticle[];
  analysis?: Record<string, unknown> | null;
  [extra: string]: unknown;
}

export interface NewsToggleState {
  enabled: boolean;
  [extra: string]: unknown;
}

export interface NewsMutationResult {
  success?: boolean;
  ok?: boolean;
  message?: string;
  [extra: string]: unknown;
}

export interface NewsAiStatus {
  available: boolean;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------- rules (legacy)
// Producer: diagnostics_state_routes.py get_trading_rules -> list[dict] from
// AuditRepository.get_trading_rules() (SQLite rule rows).

export interface TradingRuleRow {
  id?: number;
  rule_name: string;
  is_enabled?: boolean;
  enabled?: boolean;
  category?: string | null;
  parameters?: string | Record<string, unknown> | null;
  description?: string | null;
  version?: number | null;
  updated_at?: string | null;
  [extra: string]: unknown;
}

export interface RuleTogglePayload {
  rule_name: string;
  is_enabled: boolean;
  parameters?: Record<string, unknown> | null;
}

export interface RuleToggleResult {
  success: boolean;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------- config (legacy)
// Producer: diagnostics_state_routes.py (/api/config raw AppConfig dump —
// open-ended; telegram.bot_token is ALWAYS masked by the backend, BUG-072).

export interface RuntimeConfigSnapshot {
  version?: number;
  applied?: boolean;
  [extra: string]: unknown;
}

export interface RuntimeDiagnostics {
  available: boolean;
  [extra: string]: unknown;
}

export interface RuntimeApplyResult {
  success: boolean;
  message?: string;
  [extra: string]: unknown;
}

export interface ModelSwapPayload {
  model_path: string;
  [extra: string]: unknown;
}

export interface SettingsEnvelope {
  [section: string]: unknown;
}

export interface TelegramStatus {
  configured?: boolean;
  enabled?: boolean;
  connected?: boolean;
  masked_token?: string;
  bot_username?: string | null;
  [extra: string]: unknown;
}

export interface TelegramSettingsPayload {
  enabled?: boolean;
  bot_token?: string;
  chat_id?: string;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------- debug (legacy)
// Producer: debug_research_routes.py — free-form debug dicts; keep the bag
// open (`[extra]`) and let features/debug model.ts narrow what it renders.

export interface DebugHealth {
  ok?: boolean;
  checks?: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface DebugFeatures {
  features?: Record<string, boolean>;
  [extra: string]: unknown;
}

export interface ModelTestPayload {
  symbol?: string;
  timeframe?: string;
  bars?: number;
  [extra: string]: unknown;
}

// ---------------------------------------------------------------- db (legacy)
// Producer: db_console.py (prefix /api/db/console) + diagnostics_state_routes
// (/api/db/hygiene, /api/db/manage/*) + debug_research_routes (/api/db/status).

export interface DbStatus {
  available: boolean;
  [extra: string]: unknown;
}

export interface DbDatabases {
  databases: Array<{ name: string; path?: string; size_bytes?: number | null; [e: string]: unknown }>;
}

export interface DbTables {
  tables: Array<{ name: string; row_count?: number | null; [e: string]: unknown }>;
}

export interface DbColumns {
  columns: Array<{ name: string; type?: string; pk?: number; notnull?: number; [e: string]: unknown }>;
}

export interface DbRows {
  rows: Array<Record<string, unknown>>;
  total?: number;
  page?: number;
  page_size?: number;
}

export interface DbQuick {
  queries: Array<{ label?: string; sql?: string; rows?: Array<Record<string, unknown>>; [e: string]: unknown }>;
}

export interface DbConsoleQueryPayload {
  database?: string;
  sql: string;
  limit?: number;
  [extra: string]: unknown;
}

export interface DbConsoleQueryResult {
  ok: boolean;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  error?: string | null;
  [extra: string]: unknown;
}

export interface DbApiKey {
  name: string;
  prefix?: string;
  created_at?: string | null;
  scopes?: string[];
  [extra: string]: unknown;
}

export interface DbHygiene {
  available: boolean;
  [extra: string]: unknown;
}

export interface DbManageProgress {
  running?: boolean;
  phase?: string | null;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ marketplace v1
// Producer: api_v1/marketplace.py (v1 envelope).

export interface MarketplaceSeed {
  seed_id: string;
  name?: string;
  strategy?: string | null;
  enabled?: boolean;
  score?: number | null;
  [extra: string]: unknown;
}

export interface MarketplacePack {
  pack_id: string;
  name?: string;
  version?: string | null;
  installed?: boolean;
  [extra: string]: unknown;
}

export interface MarketplaceRankingRow {
  seed_id?: string;
  rank?: number;
  score?: number | null;
  [extra: string]: unknown;
}

export interface MarketplaceRepair {
  repair_id?: string;
  seed_id?: string;
  status?: string;
  [extra: string]: unknown;
}

export interface SeedScorePoint {
  t?: string;
  score?: number | null;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ accounting (legacy)
// Producer: diagnostics_state_routes /api/account/summary, /api/account/trades,
// /api/account/growth, /api/live/accounting, /api/live/state;
// debug_research_routes /api/account/performance*, equity-curve, drawdown,
// trades/{id}, strategies.

export interface AccountSummary {
  available: boolean;
  balance?: number | null;
  equity?: number | null;
  margin?: number | null;
  margin_free?: number | null;
  profit?: number | null;
  open_positions?: number | null;
  [extra: string]: unknown;
}

export interface AccountTradesPage {
  trades: Array<Record<string, unknown>>;
  total?: number;
  limit?: number;
  offset?: number;
}

export interface EquityCurvePoint {
  t: string;
  equity: number;
  balance?: number;
  [extra: string]: unknown;
}

export interface DrawdownSeries {
  points: Array<{ t: string; drawdown_pct: number; [e: string]: unknown }>;
  max_drawdown_pct?: number;
  [extra: string]: unknown;
}

export interface PerformanceBundle {
  available: boolean;
  [extra: string]: unknown;
}

export interface LiveAccounting {
  available: boolean;
  [entry: string]: unknown;
}

export interface LiveState {
  runtime_mode?: string | null;
  engine_running?: boolean;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ governance (legacy)
// Producers:
//  debug_research_routes: /api/experience/{summary,strategies,decision,models},
//    /api/forensics/{health,deploy-gate}, /api/research/{gates,promote,...}
//  model_governance_routes: /api/models/* + /api/models/governance/*

export interface ExperienceSummary {
  available: boolean;
  [extra: string]: unknown;
}

export interface ExperienceStrategies {
  strategies: Array<Record<string, unknown>>;
}

export interface ForensicsHealth {
  ok?: boolean;
  available?: boolean;
  [extra: string]: unknown;
}

export interface DeployGateResult {
  gate: string;
  passed: boolean;
  reasons?: string[];
  [extra: string]: unknown;
}

export interface ResearchGateRow {
  gate?: string;
  name?: string;
  passed?: boolean;
  status?: string;
  [extra: string]: unknown;
}

export interface ModelGovernanceStatus {
  champion?: Record<string, unknown> | null;
  challenger?: Record<string, unknown> | null;
  [extra: string]: unknown;
}

export interface ModelRegistryRow {
  model_id?: string;
  role?: string;
  state?: string;
  [extra: string]: unknown;
}

export interface ModelRunRow {
  run_id: string;
  status?: string;
  kind?: string;
  created_at?: string | null;
  [extra: string]: unknown;
}

export interface GovernanceAuditRow {
  id?: string;
  action?: string;
  actor?: string;
  at?: string;
  [extra: string]: unknown;
}

export interface GovernanceActionResult {
  success: boolean;
  message?: string;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ research (mixed)
// v1: api_v1/research.py (getV1). Legacy: debug_research_routes /api/research/*.

export interface ResearchDataset {
  dataset_id?: string;
  name?: string;
  rows?: number | null;
  [extra: string]: unknown;
}

export interface ResearchRun {
  run_id: string;
  status?: string;
  strategy_id?: string | null;
  [extra: string]: unknown;
}

export interface ResearchStrategy {
  strategy_id: string;
  name?: string;
  enabled?: boolean;
  [extra: string]: unknown;
}

export interface ResearchStatus {
  available: boolean;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ incidents (legacy)
// Producer: diagnostics_state_routes.py /api/diagnostics/* (raw JSON).

export interface DiagnosticsHealth {
  overall: string;
  subsystems?: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface IncidentListResponse {
  incidents: Array<{ incident_id?: string; id?: string; severity?: string; status?: string; title?: string; created_at?: string; [e: string]: unknown }>;
  count?: number;
}

export interface IncidentDetail {
  incident: Record<string, unknown>;
  events?: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

export interface LineageResponse {
  available: boolean;
  lineage?: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface SearchResponse {
  available: boolean;
  results?: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ liquidity (legacy)
// Producer: news_liquidity_mslie_routes.py /api/liquidity/*, /api/mslie/*.

export interface LiquidityState {
  available: boolean;
  state?: string | null;
  sections?: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface LiquidityFeatures {
  features?: Array<Record<string, unknown>>;
  available?: boolean;
  [extra: string]: unknown;
}

export interface MslieStatus {
  available: boolean;
  [extra: string]: unknown;
}

// ----------------------------------------------------- command center (legacy)
// Producer: command_center_integration.py /api/command-center/* (raw JSON;
// free-form fleet/inspector dicts — narrow in the feature model layer).

export interface CommandCenterOverview {
  available: boolean;
  [extra: string]: unknown;
}

export interface CommandCenterFleet {
  strategies: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

// ------------------------------------------------------------- replay (legacy)
// Producer: replay_routes.py /api/replay/* + server.py POST /api/replay/toggle.

export interface ReplayState {
  active: boolean;
  mode?: string | null;
  cursor?: number | null;
  [extra: string]: unknown;
}

export interface ReplayReport {
  available: boolean;
  [extra: string]: unknown;
}

// ------------------------------------------------------------ algo config (PUT)
// Producer: server.py GET/PUT /api/algo/config (legacy raw AppConfig subset).

export interface AlgoConfig {
  atr_sl_buffer_multiplier?: number;
  min_risk_reward_ratio?: number;
  ai_zone_confidence_threshold?: number;
  fvg_mitigation_sensitivity?: number;
  order_block_lookback_bars?: number;
  [extra: string]: unknown;
}
