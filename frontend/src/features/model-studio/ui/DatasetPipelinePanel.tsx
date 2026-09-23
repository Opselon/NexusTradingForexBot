/**
 * DatasetPipelinePanel — candle ingestion, Z-score normalization inspection,
 * and PyTorch training dispatch with progress tracking.
 *
 * Stateless presentation component; owned state lives in ModelStudioPage.
 */

import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import {
  compareDatasetsByGranularity,
  DOWNLOAD_SOURCES,
  DOWNLOAD_TIMEFRAMES,
  type DatasetDownloadResponse,
  type InspectFeaturesResponse,
  type ModelStudioDatasetItem,
  type ModelStudioTrainProgress,
} from "../model";

interface DatasetPipelinePanelProps {
  dimension: number;
  datasets: ModelStudioDatasetItem[];
  selectedDataset: string;
  onSelectDataset: (path: string) => void;
  // ingestion
  dlSymbol: string;
  onDlSymbolChange: (v: string) => void;
  dlTimeframe: string;
  onDlTimeframeChange: (v: string) => void;
  dlBars: number;
  onDlBarsChange: (n: number) => void;
  dlSource: string;
  onDlSourceChange: (v: string) => void;
  dlBusy: boolean;
  onDownload: () => void;
  dlResult: DatasetDownloadResponse | null;
  dlError: string;
  // feature inspection
  inspectBusy: boolean;
  onInspect: () => void;
  inspectResult: InspectFeaturesResponse | null;
  inspectError: string;
  // training
  epochs: number;
  onEpochsChange: (n: number) => void;
  learningRate: number;
  onLearningRateChange: (n: number) => void;
  trainBusy: boolean;
  trainProgress: ModelStudioTrainProgress | null;
  trainStatus: string;
  trainError: string;
  onTrain: () => void;
}

const STATUS_CLASS: Record<string, string> = {
  HEALTHY: "good",
  CLAMPED: "warn",
  WARNING: "bad",
};

export function DatasetPipelinePanel({
  dimension,
  datasets,
  selectedDataset,
  onSelectDataset,
  dlSymbol,
  onDlSymbolChange,
  dlTimeframe,
  onDlTimeframeChange,
  dlBars,
  onDlBarsChange,
  dlSource,
  onDlSourceChange,
  dlBusy,
  onDownload,
  dlResult,
  dlError,
  inspectBusy,
  onInspect,
  inspectResult,
  inspectError,
  epochs,
  onEpochsChange,
  learningRate,
  onLearningRateChange,
  trainBusy,
  trainProgress,
  trainStatus,
  trainError,
  onTrain,
}: DatasetPipelinePanelProps) {
  const t = useI18n((s) => s.t);
  const sortedDatasets = [...datasets].sort(compareDatasetsByGranularity);
  const pct =
    trainProgress && trainProgress.epochs > 0
      ? Math.min(100, Math.round((trainProgress.epoch / trainProgress.epochs) * 100))
      : 0;

  return (
    <div className="ms-grid-dual">
      {/* LEFT column: ingestion + training dispatch */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Panel
          title={t("model-studio.pipeline.ingest_title", "Historical Candle Ingestion")}
          subtitle={t(
            "model-studio.pipeline.ingest_subtitle",
            "M1/M3/M5/M15 capture — synthetic GBM, live MT5 terminal, or on-disk CSV.",
          )}
          accent
          right={
            <span className="badge neutral">
              {t("model-studio.pipeline.staged", "{n} staged", { n: datasets.length })}
            </span>
          }
        >
          <div className="ms-control-group">
            <div className="ms-form-row">
              <label htmlFor="dl-symbol">{t("model-studio.pipeline.lbl_symbol", "Instrument Symbol")}</label>
              <input
                id="dl-symbol"
                className="ms-input-styled"
                value={dlSymbol}
                onChange={(e) => onDlSymbolChange(e.target.value)}
                placeholder="XAUUSD"
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="ms-form-row">
                <label htmlFor="dl-tf">{t("model-studio.pipeline.lbl_timeframe", "Timeframe")}</label>
                <select
                  id="dl-tf"
                  className="ms-select-styled"
                  value={dlTimeframe}
                  onChange={(e) => onDlTimeframeChange(e.target.value)}
                >
                  {DOWNLOAD_TIMEFRAMES.map((tf) => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>
              <div className="ms-form-row">
                <label htmlFor="dl-src">{t("model-studio.pipeline.lbl_source", "Source")}</label>
                <select
                  id="dl-src"
                  className="ms-select-styled"
                  value={dlSource}
                  onChange={(e) => onDlSourceChange(e.target.value)}
                >
                  {DOWNLOAD_SOURCES.map((s) => (
                    <option key={s} value={s}>{s.toUpperCase()}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="ms-form-row">
              <label htmlFor="dl-bars">{t("model-studio.pipeline.lbl_bars", "Bar Count")}</label>
              <input
                id="dl-bars"
                type="number"
                min={100}
                max={500000}
                step={100}
                className="ms-input-styled"
                value={dlBars}
                onChange={(e) => onDlBarsChange(Number(e.target.value) || 0)}
              />
            </div>

            <button
              onClick={onDownload}
              disabled={dlBusy}
              className="ms-btn-action ms-btn-primary"
            >
              {dlBusy
              ? t("model-studio.pipeline.ingest_busy", "⏳ Ingesting…")
              : t("model-studio.pipeline.ingest_idle", "⬇ Ingest Dataset")}
            </button>

            {dlError && (
              <div className="ms-banner-err">
                <span>⚠</span>
                <div>
                  <strong>{t("model-studio.pipeline.ingest_err", "Ingestion Failed")}</strong>
                  <div className="tiny" style={{ marginTop: 2 }}>{dlError}</div>
                </div>
              </div>
            )}

            {dlResult && (
              <div className="ms-banner-ok">
                <div>
                  <strong>
                    {t("model-studio.pipeline.bars_ingested", "✓ {n} bars ingested", {
                      n: dlResult.rows.toLocaleString(),
                    })}
                  </strong>
                  <div className="inline-mono tiny" style={{ opacity: 0.9, marginTop: 2 }}>
                    {t(
                      "model-studio.pipeline.ingest_meta",
                      "{symbol} · {tf} · {size} · {sec}s · {thr} bars/s",
                      {
                        symbol: dlResult.symbol,
                        tf: dlResult.timeframe,
                        size: dlResult.size_display,
                        sec: dlResult.elapsed_sec.toFixed(1),
                        thr: dlResult.throughput_bars_sec.toLocaleString(),
                      },
                    )}
                  </div>
                </div>
                <span className="badge good">{dlResult.source.toUpperCase()}</span>
              </div>
            )}
          </div>
        </Panel>

        <Panel
          title={t("model-studio.pipeline.train_title", "PyTorch Training Dispatch")}
          subtitle={t(
            "model-studio.pipeline.train_subtitle",
            "Launch a ScalpNet fine-tune run against the selected dataset; progress streams live.",
          )}
          accent
          right={
            trainProgress ? (
              <span className={`badge ${trainBusy ? "warn" : "good"}`}>
                {trainBusy
                  ? t("model-studio.pipeline.badge_training", "TRAINING")
                  : t("model-studio.pipeline.badge_completed", "COMPLETED")}
              </span>
            ) : null
          }
        >
          <div className="ms-control-group">
            <div className="ms-form-row">
              <label htmlFor="train-ds">{t("model-studio.pipeline.lbl_train_ds", "Training Dataset")}</label>
              <select
                id="train-ds"
                className="ms-select-styled"
                value={selectedDataset}
                onChange={(e) => onSelectDataset(e.target.value)}
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

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="ms-form-row">
                <label htmlFor="train-epochs">{t("model-studio.pipeline.lbl_epochs", "Epochs")}</label>
                <input
                  id="train-epochs"
                  type="number"
                  min={1}
                  max={200}
                  className="ms-input-styled"
                  value={epochs}
                  onChange={(e) => onEpochsChange(Number(e.target.value) || 1)}
                />
              </div>
              <div className="ms-form-row">
                <label htmlFor="train-lr">{t("model-studio.pipeline.lbl_lr", "Learning Rate")}</label>
                <input
                  id="train-lr"
                  type="number"
                  step={0.0001}
                  min={0.00001}
                  className="ms-input-styled"
                  value={learningRate}
                  onChange={(e) => onLearningRateChange(Number(e.target.value) || 0)}
                />
              </div>
            </div>

            <button
              onClick={onTrain}
              disabled={trainBusy || !selectedDataset}
              className="ms-btn-action ms-btn-success"
            >
              {trainBusy
              ? t("model-studio.pipeline.train_busy", "⏳ Training…")
              : t("model-studio.pipeline.train_idle", "▶ Dispatch Training Run")}
            </button>

            {trainError && (
              <div className="ms-banner-err">
                <span>⚠</span>
                <div>
                  <strong>{t("model-studio.pipeline.train_err", "Training Failed")}</strong>
                  <div className="tiny" style={{ marginTop: 2 }}>{trainError}</div>
                </div>
              </div>
            )}

            {(trainBusy || trainProgress) && trainProgress && (
              <div className="ms-progress-wrap">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="tiny" style={{ color: "var(--text-dim)" }}>
                    {t("model-studio.pipeline.epoch_line", "Epoch {e} / {E}", {
                      e: trainProgress.epoch,
                      E: trainProgress.epochs,
                    })}
                    {trainProgress.stage ? ` · ${trainProgress.stage}` : ""}
                  </span>
                  <span className="inline-mono small" style={{ color: "var(--accent-strong)", fontWeight: 700 }}>
                    {pct}%
                  </span>
                </div>
                <div className="ms-progress-bar">
                  <div className="ms-progress-fill" style={{ width: `${pct}%` }} />
                </div>
                <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                  <span className="inline-mono tiny" style={{ color: "var(--green)" }}>
                    {t("model-studio.registry.loss_label", "loss")} {trainProgress.loss.toFixed(4)}
                  </span>
                  <span className="inline-mono tiny" style={{ color: "var(--amber)" }}>
                    {t("model-studio.pipeline.m_val", "val")} {trainProgress.val_loss.toFixed(4)}
                  </span>
                  <span className="inline-mono tiny" style={{ color: "var(--text-faint)" }}>
                    {t("model-studio.pipeline.m_run", "run")} <span dir="ltr">{trainProgress.run_id.substring(0, 8)}</span>
                  </span>
                </div>
                {trainStatus && (
                  <div className="inline-mono tiny" style={{ color: "var(--text-dim)", wordBreak: "break-all" }}>
                    {trainStatus}
                  </div>
                )}
              </div>
            )}
          </div>
        </Panel>
      </div>

      {/* RIGHT column: feature normalization inspector */}
      <Panel
        title={t("model-studio.pipeline.insp_title", "Feature Normalization Inspector")}
        subtitle={t(
          "model-studio.pipeline.insp_subtitle",
          "Z-score μ/σ per tensor slot; clamps, zero-variance columns, and NaN contamination.",
        )}
        accent
        right={
          inspectResult ? (
            <span className={`badge ${inspectResult.scaler_ready ? "good" : "warn"}`}>
              {inspectResult.scaler_ready
                ? t("model-studio.pipeline.badge_scaler_ready", "SCALER READY")
                : t("model-studio.pipeline.badge_not_ready", "NOT READY")}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              onClick={onInspect}
              disabled={inspectBusy || !selectedDataset}
              className="ms-btn-action ms-btn-ghost"
              style={{ color: "var(--accent-strong)" }}
            >
              {inspectBusy
              ? t("model-studio.pipeline.insp_busy", "⏳ Computing Statistics…")
              : t("model-studio.pipeline.insp_idle", "🔍 Inspect Features")}
            </button>
            <span className="tiny faint inline-mono">
              {t("model-studio.pipeline.contract_short", "{d}D contract", { d: dimension })} ·{" "}
              {selectedDataset ? (
                <span dir="ltr">{selectedDataset.split(/[/\\]/).pop()}</span>
              ) : (
                t("model-studio.pipeline.no_dataset", "no dataset")
              )}
            </span>
          </div>

          {inspectError && (
            <div className="ms-banner-err">
              <span>⚠</span>
              <div>
                <strong>{t("model-studio.pipeline.insp_err", "Inspection Failed")}</strong>
                <div className="tiny" style={{ marginTop: 2 }}>{inspectError}</div>
              </div>
            </div>
          )}

          {inspectResult && (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))",
                  gap: 8,
                  padding: 10,
                  borderRadius: 8,
                  background: "var(--bg-inset)",
                  border: "1px solid var(--border)",
                }}
              >
                {[
                  {
                    l: t("model-studio.pipeline.stat_features", "Features"),
                    v: String(inspectResult.total_features),
                    c: "var(--accent-strong)",
                  },
                  { l: t("model-studio.pipeline.stat_healthy", "Healthy"), v: String(inspectResult.healthy_features), c: "var(--green)" },
                  { l: t("model-studio.pipeline.stat_clamped", "Clamped"), v: String(inspectResult.clamped_features), c: "var(--amber)" },
                  { l: t("model-studio.pipeline.stat_nan", "NaN"), v: String(inspectResult.nan_features), c: "var(--red)" },
                  {
                    l: t("model-studio.pipeline.stat_rows", "Rows"),
                    v: inspectResult.rows_processed.toLocaleString(),
                    c: "var(--text)",
                  },
                ].map((s) => (
                  <div key={s.l}>
                    <div className="tiny faint uppercase" style={{ fontWeight: 700, letterSpacing: "0.08em" }}>
                      {s.l}
                    </div>
                    <div className="inline-mono small" style={{ color: s.c, fontWeight: 800, marginTop: 2 }}>
                      {s.v}
                    </div>
                  </div>
                ))}
              </div>

              <div className="table-wrap" style={{ maxHeight: 420 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>{t("model-studio.pipeline.th_feature", "Feature")}</th>
                      <th>{t("model-studio.pipeline.th_family", "Family")}</th>
                      <th className="num">{t("model-studio.pipeline.th_raw_min", "Raw Min")}</th>
                      <th className="num">{t("model-studio.pipeline.th_raw_max", "Raw Max")}</th>
                      <th className="num">μ</th>
                      <th className="num">σ</th>
                      <th className="num">{t("model-studio.pipeline.th_z_sample", "Z-Sample")}</th>
                      <th style={{ textAlign: "center" }}>{t("model-studio.pipeline.th_status", "Status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inspectResult.features.map((f) => (
                      <tr key={f.index}>
                        <td className="inline-mono" style={{ color: "var(--text-faint)" }}>{f.index}</td>
                        <td className="inline-mono" style={{ color: "var(--text)", fontWeight: 600 }}>{f.name}</td>
                        <td>
                          <span className={`ms-slot-family ${(f.family || "").toLowerCase()}`}>
                            {f.family}
                          </span>
                        </td>
                        <td className="num" style={{ color: "var(--text-dim)" }}>{f.raw_min.toFixed(3)}</td>
                        <td className="num" style={{ color: "var(--text-dim)" }}>{f.raw_max.toFixed(3)}</td>
                        <td className="num" style={{ color: "var(--green)" }}>{f.raw_mean.toFixed(3)}</td>
                        <td className="num" style={{ color: "var(--accent-strong)" }}>{f.raw_std.toFixed(3)}</td>
                        <td className="num" style={{ color: f.zero_variance ? "var(--red)" : "var(--text)" }}>
                          {f.normalized_sample.toFixed(3)}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <span className={`badge ${STATUS_CLASS[f.status] ?? "neutral"}`}>{f.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {!inspectResult && !inspectError && (
            <div className="tiny faint" style={{ textAlign: "center", padding: "24px 0" }}>
              {t(
                "model-studio.pipeline.empty",
                "Select a dataset and run the inspector to audit the Z-score normalization envelope.",
              )}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
