/**
 * TensorInspectorPanel — the real input contract viewer (mission §11/§12/§33).
 *
 * Shows the ACTUAL last tensor fed to the adviser model, read from
 * GET /api/position-adviser/tensor/current-input. The panel NEVER fabricates a
 * tensor: before the first evaluation the route 404s and this renders a
 * permanent "no sample yet" state instead of invented numbers.
 *
 * Raw vs normalized is the whole point: raw is the position state as measured,
 * normalized is post-scaler, and the backend reports whether raw_dim,
 * normalized_dim and the model's input_dim all agree. The UI only renders that
 * verdict — it never computes it.
 */

import { useI18n } from "@/stores/i18nStore";
import type { TensorInspectorResponse } from "../model";
import "./position-adviser.css";

interface Props {
  tensor: TensorInspectorResponse | null;
  atMs: number | null;
  error: string;
  unavailable: boolean;
  loading: boolean;
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return Math.abs(v) >= 1000 ? v.toFixed(2) : v.toFixed(6);
}

export function TensorInspectorPanel({ tensor, atMs, error, unavailable, loading }: Props) {
  const { t } = useI18n();
  const ageMs = atMs ? Date.now() - atMs : null;

  const head = (
    <div className="pa-tensor-head">
      <span className="pa-tensor-title">{t("position-adviser.tensor.title", "Tensor Inspector")}</span>
      <span
        className={`pa-tensor-source ${unavailable ? "is-unavailable" : "is-live"}`}
        title={
          unavailable
            ? t("position-adviser.tensor.unavailable_hint", "The diagnostic route is not wired on this server")
            : t("position-adviser.tensor.source_hint", "Read from the live diagnostic endpoint")
        }
      >
        {unavailable
          ? t("position-adviser.tensor.unavailable", "UNAVAILABLE")
          : tensor
            ? t("position-adviser.tensor.backend", "BACKEND")
            : loading
              ? t("position-adviser.tensor.loading", "LOADING…")
              : t("position-adviser.tensor.no_sample", "NO SAMPLE YET")}
      </span>
      {ageMs !== null && tensor ? (
        <span className="pa-tensor-age">
          {t("position-adviser.tensor.age", "read {ms} ms ago", { ms: Math.round(ageMs) })}
        </span>
      ) : null}
    </div>
  );

  if (unavailable) {
    return (
      <section className="pa-panel pa-tensor is-unavailable">
        {head}
        <p className="pa-panel-empty">
          {t(
            "position-adviser.tensor.unavailable_body",
            "This server build does not expose the tensor diagnostic route, so the panel stays empty rather than showing an invented tensor.",
          )}
        </p>
      </section>
    );
  }

  if (!tensor) {
    return (
      <section className="pa-panel pa-tensor">
        {head}
        <p className="pa-panel-empty">
          {error
            ? error
            : t(
                "position-adviser.tensor.empty_body",
                "No evaluation has run yet. Load an adviser, enable PAPER, and the first real input appears here.",
              )}
        </p>
      </section>
    );
  }

  const v = tensor.validity;
  const dimsAgree = Boolean(v?.dims_match);
  // Shape guards: a field the backend omitted must not take the whole page down
  // (BUG-544 lineage) — it renders as an honest dash, never as an invented value.
  const shape = Array.isArray(tensor.tensor_shape) ? tensor.tensor_shape : [];
  const features = Array.isArray(tensor.features) ? tensor.features : [];

  return (
    <section className="pa-panel pa-tensor">
      {head}
      <dl className="pa-tensor-meta">
        <div>
          <dt>{t("position-adviser.tensor.model", "Model")}</dt>
          <dd className="pa-mono">{tensor.model_id || "—"}</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.shape", "Tensor shape")}</dt>
          <dd className="pa-mono">[{shape.join(", ")}]</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.dtype", "DType")}</dt>
          <dd className="pa-mono">{tensor.dtype}</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.device", "Device")}</dt>
          <dd className="pa-mono">{tensor.device}</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.schema", "Feature schema")}</dt>
          <dd className="pa-mono">{tensor.feature_schema}</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.seq", "Sequence length")}</dt>
          <dd className="pa-mono">{tensor.sequence_length}</dd>
        </div>
        <div>
          <dt>{t("position-adviser.tensor.infer_ts", "Inference timestamp")}</dt>
          <dd className="pa-mono">{tensor.inference_timestamp || "—"}</dd>
        </div>
      </dl>

      <p
        className={`pa-tensor-verdict ${dimsAgree ? "is-ok" : "is-bad"}`}
        title={t(
          "position-adviser.tensor.verdict_hint",
          "raw dimension, normalized dimension and the model's input dimension must all agree",
        )}
      >
        {dimsAgree
          ? t(
              "position-adviser.tensor.dims_ok",
              "Dimensions agree: raw {raw} = normalized {norm} = model input {model}",
              { raw: v.raw_dim, norm: v.normalized_dim, model: v.model_input_dim },
            )
          : t(
              "position-adviser.tensor.dims_bad",
              "Dimension mismatch: raw {raw} / normalized {norm} / model {model}",
              { raw: v.raw_dim, norm: v.normalized_dim, model: v.model_input_dim },
            )}
      </p>

      <table className="pa-tensor-table">
        <thead>
          <tr>
            <th>{t("position-adviser.tensor.th_index", "INDEX")}</th>
            <th>{t("position-adviser.tensor.th_feature", "FEATURE")}</th>
            <th>{t("position-adviser.tensor.th_raw", "RAW")}</th>
            <th>{t("position-adviser.tensor.th_normalized", "NORMALIZED")}</th>
            <th>{t("position-adviser.tensor.th_valid", "VALID")}</th>
            <th>{t("position-adviser.tensor.th_age", "AGE (s)")}</th>
          </tr>
        </thead>
        <tbody>
          {features.map((f) => (
            <tr key={f.name} className={f.finite ? "" : "is-invalid"}>
              <td className="pa-mono">{f.index}</td>
              <td className="pa-mono">{f.name}</td>
              <td className="pa-mono">{fmt(f.raw)}</td>
              <td className="pa-mono">{fmt(f.normalized)}</td>
              <td className={f.finite ? "pa-ok" : "pa-bad"}>
                {f.finite ? "finite" : "NON-FINITE"}
              </td>
              <td className="pa-mono">{Number.isFinite(f.age_sec) ? f.age_sec.toFixed(3) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <ul className="pa-tensor-counts">
        <li className={v?.nan_count ? "pa-bad" : "pa-ok"}>
          {t("position-adviser.tensor.nan", "NaN: {n}", { n: v?.nan_count ?? 0 })}
        </li>
        <li className={v?.inf_count ? "pa-bad" : "pa-ok"}>
          {t("position-adviser.tensor.inf", "Inf: {n}", { n: v?.inf_count ?? 0 })}
        </li>
        <li>
          {t("position-adviser.tensor.zero", "zero/default: {n}", {
            n: v?.zero_default_count ?? 0,
          })}
        </li>
        <li>
          {t("position-adviser.tensor.sat", "clipped/saturated: {n}", {
            n: v?.saturated_count ?? 0,
          })}
        </li>
      </ul>
    </section>
  );
}
