/**
 * LossDistributionPanel — ranked loss-responsibility matrix.
 *
 * Replaces the raw vertical strategy-id list with a ranked contribution
 * chart: rows sorted DESC by loss share, each with a proportional heat cell,
 * a signed-dollar drawdown contribution and trade volume. The row whose
 * share crosses the "concentrated" threshold is flagged amber/red.
 *
 * Every number is a backend value. null share / null gross loss draws an
 * UNKNOWN row (dashed cell) — never a zero bar that could be read as healthy.
 */

import { useMemo } from "react";
import { clampRatio } from "@/components/viz/geometry";
import { useI18n } from "@/stores/i18nStore";
import "./loss-distribution.css";

/** One backend strategy contribution, reduced to what this panel needs. */
export interface LossRow {
  strategy_id: string;
  /** 0..1 share of account gross loss. null = unknown. */
  loss_share: number | null | undefined;
  /** Gross dollar loss the share was computed from. null = unknown. */
  gross_loss: number | null | undefined;
  trade_count?: number | null;
  net_pnl?: number | null;
  /** Optional display alias. */
  alias?: string | null;
}

export interface LossDistributionPanelProps {
  rows: LossRow[];
  /** Share above which a single strategy is "concentrated" (red flag). */
  concentratedAbove?: number;
  /** Show at most N contributors (rest folded into a summary line). */
  maxRows?: number;
  /** When the account has no recorded loss at all. */
  emptyHint?: string;
}

/** Signed money, explicit no-data (never a fabricated $0.00). */
function moneySigned(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return v < 0 ? `-$${s}` : `$${s}`;
}

/** Heat color stops from rose (highest share) to slate (lowest). */
function heatColor(share: number, maxShare: number): string {
  if (maxShare <= 0) return "var(--bg-inset)";
  const t = Math.max(0, Math.min(1, share / maxShare));
  // low -> muted slate-red, high -> solid rose
  return `color-mix(in srgb, var(--red) ${Math.round(12 + t * 74)}%, var(--bg-inset))`;
}

function shareTone(share: number, concentratedAbove: number): "bad" | "warn" | "dim" {
  if (share >= concentratedAbove) return "bad";
  if (share >= concentratedAbove * 0.4) return "warn";
  return "dim";
}

export function LossDistributionPanel({
  rows,
  concentratedAbove = 0.25,
  maxRows = 8,
  emptyHint,
}: LossDistributionPanelProps) {
  const t = useI18n((s) => s.t);
  // perf: filter + rank once per (rows, maxRows), not on every parent render.
  const { sorted, unknown, maxShare, totalShare, shown, hidden } = useMemo(() => {
    const withShare = rows.filter((r) => r.loss_share !== null && r.loss_share !== undefined);
    const sortedRows = [...withShare].sort((a, b) => (b.loss_share ?? 0) - (a.loss_share ?? 0));
    const unknownRows = rows.filter((r) => r.loss_share === null || r.loss_share === undefined);
    const max = sortedRows.length ? (sortedRows[0]?.loss_share ?? 0) : 0;
    const total = sortedRows.reduce((acc, r) => acc + (r.loss_share ?? 0), 0);
    const shownRows = sortedRows.slice(0, maxRows);
    return {
      sorted: sortedRows,
      unknown: unknownRows,
      maxShare: max,
      totalShare: total,
      shown: shownRows,
      hidden: sortedRows.length - shownRows.length,
    };
  }, [rows, maxRows]);

  if (sorted.length === 0 && unknown.length === 0) {
    return <div className="ld-empty">{emptyHint ?? t("account.strat.no_loss", "no strategy carries recorded loss")}</div>;
  }

  return (
    <div className="ld-root">
      <div className="ld-rows">
        {shown.map((r) => {
          const share = clampRatio(r.loss_share) ?? 0;
          const tone = shareTone(share, concentratedAbove);
          const tip = [
            `${r.strategy_id}${r.alias ? ` · ${r.alias}` : ""}`,
            t("account.ld.tip_share", "loss share {share}", { share: `${(share * 100).toFixed(1)}%` }),
            t("account.ld.tip_gross", "gross loss {gross}", { gross: moneySigned(r.gross_loss) }),
            t("account.ld.tip_trades", "trades {trades}", { trades: String(r.trade_count ?? "—") }),
            t("account.ld.tip_net", "net {net}", { net: moneySigned(r.net_pnl) }),
          ].join("\n");
          return (
            <div
              className="ld-row"
              key={r.strategy_id}
              title={tip}
              data-tone={tone}
            >
              <span className="ld-rank">{String(shown.indexOf(r) + 1).padStart(2, "0")}</span>
              <div className="ld-id-cell">
                <span className="ld-id">{r.alias ?? r.strategy_id}</span>
                <span className="ld-sub">
                  {t("account.ld.sub", "{share} · {gross} · {trades} trd", { share: `${(share * 100).toFixed(1)}%`, gross: moneySigned(r.gross_loss), trades: String(r.trade_count ?? "—") })}
                </span>
              </div>
              <div className="ld-bar">
                <div
                  className="ld-heat"
                  style={{ width: `${Math.max(2, share * 100)}%`, background: heatColor(share, maxShare) }}
                />
              </div>
              <span className={`ld-share ${tone}`}>{(share * 100).toFixed(1)}%</span>
            </div>
          );
        })}

        {hidden > 0 && (
          <div className="ld-more" title={t("account.ld.more_title", "{hidden} further contributors below the cutoff", { hidden: String(hidden) })}>
            {t("account.ld.more", "+{hidden} more · {pct}% combined", { hidden: String(hidden), pct: ((totalShare - shown.reduce((a, r) => a + (r.loss_share ?? 0), 0)) * 100).toFixed(1) })}
          </div>
        )}

        {unknown.length > 0 && (
          <div
            className="ld-row ld-unknown"
            title={t("account.ld.unknown_title", "{n} strategies report no loss share (unscored / DISCOVERED). Informational, not an error.", { n: String(unknown.length) })}
          >
            <span className="ld-rank">?</span>
            <div className="ld-id-cell">
              <span className="ld-id">{t("account.ld.unscored", "{n} unscored", { n: String(unknown.length) })}</span>
              <span className="ld-sub">{t("account.ld.no_attribution", "no loss attribution yet")}</span>
            </div>
            <div className="ld-bar">
              <div className="ld-heat ld-heat-unknown" />
            </div>
            <span className="ld-share dim">{t("account.ld.unknown", "UNKNOWN")}</span>
          </div>
        )}
      </div>
    </div>
  );
}
