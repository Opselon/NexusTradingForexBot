/**
 * PURPOSE:  Incidents — incident-command console (legacy tab-incidents parity):
 *           health strip · severity heat timeline · filter chips derived from
 *           the loaded rows · incident record cards · evidence-dossier drawer ·
 *           confirm-guarded reconcile · search / one-click trace / value
 *           lineage / forensic probes sections.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: @/components/primitives kit + @/features/research/ui/lane5Kit
 *           (shared READ-ONLY kit), @/hooks/useMutationFeedback, @/lib/format,
 *           ./model + ./useCases (existing queries/VOs — no new network calls),
 *           ./incidents.css (class prefix inc-), ./incidentLook (pure helpers).
 * PROVIDES: default export IncidentsPage (route /incidents) + nothing else.
 * INVARIANTS: every rendered value is a field the backend returned (missing
 *             timestamps show "—"); filters only offer severities/statuses
 *             present in the loaded rows; honest Skeleton/EmptyState/ErrorState
 *             strings are preserved; reconcile stays confirm-guarded; the
 *             drawer is a restyle — no mutation or query changes.
 * EXTEND:    add a section component in this folder; keep this page a shell
 *            (queries + section composition) so it stays under 500 lines.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import { ConfirmModal, DataTable, EmptyState, MetricCard, Panel, Segmented, SeverityBadge, Skeleton, StatusBadge } from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatNumber } from "@/lib/format";
import { CommandResultLine, FreshnessCaption, InfoRow, JsonBlock } from "../../research/ui/lane5Kit";
import { arr, num, obj, str, type Row } from "../model";
import { incidentsQueries, incidentsUseCases } from "../useCases";
import ListSection from "./ListSection";
import IncidentDrawer from "./IncidentDrawer";
import "./incidents.css";
import "./incidents-command.css";

type Tab = "list" | "search" | "lineage" | "forensics";

/** Static route contract this console speaks — read-only display of api.ts:4-15 (wave-8 hero). */
const INCIDENT_ENDPOINTS = [
  "/api/diagnostics/incidents",
  "/api/diagnostics/health",
  "/api/diagnostics/search",
  "/api/diagnostics/trace",
  "/api/diagnostics/lineage",
  "/api/diagnostics/forensics",
  "POST /api/diagnostics/incidents/reconcile",
];

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
  const recurring = arr(healthQ.data?.recurring);
  const workerStatus = str(worker.display_state) ?? str(worker.state) ?? "DISABLED";
  const recurringCount = recurring.length;

  return (
    <div className="inc-root">
      <header className="inc-hero">
        <div className="inc-hero-main">
          <div className="inc-kicker">
            <span className="inc-kicker-bar" aria-hidden="true" />
            <span className="inc-kicker-dot" aria-hidden="true" />
            SAFETY &amp; GOVERNANCE · FORENSICS
          </div>
          <h1 className="inc-title">
            <span className="inc-title-mark" aria-hidden="true">
              ⚠
            </span>
            <span className="word">Incidents</span>
          </h1>
          <p className="inc-desc">
            Forensic incident console (legacy tab-incidents): read-only records, health aggregates, bounded
            search, one-click trace, value lineage and read-only probes — plus one confirm-guarded forensic
            audit. Every figure below comes from the diagnostics envelope; missing state stays explicit,
            never fabricated.
          </p>
          <div className="inc-endpoints" aria-label="endpoints served by this console">
            {INCIDENT_ENDPOINTS.map((ep) => (
              <span className="inc-ep" key={ep}>
                {ep}
              </span>
            ))}
          </div>
        </div>
        <div className="inc-hero-side">
          <div className="inc-prov" role="group" aria-label="incident store provenance">
            <div className="inc-prov-cell">
              <span className="inc-prov-k">total / open</span>
              <span className={`inc-prov-v ${counts ? "" : "dim"}`}>
                {String(counts?.total ?? "—")} / {String(counts?.open ?? "—")}
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">crit / high</span>
              <span
                className={`inc-prov-v ${num(counts?.critical) ? "neg" : num(counts?.high) ? "warn" : "dim"}`}
              >
                {String(counts?.critical ?? "—")} / {String(counts?.high ?? "—")}
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">worker</span>
              <span className="inc-prov-v">
                <StatusBadge status={workerStatus} />
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">recurring</span>
              <span className={`inc-prov-v ${recurringCount ? "warn" : "dim"}`}>{String(recurringCount)}</span>
            </div>
          </div>
          <span className="inc-prov-fresh">
            <FreshnessCaption
              timestamp={null}
              source="diagnostics incident store"
              isFetching={listQ.isFetching}
              error={listQ.isError}
            />
          </span>
        </div>
      </header>

      <div className="inc-sec">
        <div className="inc-sec-label">
          Store health
          <span className="inc-sec-rule" aria-hidden="true" />
          <span className="inc-sec-hint">counts + worker display state + recurring fingerprints</span>
        </div>
        <div className="inc-stats">
          <MetricCard label="total / open" value={`${String(counts?.total ?? "—")} / ${String(counts?.open ?? "—")}`} tone="dim" sub="store.count() backend aggregate" />
          <MetricCard label="critical / high" value={`${String(counts?.critical ?? 0)} / ${String(counts?.high ?? 0)}`} tone={num(counts?.critical) ? "neg" : "dim"} />
          <MetricCard
            label="incident worker"
            value={<StatusBadge status={workerStatus} />}
            sub={str(worker.last_error) ?? "state decided by backend"}
          />
          <MetricCard label="recurring fingerprints" value={String(recurringCount)} tone="dim" sub="same failure seen repeatedly" />
        </div>
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

      <div className="inc-sec">
        {tab === "list" && (
          <ListSection
            listQ={listQ}
            healthQ={healthQ}
            incidents={incidents}
            recurring={recurring}
            severity={severity}
            status={status}
            setSeverity={setSeverity}
            setStatus={setStatus}
            onOpen={setOpen}
          />
        )}

        {tab === "search" && (
          <div className="grid cols-2">
            <Panel title="Search incidents (bounded, deterministic)" tight>
              <input className="input" style={{ width: "100%" }} aria-label="Search incidents" placeholder="query text (id / root cause / tag)" value={q} onChange={(e) => setQ(e.target.value)} />
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
                aria-label="Related id filter"
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
                <select aria-label="Lineage field" className="select" style={{ width: 150 }} value={lineageField} onChange={(e) => setLineageField(e.target.value)}>
                  {["pnl", "realized_r", "open_positions", "model_output"].map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
                <input className="input" style={{ width: 110 }} aria-label="Ticket for lineage" placeholder="ticket (why)" value={lineageTicket} onChange={(e) => setLineageTicket(e.target.value)} />
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
              <select aria-label="Forensic probe" className="select" style={{ width: 150 }} value={forensicKind} onChange={(e) => setForensicKind(e.target.value)}>
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
