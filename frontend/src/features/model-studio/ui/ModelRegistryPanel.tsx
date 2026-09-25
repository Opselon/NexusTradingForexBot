import { useMemo } from "react";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type {
  ActiveModelResponse,
  HotLoadResponse,
  InspectScalerResponse,
  ModelRecordDto,
  VerifyModelResponse,
} from "../model";

/**
 * Split a server-reported path on BOTH separators so a Windows root
 * (`C:\...\scaler.json`) yields just the filename, not a truncated tail.
 */
function basename(p: string): string {
  return p.split(/[/\\]/).pop() ?? p;
}

interface StatCellProps {
  label: string;
  value: string;
  color?: string;
  mono?: boolean;
}

/** One KPI cell: min-width:0 so grid children can actually truncate. */
function StatCell({ label, value, color, mono }: StatCellProps) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="tiny faint uppercase font-bold">{label}</div>
      <div
        className={`small mt-1 font-bold ${mono ? "inline-mono" : ""}`}
        title={value}
        style={{
          color: color ?? "var(--text)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </div>
    </div>
  );
}

interface ModelRegistryPanelProps {
  models: ModelRecordDto[];
  activeModel: ActiveModelResponse["active_model"];
  selectedModelId: string;
  onSelectModelId: (id: string) => void;
  enableFineTune: boolean;
  onToggleFineTune: (enabled: boolean) => void;
  attachScaler: boolean;
  onToggleAttachScaler: (attach: boolean) => void;
  hotLoadBusy: boolean;
  onHotLoad: () => void;
  hotLoadResult: HotLoadResponse | null;
  hotLoadError: string;
  verifyBusy: boolean;
  onVerify: () => void;
  verifyResult: VerifyModelResponse | null;
  rollbackBusy: boolean;
  onRollback: () => void;
  scalerResult: InspectScalerResponse | null;
  onInspectScaler: () => void;
}

export function ModelRegistryPanel({
  models,
  activeModel,
  selectedModelId,
  onSelectModelId,
  enableFineTune,
  onToggleFineTune,
  attachScaler,
  onToggleAttachScaler,
  hotLoadBusy,
  onHotLoad,
  hotLoadResult,
  hotLoadError,
  verifyBusy,
  onVerify,
  verifyResult,
  rollbackBusy,
  onRollback,
  scalerResult,
  onInspectScaler,
}: ModelRegistryPanelProps) {
  const t = useI18n((s) => s.t);
  // One derivation: the catalog's model option labels (accessor chains over
  // the models array). Memo deps are exactly that array, so an unrelated
  // parent re-render (slider drag, hot-load busy flip) never rebuilds it.
  const options = useMemo(
    () =>
      models.map((m) => ({
        id: m.id,
        label: `${m.is_active ? "★ " : ""}${m.id} [${m.dimension}D] ${m.fine_tune_enabled ? "[FT:ON]" : "[FT:OFF]"} (loss: ${m.final_loss?.toFixed(4) || "0.0000"})`,
      })),
    [models],
  );
  return (
    <Panel
      title="AI HUB: MODEL REGISTRY & RUNTIME HOT-LOADER"
      subtitle="Dynamically swap weights and calibrated scaler sidecars into engine memory without restarting the process."
      accent
      right={
        <span className="badge good">
          {models.length} Checkpoints Cataloged
        </span>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Active Model Snapshot Card */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(0, 1fr))",
            gap: 8,
            padding: 12,
            borderRadius: 8,
            background: "var(--bg-inset)",
            border: "1px solid var(--border)",
          }}
        >
          <StatCell
            label="Active Checkpoint"
            value={activeModel?.model_id || "—"}
          />
          <StatCell
            label="Tensor Dimension"
            value={activeModel ? `${activeModel.dimension}D` : "—"}
            color="var(--accent-strong)"
          />
          <StatCell
            label="Weights Hash"
            value={activeModel?.weights_sha256 ? activeModel.weights_sha256.substring(0, 14) : "—"}
            color="var(--green)"
            mono
          />
          <StatCell
            label="Scaler Sidecar"
            value={activeModel?.scaler_path ? basename(activeModel.scaler_path) : "Default (Unit)"}
            color="var(--accent-strong)"
            mono
          />
          <StatCell
            label="Lifecycle Stage"
            value={activeModel?.stage || "—"}
          />
          <StatCell
            label="Runtime Inferences"
            value={(activeModel?.inference_count ?? 0).toLocaleString()}
            color="var(--green)"
          />
        </div>

        {/* Checkpoint Selection & Controls */}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1.2fr) minmax(140px, auto)", gap: 12, alignItems: "flex-end" }}>
          <div className="ms-form-row">
            <label htmlFor="model-checkpoint-select">
              Select Model Checkpoint (SQLite Registry / On-Disk Artifacts)
            </label>
            <select
              id="model-checkpoint-select"
              value={selectedModelId}
              onChange={(e) => onSelectModelId(e.target.value)}
              className="ms-select-styled"
            >
              {models.length === 0 && <option value="">{t("model-studio.registry.no_models", "No registered models found")}</option>}
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label className="ms-checkbox-row" style={{ padding: "6px 10px" }}>
              <span className="ms-checkbox-label" style={{ fontSize: 11 }}>
                <span>🧬</span> Enable Fine-Tune Mode
              </span>
              <input
                type="checkbox"
                checked={enableFineTune}
                onChange={(e) => onToggleFineTune(e.target.checked)}
              />
            </label>
            <label className="ms-checkbox-row" style={{ padding: "6px 10px" }}>
              <span className="ms-checkbox-label" style={{ fontSize: 11 }}>
                <span>⚖️</span> Auto-Load Scaler Sidecar
              </span>
              <input
                type="checkbox"
                checked={attachScaler}
                onChange={(e) => onToggleAttachScaler(e.target.checked)}
              />
            </label>
          </div>

          <div>
            <button
              onClick={onHotLoad}
              disabled={hotLoadBusy || !selectedModelId}
              className="ms-btn-action ms-btn-success"
              style={{ padding: "10px 20px", width: "100%", whiteSpace: "nowrap" }}
            >
              {hotLoadBusy ? "⏳ Hot-Loading…" : "⚡ Hot-Load Model"}
            </button>
          </div>
        </div>

        {/* Action Toolbar */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, paddingTop: 4 }}>
          <button
            onClick={onVerify}
            disabled={verifyBusy || !selectedModelId}
            className="ms-btn-action ms-btn-ghost"
          >
            {verifyBusy ? "⏳ Verifying…" : "✓ Run Integrity Battery"}
          </button>
          <button
            onClick={onRollback}
            disabled={rollbackBusy}
            className="ms-btn-action ms-btn-ghost tx-warn"
            
          >
            {rollbackBusy ? "⏳ Rolling back…" : "↺ Rollback to Previous Champion"}
          </button>
          <button
            onClick={onInspectScaler}
            disabled={!selectedModelId}
            className="ms-btn-action ms-btn-ghost tx-accent"
            
          >
            📊 Inspect Scaler Vectors
          </button>
        </div>

        {/* Hot-Load Feedback Banners */}
        {hotLoadResult && (
          <div className="ms-banner-ok">
            <div style={{ minWidth: 0 }}>
              <strong>✓ Model {hotLoadResult.model_id} ({hotLoadResult.dimension}D) Successfully Hot-Loaded</strong>
              <div className="inline-mono tiny" style={{ opacity: 0.9, marginTop: 2, wordBreak: "break-all" }}>
                SHA256: {hotLoadResult.weights_sha256.substring(0, 16)}… • Scaler: {hotLoadResult.scaler_attached ? "Attached" : "Unit"} • Warmup: {hotLoadResult.warmup_latency_us} µs
              </div>
            </div>
            <span className="badge good">{hotLoadResult.stage}</span>
          </div>
        )}

        {hotLoadError && (
          <div className="ms-banner-err">
            <span>⚠</span>
            <div>
              <strong>{t("model-studio.registry.hotload_err", "Hot-Load Operation Failed")}</strong>
              <div className="tiny" style={{ marginTop: 2 }}>{hotLoadError}</div>
            </div>
          </div>
        )}

        {/* Pre-Load Integrity Verification Table */}
        {verifyResult && (
          <div style={{ padding: 12, borderRadius: 8, background: "var(--bg-inset)", border: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span className="small font-bold tx-accent" >
                Pre-Load Checkpoint Verification Results ({verifyResult.model_id})
              </span>
              <span className={`badge ${verifyResult.all_passed ? "good" : "warn"}`}>
                {verifyResult.all_passed ? "ALL PASSED" : "WARNINGS DETECTED"}
              </span>
            </div>
            <div tabIndex={0} className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">{t("model-studio.verdict.check_name", "Check Name")}</th>
                    <th scope="col" style={{ textAlign: "center" }}>Verdict</th>
                    <th scope="col">{t("model-studio.registry.th_diag", "Diagnostic Detail")}</th>
                  </tr>
                </thead>
                <tbody>
                  {verifyResult.checks.map((c, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{c.name}</td>
                      <td style={{ textAlign: "center" }}>
                        <span className={`badge ${c.passed ? "good" : "bad"}`}>
                          {c.passed ? "PASS" : "FAIL"}
                        </span>
                      </td>
                      <td className="tx-dim" >{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Scaler Vectors Table */}
        {scalerResult && (
          <div style={{ padding: 12, borderRadius: 8, background: "var(--bg-inset)", border: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span className="small font-bold tx-accent" >
                Attached Scaler Normalization Vectors ({scalerResult.dimension}D)
              </span>
              <span className="badge neutral">{scalerResult.features_count} Features Calibrated</span>
            </div>
            {scalerResult.features.length === 0 ? (
              <div className="tiny faint" style={{ textAlign: "center", padding: "12px 0" }}>
                {scalerResult.message || "No scaler vectors cataloged."}
              </div>
            ) : (
              <div tabIndex={0} className="table-wrap" style={{ maxHeight: 220 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col"># Index</th>
                      <th scope="col" className="num">Mean (μ)</th>
                      <th scope="col" className="num">Std Dev (σ)</th>
                      <th scope="col" style={{ textAlign: "center" }}>Clamping</th>
                      <th scope="col" style={{ textAlign: "center" }}>{t("model-studio.registry.th_zero_var", "Zero Variance")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scalerResult.features.map((f) => (
                      <tr key={f.index}>
                        <td className="inline-mono tx-faint" >feat_{f.index}</td>
                        <td className="num tx-good" >{f.mean.toFixed(4)}</td>
                        <td className="num tx-accent" >{f.std.toFixed(4)}</td>
                        <td style={{ textAlign: "center", color: "var(--text-dim)" }}>[{f.clamp_min}, {f.clamp_max}]</td>
                        <td style={{ textAlign: "center" }}>
                          <span className={`badge ${f.zero_variance ? "warn" : "good"}`}>
                            {f.zero_variance ? "YES" : "NO"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}
