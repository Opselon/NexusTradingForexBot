/**
 * PositionTimelineLookup — immutable per-ticket lifecycle events.
 *
 * Port of the legacy "Position Timeline / ticket lookup" block
 * (Web/app.js loadIntelligenceTimeline + the #intel-ticket-input input).
 *
 * GET /api/intelligence/positions/{ticket}/timeline — an append-only audit
 * join. The lookup is explicit: the operator types a ticket and presses
 * "View"; nothing is auto-guessed or fabricated. `available:false` renders
 * the backend's own reason, and an empty event list renders "No lifecycle
 * events for ticket N".
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { intelligenceTelemetryApi } from "../intelligenceApi";

export function PositionTimelineLookup() {
  const [ticket, setTicket] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);

  const timelineQ = useQuery({
    queryKey: ["intel-position-timeline", submitted],
    queryFn: ({ signal }) => intelligenceTelemetryApi.positionTimeline(submitted!, signal),
    enabled: submitted !== null && submitted.trim() !== "",
    retry: false,
  });

  const events = timelineQ.data?.events ?? [];

  return (
    <Panel title="Position timeline (ticket lookup)" tight>
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          value={ticket}
          inputMode="numeric"
          aria-label="Ticket lookup" placeholder="ticket e.g. 501"
          onChange={(e) => setTicket(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setSubmitted(ticket.trim());
          }}
        />
        <button
          className="btn small primary"
          disabled={!ticket.trim()}
          onClick={() => setSubmitted(ticket.trim())}
        >
          View
        </button>
      </div>

      {submitted === null ? (
        <EmptyState message="Enter a ticket to load its immutable position timeline." />
      ) : timelineQ.isPending ? (
        <Skeleton count={3} />
      ) : timelineQ.isError ? (
        <ErrorState
          message={timelineQ.error instanceof Error ? timelineQ.error.message : "timeline endpoint failed"}
          onRetry={() => void timelineQ.refetch()}
        />
      ) : timelineQ.data && timelineQ.data.available === false ? (
        <EmptyState
          message="Timeline unavailable — the intelligence subsystem is not attached."
          hint="/api/intelligence/positions/{ticket}/timeline answered available:false"
        />
      ) : events.length === 0 ? (
        <EmptyState message={`No lifecycle events for ticket ${submitted}.`} />
      ) : (
        <DataTable headers={[{ label: "event" }, { label: "detail" }, { label: "MFE", num: true }, { label: "MAE", num: true }]}>
          {events.map((ev, i) => (
            <tr key={i}>
              <td className="small">
                <strong>{ev.event_type || "—"}</strong>
              </td>
              <td className="tiny muted">{ev.detail || ""}</td>
              <td className="num tiny">{ev.performance?.mfe ?? 0}</td>
              <td className="num tiny">{ev.performance?.mae ?? 0}</td>
            </tr>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}
