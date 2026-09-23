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
import { DASH, FreshnessNote, asErrorText, moneyOrDash, numOrDash, pctOrDash } from "./shared";
import "./account.css";
import { useI18n } from "@/stores/i18nStore";

const KINDS: Array<{ id: PeriodKind; label: string }> = (["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => ({
  id: k,
  label: PERIOD_LABEL[k],
}));

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

function periodLabelT(k: PeriodKind, t: Translator): string {
  switch (k) {
    case "DAY": return t("account.period.broker_day", "Broker day");
    case "WEEK": return t("account.period.week", "Week");
    case "MONTH": return t("account.period.month", "Month");
    case "YEAR": return t("account.period.year", "Year");
    default: return k;
  }
}

function emptyMsgT(k: PeriodKind, t: Translator): string {
  switch (k) {
    case "DAY": return t("account.summary.empty_day", "No closed trades recorded for this day yet.");
    case "WEEK": return t("account.summary.empty_week", "No closed trades recorded for this week yet.");
    case "MONTH": return t("account.summary.empty_month", "No closed trades recorded for this month yet.");
    case "YEAR": return t("account.summary.empty_year", "No closed trades recorded for this year yet.");
    default: return "";
  }
}

export function AccountSummarySection() {
  const t = useI18n((s) => s.t);
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
        title={t("account.summary.live_title", "Live account state (accounting core)")}
        right={
          <>
            {live?.available === false && <span className="acct-chip neg">{live.error ? t("account.summary.adapter_error", "adapter error") : t("account.summary.adapter_unavailable", "adapter unavailable")}</span>}
            {live?.available === true && <span className="acct-chip pos">{live.source || t("account.summary.available", "AVAILABLE")}</span>}
            <FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label={t("account.fresh.performance", "performance")} staleAfterMs={90_000} />
          </>
        }
      >
        {perf.isPending ? (
          <Skeleton count={2} height={56} />
        ) : perf.isError ? (
          <ErrorState message={asErrorText(perf.error, t)} onRetry={() => perf.refetch()} />
        ) : (
          <div className="acct-summary">
            <MetricCard label={t("account.summary.balance", "balance")} value={moneyOrDash(live?.balance)} tone="dim" sub={live?.currency || DASH} />
            <MetricCard label={t("account.summary.equity", "equity")} value={moneyOrDash(live?.equity)} tone="dim" sub={t("account.summary.margin_sub", "margin {margin} · free {free}", { margin: moneyOrDash(live?.margin), free: moneyOrDash(live?.margin_free) })} />
            <MetricCard
              label={t("account.summary.floating_pnl", "floating PnL")}
              value={moneyOrDash(live?.floating_pnl, true)}
              tone={(live?.floating_pnl ?? 0) >= 0 ? "pos" : "neg"}
              sub={t("account.summary.open_sub", "open positions {positions} · vol {vol}", { positions: String(live?.open_positions ?? DASH), vol: numOrDash(live?.open_volume) })}
            />
            <MetricCard
              label={t("account.summary.closed_realized", "closed realized PnL")}
              value={moneyOrDash(totals?.realized_pnl, true)}
              tone={(totals?.realized_pnl ?? 0) >= 0 ? "pos" : "neg"}
              sub={t("account.summary.closed_sub", "{closed} closed · win {win}", { closed: String(totals?.closed_trades ?? DASH), win: pctOrDash(totals?.win_rate, 1) })}
            />
            <MetricCard
              label={t("account.summary.current_drawdown", "current drawdown")}
              value={pctOrDash(dd?.current_drawdown_pct)}
              tone={(dd?.current_drawdown_pct ?? 0) > 5 ? "neg" : "dim"}
              sub={
                dd?.max_drawdown_at
                  ? t("account.summary.max_sub_at", "max {max} @ {at}", { max: pctOrDash(dd?.max_drawdown_pct), at: formatDateTime(dd.max_drawdown_at) })
                  : t("account.summary.max_sub", "max {max}", { max: pctOrDash(dd?.max_drawdown_pct) })
              }
            />
            <MetricCard
              label={t("account.summary.recovery", "recovery")}
              value={pctOrDash(dd?.recovery_pct, 1)}
              tone="dim"
              sub={dd?.in_drawdown ? t("account.summary.in_dd", "in drawdown · {sec}s", { sec: numOrDash(dd?.drawdown_duration_sec, 0) }) : dd?.has_data ? t("account.summary.not_in_dd", "not in drawdown") : t("account.summary.no_samples", "no samples")}
            />
          </div>
        )}
        {perf.data?.worker && (
          <div className="tiny faint inline-mono" style={{ marginTop: 8 }}>
            {t("account.summary.worker", "worker:")} {JSON.stringify(perf.data.worker).slice(0, 220)}
          </div>
        )}
      </Panel>

      <Panel
        title={t("account.summary.period_title", "Period report · {label}", { label: periodLabelT(kind, t) })}
        right={
          <>
            <Segmented options={KINDS.map((k) => ({ id: k.id, label: periodLabelT(k.id, t) }))} value={kind} onChange={setKind} />
            <FreshnessNote updatedAtMs={period.dataUpdatedAt ?? null} label={t("account.fresh.period", "period")} />
          </>
        }
      >
        {period.isPending ? (
          <Skeleton count={3} height={40} />
        ) : period.isError ? (
          <ErrorState message={asErrorText(period.error, t)} onRetry={() => period.refetch()} />
        ) : !p || p.has_data === false ? (
          <EmptyState message={emptyMsgT(kind, t)} hint={t("account.summary.empty_hint", "The report is honest-empty: the backend says has_data=false.")} />
        ) : (
          <>
            <div className="acct-hero">
              <div>
                <div className="tiny faint">{t("account.summary.net_pnl", "NET PNL")}</div>
                <div dir="ltr" className={`big ${(p.net_pnl ?? 0) >= 0 ? "pos" : "neg"}`}>{moneyOrDash(p.net_pnl, true)}</div>
              </div>
              <div>
                <div className="tiny faint">{t("account.summary.pnl_pct", "PnL %")}</div>
                <div dir="ltr" className="big">{pctOrDash(p.pnl_pct, 2)}</div>
              </div>
              <div style={{ minWidth: 220 }}>
                <div className="tiny faint">
                  {t("account.summary.gross", "gross profit {gp} · gross loss {gl}", { gp: moneyOrDash(p.gross_profit), gl: moneyOrDash(p.gross_loss) })}
                </div>
                <div className="split" role="img" aria-label={t("account.summary.gross_alt", "gross profit {gp} vs gross loss {gl}", { gp: String(p.gross_profit ?? 0), gl: String(p.gross_loss ?? 0) })}>
                  <i className="gp" style={{ inlineSize: `${grossShare(p.gross_profit, p.gross_loss)}%` }} />
                  <i className="gl" style={{ inlineSize: `${100 - grossShare(p.gross_profit, p.gross_loss)}%` }} />
                </div>
              </div>
              <div className="statline">
                <span>{t("account.stat.trades", "{count} trades", { count: String(p.total_trades ?? DASH) })}</span>
                <span>{t("account.stat.win", "win {win} ({denom} denom)", { win: pctOrDash(p.win_rate, 1), denom: String(p.win_rate_denominator ?? "NONE") })}</span>
                <span>{t("account.stat.loss", "loss {decided} decided / {all} all", { decided: pctOrDash(p.loss_rate_decided, 1), all: pctOrDash(p.loss_rate_all, 1) })}</span>
                <span>{t("account.stat.pf", "PF {pf}", { pf: numOrDash(p.profit_factor, 3) })}</span>
                <span>{t("account.stat.avg_r", "avg R {avg} (n={count})", { avg: numOrDash(p.average_r, 3), count: String(p.r_sample_count ?? 0) })}</span>
                <span>{t("account.stat.expectancy", "expectancy {exp} · incl BE {be} ({count} BE)", { exp: moneyOrDash(p.expectancy), be: moneyOrDash(p.expectancy_breakeven_incl), count: String(p.breakeven_count ?? 0) })}</span>
                <span>{t("account.stat.best_worst", "best {best} · worst {worst}", { best: moneyOrDash(p.best_trade), worst: moneyOrDash(p.worst_trade) })}</span>
                <span>{t("account.stat.max_dd", "max DD {pct} / {usd}", { pct: pctOrDash(p.max_drawdown_pct), usd: moneyOrDash(p.max_drawdown_usd) })}</span>
                <span>{t("account.stat.hold", "hold {hold}", { hold: p.average_holding_sec != null ? `${Math.round(p.average_holding_sec)}s` : DASH })}</span>
                <span>{t("account.stat.risk_deployed", "risk deployed {risk}", { risk: moneyOrDash(p.total_risk_deployed) })}</span>
                <span>{t("account.stat.costs", "costs {costs} ({drag} drag)", { costs: moneyOrDash(p.total_costs), drag: pctOrDash(p.cost_drag_pct) })}</span>
                <span>{t("account.stat.pnl_wtd", "PnL-wtd win {win}", { win: pctOrDash(p.pnl_weighted_win_rate, 1) })}</span>
              </div>
            </div>
            {market && (
              <div className="statline" style={{ marginTop: 6 }}>
                <span>{t("account.stat.broker_day", "broker day {day}", { day: String(market.server_day ?? DASH) })}</span>
                <span>
                  {t("account.stat.market", "market {state}", { state: market.state ?? "UNKNOWN"})}
                  {market.state && market.state !== "OPEN" && market.next_open_iso ? ` ${t("account.stat.opens", "· opens {at}", { at: formatDateTime(market.next_open_iso) })}` : ""}
                </span>
                {market.last_tick_age_sec != null && <span>{t("account.stat.tick_age", "tick age {sec}s", { sec: String(Math.round(market.last_tick_age_sec)) })}</span>}
                {market.reason && <span className="faint">{market.reason}</span>}
              </div>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              {t("account.summary.period_footer", "period {key} · {start} → {end} · all figures from the accounting core (single methodology)", {
                key: String(p.key ?? DASH),
                start: p.period_start ? formatDateTime(p.period_start) : DASH,
                end: p.period_end ? formatDateTime(p.period_end) : DASH,
              })}
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
