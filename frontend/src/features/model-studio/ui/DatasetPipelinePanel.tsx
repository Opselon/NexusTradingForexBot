/**
 * DatasetPipelinePanel — candle ingestion, Z-score normalization inspection,
 * and PyTorch training dispatch with progress tracking.
 *
 * Stateless presentation component; owned state lives in ModelStudioPage.
 */

import { useMemo } from "react";
import { Panel } from "@/components/primitives";
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
  // perf: dataset sort derived only when the datasets prop changes (dep: datasets).
  const sortedDatasets = useMemo(() => [...datasets].sort(compareDatasetsByGranularity), [datasets]);
  const pct =
    trainProgress && trainProgress.epochs > 0
      ? Math.min(100, Math.round((trainProgress.epoch / trainProgress.epochs) * 100))
      : 0;

  return (
    <div className="ms-grid-dual">
      {/* LEFT column: ingestion + training dispatch */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Panel
          title="Historical Candle Ingestion"
          subtitle="M1/M3/M5/M15 capture — synthetic GBM, live MT5 terminal, or on-disk CSV."
          accent
          right={<span className="badge neutral">{datasets.length} staged</span>}
        >
          <div className="ms-control-group">
            <div className="ms-form-row">
              <label htmlFor="dl-symbol">Instrument Symbol</label>
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
                <label htmlFor="dl-tf">Timeframe</label>
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
                <label htmlFor="dl-src">Source</label>
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
              <label htmlFor="dl-bars">Bar Count</label>
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
              {dlBusy ? "⏳ Ingesting…" : "⬇ Ingest Dataset"}
            </button>

            {dlError && (
              <div className="ms-banner-err">
                <span>⚠</span>
                <div>
                  <strong>Ingestion Failed</strong>
                  <div className="tiny" style={{ marginTop: 2 }}>{dlError}</div>
                </div>
              </div>
            )}

            {dlResult && (
              <div className="ms-banner-ok">
                <div>
                  <strong>✓ {dlResult.rows.toLocaleString()} bars ingested</strong>
                  <div className="inline-mono tiny" style={{ opacity: 0.9, marginTop: 2 }}>
                    {dlResult.symbol} · {dlResult.timeframe} · {dlResult.size_display} · {dlResult.elapsed_sec.toFixed(1)}s · {dlResult.throughput_bars_sec.toLocaleString()} bars/s
                  </div>
                </div>
                <span className="badge good">{dlResult.source.toUpperCase()}</span>
              </div>
            )}
          </div>
        </Panel>

        <Panel
          title="PyTorch Training Dispatch"
          subtitle="Launch a ScalpNet fine-tune run against the selected dataset; progress streams live."
          accent
          right={
            trainProgress ? (
              <span className={`badge ${trainBusy ? "warn" : "good"}`}>
                {trainBusy ? "TRAINING" : "COMPLETED"}
              </span>
            ) : null
          }
        >
          <div className="ms-control-group">
            <div className="ms-form-row">
              <label htmlFor="train-ds">Training Dataset</label>
              <select
                id="train-ds"
                className="ms-select-styled"
                value={selectedDataset}
                onChange={(e) => onSelectDataset(e.target.value)}
              >
                {sortedDatasets.length === 0 && <option value="">No staged datasets</option>}
                {sortedDatasets.map((d) => (
                  <option key={d.path} value={d.path}>
                    {d.name} ({d.size_display})
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="ms-form-row">
                <label htmlFor="train-epochs">Epochs</label>
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
                <label htmlFor="train-lr">Learning Rate</label>
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
              {trainBusy ? "⏳ Training…" : "▶ Dispatch Training Run"}
            </button>

            {trainError && (
              <div className="ms-banner-err">
                <span>⚠</span>
                <div>
                  <strong>Training Failed</strong>
                  <div className="tiny" style={{ marginTop: 2 }}>{trainError}</div>
                </div>
              </div>
            )}

            {(trainBusy || trainProgress) && trainProgress && (
              <div className="ms-progress-wrap">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="tiny tx-dim" >
                    Epoch {trainProgress.epoch} / {trainProgress.epochs}
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
                  <span className="inline-mono tiny tx-good" >
                    loss {trainProgress.loss.toFixed(4)}
                  </span>
                  <span className="inline-mono tiny tx-warn" >
                    val {trainProgress.val_loss.toFixed(4)}
                  </span>
                  <span className="inline-mono tiny tx-faint" >
                    run {trainProgress.run_id.substring(0, 8)}
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
        title="Feature Normalization Inspector"
        subtitle="Z-score μ/σ per tensor slot; clamps, zero-variance columns, and NaN contamination."
        accent
        right={
          inspectResult ? (
            <span className={`badge ${inspectResult.scaler_ready ? "good" : "warn"}`}>
              {inspectResult.scaler_ready ? "SCALER READY" : "NOT READY"}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              onClick={onInspect}
              disabled={inspectBusy || !selectedDataset}
              className="ms-btn-action ms-btn-ghost tx-accent"
              
            >
              {inspectBusy ? "⏳ Computing Statistics…" : "🔍 Inspect Features"}
            </button>
            <span className="tiny faint inline-mono">
              {dimension}D contract · {selectedDataset ? selectedDataset.split(/[/\\]/).pop() : "no dataset"}
            </span>
          </div>

          {inspectError && (
            <div className="ms-banner-err">
              <span>⚠</span>
              <div>
                <strong>Inspection Failed</strong>
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
                  { l: "Features", v: String(inspectResult.total_features), c: "var(--accent-strong)" },
                  { l: "Healthy", v: String(inspectResult.healthy_features), c: "var(--green)" },
                  { l: "Clamped", v: String(inspectResult.clamped_features), c: "var(--amber)" },
                  { l: "NaN", v: String(inspectResult.nan_features), c: "var(--red)" },
                  { l: "Rows", v: inspectResult.rows_processed.toLocaleString(), c: "var(--text)" },
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

              <div tabIndex={0} className="table-wrap" style={{ maxHeight: 420 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">#</th>
                      <th scope="col">Feature</th>
                      <th scope="col">Family</th>
                      <th scope="col" className="num">Raw Min</th>
                      <th scope="col" className="num">Raw Max</th>
                      <th scope="col" className="num">μ</th>
                      <th scope="col" className="num">σ</th>
                      <th scope="col" className="num">Z-Sample</th>
                      <th scope="col" style={{ textAlign: "center" }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inspectResult.features.map((f) => (
                      <tr key={f.index}>
                        <td className="inline-mono tx-faint" >{f.index}</td>
                        <td className="inline-mono" style={{ color: "var(--text)", fontWeight: 600 }}>{f.name}</td>
                        <td>
                          <span className={`ms-slot-family ${(f.family || "").toLowerCase()}`}>
                            {f.family}
                          </span>
                        </td>
                        <td className="num tx-dim" >{f.raw_min.toFixed(3)}</td>
                        <td className="num tx-dim" >{f.raw_max.toFixed(3)}</td>
                        <td className="num tx-good" >{f.raw_mean.toFixed(3)}</td>
                        <td className="num tx-accent" >{f.raw_std.toFixed(3)}</td>
                        <td className="num" >
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
              Select a dataset and run the inspector to audit the Z-score normalization envelope.
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
