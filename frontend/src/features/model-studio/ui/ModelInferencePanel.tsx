/**
 * ModelInferencePanel — interactive forward pass, calibrated probability deck,
 * saliency attribution, layer activation hooks, and 70D component assembly.
 *
 * Stateless presentation component: all data + handlers are owned by the page
 * orchestrator (ModelStudioPage) and passed down.
 */

import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { Fetch70dResponse, PredictResponse } from "../model";

interface ModelInferencePanelProps {
  dimension: number;
  useLive: boolean;
  onToggleUseLive: (live: boolean) => void;
  noise: number;
  onNoiseChange: (n: number) => void;
  threshold: number;
  onThresholdChange: (t: number) => void;
  loading: boolean;
  onRunInference: () => void;
  predictData: PredictResponse | null;
  components70: NonNullable<Fetch70dResponse["slots"]>;
  contractValid: boolean | null;
  schemaHash: string;
  onFetch70d: () => void;
}

const DECISION_CLASS: Record<string, string> = {
  BUY_MARKET: "buy",
  SELL_MARKET: "sell",
  NO_TRADE: "hold",
};

const FAMILY_CLASS: Record<string, string> = {
  BASE: "base",
  NEWS: "news",
  LIQUIDITY: "liq",
};

function decisionBadgeClass(label: string): string {
  const key = Object.keys(DECISION_CLASS).find((k) => label.toUpperCase().includes(k));
  return DECISION_CLASS[key ?? "NO_TRADE"] ?? "hold";
}

export function ModelInferencePanel({
  dimension,
  useLive,
  onToggleUseLive,
  noise,
  onNoiseChange,
  threshold,
  onThresholdChange,
  loading,
  onRunInference,
  predictData,
  components70,
  contractValid,
  schemaHash,
  onFetch70d,
}: ModelInferencePanelProps) {
  const t = useI18n((s) => s.t);
  const probs = predictData?.probabilities;
  const familyCounts = components70.reduce<Record<string, number>>((acc, s) => {
    acc[s.family] = (acc[s.family] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="ms-grid-dual">
        {/* LEFT: Inference controls + decision output */}
        <Panel
          title={t("model-studio.inference.title", "Interactive Forward Pass")}
          subtitle={t(
            "model-studio.inference.subtitle",
            "Live tick tensor or Gaussian-jittered perturbation, forward through the active champion.",
          )}
          accent
        >
          <div className="ms-control-group">
            <label className="ms-checkbox-row">
              <span className="ms-checkbox-label">
                <span>📡</span>{" "}
                {t("model-studio.inference.use_live", "Use Live Tick Features")}
              </span>
              <input
                type="checkbox"
                checked={useLive}
                onChange={(e) => onToggleUseLive(e.target.checked)}
              />
            </label>

            <div className="ms-form-row">
              <label htmlFor="ms-noise-slider">{t("model-studio.inference.noise_label", "Perturbation σ (Gaussian Noise)")}</label>
              <div className="ms-range-wrapper">
                <input
                  id="ms-noise-slider"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={noise}
                  onChange={(e) => onNoiseChange(Number(e.target.value))}
                  className="ms-range-slider"
                />
                <span className="ms-range-value">{noise.toFixed(2)}</span>
              </div>
            </div>

            <div className="ms-form-row">
              <label htmlFor="ms-threshold-slider">{t("model-studio.inference.threshold_label", "Policy Threshold (Confidence Gate)")}</label>
              <div className="ms-range-wrapper">
                <input
                  id="ms-threshold-slider"
                  type="range"
                  min={0.1}
                  max={0.9}
                  step={0.01}
                  value={threshold}
                  onChange={(e) => onThresholdChange(Number(e.target.value))}
                  className="ms-range-slider"
                />
                <span className="ms-range-value">{threshold.toFixed(2)}</span>
              </div>
            </div>

            <button
              onClick={onRunInference}
              disabled={loading}
              className="ms-btn-action ms-btn-primary"
              style={{ padding: "11px 20px" }}
            >
              {loading
                ? t("model-studio.inference.run_busy", "⏳ Executing Forward Pass…")
                : t("model-studio.inference.run_idle", "▶ Run Inference")}
            </button>

            {predictData && (
              <>
                <div className="ms-decision-banner">
                  <div>
                    <div className="ms-decision-label">{t("model-studio.inference.verdict", "Signal Policy Verdict")}</div>
                    <div className="tiny faint" style={{ marginTop: 2 }}>
                      {t("model-studio.inference.latency_line", "Latency {l} ms · {d}D tensor", {
                        l: predictData.latency_ms.total_e2e.toFixed(2),
                        d: predictData.dimension,
                      })}
                    </div>
                  </div>
                  <span className={`ms-decision-badge ${decisionBadgeClass(predictData.predicted_label)}`}>
                    {predictData.predicted_label}
                  </span>
                </div>

                <div className="ms-prob-deck">
                  <ProbRow
                    label="BUY_MARKET"
                    value={probs?.buy ?? 0}
                    barClass="buy"
                    dominant={predictData.predicted_label.toUpperCase().includes("BUY")}
                  />
                  <ProbRow
                    label="SELL_MARKET"
                    value={probs?.sell ?? 0}
                    barClass="sell"
                    dominant={predictData.predicted_label.toUpperCase().includes("SELL")}
                  />
                  <ProbRow
                    label="NO_TRADE"
                    value={probs?.no_trade ?? 0}
                    barClass="hold"
                    dominant={predictData.predicted_label.toUpperCase().includes("NO_TRADE")}
                  />
                </div>

                <CalibrationStrip predict={predictData} />
              </>
            )}
          </div>
        </Panel>

        {/* RIGHT: Saliency + layer inspection */}
        <Panel
          title={t("model-studio.inference.explain_title", "Explainability: Saliency & Activation Hooks")}
          subtitle={t(
            "model-studio.inference.explain_subtitle",
            "dScore/dX gradient attribution and PyTorch forward-hook activation statistics.",
          )}
          accent
          right={
            predictData ? (
              <span className="badge good">
                {t("model-studio.inference.calibrated", "CALIBRATED")}
              </span>
            ) : null
          }
        >
          {!predictData ? (
            <div className="tiny faint" style={{ padding: "20px 4px", textAlign: "center" }}>
              {t(
                "model-studio.inference.empty",
                "Execute a forward pass to reveal gradient saliency drivers and per-layer activation norms.",
              )}
            </div>
          ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Saliency */}
            <div>
              <div className="tiny uppercase font-bold" style={{ color: "var(--accent-strong)", marginBottom: 8 }}>
                {t("model-studio.inference.top_drivers", "Top Gradient Drivers (dScore/dX)")}
              </div>
              {(!predictData.saliency ||
                (predictData.saliency.top_positive_drivers?.length ?? 0) === 0 &&
                  (predictData.saliency.top_negative_drivers?.length ?? 0) === 0) ? (
                <div className="tiny faint">{t("model-studio.inference.saliency_unavailable", "Saliency unavailable for this checkpoint.")}</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <SaliencyRow
                    title={t("model-studio.inference.sal_pos", "Positive (↑ Score)")}
                    drivers={predictData.saliency?.top_positive_drivers ?? []}
                    sign="pos"
                  />
                  <SaliencyRow
                    title={t("model-studio.inference.sal_neg", "Negative (↓ Score)")}
                    drivers={predictData.saliency?.top_negative_drivers ?? []}
                    sign="neg"
                  />
                </div>
              )}
            </div>

            {/* Layer activations */}
            <div>
              <div className="tiny uppercase font-bold" style={{ color: "var(--violet)", marginBottom: 8 }}>
                {t("model-studio.inference.layer_title", "Layer Activation Inspection (Forward Hooks)")}
              </div>
              {predictData.layer_inspection && predictData.layer_inspection.length > 0 ? (
                <div tabIndex={0} className="table-wrap" style={{ maxHeight: 260 }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th scope="col">{t("model-studio.inference.th_layer", "Layer")}</th>
                        <th scope="col">{t("model-studio.inference.th_type", "Type")}</th>
                        <th scope="col">{t("model-studio.inference.th_shape", "Shape")}</th>
                        <th scope="col" className="num">{t("model-studio.inference.th_l2", "L2 Norm")}</th>
                        <th scope="col" className="num">{t("model-studio.inference.th_mean", "Mean")}</th>
                        <th scope="col" className="num">{t("model-studio.inference.th_std", "Std")}</th>
                        <th scope="col" style={{ textAlign: "center" }}>
                          {t("model-studio.inference.th_zero", "Zero %")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {predictData.layer_inspection.map((l) => (
                        <tr key={l.layer}>
                          <td className="inline-mono" style={{ color: "var(--text)", fontWeight: 600 }}>{l.layer}</td>
                          <td className="tiny tx-dim" >{l.type}</td>
                          <td className="inline-mono tiny tx-faint" >
                            [{l.shape.join("×")}]
                          </td>
                          <td className="num tx-accent" >
                            {l.l2_norm.toFixed(4)}
                          </td>
                          <td className="num tx-good" >{l.mean.toFixed(4)}</td>
                          <td className="num">{l.std.toFixed(4)}</td>
                          <td style={{ textAlign: "center" }}>
                            <span className={`badge ${l.zero_fraction > 0.5 ? "warn" : "neutral"}`}>
                              {(l.zero_fraction * 100).toFixed(0)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="tiny faint">{t("model-studio.inference.layer_empty", "Layer hooks did not capture activations for this pass.")}</div>
              )}
            </div>
          </div>
          )}
        </Panel>
      </div>

      {/* 70D Component Assembly Matrix — only meaningful on the 70D contract */}
      {dimension === 70 && (
        <Panel
          title={t("model-studio.inference.title_70", "70D Live Multi-Source Component Assembly")}
          subtitle={t(
            "model-studio.inference.subtitle_70",
            "BASE + NEWS + LIQUIDITY tensors fused into the scalp_v3 serving contract.",
          )}
          accent
          right={
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="tiny faint inline-mono">
                {t("model-studio.inference.schema_prefix", "schema")} {schemaHash.substring(0, 10) || "—"}
              </span>
              {contractValid !== null && (
                <span className={`badge ${contractValid ? "good" : "bad"}`}>
                  {contractValid
                    ? t("model-studio.inference.contract_valid", "CONTRACT VALID")
                    : t("model-studio.inference.contract_invalid", "CONTRACT INVALID")}
                </span>
              )}
            </div>
          }
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <button onClick={onFetch70d} className="ms-btn-action ms-btn-purple">
              {t("model-studio.inference.fetch70", "⚡ Fetch Live 70D Slots")}
            </button>
            {components70.length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {Object.entries(familyCounts).map(([fam, n]) => (
                  <span key={fam} className={`ms-slot-family ${FAMILY_CLASS[fam] ?? ""}`} style={{ padding: "3px 8px" }}>
                    {fam}: {n}
                  </span>
                ))}
                <span className="badge neutral">
                  {t("model-studio.inference.slots_badge", "{n} / 70 slots", { n: components70.length })}
                </span>
              </div>
            )}
          </div>

          {components70.length === 0 ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "16px 0" }}>
              {t(
                "model-studio.inference.empty_70",
                "No 70D component snapshot loaded — fetch live slots to audit the assembled tensor.",
              )}
            </div>
          ) : (
            <div tabIndex={0} className="ms-slots-grid">
              {components70.map((s) => (
                <div key={s.index} className="ms-slot-card">
                  <span className="ms-slot-idx">{String(s.index).padStart(2, "0")}</span>
                  <span className={`ms-slot-family ${FAMILY_CLASS[s.family] ?? ""}`}>{s.family}</span>
                  <span className="ms-slot-name" title={s.name}>{s.name}</span>
                  <span className="ms-slot-val">{s.value.toFixed(3)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}

function ProbRow({
  label,
  value,
  barClass,
  dominant,
}: {
  label: string;
  value: number;
  barClass: string;
  dominant: boolean;
}) {
  return (
    <div className="ms-prob-row">
      <div className="ms-prob-meta">
        <span className={ dominant ? "tx-text" : "tx-dim" } >
          {dominant ? "◆ " : ""}
          {label}
        </span>
        <span className={ dominant ? "tx-accent" : "tx-dim" } >
          {(value * 100).toFixed(2)}%
        </span>
      </div>
      <div className="ms-prob-track">
        <div
          className={`ms-prob-fill ${barClass}`}
          style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
        />
      </div>
    </div>
  );
}

function SaliencyRow({
  title,
  drivers,
  sign,
}: {
  title: string;
  drivers: Array<{ index: number; gradient: number }>;
  sign: "pos" | "neg";
}) {
  if (drivers.length === 0) return null;
  const maxAbs = Math.max(...drivers.map((d) => Math.abs(d.gradient)), 1e-9);
  return (
    <div className="ms-saliency-block" style={{ borderTop: "none", paddingTop: 0 }}>
      <div className="tiny" style={{ color: sign === "pos" ? "var(--green)" : "var(--red)", fontWeight: 700 }}>
        {title}
      </div>
      <div className="ms-driver-row">
        {drivers.map((d) => (
          <span
            key={d.index}
            className={`ms-driver-chip ${sign}`}
            title={`feature_${d.index} · ∂score/∂x = ${d.gradient.toExponential(3)}`}
          >
            feat_{d.index} · {(d.gradient / maxAbs).toFixed(2)}σ
          </span>
        ))}
      </div>
    </div>
  );
}

function CalibrationStrip({ predict }: { predict: PredictResponse }) {
  const t = useI18n((s) => s.t);
  const items: Array<{ label: string; value: string; color?: string }> = [
    { label: t("ux.signal.confidence", "Confidence"), value: (predict.confidence * 100).toFixed(1) + "%", color: "var(--accent-strong)" },
    { label: t("model-studio.inference.margin", "Margin"), value: predict.confidence_margin.toFixed(3), color: "var(--green)" },
    {
      label: t("model-studio.inference.entropy", "Entropy"),
      value: t("model-studio.inference.bits", "{v} bits", { v: predict.shannon_entropy_bits.toFixed(3) }),
    },
    {
      label: t("model-studio.inference.numerical", "Numerical"),
      value: predict.numerical_validation.valid
        ? t("model-studio.inference.valid", "VALID")
        : t("model-studio.inference.invalid", "INVALID"),
      color: predict.numerical_validation.valid ? "var(--green)" : "var(--red)",
    },
    {
      label: t("model-studio.inference.ood_label", "OoD Max-Z"),
      value: predict.ood_metrics.max_z_score.toFixed(2) +
        (predict.ood_metrics.is_out_of_distribution ? " ⚠" : ""),
      color: predict.ood_metrics.is_out_of_distribution ? "var(--amber)" : "var(--text-dim)",
    },
  ];
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
        gap: 8,
        padding: 10,
        borderRadius: 8,
        background: "var(--bg-inset)",
        border: "1px solid var(--border)",
      }}
    >
      {items.map((it) => (
        <div key={it.label}>
          <div className="tiny faint uppercase" style={{ fontWeight: 700, letterSpacing: "0.08em" }}>
            {it.label}
          </div>
          <div className="inline-mono small" style={{ color: it.color ?? "var(--text)", marginTop: 2, fontWeight: 700 }}>
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}
