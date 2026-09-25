/**
 * PURPOSE:  Local visual-helpers for the incidents feature — severity heat
 *           ramp class, status-chip state class, timeline sorting, and filter
 *           chips derived only from the rows actually loaded.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: features/incidents/model (IncidentVo + its str/num/arr guards)
 *           and the backend status vocabulary already used by model.ts.
 * PROVIDES: sevClass / sevRank / statusClass / sortByRecency / filterChips
 *           — pure presentation mapping, no network and no new fields.
 * INVARIANTS: the severity/status strings are restated verbatim from the
 *             backend (uppercased exactly like model.ts does); unknown values
 *             never fall through to a fabricated color — they land on the
 *             unknown/faint ramp; chip lists contain only severities/statuses
 *             present in the loaded rows (data-derived, never hardcoded).
 * EXTEND:   add a ramp step in sevRank + sevClass together; keep helpers pure
 *           so incidents.css stays the only styling surface.
 */

import { type IncidentVo, type Row } from "../model";

/** Visual severity bucket — mirrors primitives SeverityBadge grouping so card
 *  heat and the kit badge in the same row can never disagree. */
export type SevBucket = "critical" | "high" | "medium" | "low" | "unknown";

/** Rank used for timeline ordering and the heat legend: higher = hotter. */
export function sevRank(severity: string | null | undefined): number {
  const s = (severity ?? "").toUpperCase();
  if (s === "CRITICAL") return 4;
  if (s === "HIGH") return 3;
  if (s === "MEDIUM") return 2;
  if (s === "LOW") return 1;
  return 0;
}

export function sevClass(severity: string | null | undefined): SevBucket {
  const s = (severity ?? "").toUpperCase();
  if (s === "CRITICAL") return "critical";
  if (s === "HIGH") return "high";
  if (s === "MEDIUM") return "medium";
  if (s === "LOW") return "low";
  return "unknown";
}

/** Backend status vocabulary (from model.ts + the list filter): statuses that
 *  count as "open" are exactly the ones model.ts treats as open. */
const OPEN_STATUSES = ["OPEN", "DETECTED", "INVESTIGATING", "NEW"];
const RESOLVED_STATUSES = ["RECOVERED", "RESOLVED", "CLOSED", "FALSE_POSITIVE"];

export type StatusState = "open" | "resolved" | "muted";

export function statusClass(status: string | null | undefined): StatusState {
  const s = (status ?? "").toUpperCase();
  if (OPEN_STATUSES.includes(s)) return "open";
  if (RESOLVED_STATUSES.includes(s)) return "resolved";
  return "muted";
}

/** Sort newest-first by last-seen, then detected, then severity heat.
 *  Rows without timestamps keep their relative order (stable sort). */
export function sortByRecency(rows: IncidentVo[]): IncidentVo[] {
  const time = (i: IncidentVo): number => {
    const l = Date.parse(i.lastSeenAt ?? "");
    if (Number.isFinite(l)) return l;
    const d = Date.parse(i.detectedAt ?? "");
    return Number.isFinite(d) ? d : NaN;
  };
  return rows
    .slice()
    .sort(
      (a, b) =>
        -compareTime(time(a), time(b)) || sevRank(b.severity) - sevRank(a.severity),
    );
}

/** Compare two parsed timestamps so NaN (no timestamp from the backend) sorts
 *  last instead of poisoning the whole comparison. */
function compareTime(a: number, b: number): number {
  if (!Number.isFinite(a) && !Number.isFinite(b)) return 0;
  if (!Number.isFinite(a)) return -1;
  if (!Number.isFinite(b)) return 1;
  return a - b;
}

export interface ChipOption {
  id: string;
  label: string;
  count: number;
}

/** Filter chips derived ONLY from severities/statuses present in the loaded
 *  rows — no hardcoded list can ever offer an empty bucket. */
export function filterChips(rows: IncidentVo[]): { severities: ChipOption[]; statuses: ChipOption[] } {
  const sev = new Map<string, number>();
  const st = new Map<string, number>();
  for (const r of rows) {
    if (r.severity) sev.set(r.severity, (sev.get(r.severity) ?? 0) + 1);
    if (r.status) st.set(r.status, (st.get(r.status) ?? 0) + 1);
  }
  const toOptions = (m: Map<string, number>): ChipOption[] =>
    Array.from(m.entries())
      .map(([id, count]) => ({ id, label: id, count }))
      .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));
  return { severities: toOptions(sev), statuses: toOptions(st) };
}

/** Evidence-row label/timestamp accessors (same honesty rule). */
export const evKind = (r: Row): string => String(r.kind ?? r.label ?? "");
export const evAt = (r: Row): string => String(r.timestamp ?? r.at ?? "");
