/**
 * Accounting bounded context — DTO contracts for the legacy /api/account/*
 * and /api/live/accounting routes (raw JSON, no v1 envelope).
 *
 * Source of truth: src/nexus_scalp/web/debug_research_routes.py (performance,
 * period reports, series, equity curve, forensics, strategies, intelligence)
 * and diagnostics_state_routes.py (summary, trades list, growth, live
 * accounting). `available:false` + `reason` is a REAL state (engine offline);
 * numeric fields are nullable by contract — "no data" never renders as zero.
 */

export interface LiveAccountState {
  available: boolean;
  source?: string;
  balance?: number | null;
  equity?: number | null;
  floating_pnl?: number | null;
  margin?: number | null;
  margin_free?: number | null;
  margin_level?: number | null;
  currency?: string;
  leverage?: number | string;
  open_positions?: number | null;
  open_volume?: number | null;
  account_login?: string;
  error?: string;
}

export interface PeriodReport {
  kind?: string;
  key?: string;
  label?: string;
  period_start?: string;
  period_end?: string;
  has_data?: boolean;
  total_trades?: number;
  win_count?: number;
  loss_count?: number;
  breakeven_count?: number;
  gross_profit?: number;
  gross_loss?: number;
  net_pnl?: number;
  commission_total?: number;
  swap_total?: number;
  starting_balance?: number | null;
  ending_balance?: number | null;
  starting_equity?: number | null;
  ending_equity?: number | null;
  pnl_pct?: number | null;
  win_rate?: number | null;
  average_win?: number | null;
  average_loss?: number | null;
  expectancy?: number | null;
  profit_factor?: number | null;
  average_r?: number | null;
  r_sample_count?: number;
  max_drawdown_pct?: number | null;
  max_drawdown_usd?: number | null;
  best_trade?: number | null;
  worst_trade?: number | null;
  average_holding_sec?: number | null;
  total_risk_deployed?: number | null;
  total_volume?: number;
  loss_rate_decided?: number | null;
  loss_rate_all?: number | null;
  win_rate_all?: number | null;
  pnl_weighted_win_rate?: number | null;
  win_rate_denominator?: string;
  expectancy_breakeven_incl?: number | null;
  avg_pnl_per_decided?: number | null;
  total_costs?: number;
  cost_drag_pct?: number | null;
}

export interface DrawdownReport {
  has_data?: boolean;
  sample_count?: number;
  current_equity?: number | null;
  peak_equity?: number | null;
  peak_balance?: number | null;
  current_drawdown_pct?: number | null;
  current_drawdown_usd?: number | null;
  max_drawdown_pct?: number | null;
  max_drawdown_usd?: number | null;
  max_drawdown_at?: string | null;
  drawdown_duration_sec?: number | null;
  recovery_duration_sec?: number | null;
  recovery_pct?: number | null;
  in_drawdown?: boolean;
  available?: boolean;
}

export interface AdvancedMetrics {
  sample_trades?: number;
  sharpe_ratio?: number | null;
  sortino_ratio?: number | null;
  calmar_ratio?: number | null;
  sqn?: number | null;
  recovery_factor?: number | null;
  payoff_ratio?: number | null;
  profit_factor?: number | null;
  average_win?: number | null;
  average_loss?: number | null;
  max_consecutive_wins?: number | null;
  max_consecutive_losses?: number | null;
  equity_volatility_pct?: number | null;
  annualized_volatility_pct?: number | null;
  downside_volatility_pct?: number | null;
  profit_standard_error?: number | null;
  stop_loss_share?: number | null;
  avg_loss_r?: number | null;
  avg_r_multiple?: number | null;
  avg_mae_r?: number | null;
  avg_mfe_r?: number | null;
  avg_hold_sec?: number | null;
  avg_risk_usd?: number | null;
  r_coverage_ratio?: number | null;
  net_pnl?: number | null;
  gross_profit?: number | null;
  gross_loss?: number | null;
  total_costs?: number | null;
  cost_drag_pct?: number | null;
  expectancy_breakeven_incl?: number | null;
  win_rate?: number | null;
  loss_rate_decided?: number | null;
  win_rate_denominator?: string;
  pnl_weighted_win_rate?: number | null;
  loss_efficiency_pct?: number | null;
  win_mae_capture_pct?: number | null;
}

/** GET /api/account/performance */
export interface AccountPerformance {
  available: boolean;
  reason?: string;
  live?: LiveAccountState;
  periods?: Record<string, PeriodReport>;
  drawdown?: DrawdownReport;
  worker?: Record<string, unknown> | null;
  totals?: { closed_trades?: number; win_count?: number; loss_count?: number; win_rate?: number | null; realized_pnl?: number | null };
  advanced?: AdvancedMetrics;
  fetched_at?: string;
}

/** GET /api/account/performance/{kind} */
export interface AccountPeriodResponse {
  available: boolean;
  reason?: string;
  period?: PeriodReport;
  market?: {
    state?: string;
    last_tick_age_sec?: number | null;
    next_open_iso?: string | null;
    reason?: string;
    server_day?: string;
    server_time_utc?: string | null;
  };
}

/** GET /api/account/performance/{kind}/series */
export interface AccountSeriesResponse {
  available: boolean;
  kind?: string;
  periods?: PeriodReport[];
}

/** GET /api/account/performance/intelligence */
export interface AccountIntelResponse {
  available: boolean;
  reason?: string;
  report?: Record<string, unknown>;
  intelligence?: {
    status?: string;
    behavior_state?: string;
    analysis_version?: string;
    anomaly_version?: string;
    trades_analyzed?: number;
    evidence_coverage?: number | null;
    behavioral_flags?: Record<string, number>;
    anomalies?: Record<string, number>;
    estimated_impact?: Record<string, unknown>;
  };
}

/** GET /api/account/equity-curve */
export interface EquityCurvePoint {
  timestamp: string;
  balance: number;
  equity: number;
  peak_equity: number;
  drawdown_pct: number;
  floating_pnl: number;
}

export interface CumulativePnlPoint {
  timestamp: string;
  ticket?: number | string | null;
  net_pnl: number;
  cumulative_pnl: number;
  outcome?: string;
  exit?: string;
}

export interface AccountEquityCurve {
  available: boolean;
  reason?: string;
  equity_curve?: EquityCurvePoint[];
  cumulative_pnl?: CumulativePnlPoint[];
  fetched_at?: string;
}

/** GET /api/account/growth — [{timestamp, balance, equity}] (audit snapshots) */
export type GrowthPoint = { timestamp: string; balance: number | null; equity: number | null };

/** GET /api/account/strategies */
export interface StrategyContribution {
  strategy_id: string;
  trade_count?: number;
  net_pnl?: number;
  gross_profit?: number;
  gross_loss?: number;
  win_count?: number;
  loss_count?: number;
  win_rate?: number | null;
  profit_factor?: number | null;
  average_r?: number | null;
  r_sample_count?: number;
  best_trade?: number | null;
  worst_trade?: number | null;
  loss_share?: number | null;
  lifecycle_state?: string;
  confidence?: number | null;
  expectancy_r?: number | null;
  recent_expectancy_r?: number | null;
  sample_count?: number | null;
}

export interface AccountStrategiesResponse {
  available: boolean;
  reason?: string;
  strategies?: StrategyContribution[];
  fetched_at?: string;
}

/** GET /api/account/trades — raw broker/ledger rows (array body). */
export interface AccountTradeRow {
  ticket?: number | string | null;
  position_id?: number | string | null;
  trade_id?: number | string | null;
  symbol?: string;
  direction?: string;
  volume?: number | null;
  status?: string;
  entry_price?: number | null;
  exit_price?: number | null;
  gross_pnl?: number | null;
  pnl?: number | null;
  net_pnl?: number | null;
  commission?: number | null;
  swap?: number | null;
  open_time?: string;
  close_time?: string;
  closed_at?: string;
  exit_time?: string;
  timestamp?: string;
  strategy_id?: string | null;
  [k: string]: unknown;
}

/** GET /api/account/trades/{ticket} — forensic trace. */
export interface TradeForensics {
  available?: boolean;
  found?: boolean;
  ticket?: number;
  trade?: Record<string, unknown> & { symbol?: string; direction?: string; volume?: number | null; opened_at?: string | null; closed_at?: string | null; duration_sec?: number | null };
  identity?: Record<string, unknown>;
  entry?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  position_path?: { mae_points?: number | null; mfe_points?: number | null; mae_usd?: number | null; mfe_usd?: number | null; mae_r?: number | null; mfe_r?: number | null };
  exit?: Record<string, unknown> & { exit_classification?: string; is_stop_exit?: boolean };
  outcome?: {
    gross_pnl?: number | null;
    commission?: number | null;
    swap?: number | null;
    net_pnl?: number | null;
    realized_r?: number | null;
    outcome?: string;
    balance_after?: number | null;
    equity_after?: number | null;
  };
  strategy_context?: Record<string, unknown>;
  model_context?: Record<string, unknown>;
  quality?: Record<string, unknown>;
  behavioral_flags?: string[];
  loss_attribution?: string | null;
  order_events?: Array<Record<string, unknown>>;
  notes?: string[];
}

/** GET /api/live/accounting — RiskEngine-authoritative live + plan. */
export interface LiveAccountingResponse {
  available: boolean;
  reason?: string;
  source?: string;
  live?: {
    balance?: number | null;
    equity?: number | null;
    floating_pnl?: number | null;
    margin_free?: number | null;
    drawdown_pct?: number | null;
    win_rate?: number | null;
    open_positions?: number | null;
  };
  plan?: {
    equity?: number | null;
    risk_pct?: number | null;
    risk_usd?: number | null;
    entry?: number | null;
    stop_loss?: number | null;
    sl_distance?: number | null;
    lot_size?: number | null;
    margin_required?: number | null;
    exposure_pct?: number | null;
    min_lot?: number | null;
    lot_step?: number | null;
    note?: string;
    [k: string]: unknown;
  } | null;
}

export const PERIOD_KINDS = ["DAY", "WEEK", "MONTH", "YEAR"] as const;
export type PeriodKind = (typeof PERIOD_KINDS)[number];
