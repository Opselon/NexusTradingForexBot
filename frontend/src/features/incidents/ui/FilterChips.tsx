/**
 * PURPOSE:  Incident filter chips — severity + status option pills derived
 *           only from the severities/statuses present in the loaded rows,
 *           with a visible result-count label.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: features/incidents/ui/incidentLook (filterChips, sevClass),
 *           features/incidents/model (IncidentVo), styles/theme.css tokens
 *           via ./incidents.css (prefix inc-).
 * PROVIDES: default export FilterChips — presentational filter control.
 * INVARIANTS: options come ONLY from the loaded rows (an empty bucket can
 *             never appear); selecting a chip sets that exact backend filter
 *             value, selecting it again clears it — same semantics as the
 *             previous <select> controls; the result count shows the rows
 *             that match right now, out of the rows actually loaded.
 * EXTEND:   add a chip group by passing another field's values through
 *           incidentLook.filterChips — no new query keys, no new requests.
 */

import type { IncidentVo } from "../model";
import { filterChips } from "./incidentLook";
import "./incidents-command.css";

function ChipGroup({
  label,
  options,
  value,
  onChange,
  sev,
}: {
  label: string;
  options: { id: string; label: string; count: number }[];
  value: string;
  onChange: (v: string) => void;
  sev?: boolean;
}) {
  if (options.length === 0) return null;
  return (
    <span className="inc-filters__group">
      <span className="inc-filters__lab">{label}</span>
      <button type="button" className="inc-chip" aria-pressed={value === ""} onClick={() => onChange("")}>
        any
      </button>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className={sev ? `inc-chip inc-sev-${o.id.toLowerCase()}` : "inc-chip"}
          aria-pressed={value === o.id}
          onClick={() => onChange(value === o.id ? "" : o.id)}
        >
          {sev ? <span className="inc-chip__dot" aria-hidden="true" /> : null}
          {o.label}
          <span className="inc-chip__n">{o.count}</span>
        </button>
      ))}
    </span>
  );
}

export default function FilterChips({
  rows,
  severity,
  status,
  onSeverity,
  onStatus,
}: {
  rows: IncidentVo[];
  severity: string;
  status: string;
  onSeverity: (v: string) => void;
  onStatus: (v: string) => void;
}) {
  const { severities, statuses } = filterChips(rows);
  const filtered =
    rows.filter((r) => (severity === "" || r.severity === severity) && (status === "" || r.status === status)).length;
  return (
    <div className="inc-filters" role="group" aria-label="Incident filters">
      <ChipGroup label="sev" options={severities} value={severity} onChange={onSeverity} sev />
      <ChipGroup label="status" options={statuses} value={status} onChange={onStatus} />
      <span className="inc-filters__spacer" />
      <span className="inc-count" role="status">
        {filtered} / {rows.length} shown
        {severity || status ? " · filter active" : ""}
      </span>
      {severity || status ? (
        <button
          type="button"
          className="inc-chip"
          onClick={() => {
            onSeverity("");
            onStatus("");
          }}
        >
          clear
        </button>
      ) : null}
    </div>
  );
}
