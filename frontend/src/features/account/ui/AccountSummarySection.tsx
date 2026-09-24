/**
 * Summary cards + canonical period report — analytics-studio pass.
 *
 * PURPOSE:  hero KPI rail over the live accounting state + a period KPI rail
 *           beside the existing period hero; every value is the backend's own
 *           number (percentages arrive in percent — no client math), and the
 *           only client-computed numbers are deltas, always labeled "derived".
 * OWNER:    uiux-w6-account
 * CONSUMES: useAccountPerformance/useAccountPeriod, PERIOD_LABEL, shared formatters,
 *           studio-math (delta helpers), studio-math-react (KpiCard)
 * PROVIDES: AccountSummarySection (default page section)
 * INVARIANTS: `has_data:false` renders the honest empty report (original string);
 *           null numeric fields render "—" — never a synthetic 0 (BUG-020);
 *           the period Segmented control re-queries the server unchanged.
 * EXTEND:   new KPI cards go in the rails below; never add client-side
 *           aggregation beyond labeled deltas of two loaded scalars.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Segmented, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime } from "@/lib/format";
import { useAccountPerformance, useAccountPeriod } from "../hooks";
import { PERIOD_LABEL, rateTone } from "../model";
import type { PeriodKind } from "../types";
import { DASH, FreshnessNote, asErrorText, moneyOrDash, numOrDash, pctOrDash } from "./shared";
import { KpiCard } from "./studio-math-react";
import { deltaTone } from "./studio-math";
import "./account.css";
import "./account-studio.css";

const KINDS: Array<{ id: PeriodKind; label: string }> = ([ "DAY", "WEEK", "MONTH", "YEAR" ] as PeriodKind[]).map((k) => ({
  id: k,
  label: PERIOD_LABEL[k],
}));

/** A derived delta only when both operands are real (else undefined → no line). */
type KpiTone = "pos" | "neg" | "dim" | "warn";
function ratioDelta(num: number | null | undefined, den: number | null | undefined): { text: string; tone: KpiTone; derived: boolean; hint: string } | undefined {
  if (num === null || num === undefined || den === null || den === undefined) return undefined;
  if (!Number.isFinite(num) || !Number.isFinite(den) || den === 0) return undefined;
  const r = (num / den) * 100;
  return { text: `${r >= 0 ? "+" : "−"}${Math.abs(r).toFixed(2)}%`, tone: deltaTone(r), derived: true, hint: "of balance" };
}

function countDelta(wins: number | null | undefined, losses: number | null | undefined): { text: string; tone: KpiTone; derived: boolean; hint: string } | undefined {
  if (wins === null || wins === undefined || losses === null || losses === undefined) return undefined;
  const d = wins - losses;
  return { text: `${d >= 0 ? "+" : "−"}${Math.abs(d)}`, tone: deltaTone(d), derived: true, hint: "wins − losses" };
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

  // perf (wave-7, ported): the worker envelope is serialized once per distinct
  // payload and then truncated, instead of serialize+truncate on every render;
  // the dep array is exactly the single field this line reads.
  const worker = perf.data?.worker;
  const workerLine = useMemo(() => (worker ? JSON.stringify(worker).slice(0, 220) : null), [worker]);
  return (
    <>
      <Panel
        title={t("account.summary.title", "Live account state (accounting core)")}
        right={
          <>
            {live?.available === false && <span className="acct-chip neg">{t("account.summary.adapter_down", "adapter {state}", { state: live.error ? t("account.summary.adapter_error", "error") : t("account.summary.adapter_unavailable", "unavailable") })}</span>}
            {live?.available === true && <span className="acct-chip pos">{live.source || "AVAILABLE"}</span>}
            <FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label={t("account.fresh.performance", "performance")} staleAfterMs={90_000} />
          </>
        }
      >
        {perf.isPending ? (
          <Skeleton count={2} height={56} />
        ) : perf.isError ? (
          <ErrorState message={asErrorText(perf.error)} onRetry={() => perf.refetch()} />
        ) : (
          <div className="acc-kpi-rail">
            <KpiCard
              label={t("account.summary.balance", "balance")}
              value={moneyOrDash(live?.balance)}
              tone="dim"
              sub={live?.currency || DASH}
            />
            <KpiCard
              label={t("account.summary.equity", "equity")}
              value={moneyOrDash(live?.equity)}
              tone="dim"
              delta={
                live?.equity !== null && live?.equity !== undefined && live?.balance !== null && live?.balance !== undefined
                  ? { ...delta(live.equity - live.balance), hint: t("account.summary.vs_balance", "vs balance") }
                  : undefined
              }
              sub={t("account.summary.margin_sub", "margin {margin} · free {free}", { margin: moneyOrDash(live?.margin), free: moneyOrDash(live?.margin_free) })}
            />
            <KpiCard
              label={t("account.summary.floating_pnl", "floating PnL")}
              value={moneyOrDash(live?.floating_pnl, true)}
              tone={live?.floating_pnl == null ? "dim" : live.floating_pnl >= 0 ? "pos" : "neg"}
              delta={ratioDelta(live?.floating_pnl, live?.balance)}
              sub={t("account.summary.floating_sub", "open positions {n} · vol {v}", { n: live?.open_positions ?? DASH, v: live?.open_volume ?? DASH })}
            />
            <KpiCard
              label={t("account.summary.realized_pnl", "closed realized PnL")}
              value={moneyOrDash(totals?.realized_pnl, true)}
              tone={totals?.realized_pnl == null ? "dim" : totals.realized_pnl >= 0 ? "pos" : "neg"}
              sub={t("account.summary.realized_sub", "{n} closed · win {rate}", { n: totals?.closed_trades ?? DASH, rate: pctOrDash(totals?.win_rate, 1) })}
            />
            <KpiCard
              label={t("account.summary.win_rate", "win rate")}
              value={pctOrDash(totals?.win_rate, 1)}
              tone={rateTone(totals?.win_rate)}
              delta={countDelta(totals?.win_count, totals?.loss_count)}
              sub={t("account.summary.win_rate_sub", "W {w} · L {l}", { w: totals?.win_count ?? DASH, l: totals?.loss_count ?? DASH })}
            />
            <KpiCard
              label={t("account.summary.current_dd", "current drawdown")}
              value={pctOrDash(dd?.current_drawdown_pct)}
              tone={dd?.current_drawdown_pct == null ? "dim" : dd.current_drawdown_pct > 5 ? "neg" : "warn"}
              sub={t("account.summary.dd_sub", "max {v}{at}", { v: pctOrDash(dd?.max_drawdown_pct), at: dd?.max_drawdown_at ? ` @ ${formatDateTime(dd.max_drawdown_at)}` : "" })}
            />
            <KpiCard
              label={t("account.summary.recovery", "recovery")}
              value={pctOrDash(dd?.recovery_pct, 1)}
              tone="dim"
              sub={dd?.in_drawdown ? t("account.summary.in_drawdown", "in drawdown · {n}s", { n: numOrDash(dd?.drawdown_duration_sec, 0) }) : dd?.has_data ? t("account.summary.not_in_drawdown", "not in drawdown") : t("account.summary.no_samples", "no samples")}
            />
          </div>
        )}
        {workerLine && (
          <div className="tiny faint inline-mono" style={{ marginTop: 8 }}>
            worker: {workerLine}
          </div>
        )}
      </Panel>

      <Panel
        title={t("account.summary.period_title", "Period report · {label}", { label: PERIOD_LABEL[kind] })}
        right={
          <>
            <Segmented options={KINDS} value={kind} onChange={setKind} />
            <FreshnessNote updatedAtMs={period.dataUpdatedAt ?? null} label={t("account.fresh.period", "period")} />
          </>
        }
      >
        {period.isPending ? (
          <Skeleton count={3} height={40} />
        ) : period.isError ? (
          <ErrorState message={asErrorText(period.error)} onRetry={() => period.refetch()} />
        ) : !p || p.has_data === false ? (
          <EmptyState message={t("account.summary.period_empty", "No closed trades recorded for this {kind} yet.", { kind: kind.toLowerCase() })} hint={t("account.summary.period_empty_hint", "The report is honest-empty: the backend says has_data=false.")} />
        ) : (
          <>
            <div className="acc-kpi-rail" style={{ marginBlockEnd: 10 }}>
              <KpiCard
                label={t("account.summary.win_rate", "win rate")}
                value={pctOrDash(p.win_rate, 1)}
                tone={rateTone(p.win_rate)}
                delta={countDelta(p.win_count, p.loss_count)}
                sub={t("account.summary.denom_sub", "{d} denom", { d: p.win_rate_denominator ?? "NONE" })}
              />
              <KpiCard
                label={t("account.summary.profit_factor", "profit factor")}
                value={numOrDash(p.profit_factor, 3)}
                tone={p.profit_factor == null ? "dim" : p.profit_factor > 1 ? "pos" : p.profit_factor < 1 ? "neg" : "dim"}
                sub={t("account.summary.be_sub", "{n} BE included", { n: p.breakeven_count ?? 0 })}
              />
              <KpiCard
                label={t("account.summary.expectancy", "expectancy")}
                value={moneyOrDash(p.expectancy)}
                tone={p.expectancy == null ? "dim" : p.expectancy > 0 ? "pos" : p.expectancy < 0 ? "neg" : "dim"}
                sub={t("account.summary.expectancy_sub", "incl BE {v}", { v: moneyOrDash(p.expectancy_breakeven_incl) })}
              />
              <KpiCard
                label={t("account.summary.avg_r", "avg R")}
                value={`${numOrDash(p.average_r, 3)}`}
                tone={p.average_r == null ? "dim" : p.average_r > 0 ? "pos" : p.average_r < 0 ? "neg" : "dim"}
                sub={t("account.summary.samples_sub", "n = {n} samples", { n: p.r_sample_count ?? 0 })}
              />
            </div>
            <div className="acct-hero">
              <div>
                <div className="tiny faint">{t("account.summary.net_pnl", "NET PNL")}</div>
                <div className={`big ${(p.net_pnl ?? 0) >= 0 ? "pos" : "neg"}`}>{moneyOrDash(p.net_pnl, true)}</div>
              </div>
              <div>
                <div className="tiny faint">{t("account.summary.pnl_pct", "PnL %")}</div>
                <div className="big">{pctOrDash(p.pnl_pct, 2)}</div>
              </div>
              <div style={{ minWidth: 220 }}>
                <div className="tiny faint">
                  {t("account.summary.gross_line", "gross profit {p} · gross loss {l}", { p: moneyOrDash(p.gross_profit), l: moneyOrDash(p.gross_loss) })}
                </div>
                <div className="split" role="img" aria-label={`gross profit ${p.gross_profit ?? 0} vs gross loss ${p.gross_loss ?? 0}`}>
                  <i className="gp" style={{ inlineSize: `${grossShare(p.gross_profit, p.gross_loss)}%` }} />
                  <i className="gl" style={{ inlineSize: `${100 - grossShare(p.gross_profit, p.gross_loss)}%` }} />
                </div>
              </div>
              <div className="statline">
                <span>{t("account.summary.l_trades", "{n} trades", { n: p.total_trades ?? DASH })}</span>
                <span>{t("account.summary.l_win", "win {rate} ({d} denom)", { rate: pctOrDash(p.win_rate, 1), d: p.win_rate_denominator ?? "NONE" })}</span>
                <span>{t("account.summary.l_loss", "loss {decided} decided / {all} all", { decided: pctOrDash(p.loss_rate_decided, 1), all: pctOrDash(p.loss_rate_all, 1) })}</span>
                <span>{t("account.summary.l_pf", "PF {v}", { v: numOrDash(p.profit_factor, 3) })}</span>
                <span>{t("account.summary.l_avgr", "avg R {v} (n={n})", { v: numOrDash(p.average_r, 3), n: p.r_sample_count ?? 0 })}</span>
                <span>{t("account.summary.l_exp", "expectancy {v} · incl BE {be} ({n} BE)", { v: moneyOrDash(p.expectancy), be: moneyOrDash(p.expectancy_breakeven_incl), n: p.breakeven_count ?? 0 })}</span>
                <span>{t("account.summary.l_bw", "best {b} · worst {w}", { b: moneyOrDash(p.best_trade), w: moneyOrDash(p.worst_trade) })}</span>
                <span>{t("account.summary.l_dd", "max DD {pct} / {usd}", { pct: pctOrDash(p.max_drawdown_pct), usd: moneyOrDash(p.max_drawdown_usd) })}</span>
                <span>{t("account.summary.l_hold", "hold {v}", { v: p.average_holding_sec != null ? `${Math.round(p.average_holding_sec)}s` : DASH })}</span>
                <span>{t("account.summary.l_risk", "risk deployed {v}", { v: moneyOrDash(p.total_risk_deployed) })}</span>
                <span>{t("account.summary.l_costs", "costs {v} ({d} drag)", { v: moneyOrDash(p.total_costs), d: pctOrDash(p.cost_drag_pct) })}</span>
                <span>{t("account.summary.l_pnlw", "PnL-wtd win {v}", { v: pctOrDash(p.pnl_weighted_win_rate, 1) })}</span>
              </div>
            </div>
            {market && (
              <div className="statline" style={{ marginTop: 6 }}>
                <span>{t("account.summary.l_broker_day", "broker day {v}", { v: market.server_day ?? DASH })}</span>
                <span>
                  {t("account.summary.l_market", "market {state}{at}", { state: market.state ?? "UNKNOWN", at: market.state && market.state !== "OPEN" && market.next_open_iso ? ` · opens ${formatDateTime(market.next_open_iso)}` : "" })}
                </span>
                {market.last_tick_age_sec != null && <span>{t("account.summary.l_tick_age", "tick age {n}s", { n: Math.round(market.last_tick_age_sec) })}</span>}
                {market.reason && <span className="faint">{market.reason}</span>}
              </div>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              {t("account.summary.l_period_foot", "period {key} · {start} → {end} · all figures from the accounting core (single methodology)", { key: p.key ?? DASH, start: p.period_start ? formatDateTime(p.period_start) : DASH, end: p.period_end ? formatDateTime(p.period_end) : DASH })}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}

/** Plain signed-money delta for the equity-vs-balance KPI (always "derived"). */
function delta(v: number): { text: string; tone: KpiTone; derived: boolean } {
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return { text: `${v < 0 ? "−" : "+"}$${s}`, tone: deltaTone(v), derived: true };
}

function grossShare(gp: number | null | undefined, gl: number | null | undefined): number {
  const a = Math.max(0, gp ?? 0);
  const b = Math.max(0, gl ?? 0);
  const tot = a + b;
  if (tot === 0) return 50;
  return Math.round((a / tot) * 100);
}
