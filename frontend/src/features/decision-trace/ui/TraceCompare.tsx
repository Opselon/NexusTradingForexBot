/**
 * TraceCompare — factual A/B diff of two recorded decisions (§37).
 *
 * Field-by-field comparison of the canonical EXEC rows. Values are rendered
 * verbatim from each record; a field one side lacks shows MISSING (evidence
 * absence), never a guessed value.
 */

import { useMemo } from "react";
import { useI18n } from "@/stores/i18nStore";
import type { DecisionRow } from "../types";

interface CompareField {
  key: string;
  label: string;
  a: string;
  b: string;
}

function val(row: DecisionRow | null, pick: (r: DecisionRow) => unknown, missing: string): string {
  if (!row) return missing;
  const v = pick(row);
  if (v === undefined || v === null || v === "") return missing;
  return String(v);
}

export function TraceCompare({
  a,
  b,
  onClose,
}: {
  a: DecisionRow;
  b: DecisionRow;
  onClose: () => void;
}) {
  const t = useI18n((s) => s.t);
  const missing = t("trace.marker.missing", "MISSING");
  const fields: CompareField[] = useMemo(
    () =>
      [
        { key: "decision_id", label: t("trace.compare.decision", "Decision"), pick: (r: DecisionRow) => r.decision_id },
        { key: "symbol", label: t("trace.compare.symbol", "Symbol"), pick: (r: DecisionRow) => r.symbol },
        { key: "status", label: t("trace.compare.status", "Status"), pick: (r: DecisionRow) => r.status },
        { key: "action", label: t("trace.compare.action", "Action"), pick: (r: DecisionRow) => r.action },
        { key: "reason_code", label: t("trace.compare.reason_code", "Reason code"), pick: (r: DecisionRow) => r.reason_code },
        { key: "rejection_reason", label: t("trace.compare.rejection", "Rejection"), pick: (r: DecisionRow) => r.rejection_reason },
        { key: "model_id", label: t("trace.compare.model", "Model"), pick: (r: DecisionRow) => r.model_id },
        { key: "model_version", label: t("trace.compare.model_version", "Model version"), pick: (r: DecisionRow) => r.model_version },
        { key: "contract", label: t("trace.compare.contract", "Contract"), pick: (r: DecisionRow) => r.contract },
        { key: "regime", label: t("trace.compare.regime", "Regime"), pick: (r: DecisionRow) => r.regime },
        { key: "started_at", label: t("trace.compare.started", "Started"), pick: (r: DecisionRow) => r.started_at },
        { key: "latency_ms", label: t("trace.compare.latency", "Latency (ms)"), pick: (r: DecisionRow) => r.latency_ms },
      ].map((f) => ({ ...f, a: val(a, f.pick, missing), b: val(b, f.pick, missing) })),
    [a, b, t, missing],
  );

  const unknownId = (id: string | null | undefined) => id ?? t("trace.marker.unknown", "UNKNOWN");

  return (
    <section className="dt-compare" aria-label={t("trace.compare.region", "Compare A vs B")}>
      <header className="dt-compare-head">
        <span>{t("trace.compare.title", "Compare A vs B")}</span>
        <button className="dt-insp-close" onClick={onClose} aria-label={t("trace.compare.close", "Close compare")}>✕</button>
      </header>
      <div className="dt-compare-cols">
        <div className="dt-compare-col-title">A · {unknownId(a.decision_id)}</div>
        <div className="dt-compare-col-title">B · {unknownId(b.decision_id)}</div>
      </div>
      <div className="dt-compare-rows">
        {fields.map((f) => (
          <div key={f.key} className={`dt-compare-row ${f.a !== f.b ? "diff" : ""}`}>
            <span className="dt-compare-val">{f.a}</span>
            <span className="dt-compare-label">{f.label}</span>
            <span className="dt-compare-val">{f.b}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
