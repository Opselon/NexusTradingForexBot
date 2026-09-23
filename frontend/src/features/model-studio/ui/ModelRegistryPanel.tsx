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
  return (
    <Panel
      title={t("model-studio.registry.title", "AI HUB: MODEL REGISTRY & RUNTIME HOT-LOADER")}
      subtitle={t(
        "model-studio.registry.subtitle",
        "Dynamically swap weights and calibrated scaler sidecars into engine memory without restarting the process.",
      )}
      accent
      right={
        <span className="badge good">
          {t("model-studio.registry.checkpoints", "{n} Checkpoints Cataloged", { n: models.length })}
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
            label={t("model-studio.registry.active_checkpoint", "Active Checkpoint")}
            value={activeModel?.model_id || "—"}
          />
          <StatCell
            label={t("model-studio.registry.tensor_dimension", "Tensor Dimension")}
            value={activeModel ? `${activeModel.dimension}D` : "—"}
            color="var(--accent-strong)"
          />
          <StatCell
            label={t("model-studio.registry.weights_hash", "Weights Hash")}
            value={activeModel?.weights_sha256 ? activeModel.weights_sha256.substring(0, 14) : "—"}
            color="var(--green)"
            mono
          />
          <StatCell
            label={t("model-studio.registry.scaler_sidecar", "Scaler Sidecar")}
            value={
              activeModel?.scaler_path
                ? basename(activeModel.scaler_path)
                : t("model-studio.registry.default_unit", "Default (Unit)")
            }
            color="var(--accent-strong)"
            mono
          />
          <StatCell
            label={t("model-studio.registry.lifecycle_stage", "Lifecycle Stage")}
            value={activeModel?.stage || "—"}
          />
          <StatCell
            label={t("model-studio.registry.runtime_inferences", "Runtime Inferences")}
            value={(activeModel?.inference_count ?? 0).toLocaleString()}
            color="var(--green)"
          />
        </div>

        {/* Checkpoint Selection & Controls */}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1.2fr) minmax(140px, auto)", gap: 12, alignItems: "flex-end" }}>
          <div className="ms-form-row">
            <label htmlFor="model-checkpoint-select">
              {t(
                "model-studio.registry.select_label",
                "Select Model Checkpoint (SQLite Registry / On-Disk Artifacts)",
              )}
            </label>
            <select
              id="model-checkpoint-select"
              value={selectedModelId}
              onChange={(e) => onSelectModelId(e.target.value)}
              className="ms-select-styled"
            >
              {models.length === 0 && (
                <option value="">{t("model-studio.registry.no_models", "No registered models found")}</option>
              )}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.is_active ? "★ " : ""}{m.id} [{m.dimension}D] {m.fine_tune_enabled ? "[FT:ON]" : "[FT:OFF]"} ({t("model-studio.registry.loss_label", "loss")}: {m.final_loss?.toFixed(4) || "0.0000"})
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label className="ms-checkbox-row" style={{ padding: "6px 10px" }}>
              <span className="ms-checkbox-label" style={{ fontSize: 11 }}>
                <span>🧬</span>{" "}
                {t("model-studio.registry.enable_ft", "Enable Fine-Tune Mode")}
              </span>
              <input
                type="checkbox"
                checked={enableFineTune}
                onChange={(e) => onToggleFineTune(e.target.checked)}
              />
            </label>
            <label className="ms-checkbox-row" style={{ padding: "6px 10px" }}>
              <span className="ms-checkbox-label" style={{ fontSize: 11 }}>
                <span>⚖️</span>{" "}
                {t("model-studio.registry.auto_scaler", "Auto-Load Scaler Sidecar")}
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
              {hotLoadBusy
                ? t("model-studio.registry.hotload_busy", "⏳ Hot-Loading…")
                : t("model-studio.registry.hotload_idle", "⚡ Hot-Load Model")}
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
            {verifyBusy
              ? t("model-studio.registry.verify_busy", "⏳ Verifying…")
              : t("model-studio.registry.verify_idle", "✓ Run Integrity Battery")}
          </button>
          <button
            onClick={onRollback}
            disabled={rollbackBusy}
            className="ms-btn-action ms-btn-ghost tx-warn"
            
          >
            {rollbackBusy
              ? t("model-studio.registry.rollback_busy", "⏳ Rolling back…")
              : t("model-studio.registry.rollback_idle", "↺ Rollback to Previous Champion")}
          </button>
          <button
            onClick={onInspectScaler}
            disabled={!selectedModelId}
            className="ms-btn-action ms-btn-ghost tx-accent"
            
          >
            {t("model-studio.registry.inspect_scaler", "📊 Inspect Scaler Vectors")}
          </button>
        </div>

        {/* Hot-Load Feedback Banners */}
        {hotLoadResult && (
          <div className="ms-banner-ok">
            <div style={{ minWidth: 0 }}>
              <strong>
                {t("model-studio.registry.hotload_ok", "✓ Model {id} ({d}D) Successfully Hot-Loaded", {
                  id: hotLoadResult.model_id,
                  d: hotLoadResult.dimension,
                })}
              </strong>
              <div className="inline-mono tiny" style={{ opacity: 0.9, marginTop: 2, wordBreak: "break-all" }}>
                {t(
                  "model-studio.registry.hotload_meta",
                  "SHA256: {h}… • Scaler: {s} • Warmup: {w} µs",
                  {
                    h: hotLoadResult.weights_sha256.substring(0, 16),
                    s: hotLoadResult.scaler_attached
                      ? t("model-studio.registry.attached", "Attached")
                      : t("model-studio.registry.unit", "Unit"),
                    w: hotLoadResult.warmup_latency_us,
                  },
                )}
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
                {t("model-studio.registry.verify_title", "Pre-Load Checkpoint Verification Results ({id})", {
                  id: verifyResult.model_id,
                })}
              </span>
              <span className={`badge ${verifyResult.all_passed ? "good" : "warn"}`}>
                {verifyResult.all_passed
                  ? t("model-studio.verdict.all_passed", "ALL PASSED")
                  : t("model-studio.verdict.warnings_detected", "WARNINGS DETECTED")}
              </span>
            </div>
            <div tabIndex={0} className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">{t("model-studio.verdict.check_name", "Check Name")}</th>
                    <th scope="col" style={{ textAlign: "center" }}>
                      {t("model-studio.verdict.verdict", "Verdict")}
                    </th>
                    <th scope="col">{t("model-studio.verdict.detail", "Diagnostic Detail")}</th>
                  </tr>
                </thead>
                <tbody>
                  {verifyResult.checks.map((c, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{c.name}</td>
                      <td style={{ textAlign: "center" }}>
                        <span className={`badge ${c.passed ? "good" : "bad"}`}>
                          {c.passed
                          ? t("model-studio.verdict.pass", "PASS")
                          : t("model-studio.verdict.fail", "FAIL")}
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
                {t("model-studio.registry.scaler_title", "Attached Scaler Normalization Vectors ({d}D)", {
                  d: scalerResult.dimension,
                })}
              </span>
              <span className="badge neutral">
                {t("model-studio.registry.features_calibrated", "{n} Features Calibrated", {
                  n: scalerResult.features_count,
                })}
              </span>
            </div>
            {scalerResult.features.length === 0 ? (
              <div className="tiny faint" style={{ textAlign: "center", padding: "12px 0" }}>
                {scalerResult.message || t("model-studio.registry.no_scaler", "No scaler vectors cataloged.")}
              </div>
            ) : (
              <div tabIndex={0} className="table-wrap" style={{ maxHeight: 220 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">{t("model-studio.registry.th_index", "# Index")}</th>
                      <th scope="col" className="num">{t("model-studio.registry.th_mean", "Mean (μ)")}</th>
                      <th scope="col" className="num">{t("model-studio.registry.th_std", "Std Dev (σ)")}</th>
                      <th scope="col" style={{ textAlign: "center" }}>
                        {t("model-studio.registry.th_clamping", "Clamping")}
                      </th>
                      <th scope="col" style={{ textAlign: "center" }}>
                        {t("model-studio.registry.th_zero_var", "Zero Variance")}
                      </th>
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
                            {f.zero_variance
                            ? t("model-studio.registry.yes", "YES")
                            : t("model-studio.registry.no", "NO")}
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
