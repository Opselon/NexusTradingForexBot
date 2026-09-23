/**
 * Strategies table — per-strategy contribution joined to Strategy
 * Intelligence (/api/account/strategies). Lifecycle/confidence are READ from
 * the registry by the backend; the UI never re-scores a strategy.
 */

import { DataTable, EmptyState, ErrorState, Panel } from "@/components/primitives";
import { Gauge, HeatBar } from "@/components/viz";
import { formatNumber } from "@/lib/format";
import { useAccountStrategies } from "../hooks";
import { DASH, FreshnessNote, asErrorText, moneyOrDash, pctOrDash } from "./shared";
import { useI18n } from "@/stores/i18nStore";

export function StrategiesSection() {
  const t = useI18n((s) => s.t);
  const strategies = useAccountStrategies();
  const rows = strategies.data?.strategies ?? [];

  return (
    <Panel
      title={t("account.strat.title", "Strategy contributions ({count})", { count: String(rows.length) })}
      right={<FreshnessNote updatedAtMs={strategies.dataUpdatedAt ?? null} label={t("account.fresh.strategies", "strategies")} />}
    >
      {strategies.isPending ? (
        <div className="viz-empty">{t("account.strat.loading", "loading contributions…")}</div>
      ) : strategies.isError ? (
        <ErrorState message={asErrorText(strategies.error, t)} onRetry={() => strategies.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message={t("account.strat.no_evidence", "NO STRATEGY EVIDENCE AVAILABLE")} hint={t("account.strat.no_evidence_hint", "Contributions need closed trades tagged with a strategy_id.")} />
      ) : (
        <>
          <DataTable
            headers={[
              { label: t("account.sth.strategy", "strategy") },
              { label: t("account.sth.trades", "trades"), num: true },
              { label: t("account.sth.net_pnl", "net PnL"), num: true },
              { label: t("account.sth.win_pct", "win %"), num: true },
              { label: t("account.sth.pf", "PF"), num: true },
              { label: t("account.sth.avg_r", "avg R"), num: true },
              { label: t("account.sth.loss_share", "loss share"), num: true },
              { label: t("account.sth.lifecycle", "lifecycle") },
              { label: t("account.sth.confidence", "confidence"), num: true },
              { label: t("account.sth.expectancy_r", "expectancy R"), num: true },
            ]}
          >
            {rows.map((s) => (
              <tr key={s.strategy_id}>
                <td>{s.strategy_id}</td>
                <td className="num">{s.trade_count ?? 0}</td>
                <td className={`num ${(s.net_pnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>{moneyOrDash(s.net_pnl, true)}</td>
                <td className="num">{pctOrDash(s.win_rate, 1)}</td>
                <td className="num">{s.profit_factor === null || s.profit_factor === undefined ? DASH : formatNumber(s.profit_factor, 3)}</td>
                <td className="num">{s.average_r === null || s.average_r === undefined ? DASH : `${formatNumber(s.average_r, 3)}R`}</td>
                <td className="num">{s.loss_share === null || s.loss_share === undefined ? DASH : `${(s.loss_share * 100).toFixed(1)}%`}</td>
                <td>
                  <span className={`badge ${lifecycleTone(s.lifecycle_state)}`}>{s.lifecycle_state || "DISCOVERED"}</span>
                </td>
                <td className="num">{s.confidence === null || s.confidence === undefined ? DASH : formatNumber(s.confidence, 4)}</td>
                <td className="num">{s.expectancy_r === null || s.expectancy_r === undefined ? DASH : `${formatNumber(s.expectancy_r, 3)}R`}</td>
              </tr>
            ))}
          </DataTable>
          <div className="grid cols-2" style={{ marginTop: 12 }}>
            <div>
              <div className="section-title">{t("account.strat.loss_resp", "loss responsibility (share of account loss)")}</div>
              <HeatBar
                invert
                items={rows
                  .filter((s) => s.loss_share !== null && s.loss_share !== undefined)
                  .map((s) => ({ label: s.strategy_id, value: s.loss_share ?? null, caption: `${((s.loss_share ?? 0) * 100).toFixed(1)}%`, title: t("account.strat.heat_net", "net {net}", { net: moneyOrDash(s.net_pnl, true) }) }))}
                emptyHint={t("account.strat.no_loss", "no strategy carries recorded loss")}
                scaleCaptions={["0%", "100%"]}
              />
            </div>
            <div>
              <div className="section-title">{t("account.strat.reg_conf", "registry confidence (strategy intelligence)")}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {rows.slice(0, 4).map((s) => (
                  <Gauge
                    key={s.strategy_id}
                    size={112}
                    label={s.strategy_id.length > 14 ? `${s.strategy_id.slice(0, 14)}…` : s.strategy_id}
                    value={s.confidence ?? null}
                    min={0}
                    max={1}
                    format={(v) => v.toFixed(2)}
                  />
                ))}
                {rows.length === 0 && <EmptyState message={t("account.strat.no_rows", "no rows")} />}
              </div>
              <div className="tiny faint">{t("account.strat.discovered_note", "DISCOVERED = observed but unscored family (informational, not an error).")}</div>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}

function lifecycleTone(state: string | undefined): string {
  const s = (state ?? "DISCOVERED").toUpperCase();
  if (s === "ACTIVE") return "good";
  if (s === "RETIRED" || s === "QUARANTINED") return "bad";
  if (s === "DISCOVERED") return "neutral";
  return "warn";
}
