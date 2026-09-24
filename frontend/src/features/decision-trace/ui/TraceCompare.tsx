/**
 * TraceCompare — factual A/B diff of two recorded decisions (§37).
 *
 * Field-by-field comparison of the canonical EXEC rows. Values are rendered
 * verbatim from each record; a field one side lacks shows MISSING (evidence
 * absence), never a guessed value.
 */

import { useMemo } from "react";
import { UNKNOWN } from "../traceGraph";
import type { DecisionRow } from "../types";

interface CompareField {
  key: string;
  label: string;
  a: string;
  b: string;
}

function val(row: DecisionRow | null, pick: (r: DecisionRow) => unknown): string {
  if (!row) return "MISSING";
  const v = pick(row);
  if (v === undefined || v === null || v === "") return "MISSING";
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
  const fields: CompareField[] = useMemo(
    () =>
      [
        { key: "decision_id", label: "Decision", pick: (r: DecisionRow) => r.decision_id },
        { key: "symbol", label: "Symbol", pick: (r: DecisionRow) => r.symbol },
        { key: "status", label: "Status", pick: (r: DecisionRow) => r.status },
        { key: "action", label: "Action", pick: (r: DecisionRow) => r.action },
        { key: "reason_code", label: "Reason code", pick: (r: DecisionRow) => r.reason_code },
        { key: "rejection_reason", label: "Rejection", pick: (r: DecisionRow) => r.rejection_reason },
        { key: "model_id", label: "Model", pick: (r: DecisionRow) => r.model_id },
        { key: "model_version", label: "Model version", pick: (r: DecisionRow) => r.model_version },
        { key: "contract", label: "Contract", pick: (r: DecisionRow) => r.contract },
        { key: "regime", label: "Regime", pick: (r: DecisionRow) => r.regime },
        { key: "started_at", label: "Started", pick: (r: DecisionRow) => r.started_at },
        { key: "latency_ms", label: "Latency (ms)", pick: (r: DecisionRow) => r.latency_ms },
      ].map((f) => ({ ...f, a: val(a, f.pick), b: val(b, f.pick) })),
    [a, b],
  );

  return (
    <section className="dt-compare" aria-label="Compare A vs B">
      <header className="dt-compare-head">
        <span>Compare A vs B</span>
        <button className="dt-insp-close" onClick={onClose} aria-label="Close compare">✕</button>
      </header>
      <div className="dt-compare-cols">
        <div className="dt-compare-col-title">A · {a.decision_id ?? UNKNOWN}</div>
        <div className="dt-compare-col-title">B · {b.decision_id ?? UNKNOWN}</div>
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
