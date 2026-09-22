/**
 * ModelInferencePanel — interactive forward pass, calibrated probability deck,
 * saliency attribution, layer activation hooks, and 70D component assembly.
 *
 * Stateless presentation component: all data + handlers are owned by the page
 * orchestrator (ModelStudioPage) and passed down.
 */

import { Panel } from "@/components/primitives";
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
          title="Interactive Forward Pass"
          subtitle="Live tick tensor or Gaussian-jittered perturbation, forward through the active champion."
          accent
        >
          <div className="ms-control-group">
            <label className="ms-checkbox-row">
              <span className="ms-checkbox-label">
                <span>📡</span> Use Live Tick Features
              </span>
              <input
                type="checkbox"
                checked={useLive}
                onChange={(e) => onToggleUseLive(e.target.checked)}
              />
            </label>

            <div className="ms-form-row">
              <label htmlFor="ms-noise-slider">Perturbation σ (Gaussian Noise)</label>
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
              <label htmlFor="ms-threshold-slider">Policy Threshold (Confidence Gate)</label>
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
              {loading ? "⏳ Executing Forward Pass…" : "▶ Run Inference"}
            </button>

            {predictData && (
              <>
                <div className="ms-decision-banner">
                  <div>
                    <div className="ms-decision-label">Signal Policy Verdict</div>
                    <div className="tiny faint" style={{ marginTop: 2 }}>
                      Latency {predictData.latency_ms.total_e2e.toFixed(2)} ms · {predictData.dimension}D tensor
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
          title="Explainability: Saliency & Activation Hooks"
          subtitle="dScore/dX gradient attribution and PyTorch forward-hook activation statistics."
          accent
          right={
            predictData ? (
              <span className="badge good">CALIBRATED</span>
            ) : null
          }
        >
          {!predictData ? (
            <div className="tiny faint" style={{ padding: "20px 4px", textAlign: "center" }}>
              Execute a forward pass to reveal gradient saliency drivers and per-layer activation norms.
            </div>
          ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Saliency */}
            <div>
              <div className="tiny uppercase font-bold" style={{ color: "var(--accent-strong)", marginBottom: 8 }}>
                Top Gradient Drivers (dScore/dX)
              </div>
              {(!predictData.saliency ||
                (predictData.saliency.top_positive_drivers?.length ?? 0) === 0 &&
                  (predictData.saliency.top_negative_drivers?.length ?? 0) === 0) ? (
                <div className="tiny faint">Saliency unavailable for this checkpoint.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <SaliencyRow
                    title="Positive (↑ Score)"
                    drivers={predictData.saliency?.top_positive_drivers ?? []}
                    sign="pos"
                  />
                  <SaliencyRow
                    title="Negative (↓ Score)"
                    drivers={predictData.saliency?.top_negative_drivers ?? []}
                    sign="neg"
                  />
                </div>
              )}
            </div>

            {/* Layer activations */}
            <div>
              <div className="tiny uppercase font-bold" style={{ color: "var(--violet)", marginBottom: 8 }}>
                Layer Activation Inspection (Forward Hooks)
              </div>
              {predictData.layer_inspection && predictData.layer_inspection.length > 0 ? (
                <div className="table-wrap" style={{ maxHeight: 260 }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th scope="col">Layer</th>
                        <th scope="col">Type</th>
                        <th scope="col">Shape</th>
                        <th scope="col" className="num">L2 Norm</th>
                        <th scope="col" className="num">Mean</th>
                        <th scope="col" className="num">Std</th>
                        <th scope="col" style={{ textAlign: "center" }}>Zero %</th>
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
                <div className="tiny faint">Layer hooks did not capture activations for this pass.</div>
              )}
            </div>
          </div>
          )}
        </Panel>
      </div>

      {/* 70D Component Assembly Matrix — only meaningful on the 70D contract */}
      {dimension === 70 && (
        <Panel
          title="70D Live Multi-Source Component Assembly"
          subtitle="BASE + NEWS + LIQUIDITY tensors fused into the scalp_v3 serving contract."
          accent
          right={
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="tiny faint inline-mono">schema {schemaHash.substring(0, 10) || "—"}</span>
              {contractValid !== null && (
                <span className={`badge ${contractValid ? "good" : "bad"}`}>
                  {contractValid ? "CONTRACT VALID" : "CONTRACT INVALID"}
                </span>
              )}
            </div>
          }
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <button onClick={onFetch70d} className="ms-btn-action ms-btn-purple">
              ⚡ Fetch Live 70D Slots
            </button>
            {components70.length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {Object.entries(familyCounts).map(([fam, n]) => (
                  <span key={fam} className={`ms-slot-family ${FAMILY_CLASS[fam] ?? ""}`} style={{ padding: "3px 8px" }}>
                    {fam}: {n}
                  </span>
                ))}
                <span className="badge neutral">{components70.length} / 70 slots</span>
              </div>
            )}
          </div>

          {components70.length === 0 ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "16px 0" }}>
              No 70D component snapshot loaded — fetch live slots to audit the assembled tensor.
            </div>
          ) : (
            <div className="ms-slots-grid">
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
  const items: Array<{ label: string; value: string; color?: string }> = [
    { label: "Confidence", value: (predict.confidence * 100).toFixed(1) + "%", color: "var(--accent-strong)" },
    { label: "Margin", value: predict.confidence_margin.toFixed(3), color: "var(--green)" },
    { label: "Entropy", value: predict.shannon_entropy_bits.toFixed(3) + " bits" },
    {
      label: "Numerical",
      value: predict.numerical_validation.valid ? "VALID" : "INVALID",
      color: predict.numerical_validation.valid ? "var(--green)" : "var(--red)",
    },
    {
      label: "OoD Max-Z",
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
