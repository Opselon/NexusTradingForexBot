/**
 * StrategiesDashboard — institutional grid layout for the strategy surface.
 *
 * Replaces the legacy stacked table + awkward 2-col bottom split with a CSS
 * Grid dashboard that fits 1080p/1440p widescreen without horizontal overflow:
 *
 *   ┌──────────────────────────────────────┬──────────────────────┐
 *   │ STRATEGY METRICS TABLE               │ REGISTRY CONFIDENCE  │
 *   │ (sticky header, sort, search, copy,  │ (grid/list toggle)   │
 *   │  or the wave-6 cards view toggle)    ├──────────────────────┤
 *   │                                      │ LOSS RESPONSIBILITY  │
 *   └──────────────────────────────────────┴──────────────────────┘
 *
 * minmax(0,…) on every track is the anti-overflow guarantee: a grid item can
 * never force its column wider than the viewport, which is what made the old
 * gauges blow out the page.
 *
 * All data comes from useAccountStrategies(); the UI never re-scores a
 * strategy (backend owns lifecycle/confidence).
 *
 * Wave 6: a cards/table view toggle (w6.account.strategyView, contract §3
 * display state) renders <StrategyCards> — same rows, no re-scoring. The
 * table keeps every existing control (sort, search, copy, column set).
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel } from "@/components/primitives";
import { ConfidenceGauge, ConfidenceMeter } from "@/components/viz";
import { useAccountStrategies } from "../hooks";
import { useUiStore } from "@/stores/uiStore";
import { FreshnessNote, asErrorText } from "./shared";
import { StrategyMetricsTable } from "./StrategyMetricsTable";
import { StrategyCards } from "./StrategyCards";
import { LossDistributionPanel } from "./LossDistributionPanel";
import { usePersistedState } from "./usePersistedState";
import "./strategies-dashboard.css";
import "./account-studio-grid.css";

type StratView = "cards" | "table";
const isStratView = (v: unknown): v is StratView => v === "cards" || v === "table";

type ConfidenceView = "grid" | "list";

export function StrategiesDashboard() {
  const strategies = useAccountStrategies();
  // Derived row arrays are computed once per response identity
  // (`strategies.data` only changes when a fetch lands) — deps are exactly
  // the payload each derivation reads.
  const rows = useMemo(() => strategies.data?.strategies ?? [], [strategies.data]);
  const pushToast = useUiStore((s) => s.pushToast);
  const [view, setView] = useState<ConfidenceView>("grid");
  const [stratView, setStratView] = usePersistedState<StratView>("strategyView", "cards", {
    isValid: isStratView,
  });

  const confidenceRows = useMemo(() => rows.slice(0, 12), [rows]);
  const lossRows = useMemo(
    () =>
      rows.map((s) => ({
        strategy_id: s.strategy_id,
        loss_share: s.loss_share,
        gross_loss: s.gross_loss,
        trade_count: s.trade_count,
        net_pnl: s.net_pnl,
      })),
    [rows],
  );

  return (
    <div className="sd-root">
      <div className="sd-main">
        <Panel
          title={`Strategy contributions (${rows.length})`}
          right={
            <>
              <div className="acc-toggle" role="group" aria-label="Contributions view">
                <button
                  className={stratView === "cards" ? "active" : ""}
                  onClick={() => setStratView("cards")}
                  aria-pressed={stratView === "cards"}
                >
                  cards
                </button>
                <button
                  className={stratView === "table" ? "active" : ""}
                  onClick={() => setStratView("table")}
                  aria-pressed={stratView === "table"}
                >
                  table
                </button>
              </div>
              <FreshnessNote updatedAtMs={strategies.dataUpdatedAt ?? null} label="strategies" />
            </>
          }
        >
          {strategies.isPending ? (
            <div className="sd-loading">loading contributions…</div>
          ) : strategies.isError ? (
            <ErrorState message={asErrorText(strategies.error)} onRetry={() => strategies.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              message="NO STRATEGY EVIDENCE AVAILABLE"
              hint="Contributions need closed trades tagged with a strategy_id."
            />
          ) : stratView === "cards" ? (
            <StrategyCards
              rows={rows}
              onCopy={(_id, ok) =>
                pushToast(ok ? "ok" : "fail", ok ? "strategy id copied" : "copy failed — clipboard unavailable")
              }
            />
          ) : (
            <StrategyMetricsTable
              rows={rows}
              onCopy={(_id, ok) =>
                pushToast(ok ? "ok" : "fail", ok ? "strategy id copied" : "copy failed — clipboard unavailable")
              }
            />
          )}
        </Panel>
      </div>

      <div className="sd-side">
        <Panel
          title="Registry confidence"
          right={
            <div className="sd-view-toggle" role="group" aria-label="Confidence view">
              <button
                className={view === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
                aria-pressed={view === "grid"}
              >
                grid
              </button>
              <button
                className={view === "list" ? "active" : ""}
                onClick={() => setView("list")}
                aria-pressed={view === "list"}
              >
                list
              </button>
            </div>
          }
        >
          {confidenceRows.length === 0 ? (
            <EmptyState message="no confidence scores" />
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
            <div tabIndex={0} className="sd-meter-list">
              {confidenceRows.map((s) => (
                <ConfidenceMeter key={s.strategy_id} value={s.confidence} label={s.strategy_id} />
              ))}
            </div>
          )}
          <div className="sd-note tiny faint">
            DISCOVERED = observed but unscored family (informational, not an error). Tier: ≥0.70 HIGH · ≥0.50 MID · &lt;0.50 LOW.
          </div>
        </Panel>

        <Panel title="Loss responsibility" subtitle="share of account gross loss, ranked">
          <LossDistributionPanel rows={lossRows} />
        </Panel>
      </div>
    </div>
  );
}
