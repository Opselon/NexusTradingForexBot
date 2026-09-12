/**
 * Audit — real audit viewer over the backend audit API.
 *
 * Data sources (backend audit layer ONLY — React never touches SQLite):
 *  - /api/v1/observability/events : audit_events tail (event_type filter, page)
 *  - /api/v1/audit/events         : audit_ledger tail (status filter, page)
 *  - /api/v1/incidents            : incident inventory (severity filter, page)
 *  - /api/v1/database/status      : db metadata (filename/size/table count)
 *  - /api/v1/database/integrity   : quick_check + row counts
 * Pagination follows the v1 contract (page/page_size, has_more).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { auditApi } from "@/api/auditApi";
import type { AuditEventRow, AuditLedgerRow, IncidentRow } from "@/types/domain";
import { DataTable, EmptyState, ErrorState, LoadingState, MetricCard, Panel, Segmented, SeverityBadge } from "@/components/primitives";
import { formatDateTime, formatMoney, formatNumber } from "@/lib/format";
import { ApiError } from "@/types/api";

const PAGE_SIZE = 25;

type Tab = "events" | "ledger" | "incidents" | "db";

function parsePayload(payload: AuditEventRow["payload"]): Record<string, unknown> | null {
  if (!payload) return null;
  if (typeof payload === "object") return payload;
  try {
    return JSON.parse(payload) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export default function AuditPage() {
  const [tab, setTab] = useState<Tab>("events");
  const [eventPage, setEventPage] = useState(1);
  const [ledgerPage, setLedgerPage] = useState(1);
  const [incidentPage, setIncidentPage] = useState(1);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");

  const eventsQuery = useQuery({
    queryKey: ["audit-events", eventPage, eventTypeFilter],
    queryFn: ({ signal }) => auditApi.systemEvents({ page: eventPage, page_size: PAGE_SIZE, eventType: eventTypeFilter || undefined }, signal),
    placeholderData: (prev) => prev,
    retry: false,
  });
  const ledgerQuery = useQuery({
    queryKey: ["audit-ledger", ledgerPage, statusFilter],
    queryFn: ({ signal }) => auditApi.ledgerEvents({ page: ledgerPage, page_size: PAGE_SIZE, status: statusFilter || undefined }, signal),
    placeholderData: (prev) => prev,
    retry: false,
  });
  const incidentsQuery = useQuery({
    queryKey: ["incidents", incidentPage, severityFilter],
    queryFn: ({ signal }) => auditApi.incidents({ page: incidentPage, page_size: PAGE_SIZE, severity: severityFilter || undefined }, signal),
    placeholderData: (prev) => prev,
    retry: false,
  });
  const dbStatusQuery = useQuery({
    queryKey: ["db-status"],
    queryFn: ({ signal }) => auditApi.databaseStatus(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const dbIntegrityQuery = useQuery({
    queryKey: ["db-integrity"],
    queryFn: ({ signal }) => auditApi.databaseIntegrity(signal),
    refetchInterval: 120_000,
    retry: false,
  });

  const tabSeg = (
    <Segmented<Tab>
      options={[
        { id: "events", label: "Event stream" },
        { id: "ledger", label: "Trade ledger" },
        { id: "incidents", label: "Incidents" },
        { id: "db", label: "Integrity" },
      ]}
      value={tab}
      onChange={setTab}
    />
  );

  const pager = (page: number, hasMore: boolean, setPage: (p: number) => void): JSX.Element => (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ prev</button>
      <span className="small faint inline-mono">page {page}</span>
      <button className="btn small" disabled={!hasMore} onClick={() => setPage(page + 1)}>next ›</button>
    </span>
  );

  return (
    <div>
      <Panel title="Audit database (backend-reported metadata)">
        <div className="grid cols-4">
          <div className="metric">
            <div className="k">database</div>
            <div className="v dim small" style={{ fontSize: 13 }}>{String(dbStatusQuery.data?.filename ?? "—")}</div>
            <div className="s">{dbStatusQuery.data?.exists ? `${formatNumber(Number(dbStatusQuery.data.size_bytes ?? 0) / 1024 / 1024, 2)} MB · ${dbStatusQuery.data.table_count ?? "?"} tables` : "not present"}</div>
          </div>
          <MetricCard label="quick_check" value={String(dbIntegrityQuery.data?.quick_check ?? "—")} tone={dbIntegrityQuery.data?.quick_check === "ok" ? "pos" : "dim"} />
          <MetricCard label="audit_signals rows" value={String((dbIntegrityQuery.data?.row_counts as Record<string, number> | undefined)?.audit_signals ?? "—")} tone="dim" />
          <MetricCard label="audit_ledger rows" value={String((dbIntegrityQuery.data?.row_counts as Record<string, number> | undefined)?.audit_ledger ?? "—")} tone="dim" />
        </div>
        <div className="small faint" style={{ marginTop: 8 }}>
          Access goes through the backend audit layer (bounded, read-only endpoints). No database paths, drivers or SQL ever reach the browser beyond the operator-visible filename the API itself publishes.
        </div>
      </Panel>

      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        {tabSeg}
      </div>

      {tab === "events" && (
        <Panel
          title="audit_events (system event stream)"
          tight
          right={
            <>
              <input className="input" placeholder="event_type filter…" value={eventTypeFilter} onChange={(e) => { setEventTypeFilter(e.target.value); setEventPage(1); }} style={{ width: 180 }} />
              {pager(eventPage, eventsQuery.data?.has_more ?? false, setEventPage)}
            </>
          }
        >
          {eventsQuery.isPending && !eventsQuery.data ? (
            <LoadingState />
          ) : eventsQuery.isError && !eventsQuery.data ? (
            <ErrorState message={eventsQuery.error instanceof ApiError ? eventsQuery.error.message : "Audit events unavailable"} onRetry={() => eventsQuery.refetch()} />
          ) : (eventsQuery.data?.items.length ?? 0) === 0 ? (
            <EmptyState message="No audit events match." hint="Adjust the event_type filter or wait for engine activity." />
          ) : (
            <DataTable headers={[{ label: "ID" }, { label: "Time" }, { label: "Type" }, { label: "Payload (summary)" }]}>
              {(eventsQuery.data?.items ?? []).map((row: AuditEventRow) => {
                const payload = parsePayload(row.payload);
                return (
                  <tr key={String(row.id)}>
                    <td>{String(row.id)}</td>
                    <td>{row.created_at ? formatDateTime(row.created_at) : "—"}</td>
                    <td>{row.event_type ?? "—"}</td>
                    <td className="small" style={{ maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {payload ? Object.entries(payload).slice(0, 5).map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join(" ") : "—"}
                    </td>
                  </tr>
                );
              })}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "ledger" && (
        <Panel
          title="audit_ledger (trade records)"
          tight
          right={
            <>
              <input className="input" placeholder="status filter…" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setLedgerPage(1); }} style={{ width: 140 }} />
              {pager(ledgerPage, ledgerQuery.data?.has_more ?? false, setLedgerPage)}
            </>
          }
        >
          {ledgerQuery.isPending && !ledgerQuery.data ? (
            <LoadingState />
          ) : ledgerQuery.isError && !ledgerQuery.data ? (
            <ErrorState message={ledgerQuery.error instanceof ApiError ? ledgerQuery.error.message : "Ledger unavailable"} onRetry={() => ledgerQuery.refetch()} />
          ) : (ledgerQuery.data?.items.length ?? 0) === 0 ? (
            <EmptyState message="No ledger rows match." />
          ) : (
            <DataTable headers={[{ label: "Ticket" }, { label: "Symbol" }, { label: "Dir" }, { label: "Volume", num: true }, { label: "Entry", num: true }, { label: "Status" }, { label: "PnL", num: true }, { label: "Time" }]}>
              {(ledgerQuery.data?.items ?? []).map((row: AuditLedgerRow, i) => (
                <tr key={`${row.ticket ?? "x"}-${i}`}>
                  <td>{row.ticket ?? "—"}</td>
                  <td>{row.symbol ?? "—"}</td>
                  <td>{row.direction ?? "—"}</td>
                  <td className="num">{formatNumber(row.volume)}</td>
                  <td className="num">{row.entry_price === null ? "—" : row.entry_price.toFixed(2)}</td>
                  <td>{row.status ?? "—"}</td>
                  <td className={`num ${row.pnl !== null && row.pnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>{formatMoney(row.pnl)}</td>
                  <td>{row.timestamp ? formatDateTime(row.timestamp) : "—"}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "incidents" && (
        <Panel
          title="Incident inventory"
          tight
          right={
            <>
              <select className="select" value={severityFilter} onChange={(e) => { setSeverityFilter(e.target.value); setIncidentPage(1); }}>
                <option value="">all severities</option>
                <option value="CRITICAL">CRITICAL</option>
                <option value="HIGH">HIGH</option>
                <option value="MEDIUM">MEDIUM</option>
                <option value="LOW">LOW</option>
              </select>
              {pager(incidentPage, incidentsQuery.data?.has_more ?? false, setIncidentPage)}
            </>
          }
        >
          {incidentsQuery.isPending && !incidentsQuery.data ? (
            <LoadingState />
          ) : incidentsQuery.isError && !incidentsQuery.data ? (
            <ErrorState message={incidentsQuery.error instanceof ApiError ? incidentsQuery.error.message : "Incident store unavailable"} onRetry={() => incidentsQuery.refetch()} />
          ) : (incidentsQuery.data?.items.length ?? 0) === 0 ? (
            <EmptyState message="No incidents match." />
          ) : (
            <DataTable headers={[{ label: "ID" }, { label: "Severity" }, { label: "Status" }, { label: "Category" }, { label: "Component" }, { label: "Title" }, { label: "Created" }]}>
              {(incidentsQuery.data?.items ?? []).map((row: IncidentRow) => (
                <tr key={String(row.incident_id ?? row.id)}>
                  <td>{String(row.incident_id ?? row.id ?? "—")}</td>
                  <td><SeverityBadge severity={row.severity} /></td>
                  <td>{row.status ?? "—"}</td>
                  <td>{row.category ?? "—"}</td>
                  <td>{row.component ?? "—"}</td>
                  <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title ?? "—"}</td>
                  <td>{row.created_at ? formatDateTime(row.created_at) : "—"}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {tab === "db" && (
        <Panel title="Database integrity (read-only PRAGMA via backend)">
          {dbIntegrityQuery.isPending ? (
            <LoadingState />
          ) : dbIntegrityQuery.isError ? (
            <ErrorState message="Integrity endpoint unavailable." onRetry={() => dbIntegrityQuery.refetch()} />
          ) : dbIntegrityQuery.data ? (
            <dl className="kv" style={{ padding: "4px 4px" }}>
              <dt>quick_check</dt>
              <dd>{String(dbIntegrityQuery.data.quick_check ?? "—")}</dd>
              {Object.entries((dbIntegrityQuery.data.row_counts as Record<string, number>) ?? {}).map(([t, n]) => (
                <div key={t} style={{ display: "contents" }}>
                  <dt>{t}</dt>
                  <dd>{n === null ? "—" : n.toLocaleString("en-US")}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </Panel>
      )}
    </div>
  );
}
