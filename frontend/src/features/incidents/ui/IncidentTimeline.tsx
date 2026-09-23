/**
 * PURPOSE:  Severity heat timeline — time-ordered incidents on a vertical rail
 *           with severity-colored dots, newest emphasized.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: features/incidents/model (IncidentVo), features/incidents/ui/
 *           incidentLook (sort + severity class), styles/theme.css tokens via
 *           ./incidents.css (class prefix inc-).
 * PROVIDES: default export IncidentTimeline — presentational list only.
 * INVARIANTS: rows are the caller's loaded incidents (never fetched here);
 *             timestamps come straight from the backend (formatDateTime);
 *             clicking a row calls onSelect with that incident's backend id;
 *             an empty list renders an honest note, never a fake skeleton.
 * EXTEND:   add rail columns in incidents.css (.inc-rail-btn grid) and read
 *           new fields from IncidentVo only — no new network calls.
 */

import { formatDateTime, formatTime } from "@/lib/format";
import type { IncidentVo } from "../model";
import { sevClass, sortByRecency } from "./incidentLook";
import "./incidents-command.css";

export default function IncidentTimeline({
  rows,
  onSelect,
  selectedId,
  emptyLabel = "No incidents in the loaded window.",
}: {
  rows: IncidentVo[];
  onSelect: (id: string) => void;
  selectedId?: string | null;
  emptyLabel?: string;
}) {
  if (rows.length === 0) {
    return <div className="inc-rail-empty">{emptyLabel}</div>;
  }
  const ordered = sortByRecency(rows);
  return (
    <ol className="inc-rail">
      {ordered.map((i) => (
        <li key={i.id} className={`inc-rail-item inc-sev-${sevClass(i.severity)}`}>
          <button
            type="button"
            className="inc-rail-btn"
            aria-current={selectedId === i.id ? "true" : undefined}
            onClick={() => onSelect(i.id)}
          >
            <span className="inc-rail-dot" aria-hidden="true">
              <i />
            </span>
            <time className="inc-rail-time" dateTime={i.lastSeenAt ?? i.detectedAt ?? undefined} title={`last seen ${formatDateTime(i.lastSeenAt)}`}>
              {formatTime(i.lastSeenAt) === "—" ? formatTime(i.detectedAt) : formatTime(i.lastSeenAt)}
            </time>
            <span className="inc-rail-body">
              <span className="inc-rail-id">{i.id}</span>
              <span className="inc-rail-meta">
                {i.component} · {i.category}
                {i.operation !== "—" ? ` · ${i.operation}` : ""}
              </span>
            </span>
            <span className="inc-rail-side">
              {i.repeatedCount > 1 ? <span className="inc-rail-x">×{i.repeatedCount}</span> : null}
              {i.isRegression ? <span className="inc-rail-x">REGRESSION</span> : null}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}
