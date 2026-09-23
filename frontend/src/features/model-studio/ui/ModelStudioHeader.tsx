import { useI18n } from "@/stores/i18nStore";
import type { ActiveModelResponse, ModelStudioOverviewDto } from "../model";

interface ModelStudioHeaderProps {
  dimension: number;
  onDimensionChange: (dim: number) => void;
  overview: ModelStudioOverviewDto | null;
  activeModel: ActiveModelResponse["active_model"];
  onRefresh: () => void;
}

export function ModelStudioHeader({
  dimension,
  onDimensionChange,
  overview,
  activeModel,
  onRefresh,
}: ModelStudioHeaderProps) {
  const t = useI18n((s) => s.t);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Studio Banner */}
      <div className="ms-header">
        <div className="ms-header-title-group">
          <div className="ms-brain-icon">🧠</div>
          <div className="ms-title-wrap">
            <h1>
              {t("model-studio.header.title", "NEURAL MODEL STUDIO")}
              <span className="ms-dim-badge">
                {t("model-studio.header.dim_badge", "{d}D TENSOR", { d: dimension })}
              </span>
            </h1>
            <div className="ms-title-subtitle">
              {t(
                "model-studio.header.subtitle",
                "Interactive deep learning inference, gradient saliency explainability, 70D live tensor assembly, and PyTorch training dispatch.",
              )}
            </div>
          </div>
        </div>

        <div className="ms-header-actions">
          <div className="ms-dim-segmented">
            <button
              onClick={() => onDimensionChange(50)}
              className={`ms-dim-btn ${dimension === 50 ? "active" : ""}`}
            >
              50D (scalp_v1)
            </button>
            <button
              onClick={() => onDimensionChange(70)}
              className={`ms-dim-btn ${dimension === 70 ? "active" : ""}`}
            >
              70D (scalp_v3)
            </button>
          </div>

          <button
            onClick={onRefresh}
            className="ms-btn-action ms-btn-ghost"
            title={t(
              "model-studio.header.refresh_title",
              "Refresh active model, registry, and overview metrics",
            )}
          >
            ↻ {t("common.refresh", "Refresh")}
          </button>
        </div>
      </div>

      {/* Champion Runtime Telemetry Ribbon */}
      <div className="ms-champion-banner">
        <div className="ms-champ-left" style={{ minWidth: 0 }}>
          <span className="ms-live-pulse-dot" />
          <div style={{ minWidth: 0 }}>
            <span className="tiny faint uppercase" style={{ display: "block", letterSpacing: "0.1em" }}>
              {t("model-studio.header.active_champion", "Active Runtime Champion")}
            </span>
            <span
              className="ms-champ-name"
              title={activeModel?.model_id}
              style={{ display: "block", maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {activeModel?.model_id
                ? activeModel.model_id
                : t("model-studio.header.no_champion", "NO ACTIVE CHAMPION IN MEMORY")}
            </span>
          </div>

          {activeModel && (
            <div className="ms-champ-tags">
              <span className="ms-pill champ">
                {activeModel.dimension}D •{" "}
                {activeModel.stage || t("model-studio.header.stage_champion", "CHAMPION")}
              </span>
              <span className={`ms-pill ${activeModel.fine_tune_enabled ? "ft-on" : "ft-off"}`}>
                {activeModel.fine_tune_enabled
                  ? t("model-studio.header.ft_on", "FT: ON")
                  : t("model-studio.header.ft_off", "FT: OFF")}
              </span>
              <span className={`ms-pill ${activeModel.scaler_ready ? "scaler-ok" : "scaler-warn"}`}>
                {activeModel.scaler_ready
                  ? t("model-studio.header.scaler_attached", "SCALER: ATTACHED")
                  : t("model-studio.header.scaler_standby", "SCALER: STANDBY")}
              </span>
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <div style={{ textAlign: "right" }}>
            <span className="tiny faint" style={{ display: "block" }}>
              {t("model-studio.header.weights_sha", "Weights SHA256")}
            </span>
            <span className="inline-mono small tx-accent" >
              {activeModel?.weights_sha256 ? activeModel.weights_sha256.substring(0, 12) : "—"}
            </span>
          </div>
          <div style={{ textAlign: "right" }}>
            <span className="tiny faint" style={{ display: "block" }}>
              {t("model-studio.header.engine_inferences", "Engine Inferences")}
            </span>
            <span className="inline-mono small" style={{ color: "var(--green)", fontWeight: 700 }}>
              {activeModel?.inference_count?.toLocaleString() ?? 0}
            </span>
          </div>
        </div>
      </div>

      {/* KPI Overview Deck */}
      <div className="ms-metrics-grid">
        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.architecture", "Architecture")}</div>
          <div className="ms-metric-value">{overview?.architecture || "ScalpNet"}</div>
          <div className="ms-metric-sub">{t("model-studio.header.layer_spec", "Layer Spec")}</div>
        </div>

        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.model_source", "Model Source")}</div>
          <div className="ms-metric-value tx-accent"  title={overview?.model_source || "ONLINE"}>
            {overview?.model_source || "ONLINE"}
          </div>
          <div className="ms-metric-sub">{t("model-studio.header.memory_state", "Memory State")}</div>
        </div>

        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.parameters", "Parameters")}</div>
          <div className="ms-metric-value">
            {overview?.parameter_count ? overview.parameter_count.toLocaleString() : "—"}
          </div>
          <div className="ms-metric-sub">
            {t("model-studio.header.trainable", "Trainable: {n}", {
              n: overview?.trainable_parameters ? overview.trainable_parameters.toLocaleString() : "—",
            })}
          </div>
        </div>

        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.weights_sha", "Weights SHA256")}</div>
          <div className="ms-metric-value" style={{ color: "var(--green)", fontSize: 13 }}>
            {overview?.weights_sha256 ? overview.weights_sha256.substring(0, 12) : "—"}
          </div>
          <div className="ms-metric-sub">{t("model-studio.header.artifact_integrity", "Artifact Integrity")}</div>
        </div>

        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.execution_device", "Execution Device")}</div>
          <div className="ms-metric-value">{overview?.device ? overview.device.toUpperCase() : "CPU"}</div>
          <div className="ms-metric-sub">{t("model-studio.header.torch_runtime", "PyTorch Runtime")}</div>
        </div>

        <div className="ms-metric-card">
          <div className="ms-metric-label">{t("model-studio.header.scaler_status", "Scaler Status")}</div>
          <div className="ms-metric-value tx-good" >
            {overview?.scaler_stats?.status || "READY"}
          </div>
          <div className="ms-metric-sub">
            {t("model-studio.header.clamped_cols", "Clamped: {n} cols", {
              n: overview?.scaler_stats?.clamped_cols ?? 0,
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
