/**
 * PositionDatasetPanel — Layer-2 position-management dataset generator.
 *
 * Simulates historical trade decisions to produce position-state samples
 * (unrealized R, position age, ATR) with anti-leakage KEEP/CLOSE/REDUCE
 * labels and chronological purge/embargo splits.
 *
 * Stateless presentation component; owned state lives in ModelStudioPage.
 */

import { useMemo } from "react";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import {
  compareDatasetsByGranularity,
  type ModelStudioDatasetItem,
  type OptimalAction,
  type PositionDatasetResponse,
} from "../model";

interface PositionDatasetPanelProps {
  dimension: number;
  datasets: ModelStudioDatasetItem[];
  posSource: string;
  onPosSourceChange: (path: string) => void;
  posMaxHolding: number;
  onPosMaxHoldingChange: (n: number) => void;
  posTargetAtr: number;
  onPosTargetAtrChange: (n: number) => void;
  posFriction: number;
  onPosFrictionChange: (n: number) => void;
  posBusy: boolean;
  onGenerate: () => void;
  posResult: PositionDatasetResponse | null;
  posError: string;
}

const ACTION_COLORS: Record<OptimalAction, string> = {
  KEEP: "var(--green)",
  CLOSE: "var(--red)",
  REDUCE: "var(--amber)",
};

export function PositionDatasetPanel({
  dimension,
  datasets,
  posSource,
  onPosSourceChange,
  posMaxHolding,
  onPosMaxHoldingChange,
  posTargetAtr,
  onPosTargetAtrChange,
  posFriction,
  onPosFrictionChange,
  posBusy,
  onGenerate,
  posResult,
  posError,
}: PositionDatasetPanelProps) {
  const t = useI18n((s) => s.t);
  // perf: dataset sort derived only when the datasets prop changes (dep: datasets).
  const sortedDatasets = useMemo(() => [...datasets].sort(compareDatasetsByGranularity), [datasets]);
  const actions = posResult?.actions_distribution ?? {};
  const totalActions = Object.values(actions).reduce((a, b) => a + (b ?? 0), 0) || 1;

  return (
    <div className="ms-grid-dual">
      {/* LEFT: simulation parameters */}
      <Panel
        title={t("model-studio.position.sim_title", "Position-State Simulation (Layer-2)")}
        subtitle={t(
          "model-studio.position.sim_subtitle",
          "Replay historical entries to synthesize position-state samples with continuation-value labels.",
        )}
        accent
        right={
          <span className="badge neutral">
            {t("model-studio.pipeline.contract_short", "{d}D contract", { d: dimension })}
          </span>
        }
      >
        <div className="ms-control-group">
          <div className="ms-form-row">
            <label htmlFor="pos-source">{t("model-studio.position.lbl_source_ds", "Source Candle Dataset")}</label>
            <select
              id="pos-source"
              className="ms-select-styled"
              value={posSource}
              onChange={(e) => onPosSourceChange(e.target.value)}
            >
              {sortedDatasets.length === 0 && (
                <option value="">{t("model-studio.pipeline.no_datasets", "No staged datasets")}</option>
              )}
              {sortedDatasets.map((d) => (
                <option key={d.path} value={d.path}>
                  {d.name} ({d.size_display})
                </option>
              ))}
            </select>
          </div>

          <div className="ms-form-row">
            <label htmlFor="pos-hold">{t("model-studio.position.lbl_max_hold", "Max Holding Window (bars)")}</label>
            <input
              id="pos-hold"
              type="number"
              min={1}
              max={500}
              className="ms-input-styled"
              value={posMaxHolding}
              onChange={(e) => onPosMaxHoldingChange(Number(e.target.value) || 1)}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <div className="ms-form-row">
              <label htmlFor="pos-atr">{t("model-studio.position.lbl_target_atr", "Target ATR ×")}</label>
              <input
                id="pos-atr"
                type="number"
                step={0.1}
                min={0.1}
                className="ms-input-styled"
                value={posTargetAtr}
                onChange={(e) => onPosTargetAtrChange(Number(e.target.value) || 0)}
              />
            </div>
            <div className="ms-form-row">
              <label htmlFor="pos-friction">{t("model-studio.position.lbl_friction", "Friction (pips)")}</label>
              <input
                id="pos-friction"
                type="number"
                step={0.01}
                min={0}
                className="ms-input-styled"
                value={posFriction}
                onChange={(e) => onPosFrictionChange(Number(e.target.value) || 0)}
              />
            </div>
          </div>

          <button
            onClick={onGenerate}
            disabled={posBusy || !posSource}
            className="ms-btn-action ms-btn-purple"
          >
            {posBusy
              ? t("model-studio.position.gen_busy", "⏳ Simulating Position States…")
              : t("model-studio.position.gen_idle", "↯ Generate Position Dataset")}
          </button>

          {posError && (
            <div className="ms-banner-err">
              <span>⚠</span>
              <div>
                <strong>{t("model-studio.position.gen_err", "Generation Failed")}</strong>
                <div className="tiny" style={{ marginTop: 2 }}>{posError}</div>
              </div>
            </div>
          )}

          {posResult && (
            <div className="ms-banner-ok">
              <div>
                <strong>
                  {t("model-studio.position.gen_ok", "✓ {n} position-state samples", {
                    n: posResult.total_samples.toLocaleString(),
                  })}
                </strong>
                <div className="inline-mono tiny" style={{ opacity: 0.9, marginTop: 2, wordBreak: "break-all" }}>
                  {t(
                    "model-studio.position.gen_meta",
                    "{trades} trades · {sec}s · {file}",
                    {
                      trades: posResult.simulated_trades.toLocaleString(),
                      sec: posResult.elapsed_sec.toFixed(1),
                      file: posResult.dataset_path.split(/[/\\]/).pop() ?? "",
                    },
                  )}
                </div>
              </div>
              <span className="badge good">{t("model-studio.position.embargoed", "EMBARGOED")}</span>
            </div>
          )}
        </div>
      </Panel>

      {/* RIGHT: label distribution + split integrity */}
      <Panel
        title={t("model-studio.position.dist_title", "Anti-Leakage Label Distribution")}
        subtitle={t(
          "model-studio.position.dist_subtitle",
          "Chronological purge and embargo splits; continuation value across simulated trades.",
        )}
        accent
        right={
          posResult ? (
            <span className="badge good" dir="ltr">
              {posResult.sha256.substring(0, 10)}…
            </span>
          ) : null
        }
      >
        {!posResult ? (
          <div className="tiny faint" style={{ textAlign: "center", padding: "40px 0" }}>
            {t(
              "model-studio.position.empty_dist",
              "Run the simulation to audit the KEEP / CLOSE / REDUCE label balance and embargo split integrity.",
            )}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Label balance meters */}
            <div className="ms-prob-deck">
              {(Object.keys(ACTION_COLORS) as OptimalAction[]).map((act) => {
                const count = actions[act] ?? 0;
                const pctv = (count / totalActions) * 100;
                return (
                  <div key={act} className="ms-prob-row">
                    <div className="ms-prob-meta">
                      <span style={{ color: ACTION_COLORS[act] }}>{act}</span>
                      <span className="tx-dim" >
                        {count.toLocaleString()} · {pctv.toFixed(1)}%
                      </span>
                    </div>
                    <div className="ms-prob-track">
                      <div
                        className="ms-prob-fill"
                        style={{
                          width: `${Math.min(100, pctv)}%`,
                          background: ACTION_COLORS[act],
                          boxShadow: `0 0 10px ${ACTION_COLORS[act]}55`,
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Continuation-value telemetry */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                gap: 8,
                padding: 12,
                borderRadius: 8,
                background: "var(--bg-inset)",
                border: "1px solid var(--border)",
              }}
            >
              {[
                {
                  l: t("model-studio.position.stat_mean_cont", "Mean Continuation Value"),
                  v: posResult.mean_continuation_value.toFixed(4),
                  c: posResult.mean_continuation_value >= 0 ? "var(--green)" : "var(--red)",
                },
                {
                  l: t("model-studio.position.stat_mean_hold", "Mean Holding Bars"),
                  v: posResult.mean_holding_bars.toFixed(1),
                  c: "var(--accent-strong)",
                },
                {
                  l: t("model-studio.position.stat_sim_trades", "Simulated Trades"),
                  v: posResult.simulated_trades.toLocaleString(),
                  c: "var(--text)",
                },
              ].map((m) => (
                <div key={m.l}>
                  <div className="tiny faint uppercase" style={{ fontWeight: 700, letterSpacing: "0.08em" }}>
                    {m.l}
                  </div>
                  <div className="inline-mono small" style={{ color: m.c, fontWeight: 800, marginTop: 2 }}>
                    {m.v}
                  </div>
                </div>
              ))}
            </div>

            {/* Chronological split ledger */}
            <div>
              <div className="tiny uppercase font-bold" style={{ color: "var(--violet)", marginBottom: 8 }}>
                {t("model-studio.position.splits_title", "Chronological Purge / Embargo Splits")}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {Object.entries(posResult.splits).map(([name, count]) => (
                  <div
                    key={name}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                      padding: "7px 12px",
                      borderRadius: 7,
                      background: "var(--bg-inset)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <span className="inline-mono tiny tx-dim" >{name}</span>
                    <span className="inline-mono small" style={{ color: "var(--green)", fontWeight: 700 }}>
                      {t("model-studio.position.samples", "{n} samples", { n: count.toLocaleString() })}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="tiny faint">
              {t(
                "model-studio.position.integrity_note",
                "Dataset integrity SHA256 pins the exact label set — the adviser re-derives labels on load and rejects any drift from this fingerprint.",
              )}
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
