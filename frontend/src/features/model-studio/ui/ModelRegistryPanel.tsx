import { useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { modelStudioApi } from "../api";
import type {
  ActiveModelResponse,
  HotLoadResponse,
  InspectScalerResponse,
  LoadContractVerifyResponse,
  ModelCertificateDto,
  ModelRecordDto,
  QualityReportsResponse,
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

  const [certifyBusy, setCertifyBusy] = useState(false);
  const [certificateResult, setCertificateResult] = useState<ModelCertificateDto | null>(null);
  const [certificateError, setCertificateError] = useState("");

  const [qualityBusy, setQualityBusy] = useState(false);
  const [qualityResult, setQualityResult] = useState<QualityReportsResponse | null>(null);
  const [qualityError, setQualityError] = useState("");

  const [contractBusy, setContractBusy] = useState(false);
  const [contractResult, setContractResult] = useState<LoadContractVerifyResponse | null>(null);
  const [contractError, setContractError] = useState("");

  useEffect(() => {
    setCertificateResult(null);
    setCertificateError("");
    setQualityResult(null);
    setQualityError("");
    setContractResult(null);
    setContractError("");
    if (selectedModelId) {
      modelStudioApi
        .modelCertificate(selectedModelId)
        .then((res) => {
          if (res?.certificate) setCertificateResult(res.certificate);
        })
        .catch(() => {
          /* uncertified or pending */
        });
    }
  }, [selectedModelId]);

  const handleCertify = async () => {
    if (!selectedModelId) return;
    setCertifyBusy(true);
    setCertificateError("");
    try {
      const res = await modelStudioApi.certifyModel(selectedModelId);
      setCertificateResult(res.certificate);
    } catch (err) {
      setCertificateError(err instanceof Error ? err.message : String(err));
      setCertificateResult(null);
    } finally {
      setCertifyBusy(false);
    }
  };

  const handleInspectQuality = async () => {
    if (!selectedModelId) return;
    setQualityBusy(true);
    setQualityError("");
    try {
      const res = await modelStudioApi.qualityReports(selectedModelId);
      setQualityResult(res);
    } catch (err) {
      setQualityError(err instanceof Error ? err.message : String(err));
      setQualityResult(null);
    } finally {
      setQualityBusy(false);
    }
  };

  const handleVerifyContract = async () => {
    if (!selectedModelId) return;
    setContractBusy(true);
    setContractError("");
    try {
      const res = await modelStudioApi.verifyLoadContract({ model_id: selectedModelId });
      setContractResult(res);
    } catch (err) {
      setContractError(err instanceof Error ? err.message : String(err));
      setContractResult(null);
    } finally {
      setContractBusy(false);
    }
  };

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
            onClick={handleCertify}
            disabled={certifyBusy || !selectedModelId}
            className="ms-btn-action ms-btn-ghost tx-accent"
          >
            {certifyBusy ? "⏳ Certifying…" : "🏆 Certify Model (10 Gates)"}
          </button>
          <button
            onClick={handleInspectQuality}
            disabled={qualityBusy || !selectedModelId}
            className="ms-btn-action ms-btn-ghost"
          >
            {qualityBusy ? "⏳ Loading…" : "📋 Quality Reports"}
          </button>
          <button
            onClick={handleVerifyContract}
            disabled={contractBusy || !selectedModelId}
            className="ms-btn-action ms-btn-ghost tx-good"
          >
            {contractBusy ? "⏳ Checking…" : "🔒 Verify Load Contract"}
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

        {/* Model Certificate Banner & Gates */}
        {certificateError && (
          <div className="ms-banner-err">
            <span>⚠</span>
            <div>
              <strong>Certification Check Failed</strong>
              <div className="tiny" style={{ marginTop: 2 }}>{certificateError}</div>
            </div>
          </div>
        )}

        {certificateResult && (
          <div style={{ padding: 12, borderRadius: 8, background: "var(--bg-inset)", border: `1px solid ${certificateResult.certified ? "var(--green)" : "var(--warn)"}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
              <div>
                <span className="small font-bold" style={{ color: certificateResult.certified ? "var(--green)" : "var(--warn)" }}>
                  {certificateResult.certified ? "🏆 MODEL CERTIFIED" : "⛔ MODEL CERTIFICATION REJECTED"}
                </span>
                <span className="tiny inline-mono tx-dim" style={{ marginLeft: 8 }}>
                  v: {certificateResult.certification_version} • {certificateResult.schema_id} ({certificateResult.dimension}D) • L={certificateResult.sequence_length}
                </span>
              </div>
              <span className={`badge ${certificateResult.certified ? "good" : "bad"}`}>
                {certificateResult.model_status}
              </span>
            </div>

            {certificateResult.rejection_reason && (
              <div className="tiny" style={{ color: "var(--warn)", marginBottom: 8, background: "rgba(255, 100, 100, 0.1)", padding: "6px 10px", borderRadius: 4 }}>
                <strong>Rejection Reason:</strong> {certificateResult.rejection_reason}
              </div>
            )}

            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
              <span className="tiny font-bold uppercase faint" style={{ alignSelf: "center", marginRight: 4 }}>Stages:</span>
              {certificateResult.passed_stages.map((st) => (
                <span key={st} className="badge good" style={{ fontSize: 10 }}>✓ {st}</span>
              ))}
              {certificateResult.failed_stages.map((st) => (
                <span key={st} className="badge bad" style={{ fontSize: 10 }}>✗ {st}</span>
              ))}
            </div>

            {certificateResult.gates && certificateResult.gates.length > 0 && (
              <div tabIndex={0} className="table-wrap" style={{ maxHeight: 200 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Gate</th>
                      <th scope="col" style={{ textAlign: "center" }}>Verdict</th>
                      <th scope="col">Evaluation Summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {certificateResult.gates.map((g, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 600, color: "var(--text)" }}>{g.gate}</td>
                        <td style={{ textAlign: "center" }}>
                          <span className={`badge ${g.passed ? "good" : "bad"}`}>
                            {g.passed ? "PASS" : "FAIL"}
                          </span>
                        </td>
                        <td className="tx-dim">{g.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Quality Reports Section */}
        {qualityError && (
          <div className="ms-banner-err">
            <span>⚠</span>
            <div>
              <strong>Failed to Load Quality Reports</strong>
              <div className="tiny" style={{ marginTop: 2 }}>{qualityError}</div>
            </div>
          </div>
        )}

        {qualityResult && (
          <div style={{ padding: 12, borderRadius: 8, background: "var(--bg-inset)", border: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="small font-bold tx-accent">
              📊 Certified Pipeline Quality Reports ({qualityResult.model_id})
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
              {/* Dataset Quality */}
              {qualityResult.dataset_quality ? (
                <div style={{ padding: 8, background: "var(--bg)", borderRadius: 6, border: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span className="tiny font-bold uppercase">1. Dataset Quality</span>
                    <span className={`badge ${qualityResult.dataset_quality.quality_status === "PASS" ? "good" : "bad"}`}>
                      {qualityResult.dataset_quality.quality_status}
                    </span>
                  </div>
                  <div className="tiny tx-dim">Valid: {qualityResult.dataset_quality.valid_rows.toLocaleString()} / {qualityResult.dataset_quality.raw_rows.toLocaleString()} rows</div>
                  <div className="tiny tx-dim">Rejected: {qualityResult.dataset_quality.rejected_rows} • Gaps: {qualityResult.dataset_quality.gap_events}</div>
                  <div className="tiny tx-dim">Outliers: {qualityResult.dataset_quality.outlier_candidates}</div>
                  <div className="tiny inline-mono tx-faint" style={{ marginTop: 4, wordBreak: "break-all" }}>FP: {qualityResult.dataset_quality.fingerprint?.substring(0, 16)}…</div>
                </div>
              ) : (
                <div className="tiny faint" style={{ padding: 8 }}>Dataset quality report not attached.</div>
              )}

              {/* Feature Quality */}
              {qualityResult.feature_quality ? (
                <div style={{ padding: 8, background: "var(--bg)", borderRadius: 6, border: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span className="tiny font-bold uppercase">2. Feature Quality</span>
                    <span className={`badge ${qualityResult.feature_quality.quality_status === "PASS" ? "good" : "bad"}`}>
                      {qualityResult.feature_quality.quality_status}
                    </span>
                  </div>
                  <div className="tiny tx-dim">Schema: {qualityResult.feature_quality.schema_id} ({qualityResult.feature_quality.dimension}D)</div>
                  <div className="tiny tx-dim">Passed: {qualityResult.feature_quality.passed} • Warned: {qualityResult.feature_quality.warned} • Failed: {qualityResult.feature_quality.failed}</div>
                  {qualityResult.feature_quality.constant_features.length > 0 && (
                    <div className="tiny tx-warn">Constants: {qualityResult.feature_quality.constant_features.join(", ")}</div>
                  )}
                  {qualityResult.feature_quality.nan_features.length > 0 && (
                    <div className="tiny tx-warn">NaNs: {qualityResult.feature_quality.nan_features.join(", ")}</div>
                  )}
                </div>
              ) : (
                <div className="tiny faint" style={{ padding: 8 }}>Feature quality report not attached.</div>
              )}

              {/* Label Quality */}
              {qualityResult.label_quality ? (
                <div style={{ padding: 8, background: "var(--bg)", borderRadius: 6, border: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span className="tiny font-bold uppercase">3. Label Quality</span>
                    <span className={`badge ${qualityResult.label_quality.quality_status === "PASS" ? "good" : "bad"}`}>
                      {qualityResult.label_quality.quality_status}
                    </span>
                  </div>
                  <div className="tiny tx-dim">Density: {(qualityResult.label_quality.label_density * 100).toFixed(1)}% ({qualityResult.label_quality.labeled_rows.toLocaleString()} bars)</div>
                  <div className="tiny tx-dim">NO_TRADE: {(qualityResult.label_quality.no_trade_percentage * 100).toFixed(1)}%</div>
                  <div className="tiny inline-mono tx-faint">
                    {Object.entries(qualityResult.label_quality.class_distribution || {}).map(([k, v]) => `${k}:${v}`).join(" | ")}
                  </div>
                  {qualityResult.label_quality.collapsed_classes.length > 0 && (
                    <div className="tiny tx-warn">Collapsed: {qualityResult.label_quality.collapsed_classes.join("; ")}</div>
                  )}
                </div>
              ) : (
                <div className="tiny faint" style={{ padding: 8 }}>Label quality report not attached.</div>
              )}
            </div>
          </div>
        )}

        {/* Load Contract Verification Section */}
        {contractError && (
          <div className="ms-banner-err">
            <span>⚠</span>
            <div>
              <strong>Load Contract Check Refused</strong>
              <div className="tiny" style={{ marginTop: 2 }}>{contractError}</div>
            </div>
          </div>
        )}

        {contractResult && (
          <div className={contractResult.verified ? "ms-banner-ok" : "ms-banner-err"}>
            <div>
              <strong>{contractResult.verified ? "✓ Load Contract Compatible" : "⛔ Load Contract Incompatible"}</strong>
              <div className="tiny" style={{ marginTop: 2 }}>{contractResult.detail}</div>
              <div className="tiny inline-mono tx-faint" style={{ marginTop: 2 }}>
                Schema: {contractResult.schema_id} • Dim: {contractResult.dimension}D • L={contractResult.sequence_length} • Status: {contractResult.model_status}
              </div>
            </div>
            <span className={`badge ${contractResult.verified ? "good" : "bad"}`}>
              {contractResult.verified ? "COMPATIBLE" : "REFUSED"}
            </span>
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
