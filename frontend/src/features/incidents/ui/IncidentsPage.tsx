/**
 * Incidents — forensic console (legacy tab-incidents parity).
 *
 * Sections: health strip (counts + worker display_state + recurring
 * fingerprints) · filterable incident list with detail drawer (report +
 * zip href) · confirm-guarded reconcile · search / one-click trace /
 * value lineage / forensic probes sections.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Segmented,
  SeverityBadge,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber } from "@/lib/format";
import { CommandResultLine, DistBars, FreshnessCaption, InfoRow, JsonBlock } from "../../research/ui/lane5Kit";
import { arr, num, obj, str, type Row } from "../model";
import { incidentsQueries, incidentsUseCases } from "../useCases";
import IncidentDrawer from "./IncidentDrawer";

type Tab = "list" | "search" | "lineage" | "forensics";

export default function IncidentsPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("list");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [traceQ, setTraceQ] = useState("");
  const [lineageField, setLineageField] = useState("pnl");
  const [lineageTicket, setLineageTicket] = useState("");
  const [forensicKind, setForensicKind] = useState("accounting");
  const [confirmReconcile, setConfirmReconcile] = useState(false);
  const cmd = useMutationFeedback();
  const qc = useQueryClient();

  const listQ = useQuery({
    queryKey: ["incidents", "list", severity, status],
    queryFn: ({ signal }) => incidentsQueries.list({ severity: severity || undefined, status: status || undefined }, signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const healthQ = useQuery({
    queryKey: ["incidents", "health"],
    queryFn: ({ signal }) => incidentsQueries.health(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const searchQ = useQuery({
    queryKey: ["incidents", "search", q],
    queryFn: ({ signal }) => incidentsQueries.search(q, signal),
    retry: false,
    enabled: tab === "search" && q.trim().length > 0,
  });
  const traceQuery = useQuery({
    queryKey: ["incidents", "trace", traceQ],
    queryFn: ({ signal }) => incidentsQueries.trace(traceQ, signal),
    retry: false,
    enabled: tab === "search" && traceQ.trim().length > 0,
  });
  const lineageQ = useQuery({
    queryKey: ["incidents", "lineage", lineageField, lineageTicket],
    queryFn: ({ signal }) => incidentsQueries.lineage(lineageField, lineageTicket, signal),
    retry: false,
    enabled: tab === "lineage",
  });
  const forensicsQ = useQuery({
    queryKey: ["incidents", "forensics", forensicKind],
    queryFn: ({ signal }) => incidentsQueries.forensics(forensicKind, "", signal),
    retry: false,
    enabled: tab === "forensics",
  });

  const incidents = incidentsUseCases.voList(listQ.data?.incidents ?? []);
  const counts = healthQ.data?.counts ?? listQ.data?.counts;
  const worker = obj(healthQ.data?.worker);

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>Incidents</h2>
        <span className="muted small">forensic incident console · read-only records + guarded audit</span>
        <FreshnessCaption timestamp={null} source="diagnostics incident store" isFetching={listQ.isFetching} error={listQ.isError} />
      </div>

      <div className="grid cols-4">
        <MetricCard label="total / open" value={`${String(counts?.total ?? "—")} / ${String(counts?.open ?? "—")}`} tone="dim" sub="store.count() backend aggregate" />
        <MetricCard label="critical / high" value={`${String(counts?.critical ?? 0)} / ${String(counts?.high ?? 0)}`} tone={num(counts?.critical) ? "neg" : "dim"} />
        <MetricCard
          label="incident worker"
          value={<StatusBadge status={str(worker.display_state) ?? str(worker.state) ?? "DISABLED"} />}
          sub={str(worker.last_error) ?? "state decided by backend"}
        />
        <MetricCard
          label="recurring fingerprints"
          value={String(arr(healthQ.data?.recurring).length)}
          tone="dim"
          sub="same failure seen repeatedly"
        />
      </div>

      <Panel
        title="Reconcile (forensic audit)"
        right={
          <button className="btn small danger" disabled={cmd.state.running} onClick={() => setConfirmReconcile(true)}>
            run forensic audit
          </button>
        }
        tight
      >
        <div className="small muted">
          Re-runs every forensic probe (accounting, timebase, outcome, learning, split-fill) against the CURRENT database and reconciles incident
          impact/evidence in place — read-only regarding trading state, never creates duplicates.
        </div>
        <CommandResultLine state={cmd.state} />
      </Panel>

      <div style={{ height: 12 }} />
      <Segmented
        options={[
          { id: "list" as const, label: `Incidents (${incidents.length})` },
          { id: "search" as const, label: "Search / Trace" },
          { id: "lineage" as const, label: "Value lineage" },
          { id: "forensics" as const, label: "Forensics" },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "list" && (
          <>
            <div className="grid cols-2">
              <Panel title="By component" tight>
                <DistBars
                  rows={Object.entries(obj(healthQ.data?.by_component)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
                  tone="var(--violet)"
                />
              </Panel>
              <Panel title="Recurring (fingerprint)" tight>
                {arr(healthQ.data?.recurring).length === 0 ? (
                  <EmptyState message="No recurring incidents." />
                ) : (
                  <DataTable headers={[{ label: "fingerprint" }, { label: "seen", num: true }, { label: "severity" }]}>
                    {arr(healthQ.data?.recurring)
                      .slice(0, 10)
                      .map((r: Row, i: number) => (
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
            <Panel
              title="Incident list"
              right={
                <div style={{ display: "flex", gap: 6 }}>
                  <select className="select" style={{ width: 110 }} value={severity} onChange={(e) => setSeverity(e.target.value)}>
                    <option value="">severity: any</option>
                    {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <select className="select" style={{ width: 110 }} value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="">status: any</option>
                    {["OPEN", "INVESTIGATING", "RECOVERED", "FALSE_POSITIVE", "CLOSED"].map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
              }
              tight
            >
              {listQ.isPending ? (
                <Skeleton count={5} />
              ) : listQ.isError ? (
                <ErrorState message={listQ.error instanceof Error ? listQ.error.message : "incidents failed"} onRetry={() => void listQ.refetch()} />
              ) : incidents.length === 0 ? (
                <EmptyState message="No incidents match." hint="the store is empty or filters exclude everything" />
              ) : (
                <DataTable
                  headers={[
                    { label: "incident" },
                    { label: "sev" },
                    { label: "status" },
                    { label: "component" },
                    { label: "category" },
                    { label: "×", num: true },
                    { label: "last seen" },
                    { label: "" },
                  ]}
                >
                  {incidents.map((i) => (
                    <tr key={i.id}>
                      <td className="inline-mono tiny" title={i.id}>
                        {i.id.slice(0, 14)}
                      </td>
                      <td>
                        <SeverityBadge severity={i.severity} />
                      </td>
                      <td>
                        <StatusBadge status={i.status} />
                      </td>
                      <td className="tiny">{i.component}</td>
                      <td className="tiny muted">{i.category}</td>
                      <td className="num tiny">{i.repeatedCount > 1 ? `×${i.repeatedCount}` : ""}</td>
                      <td className="tiny">{formatDateTime(i.lastSeenAt)}</td>
                      <td>
                        <button className="btn small ghost" onClick={() => setOpen(i.id)}>
                          open
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </Panel>
          </>
        )}

        {tab === "search" && (
          <div className="grid cols-2">
            <Panel title="Search incidents (bounded, deterministic)" tight>
              <input className="input" style={{ width: "100%" }} placeholder="query text (id / root cause / tag)" value={q} onChange={(e) => setQ(e.target.value)} />
              <div style={{ marginTop: 8 }}>
                {q.trim() === "" ? (
                  <EmptyState message="Type a query — the backend answers {available:true, incidents:[]} for empty queries." />
                ) : searchQ.isFetching ? (
                  <Skeleton count={2} />
                ) : incidentsUseCases.voList(searchQ.data?.incidents ?? []).length === 0 ? (
                  <EmptyState message="No incidents matched." />
                ) : (
                  <DataTable headers={[{ label: "incident" }, { label: "sev" }, { label: "status" }, { label: "" }]}>
                    {incidentsUseCases.voList(searchQ.data?.incidents ?? []).map((i) => (
                      <tr key={i.id}>
                        <td className="inline-mono tiny">{i.id.slice(0, 14)}</td>
                        <td>
                          <SeverityBadge severity={i.severity} />
                        </td>
                        <td>
                          <StatusBadge status={i.status} />
                        </td>
                        <td>
                          <button className="btn small ghost" onClick={() => setOpen(i.id)}>
                            open
                          </button>
                        </td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </div>
            </Panel>
            <Panel title="One-click trace (incident / ticket / run / model)" tight>
              <input
                className="input"
                style={{ width: "100%" }}
                placeholder="incident_id | ticket | execution_id | order_id | model_id | research_run_id"
                value={traceQ}
                onChange={(e) => setTraceQ(e.target.value)}
              />
              <div style={{ marginTop: 8 }}>
                {traceQ.trim() === "" ? (
                  <EmptyState message="Missing hops are reported as missing_link + reason — never fabricated." />
                ) : traceQuery.isFetching ? (
                  <Skeleton count={2} />
                ) : (
                  <JsonBlock value={traceQuery.data?.trace} maxChars={3000} />
                )}
              </div>
            </Panel>
          </div>
        )}

        {tab === "lineage" && (
          <Panel
            title="Value lineage (how a number is derived)"
            right={
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 150 }} value={lineageField} onChange={(e) => setLineageField(e.target.value)}>
                  {["pnl", "realized_r", "open_positions", "model_output"].map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
                <input className="input" style={{ width: 110 }} placeholder="ticket (why)" value={lineageTicket} onChange={(e) => setLineageTicket(e.target.value)} />
              </div>
            }
            tight
          >
            {lineageQ.isPending ? (
              <Skeleton count={3} />
            ) : lineageQ.isError ? (
              <EmptyState message={lineageQ.error instanceof Error ? lineageQ.error.message : "lineage failed"} />
            ) : (
              <>
                <dl className="kv" style={{ marginBottom: 8 }}>
                  <InfoRow label="field" value={lineageQ.data?.field ?? "—"} />
                  <InfoRow label="source" value={lineageQ.data?.source ?? "—"} />
                  <InfoRow label="hops" value={formatNumber(arr(lineageQ.data?.hops).length, 0)} />
                </dl>
                <DataTable headers={[{ label: "#" }, { label: "hop" }, { label: "table / fn" }, { label: "note" }]}>
                  {arr(lineageQ.data?.hops).map((h: Row, i: number) => (
                    <tr key={i}>
                      <td className="num tiny">{i + 1}</td>
                      <td className="small">{str(h.name) ?? str(h.step) ?? "—"}</td>
                      <td className="inline-mono tiny">{str(h.source) ?? str(h.table) ?? "—"}</td>
                      <td className="tiny muted">{str(h.note) ?? str(h.detail) ?? ""}</td>
                    </tr>
                  ))}
                </DataTable>
                {lineageTicket && (
                  <>
                    <div className="section-title" style={{ marginTop: 10 }}>
                      why closed / why no learning (ticket {lineageTicket})
                    </div>
                    <JsonBlock value={{ why_closed: lineageQ.data?.why_closed, why_no_learning: lineageQ.data?.why_no_learning }} maxChars={2500} />
                  </>
                )}
              </>
            )}
          </Panel>
        )}

        {tab === "forensics" && (
          <Panel
            title="Read-only forensic probes"
            right={
              <select className="select" style={{ width: 150 }} value={forensicKind} onChange={(e) => setForensicKind(e.target.value)}>
                <option value="accounting">accounting</option>
                <option value="timebase">timebase</option>
              </select>
            }
            tight
          >
            {forensicsQ.isPending ? (
              <Skeleton count={3} />
            ) : forensicsQ.isError ? (
              <EmptyState message={forensicsQ.error instanceof Error ? forensicsQ.error.message : "probe failed"} />
            ) : (
              <JsonBlock value={forensicsQ.data} maxChars={5000} />
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              probes never write; classification counts come straight from the backend engine.
            </div>
          </Panel>
        )}
      </div>

      {open && <IncidentDrawer incidentId={open} onClose={() => setOpen(null)} />}

      {confirmReconcile && (
        <ConfirmModal
          title="Run forensic audit (reconcile)"
          danger
          confirmLabel="Run audit"
          busy={cmd.state.running}
          onCancel={() => setConfirmReconcile(false)}
          onConfirm={async () => {
            setConfirmReconcile(false);
            await cmd.run(async () => {
              const res = await incidentsUseCases.reconcile();
              const v = incidentsUseCases.reconcileVerdict(res);
              return { ok: v.ok, success: v.ok, message: v.message, status: v.ok ? 200 : 500 };
            });
            void qc.invalidateQueries({ queryKey: ["incidents"] });
          }}
        >
          <div className="small">
            Runs 5 forensic probe families over the current audit DB and updates stored incident records (impact/evidence). It does not touch
            orders, positions, or the engine.
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
