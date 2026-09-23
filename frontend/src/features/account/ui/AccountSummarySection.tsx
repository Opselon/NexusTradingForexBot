/**
 * Summary cards + canonical period report.
 *
 * Values are the backend's own numbers (percentages already in percent — no
 * client math). The period selector re-queries the server for that granularity;
 * `has_data:false` renders an explicit empty report instead of zeros.
 */

import { useState } from "react";
import { EmptyState, ErrorState, MetricCard, Panel, Segmented, Skeleton } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useAccountPerformance, useAccountPeriod } from "../hooks";
import { PERIOD_LABEL } from "../model";
import type { PeriodKind } from "../types";
import { DASH, FreshnessNote, errorProps, moneyOrDash, numOrDash, pctOrDash } from "./shared";
import "./account.css";

const KINDS: Array<{ id: PeriodKind; label: string }> = (["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => ({
  id: k,
  label: PERIOD_LABEL[k],
}));

export function AccountSummarySection() {
  const perf = useAccountPerformance();
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const period = useAccountPeriod(kind);

  const live = perf.data?.live;
  const totals = perf.data?.totals;
  const dd = perf.data?.drawdown;
  const p = period.data?.period;
  const market = period.data?.market;

  return (
    <>
      <Panel
        title="Live account state (accounting core)"
        right={
          <>
            {live?.available === false && <span className="acct-chip neg">adapter {live.error ? "error" : "unavailable"}</span>}
            {live?.available === true && <span className="acct-chip pos">{live.source || "AVAILABLE"}</span>}
            <FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label="performance" staleAfterMs={90_000} />
          </>
        }
      >
        {perf.isPending ? (
          <Skeleton count={2} height={56} />
        ) : perf.isError ? (
          <ErrorState {...errorProps(perf.error)} onRetry={() => perf.refetch()} />
        ) : (
          <div className="acct-summary">
            <MetricCard label="balance" value={moneyOrDash(live?.balance)} tone="dim" sub={live?.currency || DASH} />
            <MetricCard label="equity" value={moneyOrDash(live?.equity)} tone="dim" sub={`margin ${moneyOrDash(live?.margin)} · free ${moneyOrDash(live?.margin_free)}`} />
            <MetricCard
              label="floating PnL"
              value={moneyOrDash(live?.floating_pnl, true)}
              tone={(live?.floating_pnl ?? 0) >= 0 ? "pos" : "neg"}
              sub={`open positions ${live?.open_positions ?? DASH} · vol ${numOrDash(live?.open_volume)}`}
            />
            <MetricCard
              label="closed realized PnL"
              value={moneyOrDash(totals?.realized_pnl, true)}
              tone={(totals?.realized_pnl ?? 0) >= 0 ? "pos" : "neg"}
              sub={`${totals?.closed_trades ?? DASH} closed · win ${pctOrDash(totals?.win_rate, 1)}`}
            />
            <MetricCard
              label="current drawdown"
              value={pctOrDash(dd?.current_drawdown_pct)}
              tone={(dd?.current_drawdown_pct ?? 0) > 5 ? "neg" : "dim"}
              sub={`max ${pctOrDash(dd?.max_drawdown_pct)}${dd?.max_drawdown_at ? ` @ ${formatDateTime(dd.max_drawdown_at)}` : ""}`}
            />
            <MetricCard
              label="recovery"
              value={pctOrDash(dd?.recovery_pct, 1)}
              tone="dim"
              sub={dd?.in_drawdown ? `in drawdown · ${numOrDash(dd?.drawdown_duration_sec, 0)}s` : dd?.has_data ? "not in drawdown" : "no samples"}
            />
          </div>
        )}
        {perf.data?.worker && (
          <div className="tiny faint inline-mono" style={{ marginTop: 8 }}>
            worker: {JSON.stringify(perf.data.worker).slice(0, 220)}
          </div>
        )}
      </Panel>

      <Panel
        title={`Period report · ${PERIOD_LABEL[kind]}`}
        right={
          <>
            <Segmented options={KINDS} value={kind} onChange={setKind} />
            <FreshnessNote updatedAtMs={period.dataUpdatedAt ?? null} label="period" />
          </>
        }
      >
        {period.isPending ? (
          <Skeleton count={3} height={40} />
        ) : period.isError ? (
          <ErrorState {...errorProps(period.error)} onRetry={() => period.refetch()} />
        ) : !p || p.has_data === false ? (
          <EmptyState message={`No closed trades recorded for this ${kind.toLowerCase()} yet.`} hint="The report is honest-empty: the backend says has_data=false." />
        ) : (
          <>
            <div className="acct-hero">
              <div>
                <div className="tiny faint">NET PNL</div>
                <div className={`big ${(p.net_pnl ?? 0) >= 0 ? "pos" : "neg"}`}>{moneyOrDash(p.net_pnl, true)}</div>
              </div>
              <div>
                <div className="tiny faint">PnL %</div>
                <div className="big">{pctOrDash(p.pnl_pct, 2)}</div>
              </div>
              <div style={{ minWidth: 220 }}>
                <div className="tiny faint">
                  gross profit {moneyOrDash(p.gross_profit)} · gross loss {moneyOrDash(p.gross_loss)}
                </div>
                <div className="split" role="img" aria-label={`gross profit ${p.gross_profit ?? 0} vs gross loss ${p.gross_loss ?? 0}`}>
                  <i className="gp" style={{ inlineSize: `${grossShare(p.gross_profit, p.gross_loss)}%` }} />
                  <i className="gl" style={{ inlineSize: `${100 - grossShare(p.gross_profit, p.gross_loss)}%` }} />
                </div>
              </div>
              <div className="statline">
                <span>{p.total_trades ?? DASH} trades</span>
                <span>win {pctOrDash(p.win_rate, 1)} ({p.win_rate_denominator ?? "NONE"} denom)</span>
                <span>loss {pctOrDash(p.loss_rate_decided, 1)} decided / {pctOrDash(p.loss_rate_all, 1)} all</span>
                <span>PF {numOrDash(p.profit_factor, 3)}</span>
                <span>avg R {numOrDash(p.average_r, 3)} (n={p.r_sample_count ?? 0})</span>
                <span>expectancy {moneyOrDash(p.expectancy)} · incl BE {moneyOrDash(p.expectancy_breakeven_incl)} ({p.breakeven_count ?? 0} BE)</span>
                <span>best {moneyOrDash(p.best_trade)} · worst {moneyOrDash(p.worst_trade)}</span>
                <span>max DD {pctOrDash(p.max_drawdown_pct)} / {moneyOrDash(p.max_drawdown_usd)}</span>
                <span>hold {p.average_holding_sec != null ? `${Math.round(p.average_holding_sec)}s` : DASH}</span>
                <span>risk deployed {moneyOrDash(p.total_risk_deployed)}</span>
                <span>costs {moneyOrDash(p.total_costs)} ({pctOrDash(p.cost_drag_pct)} drag)</span>
                <span>PnL-wtd win {pctOrDash(p.pnl_weighted_win_rate, 1)}</span>
              </div>
            </div>
            {market && (
              <div className="statline" style={{ marginTop: 6 }}>
                <span>broker day {market.server_day ?? DASH}</span>
                <span>
                  market {market.state ?? "UNKNOWN"}
                  {market.state && market.state !== "OPEN" && market.next_open_iso ? ` · opens ${formatDateTime(market.next_open_iso)}` : ""}
                </span>
                {market.last_tick_age_sec != null && <span>tick age {Math.round(market.last_tick_age_sec)}s</span>}
                {market.reason && <span className="faint">{market.reason}</span>}
              </div>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              period {p.key ?? DASH} · {p.period_start ? formatDateTime(p.period_start) : DASH} → {p.period_end ? formatDateTime(p.period_end) : DASH} · all figures
              from the accounting core (single methodology)
            </div>
          </>
        )}
      </Panel>
    </>
  );
}

function grossShare(gp: number | null | undefined, gl: number | null | undefined): number {
  const a = Math.max(0, gp ?? 0);
  const b = Math.max(0, gl ?? 0);
  const tot = a + b;
  if (tot === 0) return 50;
  return Math.round((a / tot) * 100);
}
