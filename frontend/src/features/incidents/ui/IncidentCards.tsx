/**
 * PURPOSE:  Incident record list rendered as command cards — monospace key
 *           figures, cause/impact summary lines, hover + focus rings.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: features/incidents/model (IncidentVo), features/incidents/ui/
 *           incidentLook (severity class), @/components/primitives badges,
 *           styles/theme.css tokens via ./incidents.css (prefix inc-).
 * PROVIDES: default export IncidentCards — presentational grid only.
 * INVARIANTS: every rendered value is an IncidentVo field the backend filled
 *             (missing timestamps show "—" from formatDateTime, never a
 *             placeholder date); summary lines are the backend's own
 *             recommended-action / component / category strings; the "open"
 *             button keeps the existing drawer-open control.
 * EXTEND:    add a .inc-card__line row reading another IncidentVo field — no
 *            new queries, no mutation of the drawer contract.
 */

import { SeverityBadge } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { num, obj, type IncidentVo } from "../model";
import { sevClass, sortByRecency } from "./incidentLook";
import StatusChip from "./StatusChip";
import "./incidents-command.css";

/** One-line impact summary read off the backend impact payload (derived:
 *  counts only, labeled as such). Empty payload → no line rendered. */
function impactSummary(i: IncidentVo): string | null {
  const impact = obj(i.raw.impact);
  const parts: string[] = [];
  const rec = num(impact.affected_records);
  const mod = num(impact.affected_models);
  const trd = num(impact.affected_trades);
  if (rec !== null && rec > 0) parts.push(`${rec} records`);
  if (trd !== null && trd > 0) parts.push(`${trd} trades`);
  if (mod !== null && mod > 0) parts.push(`${mod} models`);
  return parts.length ? `impact: ${parts.join(" · ")} (derived)` : null;
}

/** Cause summary — the backend's own root_cause, else its attribution status.
 *  Never reworded; when neither exists the line is omitted (not invented). */
function causeSummary(i: IncidentVo): string | null {
  const cause = typeof i.raw.root_cause === "string" && i.raw.root_cause ? i.raw.root_cause : null;
  const status = typeof i.raw.root_cause_status === "string" && i.raw.root_cause_status ? i.raw.root_cause_status : null;
  return cause ?? (status ? `root cause status: ${status}` : null);
}

export default function IncidentCards({
  rows,
  onSelect,
}: {
  rows: IncidentVo[];
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="inc-cards">
      {sortByRecency(rows).map((i) => (
        <li key={i.id} className={`inc-card inc-sev-${sevClass(i.severity)}`}>
          <div className="inc-card__top">
            <SeverityBadge severity={i.severity} />
            <span className="inc-card__id" title={i.id}>
              {i.id}
            </span>
            {i.repeatedCount > 1 ? <span className="inc-card__x">×{i.repeatedCount}</span> : null}
            {i.isRegression ? <span className="inc-card__x">REGRESSION</span> : null}
          </div>

          <div className="inc-card__times">
            <span>
              <b>detected </b>
              {formatDateTime(i.detectedAt)}
            </span>
            <span>
              <b>last seen </b>
              {formatDateTime(i.lastSeenAt)}
            </span>
          </div>

          <div className="inc-card__meta">
            <span className="inc-mono">{i.component}</span> / {i.category}
            {i.operation !== "—" ? <> · <span className="inc-mono">{i.operation}</span></> : null}
          </div>

          {causeSummary(i) ? (
            <div className="inc-card__line">
              <span className="inc-card__k">cause</span>
              <span className="inc-card__v">{causeSummary(i)}</span>
            </div>
          ) : null}
          {impactSummary(i) ? (
            <div className="inc-card__line">
              <span className="inc-card__k">impact</span>
              <span className="inc-card__v dim">{impactSummary(i)}</span>
            </div>
          ) : null}

          {i.recommendedAction ? (
            <div className="inc-card__line">
              <span className="inc-card__k">action</span>
              <span className="inc-card__v dim">{i.recommendedAction}</span>
            </div>
          ) : null}
          {i.relatedBugId ? (
            <div className="inc-card__line">
              <span className="inc-card__k">bug</span>
              <span className="inc-card__v inc-mono">{i.relatedBugId}</span>
            </div>
          ) : null}

          <div className="inc-card__foot">
            <StatusChip status={i.status} />
            <span className="inc-card__tags">
              <span className="inc-card__tag">{i.open ? "unresolved" : "not-open"}</span>
            </span>
            <button type="button" className="btn small ghost" onClick={() => onSelect(i.id)}>
              dossier →
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
