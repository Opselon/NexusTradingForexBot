/**
 * PURPOSE:  The Incidents list tab — severity heat timeline + filter chips +
 *           incident record cards + the health distribution panels, so the
 *           page shell stays a thin composition layer.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: @/components/primitives kit (Panel/DataTable/EmptyState/ErrorState/
 *           Skeleton/SeverityBadge), @/features/research/ui/lane5Kit
 *           (DistBars — READ-ONLY shared kit), ./model (DTOs + guards),
 *           ./incidentLook (recency sort), ./incidents.css (prefix inc-).
 * PROVIDES: default export ListSection — presentational, driven by props.
 * INVARIANTS: rows come from the parent's already-loaded query (no fetching);
 *             loading/error/empty states keep the pre-existing honest strings;
 *             the severity/status filters keep the backend query semantics
 *             (a selected chip is passed up as the list filter, "any" clears
 *             it exactly like the previous <select> controls did).
 * EXTEND:   add a panel reading another health/list field through ./model —
 *           no new query keys, no new network calls.
 */

import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, SeverityBadge, Skeleton } from "@/components/primitives";
import { DistBars } from "../../research/ui/lane5Kit";
import { num, obj, str, type IncidentListDto, type DiagnosticsHealthDto, type IncidentVo, type Row } from "../model";
import FilterChips from "./FilterChips";
import IncidentCards from "./IncidentCards";
import IncidentTimeline from "./IncidentTimeline";

type AnyQuery = UseQueryResult<IncidentListDto | DiagnosticsHealthDto, unknown>;

export default function ListSection({
  listQ,
  healthQ,
  incidents,
  recurring,
  severity,
  status,
  setSeverity,
  setStatus,
  onOpen,
}: {
  listQ: AnyQuery;
  healthQ: AnyQuery;
  incidents: IncidentVo[];
  recurring: Row[];
  severity: string;
  status: string;
  setSeverity: (v: string) => void;
  setStatus: (v: string) => void;
  onOpen: (id: string) => void;
}) {
  // perf(7): memoized on the exact payload the bars read (Object.entries identity
  // changes only when the health payload lands).
  const byComponent = useMemo(
    () =>
      Object.entries(obj((healthQ.data as DiagnosticsHealthDto | undefined)?.by_component)).map(([k, v]) => ({
        label: k,
        count: num(v) ?? 0,
      })),
    [healthQ.data],
  );

  const visible = incidents.filter(
    (i) => (severity === "" || i.severity === severity) && (status === "" || i.status === status),
  );

  return (
    <>
      <div className="grid cols-2">
        <Panel title="Severity heat timeline" subtitle="time-ordered · newest first" tight accent>
          {listQ.isPending ? (
            <div style={{ padding: 12 }}>
              <Skeleton count={5} />
            </div>
          ) : listQ.isError ? (
            <ErrorState message={listQ.error instanceof Error ? listQ.error.message : "incidents failed"} onRetry={() => void listQ.refetch()} />
          ) : (
            <IncidentTimeline rows={incidents} onSelect={onOpen} selectedId={null} />
          )}
        </Panel>
        <Panel title="By component" tight accent>
          <div style={{ padding: "10px 14px" }}>
            <DistBars rows={byComponent} tone="var(--violet)" />
          </div>
          <div className="section-title" style={{ margin: "10px 14px 4px" }}>
            Recurring (fingerprint)
          </div>
          {recurring.length === 0 ? (
            <div style={{ padding: "0 14px 12px" }}>
              <EmptyState message="No recurring incidents." />
            </div>
          ) : (
            <DataTable headers={[{ label: "fingerprint" }, { label: "seen", num: true }, { label: "severity" }]}>
              {recurring.slice(0, 10).map((r: Row, i: number) => (
                <tr key={i}>
                  <td className="inline-mono tiny">{str(r.fingerprint)?.slice(0, 16) ?? "—"}</td>
                  <td className="num tiny">{num(r.count) ?? num(r.repeated_count) ?? "—"}</td>
                  <td>
                    <SeverityBadge severity={str(r.severity)} />
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      </div>

      <Panel title="Incident records" tight accent>
        <FilterChips rows={incidents} severity={severity} status={status} onSeverity={setSeverity} onStatus={setStatus} />
        {listQ.isPending ? (
          <div style={{ padding: 12 }}>
            <Skeleton count={5} />
          </div>
        ) : listQ.isError ? (
          <ErrorState message={listQ.error instanceof Error ? listQ.error.message : "incidents failed"} onRetry={() => void listQ.refetch()} />
        ) : incidents.length === 0 ? (
          <div style={{ padding: 12 }}>
            <EmptyState message="No incidents match." hint="the store is empty or filters exclude everything" />
          </div>
        ) : (
          <IncidentCards rows={visible} onSelect={onOpen} />
        )}
      </Panel>
    </>
  );
}
