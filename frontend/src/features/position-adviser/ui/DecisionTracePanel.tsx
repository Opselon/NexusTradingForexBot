/**
 * DecisionTracePanel — the live decision pipeline (mission §16/§34).
 *
 * Renders the last ADVISORY the backend produced, with the model output,
 * the policy verdict, and the measured per-stage latencies. Nothing here is
 * synthesized: every value comes from GET /api/position-adviser/decision/current,
 * which 404s until a real advisory exists.
 */

import { useI18n } from "@/stores/i18nStore";
import type { DecisionTraceResponse } from "../model";
import "./position-adviser.css";

interface Props {
  trace: DecisionTraceResponse | null;
  atMs: number | null;
  error: string;
  unavailable: boolean;
  loading: boolean;
}

export function DecisionTracePanel({ trace, atMs, error, unavailable, loading }: Props) {
  const { t } = useI18n();
  const ageMs = atMs ? Date.now() - atMs : null;

  const head = (
    <div className="pa-tensor-head">
      <span className="pa-tensor-title">{t("position-adviser.trace.title", "Live Decision Trace")}</span>
      <span
        className={`pa-tensor-source ${unavailable ? "is-unavailable" : trace ? "is-live" : ""}`}
      >
        {unavailable
          ? t("position-adviser.tensor.unavailable", "UNAVAILABLE")
          : trace
            ? t("position-adviser.tensor.backend", "BACKEND")
            : loading
              ? t("position-adviser.tensor.loading", "LOADING…")
              : t("position-adviser.trace.none", "NO DECISION YET")}
      </span>
      {ageMs !== null && trace ? (
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
            "position-adviser.trace.unavailable_body",
            "This server build does not expose the decision-trace route.",
          )}
        </p>
      </section>
    );
  }

  if (!trace) {
    return (
      <section className="pa-panel pa-tensor">
        {head}
        <p className="pa-panel-empty">
          {error
            ? error
            : t(
                "position-adviser.trace.empty_body",
                "No advisory yet. Enable the adviser to produce one.",
              )}
        </p>
      </section>
    );
  }

  const d = trace.decision;
  const probs = Object.entries(d.probabilities).sort((a, b) => b[1] - a[1]);

  return (
    <section className="pa-panel pa-tensor">
      {head}
      <ol className="pa-trace-stages">
        <li>
          <span className="pa-trace-stage">{t("position-adviser.trace.position", "POSITION")}</span>
          <span className="pa-mono">
            #{trace.position.ticket} · snapshot {trace.position.snapshot_id ?? "—"} ·{" "}
            {trace.position.snapshot_age_ms !== null
              ? `${Math.round(trace.position.snapshot_age_ms)} ms old`
              : "—"}
          </span>
        </li>
        <li>
          <span className="pa-trace-stage">{t("position-adviser.trace.model", "MODEL")}</span>
          <span className="pa-mono">
            {trace.model.model_id} · {trace.model.model_dimension}D · {trace.model.activation}
          </span>
        </li>
        <li>
          <span className="pa-trace-stage">{t("position-adviser.trace.decision", "DECISION")}</span>
          <span className="pa-decision-action" data-action={d.action}>
            {d.action}
          </span>
          <span className="pa-mono">conf {(d.confidence * 100).toFixed(1)}%</span>
        </li>
        <li>
          <span className="pa-trace-stage">{t("position-adviser.trace.policy", "POLICY")}</span>
          <span>
            {d.applied
              ? t("position-adviser.trace.applied", "applied to hold score")
              : d.not_applied_reason || t("position-adviser.trace.not_applied", "not applied")}
          </span>
        </li>
        <li>
          <span className="pa-trace-stage">{t("position-adviser.trace.ts", "TIMESTAMP")}</span>
          <span className="pa-mono">{d.evaluated_at || "—"}</span>
        </li>
      </ol>

      <div className="pa-probs">
        {probs.map(([name, value]) => (
          <div key={name} className="pa-prob">
            <span className="pa-prob-name pa-mono">{name}</span>
            <span className="pa-prob-bar">
              <span
                className={name === d.action ? "pa-prob-fill is-winner" : "pa-prob-fill"}
                style={{ width: `${Math.max(2, Math.round(value * 100))}%` }}
              />
            </span>
            <span className="pa-prob-val pa-mono">{(value * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>

      <ul className="pa-tensor-counts">
        <li>
          {t("position-adviser.latency.total", "total: {ms} ms", {
            ms: trace.latency.total_ms.toFixed(3),
          })}
        </li>
        <li>
          {t("position-adviser.latency.feature", "feature build: {ms} ms", {
            ms: trace.latency.feature_ms !== null ? trace.latency.feature_ms.toFixed(3) : "—",
          })}
        </li>
        <li>
          {t("position-adviser.latency.inference", "inference: {ms} ms", {
            ms: trace.latency.inference_ms !== null ? trace.latency.inference_ms.toFixed(3) : "—",
          })}
        </li>
      </ul>
    </section>
  );
}
