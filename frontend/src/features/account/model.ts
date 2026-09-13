/**
 * Account model — DTO -> view-model mapping + invariants (pure, unit-testable).
 *
 * EDD rules:
 *  - `available:false` (ENGINE_UNAVAILABLE) is a REAL state, surfaced as an
 *    error-ish panel — never as a panel of zeros.
 *  - nullable financial fields stay null through the mappers; formatting shows
 *    "—" (the no-synthetic-numbers invariant, BUG-020 lineage).
 *  - trade rows arrive from two possible producers (broker history or ledger
 *    fallback); the mapper normalizes ids/timestamps/PnL WITHOUT recomputing.
 */

import type { AccountTradeRow, GrowthPoint, PeriodKind, TradeForensics } from "./types";

export const PERIOD_LABEL: Record<PeriodKind, string> = {
  DAY: "Broker day",
  WEEK: "Week",
  MONTH: "Month",
  YEAR: "Year",
};

/** Period kind validator for user-supplied selectors (backend 400s on junk). */
export function asPeriodKind(raw: string): PeriodKind | null {
  const v = raw.trim().toUpperCase();
  return v === "DAY" || v === "WEEK" || v === "MONTH" || v === "YEAR" ? v : null;
}

/** Normalize one /api/account/trades row (broker OR ledger shape). */
export interface TradeVM {
  id: string;
  numericId: number | null;
  symbol: string;
  direction: string;
  volume: number | null;
  entryPrice: number | null;
  exitPrice: number | null;
  netPnl: number | null;
  grossPnl: number | null;
  commission: number | null;
  swap: number | null;
  status: string;
  openedAt: string | null;
  closedAt: string | null;
  strategyId: string | null;
  realizedR: number | null;
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function str(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  return String(v);
}

export function toTradeVM(row: AccountTradeRow): TradeVM {
  const rawId = row.ticket ?? row.position_id ?? row.trade_id ?? null;
  const parsed = rawId === null ? NaN : Number(rawId);
  return {
    id: rawId === null ? "—" : String(rawId),
    numericId: Number.isFinite(parsed) ? parsed : null,
    symbol: str(row.symbol) ?? "—",
    direction: (str(row.direction) ?? "UNKNOWN").toUpperCase(),
    volume: num(row.volume),
    entryPrice: num(row.entry_price),
    exitPrice: num(row.exit_price),
    netPnl: num(row.net_pnl) ?? num(row.pnl),
    grossPnl: num(row.gross_pnl),
    commission: num(row.commission),
    swap: num(row.swap),
    status: str(row.status) ?? "—",
    openedAt: str(row.open_time),
    closedAt: str(row.close_time) ?? str(row.closed_at) ?? str(row.exit_time) ?? str(row.timestamp),
    strategyId: str(row.strategy_id),
    realizedR: num(row.realized_r),
  };
}

/** Growth rows -> chart points; a malformed row becomes a null sample (gap). */
export function toGrowthPoints(rows: GrowthPoint[] | null | undefined): Array<{ timestamp: string; balance: number | null; equity: number | null }> {
  return (rows ?? []).map((r) => ({
    timestamp: String(r.timestamp ?? ""),
    balance: num(r.balance),
    equity: num(r.equity),
  }));
}

/**
 * Win-rate denominators decide badge tone: the backend sends the ratio in
 * PERCENT already (win_rate: 62.5) — never re-derive, only classify the word.
 */
export function rateTone(pct: number | null | undefined): "pos" | "neg" | "dim" {
  if (pct === null || pct === undefined) return "dim";
  if (pct >= 50) return "pos";
  if (pct > 0) return "neg";
  return "dim";
}

/** Forensics: does a drawer waterfall have anything real to show? */
export function hasWaterfall(f: TradeForensics | undefined): boolean {
  const o = f?.outcome;
  if (!o) return false;
  return o.gross_pnl !== null && o.gross_pnl !== undefined && o.net_pnl !== null && o.net_pnl !== undefined;
}

/** Risk-plan input validation mirrors the backend semantics (positive numbers). */
export function validatePlanInputs(v: { equity: string; entry: string; stopLoss: string; riskPct: string }): Record<string, string | null> {
  const err = (raw: string, opts: { required?: boolean; min?: number; max?: number }): string | null => {
    const t = raw.trim();
    if (t === "") return opts.required ? "required" : null;
    if (!/^\d+(\.\d+)?$/.test(t)) return "must be a positive number";
    const n = Number(t);
    if (opts.min !== undefined && n < opts.min) return `must be ≥ ${opts.min}`;
    if (opts.max !== undefined && n > opts.max) return `must be ≤ ${opts.max}`;
    return null;
  };
  const errors: Record<string, string | null> = {
    equity: err(v.equity, { min: 0.01, max: 1_000_000_000 }),
    entry: err(v.entry, { min: 0.00001, max: 1_000_000 }),
    stopLoss: err(v.stopLoss, { min: 0.00001, max: 1_000_000 }),
    riskPct: err(v.riskPct, { min: 0.01, max: 100 }),
  };
  if (!errors.entry && !errors.stopLoss && Number(v.entry) === Number(v.stopLoss)) {
    errors.stopLoss = "stop must differ from entry";
  }
  return errors;
}

/** Compact stat rows for the advanced-metrics grid (label, value, tone). */
export interface MetricRow {
  key: string;
  label: string;
  digits: number;
  suffix?: string;
  money?: boolean;
  percentOfOne?: boolean;
}

export const ADVANCED_ROWS: MetricRow[] = [
  { key: "sharpe_ratio", label: "Sharpe", digits: 2 },
  { key: "sortino_ratio", label: "Sortino", digits: 2 },
  { key: "calmar_ratio", label: "Calmar", digits: 2 },
  { key: "sqn", label: "SQN", digits: 2 },
  { key: "recovery_factor", label: "Recovery factor", digits: 2 },
  { key: "payoff_ratio", label: "Payoff ratio", digits: 2 },
  { key: "profit_factor", label: "Profit factor", digits: 3 },
  { key: "average_win", label: "Average win", digits: 2, money: true },
  { key: "average_loss", label: "Average loss", digits: 2, money: true },
  { key: "max_consecutive_wins", label: "Max win streak", digits: 0 },
  { key: "max_consecutive_losses", label: "Max loss streak", digits: 0 },
  { key: "equity_volatility_pct", label: "Equity volatility %", digits: 2, suffix: "%" },
  { key: "stop_loss_share", label: "Stop-loss share", digits: 1, percentOfOne: true },
  { key: "avg_r_multiple", label: "Avg win R", digits: 3, suffix: "R" },
  { key: "avg_loss_r", label: "Avg loss R", digits: 3, suffix: "R" },
  { key: "avg_mae_r", label: "Avg MAE", digits: 3, suffix: "R" },
  { key: "avg_mfe_r", label: "Avg MFE", digits: 3, suffix: "R" },
  { key: "avg_hold_sec", label: "Avg hold", digits: 0, suffix: "s" },
  { key: "avg_risk_usd", label: "Avg risk $", digits: 2, money: true },
  { key: "r_coverage_ratio", label: "R coverage", digits: 1, percentOfOne: true },
];
