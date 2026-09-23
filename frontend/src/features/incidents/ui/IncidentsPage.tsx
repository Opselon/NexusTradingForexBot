/**
 * Incidents — forensic console (legacy tab-incidents parity).
 *
 * Sections: health strip (counts + worker display_state + recurring
 * fingerprints) · filterable incident list with detail drawer (report +
 * zip href) · confirm-guarded reconcile · search / one-click trace /
 * value lineage / forensic probes sections.
 *
 * Wave-8 presentation upgrade: modern operator hero (visible h1, kicker,
 * desc, provenance side restating backend state) + clear section labels.
 * Strictly presentation-layer — every query key, refetchInterval, retry,
 * enabled flag, mutation, ConfirmModal gate and rendered value below is
 * unchanged; only structure/classes moved. Styles: ui/incidents.css (.inc-*).
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
import { useI18n } from "@/stores/i18nStore";
import { CommandResultLine, DistBars, FreshnessCaption, InfoRow, JsonBlock } from "../../research/ui/lane5Kit";
import { arr, num, obj, str, type Row } from "../model";
import { incidentsQueries, incidentsUseCases } from "../useCases";
import IncidentDrawer from "./IncidentDrawer";
import "./incidents.css";

type Tab = "list" | "search" | "lineage" | "forensics";

/** Static route contract this console speaks — read-only display of api.ts:4-15. */
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
  const t = useI18n((s) => s.t);
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
  const workerStatus = str(worker.display_state) ?? str(worker.state) ?? "DISABLED";
  const recurringCount = arr(healthQ.data?.recurring).length;
  // Tab notes + enum labels resolve at render time so a language switch applies.
  const TAB_NOTES: Record<Tab, string> = {
    list: t("incidents.tab.note_list", "filterable records · detail drawer (report + evidence zip)"),
    search: t("incidents.tab.note_search", "bounded, deterministic search · missing trace hops reported, never invented"),
    lineage: t("incidents.tab.note_lineage", "how a number is derived · why_closed / why_no_learning"),
    forensics: t("incidents.tab.note_forensics", "read-only probes · counts straight from the backend engine"),
  };
  const sevLabels: Record<string, string> = {
    CRITICAL: t("incidents.sev.critical", "CRITICAL"),
    HIGH: t("incidents.sev.high", "HIGH"),
    MEDIUM: t("incidents.sev.medium", "MEDIUM"),
    LOW: t("incidents.sev.low", "LOW"),
  };
  const statusLabels: Record<string, string> = {
    OPEN: t("incidents.status.open", "OPEN"),
    INVESTIGATING: t("incidents.status.investigating", "INVESTIGATING"),
    RECOVERED: t("incidents.status.recovered", "RECOVERED"),
    FALSE_POSITIVE: t("incidents.status.false_positive", "FALSE_POSITIVE"),
    CLOSED: t("incidents.status.closed", "CLOSED"),
  };

  return (
    <div className="inc-page">
      <header className="inc-hero">
        <div className="inc-hero-main">
          <div className="inc-kicker">
            <span className="inc-kicker-bar" aria-hidden="true" />
            <span className="inc-kicker-dot" aria-hidden="true" />
            {t("incidents.page.kicker", "SAFETY & GOVERNANCE · FORENSICS")}
          </div>
          <h1 className="inc-title">
            <span className="inc-title-mark" aria-hidden="true">
              ⚠
            </span>
            <span className="word">{t("nav.feature.incidents", "Incidents")}</span>
          </h1>
          <p className="inc-desc">
            {t(
              "incidents.page.desc",
              "Forensic incident console (legacy tab-incidents): read-only records, health aggregates, bounded search, one-click trace, value lineage and read-only probes — plus one confirm-guarded forensic audit. Every figure below comes from the diagnostics envelope; missing state stays explicit, never fabricated.",
            )}
          </p>
          <div className="inc-endpoints" aria-label={t("incidents.hero.ep_a11y", "endpoints served by this console")}>
            {INCIDENT_ENDPOINTS.map((ep) => (
              <span className="inc-ep" key={ep}>
                {ep}
              </span>
            ))}
          </div>
        </div>
        <div className="inc-hero-side">
          <div className="inc-prov" role="group" aria-label={t("incidents.hero.prov_a11y", "incident store provenance")}>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">{t("incidents.metric.total_open", "total / open")}</span>
              <span className={`inc-prov-v ${counts ? "" : "dim"}`}>
                {String(counts?.total ?? "—")} / {String(counts?.open ?? "—")}
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">{t("incidents.prov.crit_high", "crit / high")}</span>
              <span
                className={`inc-prov-v ${num(counts?.critical) ? "neg" : num(counts?.high) ? "warn" : "dim"}`}
              >
                {String(counts?.critical ?? "—")} / {String(counts?.high ?? "—")}
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">{t("incidents.prov.worker", "worker")}</span>
              <span className="inc-prov-v">
                <StatusBadge status={workerStatus} />
              </span>
            </div>
            <div className="inc-prov-cell">
              <span className="inc-prov-k">{t("incidents.prov.recurring", "recurring")}</span>
              <span className={`inc-prov-v ${recurringCount ? "warn" : "dim"}`}>{String(recurringCount)}</span>
            </div>
          </div>
          <span className="inc-prov-fresh">
            <FreshnessCaption
              timestamp={null}
              source={t("incidents.page.source", "diagnostics incident store")}
              isFetching={listQ.isFetching}
              error={listQ.isError}
            />
          </span>
        </div>
      </header>

      <div className="inc-sec">
        <div className="inc-sec-label">
          {t("incidents.section.store_health", "Store health")}
          <span className="inc-sec-rule" aria-hidden="true" />
          <span className="inc-sec-hint">
            {t("incidents.section.store_health_hint", "counts + worker display state + recurring fingerprints")}
          </span>
        </div>

        <div className="inc-stats">
          <MetricCard
            label={t("incidents.metric.total_open", "total / open")}
            value={`${String(counts?.total ?? "—")} / ${String(counts?.open ?? "—")}`}
            tone="dim"
            sub={t("incidents.metric.total_open_sub", "store.count() backend aggregate")}
          />
          <MetricCard
            label={t("incidents.metric.critical_high", "critical / high")}
            value={`${String(counts?.critical ?? 0)} / ${String(counts?.high ?? 0)}`}
            tone={num(counts?.critical) ? "neg" : "dim"}
          />
          <MetricCard
            label={t("incidents.metric.worker", "incident worker")}
            value={<StatusBadge status={workerStatus} />}
            sub={str(worker.last_error) ?? t("incidents.metric.worker_sub", "state decided by backend")}
          />
          <MetricCard
            label={t("incidents.metric.recurring", "recurring fingerprints")}
            value={String(recurringCount)}
            tone="dim"
            sub={t("incidents.metric.recurring_sub", "same failure seen repeatedly")}
          />
        </div>

        <Panel
          title={t("incidents.panel.reconcile", "Reconcile (forensic audit)")}
          right={
            <button className="btn small danger" disabled={cmd.state.running} onClick={() => setConfirmReconcile(true)}>
              {t("incidents.reconcile.btn", "run forensic audit")}
            </button>
          }
          tight
        >
          <div className="small muted">
            {t(
              "incidents.reconcile.note",
              "Re-runs every forensic probe (accounting, timebase, outcome, learning, split-fill) against the CURRENT database and reconciles incident impact/evidence in place — read-only regarding trading state, never creates duplicates.",
            )}
          </div>
          <CommandResultLine state={cmd.state} />
        </Panel>
      </div>

      <div className="inc-sec">
        <div className="inc-sec-label">
          {t("incidents.section.workbench", "Workbench")}
          <span className="inc-sec-rule" aria-hidden="true" />
          <span className="inc-sec-hint">
            {t("incidents.section.workbench_hint", "list · search / trace · value lineage · forensic probes")}
          </span>
        </div>

        <div className="inc-tabs">
          <Segmented
            options={[
              { id: "list" as const, label: t("incidents.tab.list", "Incidents ({n})", { n: incidents.length }) },
              { id: "search" as const, label: t("incidents.tab.search", "Search / Trace") },
              { id: "lineage" as const, label: t("incidents.tab.lineage", "Value lineage") },
              { id: "forensics" as const, label: t("incidents.tab.forensics", "Forensics") },
            ]}
            value={tab}
            onChange={setTab}
          />
          <span className="inc-tabs-note">{TAB_NOTES[tab]}</span>
        </div>

        <div className="inc-tabbody">
          {tab === "list" && (
            <>
              <div className="inc-list-grid">
                <Panel title={t("incidents.panel.by_component", "By component")} tight>
                  <DistBars
                    rows={Object.entries(obj(healthQ.data?.by_component)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))}
                    tone="var(--violet)"
                  />
                </Panel>
                <Panel title={t("incidents.panel.recurring", "Recurring (fingerprint)")} tight>
                  {recurringCount === 0 ? (
                    <EmptyState message={t("incidents.empty.recurring", "No recurring incidents.")} />
                  ) : (
                    <DataTable
                      headers={[
                        { label: t("incidents.th.fingerprint", "fingerprint") },
                        { label: t("incidents.th.seen", "seen"), num: true },
                        { label: t("incidents.th.severity", "severity") },
                      ]}
                    >
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
                title={t("incidents.panel.list", "Incident list")}
                right={
                  <div style={{ display: "flex", gap: 6 }}>
                    <select
                      aria-label={t("incidents.filter.severity_a11y", "Severity filter")}
                      className="select"
                      style={{ width: 110 }}
                      value={severity}
                      onChange={(e) => setSeverity(e.target.value)}
                    >
                      <option value="">{t("incidents.filter.severity_any", "severity: any")}</option>
                      {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => (
                        <option key={s} value={s}>
                          {sevLabels[s] ?? s}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label={t("incidents.filter.status_a11y", "Status filter")}
                      className="select"
                      style={{ width: 110 }}
                      value={status}
                      onChange={(e) => setStatus(e.target.value)}
                    >
                      <option value="">{t("incidents.filter.status_any", "status: any")}</option>
                      {["OPEN", "INVESTIGATING", "RECOVERED", "FALSE_POSITIVE", "CLOSED"].map((s) => (
                        <option key={s} value={s}>
                          {statusLabels[s] ?? s}
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
                  <ErrorState
                    message={listQ.error instanceof Error ? listQ.error.message : t("incidents.empty.list_failed", "incidents failed")}
                    onRetry={() => void listQ.refetch()}
                  />
                ) : incidents.length === 0 ? (
                  <EmptyState
                    message={t("incidents.empty.no_match", "No incidents match.")}
                    hint={t("incidents.empty.no_match_hint", "the store is empty or filters exclude everything")}
                  />
                ) : (
                  <DataTable
                    headers={[
                      { label: t("incidents.th.incident", "incident") },
                      { label: t("incidents.th.sev", "sev") },
                      { label: t("incidents.th.status", "status") },
                      { label: t("incidents.th.component", "component") },
                      { label: t("incidents.th.category", "category") },
                      { label: "×", num: true },
                      { label: t("incidents.th.last_seen", "last seen") },
                      { label: "" },
                    ]}
                  >
                    {incidents.map((i) => (
                      <tr key={i.id}>
                        <td className="inline-mono tiny inc-id" title={i.id}>
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
                            {t("incidents.action.open", "open")}
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
            <div className="inc-list-grid">
              <Panel title={t("incidents.panel.search", "Search incidents (bounded, deterministic)")} tight>
                <input
                  className="input"
                  style={{ width: "100%" }}
                  aria-label={t("incidents.search.a11y", "Search incidents")}
                  placeholder={t("incidents.search.ph", "query text (id / root cause / tag)")}
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
                <div style={{ marginTop: 8 }}>
                  {q.trim() === "" ? (
                    <EmptyState
                      message={t(
                        "incidents.empty.type_query",
                        "Type a query — the backend answers {available:true, incidents:[]} for empty queries.",
                      )}
                    />
                  ) : searchQ.isFetching ? (
                    <Skeleton count={2} />
                  ) : incidentsUseCases.voList(searchQ.data?.incidents ?? []).length === 0 ? (
                    <EmptyState message={t("incidents.empty.search_none", "No incidents matched.")} />
                  ) : (
                    <DataTable
                      headers={[
                        { label: t("incidents.th.incident", "incident") },
                        { label: t("incidents.th.sev", "sev") },
                        { label: t("incidents.th.status", "status") },
                        { label: "" },
                      ]}
                    >
                      {incidentsUseCases.voList(searchQ.data?.incidents ?? []).map((i) => (
                        <tr key={i.id}>
                          <td className="inline-mono tiny inc-id">{i.id.slice(0, 14)}</td>
                          <td>
                            <SeverityBadge severity={i.severity} />
                          </td>
                          <td>
                            <StatusBadge status={i.status} />
                          </td>
                          <td>
                            <button className="btn small ghost" onClick={() => setOpen(i.id)}>
                              {t("incidents.action.open", "open")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </DataTable>
                  )}
                </div>
              </Panel>
              <Panel title={t("incidents.panel.trace", "One-click trace (incident / ticket / run / model)")} tight>
                <input
                  className="input"
                  style={{ width: "100%" }}
                  aria-label={t("incidents.trace.a11y", "Related id filter")}
                  placeholder="incident_id | ticket | execution_id | order_id | model_id | research_run_id"
                  value={traceQ}
                  onChange={(e) => setTraceQ(e.target.value)}
                />
                <div style={{ marginTop: 8 }}>
                  {traceQ.trim() === "" ? (
                    <EmptyState
                      message={t(
                        "incidents.empty.trace_hint",
                        "Missing hops are reported as missing_link + reason — never fabricated.",
                      )}
                    />
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
              title={t("incidents.panel.lineage", "Value lineage (how a number is derived)")}
              right={
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    aria-label={t("incidents.lineage.field_a11y", "Lineage field")}
                    className="select"
                    style={{ width: 150 }}
                    value={lineageField}
                    onChange={(e) => setLineageField(e.target.value)}
                  >
                    {["pnl", "realized_r", "open_positions", "model_output"].map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    style={{ width: 110 }}
                    aria-label={t("incidents.lineage.ticket_a11y", "Ticket for lineage")}
                    placeholder={t("incidents.lineage.ph_ticket", "ticket (why)")}
                    value={lineageTicket}
                    onChange={(e) => setLineageTicket(e.target.value)}
                  />
                </div>
              }
              tight
            >
              {lineageQ.isPending ? (
                <Skeleton count={3} />
              ) : lineageQ.isError ? (
                <EmptyState
                  message={lineageQ.error instanceof Error ? lineageQ.error.message : t("incidents.empty.lineage_failed", "lineage failed")}
                />
              ) : (
                <>
                  <dl className="kv" style={{ marginBottom: 8 }}>
                    <InfoRow label={t("incidents.lineage.field", "field")} value={lineageQ.data?.field ?? "—"} />
                    <InfoRow label={t("incidents.lineage.source", "source")} value={lineageQ.data?.source ?? "—"} />
                    <InfoRow label={t("incidents.lineage.hops", "hops")} value={formatNumber(arr(lineageQ.data?.hops).length, 0)} />
                  </dl>
                  <DataTable
                    headers={[
                      { label: "#" },
                      { label: t("incidents.th.hop", "hop") },
                      { label: t("incidents.th.table_fn", "table / fn") },
                      { label: t("incidents.th.note", "note") },
                    ]}
                  >
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
                        {t("incidents.section.why", "why closed / why no learning (ticket {ticket})", { ticket: lineageTicket })}
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
              title={t("incidents.panel.probes", "Read-only forensic probes")}
              right={
                <select
                  aria-label={t("incidents.probe.a11y", "Forensic probe")}
                  className="select"
                  style={{ width: 150 }}
                  value={forensicKind}
                  onChange={(e) => setForensicKind(e.target.value)}
                >
                  <option value="accounting">{t("incidents.probe.accounting", "accounting")}</option>
                  <option value="timebase">{t("incidents.probe.timebase", "timebase")}</option>
                </select>
              }
              tight
            >
              {forensicsQ.isPending ? (
                <Skeleton count={3} />
              ) : forensicsQ.isError ? (
                <EmptyState
                  message={forensicsQ.error instanceof Error ? forensicsQ.error.message : t("incidents.empty.probe_failed", "probe failed")}
                />
              ) : (
                <JsonBlock value={forensicsQ.data} maxChars={5000} />
              )}
              <div className="tiny faint" style={{ marginTop: 6 }}>
                {t(
                  "incidents.note.probes",
                  "probes never write; classification counts come straight from the backend engine.",
                )}
              </div>
            </Panel>
          )}
        </div>
      </div>

      {open && <IncidentDrawer incidentId={open} onClose={() => setOpen(null)} />}

      {confirmReconcile && (
        <ConfirmModal
          title={t("incidents.confirm.title", "Run forensic audit (reconcile)")}
          danger
          confirmLabel={t("incidents.confirm.run", "Run audit")}
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
            {t(
              "incidents.confirm.body",
              "Runs 5 forensic probe families over the current audit DB and updates stored incident records (impact/evidence). It does not touch orders, positions, or the engine.",
            )}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
