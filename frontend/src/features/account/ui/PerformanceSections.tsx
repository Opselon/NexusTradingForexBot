/**
 * Performance metrics + per-kind series + Performance Intelligence.
 *
 * The advanced grid echoes `advanced` from /api/account/performance (computed
 * by the accounting core over `sample_trades` closed trades); missing stats
 * render "—" — never a confident 0 (legacy acctNum contract). The series chart
 * draws net_pnl per consecutive period straight from /performance/{kind}/series.
 * The intelligence panel echoes the structured PerformanceReport summary that
 * the Telegram daily report consumes — same object, so UI and report agree.
 */

import { useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { Sparkline } from "@/components/viz";
import { formatDateTime } from "@/lib/format";
import { useAccountPerformance, useAccountIntelligence, useAccountSeries } from "../hooks";
import { ADVANCED_ROWS } from "../model";
import type { PeriodKind } from "../types";
import { DASH, FreshnessNote, asErrorText, moneyOrDash } from "./shared";
import { useI18n } from "@/stores/i18nStore";

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** ADVANCED_ROWS display labels (metric names shown to humans). */
function advLabelT(key: string, t: Translator): string {
  switch (key) {
    case "sharpe_ratio": return t("account.adv.sharpe", "Sharpe");
    case "sortino_ratio": return t("account.adv.sortino", "Sortino");
    case "calmar_ratio": return t("account.adv.calmar", "Calmar");
    case "sqn": return t("account.adv.sqn", "SQN");
    case "recovery_factor": return t("account.adv.recovery_factor", "Recovery factor");
    case "payoff_ratio": return t("account.adv.payoff_ratio", "Payoff ratio");
    case "profit_factor": return t("account.adv.profit_factor", "Profit factor");
    case "average_win": return t("account.adv.average_win", "Average win");
    case "average_loss": return t("account.adv.average_loss", "Average loss");
    case "max_consecutive_wins": return t("account.adv.max_win_streak", "Max win streak");
    case "max_consecutive_losses": return t("account.adv.max_loss_streak", "Max loss streak");
    case "equity_volatility_pct": return t("account.adv.equity_volatility", "Equity volatility %");
    case "stop_loss_share": return t("account.adv.stop_loss_share", "Stop-loss share");
    case "avg_r_multiple": return t("account.adv.avg_win_r", "Avg win R");
    case "avg_loss_r": return t("account.adv.avg_loss_r", "Avg loss R");
    case "avg_mae_r": return t("account.adv.avg_mae", "Avg MAE");
    case "avg_mfe_r": return t("account.adv.avg_mfe", "Avg MFE");
    case "avg_hold_sec": return t("account.adv.avg_hold", "Avg hold");
    case "avg_risk_usd": return t("account.adv.avg_risk", "Avg risk $");
    case "r_coverage_ratio": return t("account.adv.r_coverage", "R coverage");
    default: return key;
  }
}

/** Period button captions (human words for the DAY/WEEK/MONTH/YEAR switch). */
function periodBtnT(k: PeriodKind, t: Translator): string {
  switch (k) {
    case "DAY": return t("account.pbtn.day", "DAY");
    case "WEEK": return t("account.pbtn.week", "WEEK");
    case "MONTH": return t("account.pbtn.month", "MONTH");
    case "YEAR": return t("account.pbtn.year", "YEAR");
    default: return k;
  }
}

/** Series panel title per period word. */
function seriesTitleT(k: PeriodKind, t: Translator): string {
  switch (k) {
    case "DAY": return t("account.series.title_day", "Net PnL per day (last 30 consecutive periods)");
    case "WEEK": return t("account.series.title_week", "Net PnL per week (last 30 consecutive periods)");
    case "MONTH": return t("account.series.title_month", "Net PnL per month (last 30 consecutive periods)");
    case "YEAR": return t("account.series.title_year", "Net PnL per year (last 30 consecutive periods)");
    default: return k;
  }
}

export function AdvancedMetricsSection() {
  const t = useI18n((s) => s.t);
  const perf = useAccountPerformance();
  const a = perf.data?.advanced;

  return (
    <Panel
      title={t("account.adv.title", "Risk-adjusted performance")}
      right={<FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label={t("account.fresh.advanced", "advanced")} staleAfterMs={90_000} />}
    >
      {perf.isPending ? (
        <Skeleton count={4} height={38} />
      ) : perf.isError ? (
        <ErrorState message={asErrorText(perf.error, t)} onRetry={() => perf.refetch()} />
      ) : !a ? (
        <EmptyState message={t("account.adv.empty", "No advanced metrics computed.")} hint={t("account.adv.empty_hint", "Needs closed trades in the accounting core.")} />
      ) : (
        <>
          <div className="acct-grid-stats">
            {ADVANCED_ROWS.map((row) => {
              const raw = (a as Record<string, number | null | undefined>)[row.key];
              const shown =
                raw === null || raw === undefined
                  ? DASH
                  : row.percentOfOne
                    ? `${(raw * 100).toFixed(1)}%`
                    : row.money
                      ? moneyOrDash(raw)
                      : `${raw.toFixed(row.digits)}${row.suffix ?? ""}`;
              const tone = raw === null || raw === undefined ? "" : raw > 0 ? "pos" : raw < 0 ? "neg" : "";
              return (
                <div className="acct-stat" key={row.key} title={row.key}>
                  <div className="k">{advLabelT(row.key, t)}</div>
                  <div className={`v ${tone}`}>{shown}</div>
                </div>
              );
            })}
          </div>
          <div className="tiny faint" style={{ marginTop: 8 }}>
            {t("account.adv.source_note", "source: accounting core · {trades} closed trades · sharpe/sortino/calmar/sqn computed over the same realized series the period reports use", { trades: String(a.sample_trades ?? 0) })}
          </div>
        </>
      )}
    </Panel>
  );
}

export function PeriodSeriesSection() {
  const t = useI18n((s) => s.t);
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const series = useAccountSeries(kind, 30);

  return (
    <Panel
      title={seriesTitleT(kind, t)}
      right={
        <>
          <span className="acct-actions">
            {(["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => (
              <button key={k} className={`btn small ${kind === k ? "primary" : "ghost"}`} onClick={() => setKind(k)}>
                {periodBtnT(k, t)}
              </button>
            ))}
          </span>
          <FreshnessNote updatedAtMs={series.dataUpdatedAt ?? null} label={t("account.fresh.series", "series")} />
        </>
      }
    >
      {series.isPending ? (
        <Skeleton count={2} height={60} />
      ) : series.isError ? (
        <ErrorState message={asErrorText(series.error, t)} onRetry={() => series.refetch()} />
      ) : (series.data ?? []).length === 0 ? (
        <EmptyState message={t("account.series.empty", "No periods returned.")} />
      ) : (
        <>
          <div className="spark-cell" style={{ gap: 14 }}>
            <Sparkline
              values={(series.data ?? []).map((p) => (typeof p.net_pnl === "number" ? p.net_pnl : null))}
              width={420}
              height={54}
              label={t("account.series.spark_label", "net pnl per {period}", { period: periodBtnT(kind, t) })}
            />
            <div className="tiny muted">
              {t("account.series.last", "{count} periods · last {last}", { count: String((series.data ?? []).length), last: moneyOrDash(series.data?.[0]?.net_pnl ?? null) })}
            </div>
          </div>
          <div style={{ display: "grid", gap: 2, marginTop: 10, maxHeight: 220, overflowY: "auto" }}>
            {(series.data ?? []).map((p, i) => (
              <div className="statline" key={`${p.key}-${i}`} style={{ fontSize: 11, justifyContent: "space-between" }}>
                <span>{p.key ?? `#${i}`}</span>
                <span>{t("account.stat.trades", "{count} trades", { count: String(p.total_trades ?? 0) })}</span>
                <span className={ (p.net_pnl ?? 0) >= 0 ? "tx-good" : "tx-bad" } >{moneyOrDash(p.net_pnl, true)}</span>
                <span className="faint">{t("account.series.wr", "wr {wr}", { wr: p.win_rate === null || p.win_rate === undefined ? DASH : `${p.win_rate.toFixed(0)}%` })}</span>
              </div>
            ))}
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>{t("account.series.oldest_note", "oldest → newest; the accounting worker keeps consecutive-period rows server-side.")}</div>
        </>
      )}
    </Panel>
  );
}

export function PerformanceIntelligenceSection() {
  const t = useI18n((s) => s.t);
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const intel = useAccountIntelligence(kind);
  const [showReport, setShowReport] = useState(false);

  const i = intel.data?.intelligence;

  return (
    <Panel
      title={t("account.intel.title", "Performance intelligence (report engine)")}
      right={
        <>
          <span className="acct-actions">
            {(["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => (
              <button key={k} className={`btn small ${kind === k ? "primary" : "ghost"}`} onClick={() => setKind(k)}>
                {periodBtnT(k, t)}
              </button>
            ))}
          </span>
          <button className="btn small ghost" onClick={() => setShowReport((v) => !v)}>
            {showReport ? t("account.intel.summary", "summary") : t("account.intel.full_report", "full report")}
          </button>
        </>
      }
    >
      {intel.isPending ? (
        <Skeleton count={2} height={44} />
      ) : intel.isError ? (
        <ErrorState message={asErrorText(intel.error, t)} onRetry={() => intel.refetch()} />
      ) : !intel.data ? (
        <EmptyState message={t("account.intel.empty", "No intelligence report.")} />
      ) : showReport ? (
        <pre tabIndex={0} style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, fontSize: 10.5, maxHeight: 420, overflow: "auto", fontFamily: "var(--mono)", margin: 0 }}>
          {JSON.stringify(intel.data.report ?? {}, null, 2)}
        </pre>
      ) : (
        <div className="grid cols-2">
          <div>
            <dl className="kv">
              <dt>{t("account.intel.anomaly_state", "anomaly state")}</dt>
              <dd>
                <StatusBadge status={i?.status ?? "NO_DATA"} />
              </dd>
              <dt>{t("account.intel.behavior_state", "behavior state")}</dt>
              <dd>
                <StatusBadge status={i?.behavior_state ?? "NO_DATA"} />
              </dd>
              <dt>{t("account.intel.trades_analyzed", "trades analyzed")}</dt>
              <dd>{i?.trades_analyzed ?? DASH}</dd>
              <dt>{t("account.intel.evidence_coverage", "evidence coverage")}</dt>
              <dd>{i?.evidence_coverage == null ? DASH : `${(i.evidence_coverage * 100).toFixed(0)}%`}</dd>
              <dt>{t("account.intel.versions", "versions")}</dt>
              <dd className="tiny">
                {t("account.intel.versions_line", "behavior {behavior} · anomaly {anomaly}", { behavior: i?.analysis_version || DASH, anomaly: i?.anomaly_version || DASH })}
              </dd>
            </dl>
          </div>
          <div>
            <div className="section-title">{t("account.intel.flags_title", "behavioral flags (backend counts)")}</div>
            {Object.keys(i?.behavioral_flags ?? {}).length === 0 ? (
              <EmptyState message={t("account.intel.flags_empty", "No behavioral flags counted.")} />
            ) : (
              <div className="statline">
                {Object.entries(i?.behavioral_flags ?? {}).map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{v}</b>
                  </span>
                ))}
              </div>
            )}
            <div className="section-title" style={{ marginTop: 10 }}>
              {t("account.intel.anomalies_title", "anomalies")}
            </div>
            {Object.keys(i?.anomalies ?? {}).length === 0 ? (
              <EmptyState message={t("account.intel.anomalies_empty", "No anomalies counted for this period.")} />
            ) : (
              <div className="statline">
                {Object.entries(i?.anomalies ?? {}).map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{v}</b>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      <div className="tiny faint" style={{ marginTop: 8 }}>
        {t("account.intel.note", "deterministic multi-stage enrichment over the accounting core — the same object the Telegram daily report consumes (read-only, never writes financial truth) · period {period}", { period: periodBtnT(kind, t) })}
      </div>
      <FreshnessNote updatedAtMs={intel.dataUpdatedAt ?? null} label={t("account.fresh.intelligence", "intelligence")} />
    </Panel>
  );
}

/** Human-readable line: latest snapshot time if present in the report. */
export function intelGeneratedAt(report: Record<string, unknown> | undefined): string {
  const g = report?.generated_at;
  return typeof g === "string" ? formatDateTime(g) : DASH;
}
