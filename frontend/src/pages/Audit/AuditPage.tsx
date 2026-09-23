/**
 * Audit — real audit viewer over the backend audit API.
 *
 * Data sources (backend audit layer ONLY — React never touches SQLite):
 *  - /api/v1/observability/events : audit_events tail (event_type filter, page)
 *  - /api/v1/audit/events         : audit_ledger tail (status filter, page)
 *  - /api/v1/incidents            : incident inventory (severity filter, page)
 *  - /api/v1/database/status      : db metadata (filename/size/table count)
 *  - /api/v1/database/integrity   : quick_check + bounded row counts
 * Pagination follows the v1 contract (page/page_size, has_more).
 *
 * Upgrades (feature bar): row-detail drawer (raw payload, never prettified
 * into prose), client-side CSV export of exactly the current page's rows,
 * filter chips that reset paging to 1, per-view freshness captions, and the
 * shared skeleton/error+retry/empty state machine on every tab.
 */

import { useMemo, useState, type JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { auditApi } from "@/api/auditApi";
import type { AuditEventRow, AuditLedgerRow, IncidentRow } from "@/types/domain";
import {
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  MetricCard,
  Panel,
  Segmented,
  SeverityBadge,
} from "@/components/primitives";
import { LiveAgeNote, errorText } from "@/pages/_shared/SectionState";
import { Drawer, InfoChip, JsonBlock } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime, formatMoney, formatNumber } from "@/lib/format";
import { ApiError } from "@/types/api";
import "@/pages/_shared/pages.css";

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
  const [drawer, setDrawer] = useState<{ title: string; body: unknown } | null>(null);

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

  const eventRows = useMemo(() => eventsQuery.data?.items ?? [], [eventsQuery.data]);
  const ledgerRows = useMemo(() => ledgerQuery.data?.items ?? [], [ledgerQuery.data]);
  const incidentRows = useMemo(() => incidentsQuery.data?.items ?? [], [incidentsQuery.data]);

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
        <div className="l4-toolbar" style={{ marginTop: 8 }}>
          <span className="small faint" style={{ flex: 1 }}>
            Access goes through the backend audit layer (bounded, read-only endpoints). No database paths, drivers or SQL ever reach the browser beyond the
            operator-visible filename the API itself publishes.
          </span>
          <LiveAgeNote label="metadata age" atMs={dbStatusQuery.dataUpdatedAt || null} />
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
              <input
                className="input"
                placeholder="event_type filter…"
                value={eventTypeFilter}
                onChange={(e) => { setEventTypeFilter(e.target.value); setEventPage(1); }}
                style={{ width: 180 }}
                aria-label="filter by event type"
              />
              {eventRows.length > 0 && (
                <button
                  className="btn small ghost"
                  onClick={() =>
                    downloadCsv({
                      filename: `nse-audit-events-p${eventPage}-${stampForFilename()}.csv`,
                      headers: ["id", "created_at", "event_type", "payload"],
                      rows: eventRows.map((r) => [r.id, r.created_at ?? "", r.event_type ?? "", typeof r.payload === "string" ? r.payload : JSON.stringify(r.payload ?? "")]),
                    })
                  }
                  title="exports THIS page of the current filtered view (backend rows verbatim)"
                >
                  ⇩ CSV
                </button>
              )}
              {pager(eventPage, eventsQuery.data?.has_more ?? false, setEventPage)}
            </>
          }
        >
          {eventsQuery.isPending && !eventsQuery.data ? (
            <LoadingState />
          ) : eventsQuery.isError && !eventsQuery.data ? (
            <ErrorState message={errorText(eventsQuery.error, "Audit events unavailable")} requestId={eventsQuery.error instanceof ApiError ? eventsQuery.error.requestId : null} onRetry={() => void eventsQuery.refetch()} />
          ) : eventRows.length === 0 ? (
            <EmptyState message={eventTypeFilter ? `No audit events match “${eventTypeFilter}”.` : "No audit events match."} hint="Adjust the event_type filter or wait for engine activity." />
          ) : (
            <DataTable headers={[{ label: "ID" }, { label: "Time" }, { label: "Type" }, { label: "Payload (summary)" }, { label: "" }]}>
              {eventRows.map((row: AuditEventRow) => {
                const payload = parsePayload(row.payload);
                return (
                  <tr key={String(row.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); } }} className="l4-clickable" onClick={() => setDrawer({ title: `audit_event #${row.id} · ${row.event_type ?? ""}`, body: payload ?? row.payload })}>
                    <td>{String(row.id)}</td>
                    <td>{row.created_at ? formatDateTime(row.created_at) : "—"}</td>
                    <td>{row.event_type ?? "—"}</td>
                    <td className="small" style={{ maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {payload ? Object.entries(payload).slice(0, 5).map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join(" ") : "—"}
                    </td>
                    <td><span className="l4-chip accent">⤢</span></td>
                  </tr>
                );
              })}
            </DataTable>
          )}
          {eventRows.length > 0 && <div className="l4-note" style={{ padding: "6px 12px" }}>row click opens the raw payload drawer (backend text, unmodified).</div>}
        </Panel>
      )}

      {tab === "ledger" && (
        <Panel
          title="audit_ledger (trade records)"
          tight
          right={
            <>
              <input
                className="input"
                placeholder="status filter…"
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setLedgerPage(1); }}
                style={{ width: 140 }}
                aria-label="filter by status"
              />
              {ledgerRows.length > 0 && (
                <button
                  className="btn small ghost"
                  onClick={() =>
                    downloadCsv({
                      filename: `nse-audit-ledger-p${ledgerPage}-${stampForFilename()}.csv`,
                      headers: ["ticket", "symbol", "direction", "volume", "entry_price", "status", "pnl", "timestamp"],
                      rows: ledgerRows.map((r) => [r.ticket ?? "", r.symbol ?? "", r.direction ?? "", r.volume ?? "", r.entry_price ?? "", r.status ?? "", r.pnl ?? "", r.timestamp ?? ""]),
                    })
                  }
                >
                  ⇩ CSV
                </button>
              )}
              {pager(ledgerPage, ledgerQuery.data?.has_more ?? false, setLedgerPage)}
            </>
          }
        >
          {ledgerQuery.isPending && !ledgerQuery.data ? (
            <LoadingState />
          ) : ledgerQuery.isError && !ledgerQuery.data ? (
            <ErrorState message={errorText(ledgerQuery.error, "Ledger unavailable")} requestId={ledgerQuery.error instanceof ApiError ? ledgerQuery.error.requestId : null} onRetry={() => void ledgerQuery.refetch()} />
          ) : ledgerRows.length === 0 ? (
            <EmptyState message={statusFilter ? `No ledger rows match “${statusFilter}”.` : "No ledger rows match."} />
          ) : (
            <DataTable headers={[{ label: "Ticket" }, { label: "Symbol" }, { label: "Dir" }, { label: "Volume", num: true }, { label: "Entry", num: true }, { label: "Status" }, { label: "PnL", num: true }, { label: "Time" }]}>
              {ledgerRows.map((row: AuditLedgerRow, i) => (
                <tr key={row.ticket !== null && row.ticket !== undefined ? String(row.ticket) : `row-${i}`} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); } }} className="l4-clickable" onClick={() => setDrawer({ title: `audit_ledger ticket ${row.ticket ?? "—"}`, body: row })}>
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
              <select className="select" value={severityFilter} onChange={(e) => { setSeverityFilter(e.target.value); setIncidentPage(1); }} aria-label="severity filter">
                <option value="">all severities</option>
                <option value="CRITICAL">CRITICAL</option>
                <option value="HIGH">HIGH</option>
                <option value="MEDIUM">MEDIUM</option>
                <option value="LOW">LOW</option>
              </select>
              {incidentRows.length > 0 && (
                <button
                  className="btn small ghost"
                  onClick={() =>
                    downloadCsv({
                      filename: `nse-incidents-p${incidentPage}-${stampForFilename()}.csv`,
                      headers: ["incident_id", "severity", "status", "category", "component", "title", "created_at"],
                      rows: incidentRows.map((r) => [String(r.incident_id ?? r.id ?? ""), r.severity ?? "", r.status ?? "", r.category ?? "", r.component ?? "", r.title ?? "", r.created_at ?? ""]),
                    })
                  }
                >
                  ⇩ CSV
                </button>
              )}
              {pager(incidentPage, incidentsQuery.data?.has_more ?? false, setIncidentPage)}
            </>
          }
        >
          {incidentsQuery.isPending && !incidentsQuery.data ? (
            <LoadingState />
          ) : incidentsQuery.isError && !incidentsQuery.data ? (
            <ErrorState message={errorText(incidentsQuery.error, "Incident store unavailable")} requestId={incidentsQuery.error instanceof ApiError ? incidentsQuery.error.requestId : null} onRetry={() => void incidentsQuery.refetch()} />
          ) : incidentRows.length === 0 ? (
            <EmptyState message={severityFilter ? `No incidents match severity “${severityFilter}”.` : "No incidents match."} />
          ) : (
            <DataTable headers={[{ label: "ID" }, { label: "Severity" }, { label: "Status" }, { label: "Category" }, { label: "Component" }, { label: "Title" }, { label: "Created" }]}>
              {incidentRows.map((row: IncidentRow) => (
                <tr key={String(row.incident_id ?? row.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.currentTarget.click(); } }} className="l4-clickable" onClick={() => setDrawer({ title: `incident ${String(row.incident_id ?? row.id ?? "—")}`, body: row })}>
                  <td>{String(row.incident_id ?? row.id ?? "—")}</td>
                  <td><SeverityBadge severity={row.severity} /></td>
                  <td>{row.status ?? "—"}</td>
                  <td>{row.category ?? "—"}</td>
                  <td>{row.component ?? "—"}</td>
                  <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={row.title}>{row.title ?? "—"}</td>
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
            <ErrorState message="Integrity endpoint unavailable." requestId={dbIntegrityQuery.error instanceof ApiError ? dbIntegrityQuery.error.requestId : null} onRetry={() => void dbIntegrityQuery.refetch()} />
          ) : dbIntegrityQuery.data ? (
            <>
              <div className="l4-chip-row" style={{ marginBlockEnd: 10 }}>
                <InfoChip k="quick_check" v={String(dbIntegrityQuery.data.quick_check ?? "—")} tone={dbIntegrityQuery.data.quick_check === "ok" ? "good" : "warn"} />
                <InfoChip k="tables counted" v={String(Object.keys((dbIntegrityQuery.data.row_counts as Record<string, number>) ?? {}).length)} />
              </div>
              <dl className="kv" style={{ padding: "4px 4px" }}>
                {Object.entries((dbIntegrityQuery.data.row_counts as Record<string, number>) ?? {}).map(([t, n]) => (
                  <div key={t} style={{ display: "contents" }}>
                    <dt>{t}</dt>
                    <dd>{n === null ? "—" : n.toLocaleString("en-US")}</dd>
                  </div>
                ))}
              </dl>
            </>
          ) : null}
        </Panel>
      )}

      {drawer && (
        <Drawer title={drawer.title} onClose={() => setDrawer(null)}>
          <JsonBlock value={drawer.body} label="backend payload (verbatim)" />
        </Drawer>
      )}
    </div>
  );
}
