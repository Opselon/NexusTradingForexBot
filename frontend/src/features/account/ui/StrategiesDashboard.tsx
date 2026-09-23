/**
 * StrategiesDashboard — institutional grid layout for the strategy surface.
 *
 * Replaces the legacy stacked table + awkward 2-col bottom split with a CSS
 * Grid dashboard that fits 1080p/1440p widescreen without horizontal overflow:
 *
 *   ┌──────────────────────────────────────┬──────────────────────┐
 *   │ STRATEGY METRICS TABLE               │ REGISTRY CONFIDENCE  │
 *   │ (sticky header, sort, search, copy)  │ (grid/list toggle)   │
 *   │                                      ├──────────────────────┤
 *   │                                      │ LOSS RESPONSIBILITY  │
 *   └──────────────────────────────────────┴──────────────────────┘
 *
 * minmax(0,…) on every track is the anti-overflow guarantee: a grid item can
 * never force its column wider than the viewport, which is what made the old
 * gauges blow out the page.
 *
 * All data comes from useAccountStrategies(); the UI never re-scores a
 * strategy (backend owns lifecycle/confidence).
 */

import { useState } from "react";
import { EmptyState, ErrorState, Panel } from "@/components/primitives";
import { ConfidenceGauge, ConfidenceMeter } from "@/components/viz";
import { useAccountStrategies } from "../hooks";
import { useUiStore } from "@/stores/uiStore";
import { FreshnessNote, asErrorText } from "./shared";
import { useI18n } from "@/stores/i18nStore";
import { StrategyMetricsTable } from "./StrategyMetricsTable";
import { LossDistributionPanel } from "./LossDistributionPanel";
import "./strategies-dashboard.css";

type ConfidenceView = "grid" | "list";

export function StrategiesDashboard() {
  const t = useI18n((s) => s.t);
  const strategies = useAccountStrategies();
  const rows = strategies.data?.strategies ?? [];
  const pushToast = useUiStore((s) => s.pushToast);
  const [view, setView] = useState<ConfidenceView>("grid");

  const confidenceRows = rows.slice(0, 12);
  const lossRows = rows.map((s) => ({
    strategy_id: s.strategy_id,
    loss_share: s.loss_share,
    gross_loss: s.gross_loss,
    trade_count: s.trade_count,
    net_pnl: s.net_pnl,
  }));

  return (
    <div className="sd-root">
      <div className="sd-main">
        <Panel
          title={t("account.strat.title", "Strategy contributions ({count})", { count: String(rows.length) })}
          right={<FreshnessNote updatedAtMs={strategies.dataUpdatedAt ?? null} label={t("account.fresh.strategies", "strategies")} />}
        >
          {strategies.isPending ? (
            <div className="sd-loading">{t("account.strat.loading", "loading contributions…")}</div>
          ) : strategies.isError ? (
            <ErrorState message={asErrorText(strategies.error, t)} onRetry={() => strategies.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              message={t("account.strat.no_evidence", "NO STRATEGY EVIDENCE AVAILABLE")}
              hint={t("account.strat.no_evidence_hint", "Contributions need closed trades tagged with a strategy_id.")}
            />
          ) : (
            <StrategyMetricsTable
              rows={rows}
              onCopy={(_id, ok) =>
                pushToast(ok ? "ok" : "fail", ok ? t("account.sd.copied", "strategy id copied") : t("account.sd.copy_failed", "copy failed — clipboard unavailable"))
              }
            />
          )}
        </Panel>
      </div>

      <div className="sd-side">
        <Panel
          title={t("account.sd.reg_confidence", "Registry confidence")}
          right={
            <div className="sd-view-toggle" role="group" aria-label={t("account.sd.view_aria", "Confidence view")}>
              <button
                className={view === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
                aria-pressed={view === "grid"}
              >
                {t("account.sd.view_grid", "grid")}
              </button>
              <button
                className={view === "list" ? "active" : ""}
                onClick={() => setView("list")}
                aria-pressed={view === "list"}
              >
                {t("account.sd.view_list", "list")}
              </button>
            </div>
          }
        >
          {confidenceRows.length === 0 ? (
            <EmptyState message={t("account.sd.no_scores", "no confidence scores")} />
          ) : view === "grid" ? (
            <div className="cg-grid">
              {confidenceRows.map((s) => (
                <ConfidenceGauge
                  key={s.strategy_id}
                  value={s.confidence}
                  label={s.strategy_id}
                  compact
                />
              ))}
            </div>
          ) : (
            <div className="sd-meter-list">
              {confidenceRows.map((s) => (
                <ConfidenceMeter key={s.strategy_id} value={s.confidence} label={s.strategy_id} />
              ))}
            </div>
          )}
          <div className="sd-note tiny faint">
            {t("account.sd.tier_note", "DISCOVERED = observed but unscored family (informational, not an error). Tier: ≥0.70 HIGH · ≥0.50 MID · <0.50 LOW.")}
          </div>
        </Panel>

        <Panel title={t("account.sd.loss_resp", "Loss responsibility")} subtitle={t("account.sd.loss_resp_sub", "share of account gross loss, ranked")}>
          <LossDistributionPanel rows={lossRows} />
        </Panel>
      </div>
    </div>
  );
}
