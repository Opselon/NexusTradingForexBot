/**
 * PURPOSE:  Derive the Risk page's limit rows (value vs backend limit) from
 *           the three existing risk payloads — one list feeding both the
 *           radial gauges and the stress/limits matrix.
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: types/domain (V1RiskStatus.risk_config, V1RiskSummary.exposure,
 *           AccountState, snapshot.spread) — every field read is declared
 *           there; nothing is invented, nothing is fetched.
 * PROVIDES: buildLimitRows() → RiskLimitRow[]
 * INVARIANTS: a missing value or limit stays null (rendered "—"/indeterminate,
 *             never 0, never satisfied); no verdict words are produced here.
 * EXTEND:   add a row by declaring its payload paths — do not compute new
 *           verdicts or thresholds in this file.
 */

import type { AccountState, V1RiskStatus, V1RiskSummary } from "@/types/domain";
import type { RiskLimitRow } from "./riskThresholds";

type RiskConfig = NonNullable<V1RiskStatus["risk_config"]>;

export interface LimitRowInputs {
  /** /api/v1/risk/status → risk_config (engine limits); null = payload absent. */
  cfg: RiskConfig | null | undefined;
  /** /api/v1/risk/summary → exposure block; may be unavailable. */
  exposure: V1RiskSummary["exposure"] | undefined;
  /** Canonical snapshot account block (drawdown, open_positions fallback). */
  account: AccountState | undefined;
  /** Canonical snapshot live spread (points); null/undefined = not measured. */
  spread: number | null | undefined;
}

function finite(n: number | null | undefined): number | null {
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

/**
 * Equity-relative margin usage % — arithmetic on two backend numbers
 * (exposure.account.margin ÷ exposure.account.equity × 100). Either side
 * missing → null; the caption on the gauge restates this derivation.
 */
export function marginUsagePct(exposure: LimitRowInputs["exposure"]): number | null {
  const margin = finite(exposure?.account?.margin ?? null);
  const equity = finite(exposure?.account?.equity ?? null);
  if (margin === null || equity === null || equity === 0) return null;
  return (margin / equity) * 100;
}

/**
 * The visible limit rows, in reading order. Every pair is (a value the
 * backend measured, a limit the backend configured) — where either side is
 * absent the row still renders so the operator sees the honest "—".
 *
 * Margin level is included with a permanent null limit: the payload carries
 * NO margin floor, so its row (and the MarginArc beside it) is indeterminate
 * by design — a missing budget is never drawn as headroom.
 */
export function buildLimitRows({ cfg, exposure, account, spread }: LimitRowInputs): RiskLimitRow[] {
  // Open positions: prefer the v1 summary count, fall back to the canonical
  // snapshot — the same two reads RiskPage already cross-checks for drift.
  const openPositions = exposure?.available
    ? finite(exposure.open_positions ?? null)
    : finite(account?.open_positions ?? null);

  const marginLevel = finite(exposure?.account?.margin_level ?? null) ?? finite(account?.margin_level ?? null);

  return [
    {
      id: "drawdown",
      label: "Account drawdown",
      field: "snapshot.account.drawdown vs risk_config.max_account_drawdown_pct",
      value: finite(account?.drawdown ?? null),
      limit: finite(cfg?.max_account_drawdown_pct ?? null),
      unit: "%",
      digits: 1,
      direction: "le",
    },
    {
      id: "volume",
      label: "Position volume",
      field: "risk/summary.exposure.total_volume vs risk_config.max_allowed_lots",
      value: finite(exposure?.total_volume ?? null),
      limit: finite(cfg?.max_allowed_lots ?? null),
      unit: " lots",
      digits: 2,
      direction: "le",
    },
    {
      id: "margin-usage",
      label: "Margin usage",
      field: "exposure.account.margin ÷ equity × 100 vs risk_config.max_margin_usage_pct",
      value: marginUsagePct(exposure),
      limit: finite(cfg?.max_margin_usage_pct ?? null),
      unit: "%",
      digits: 1,
      direction: "le",
    },
    {
      id: "spread",
      label: "Spread",
      field: "snapshot.spread vs risk_config.max_spread_points",
      value: finite(spread ?? null),
      limit: finite(cfg?.max_spread_points ?? null),
      unit: " pts",
      digits: 1,
      direction: "le",
    },
    {
      id: "positions",
      label: "Open positions",
      field: "exposure.open_positions (fallback snapshot.account.open_positions) vs risk_config.max_concurrent_positions",
      value: openPositions,
      limit: finite(cfg?.max_concurrent_positions ?? null),
      unit: "",
      digits: 0,
      direction: "le",
    },
    {
      id: "margin-level",
      label: "Margin level",
      field: "exposure.account.margin_level (broker %) — payload carries NO floor, limit stays null",
      value: marginLevel,
      limit: null,
      unit: "%",
      digits: 1,
      direction: "ge",
    },
  ];
}
