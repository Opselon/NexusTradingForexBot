/**
 * PURPOSE:  Incident record drawer restyled as an EVIDENCE DOSSIER — grouped
 *           sections (what / when / impact / evidence / refs) with a clear
 *           visual hierarchy and monospace ids; actions and behavior are the
 *           pre-existing ones (detail/timeline/traces/report tabs, report
 *           export, evidence-zip href) — restyle only, no mutation changes.
 * OWNER:    uiux-w6-incidents  (future edits belong to this lane)
 * CONSUMES: @/components/primitives (MetricCard/Panel/Skeleton/ErrorState/
 *           EmptyState/SeverityBadge/StatusBadge/DataTable),
 *           features/research/ui/lane5Kit (Drawer/InfoRow/JsonBlock/StatusPill
 *           — READ-ONLY shared kit), features/incidents/api (zip href),
 *           features/incidents/model + useCases (existing queries),
 *           features/incidents/ui/incidentLook (chip/heat classes),
 *           styles/theme.css tokens via ./incidents.css (prefix inc-).
 * PROVIDES: default export IncidentDrawer — same props as before.
 * INVARIANTS: query keys, fetch enablement, export href, empty/error strings
 *             and the confirm-free read-only behavior are unchanged; every
 *             section renders only when its backend fields exist; a group
 *             with no backend payload shows the pre-existing EmptyState.
 * EXTEND:   add a dossier group in renderDetail reading a model.ts field;
 *           never add a network call (this wave is UI-only).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, MetricCard, Panel, Skeleton, SeverityBadge, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { incidentZipHref } from "../api";
import { arr, bool, num, obj, str, type IncidentDto, type Row } from "../model";
import { incidentsQueries } from "../useCases";
import { Drawer, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { evAt, evKind, sevClass } from "./incidentLook";
import StatusChip from "./StatusChip";
import "./incidents-command.css";

type Tab = "detail" | "timeline" | "traces" | "report";

/** Timeline tab: backend events as a vertical rail (newest first, as before). */
function TimelineRail({ inc }: { inc: IncidentDto }) {
  // perf(7): the rail's slice/reverse/map chain is memoized on the exact
  // payload array it reads — identical <li> rows, rebuilt only on payload change.
  const items = useMemo(
    () =>
      arr(inc.timeline)
        .slice()
        .reverse()
        .slice(0, 100)
        .map((t: Row, i: number) => {
          const ev = str(t.event) ?? str(t.event_type) ?? "—";
          return (
            <li key={i} className="inc-rail-item inc-sev-unknown">
              <div className="inc-rail-btn" style={{ cursor: "default" }}>
                <span className="inc-rail-dot" aria-hidden="true">
                  <i />
                </span>
                <time className="inc-rail-time" dateTime={str(t.timestamp) ?? undefined}>
                  {formatDateTime(str(t.timestamp))}
                </time>
                <span className="inc-rail-body">
                  <span className="inc-rail-id">{ev}</span>
                  <span className="inc-rail-meta">{str(t.detail) ?? str(t.message) ?? ""}</span>
                </span>
              </div>
            </li>
          );
        }),
    [inc?.timeline],
  );
  if (arr(inc.timeline).length === 0) return <EmptyState message="No timeline events recorded." />;
  return <ol className="inc-rail">{items}</ol>;
}

export default function IncidentDrawer({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("detail");
  const detailQ = useQuery({
    queryKey: ["incidents", "detail", incidentId],
    queryFn: ({ signal }) => incidentsQueries.detail(incidentId, signal),
    retry: false,
  });
  const reportQ = useQuery({
    queryKey: ["incidents", "report", incidentId],
    queryFn: ({ signal }) => incidentsQueries.report(incidentId, signal),
    retry: false,
    enabled: tab === "report",
  });
  const zipQ = useQuery({
    queryKey: ["incidents", "zip", incidentId],
    queryFn: ({ signal }) => incidentsQueries.zip(incidentId, signal),
    retry: false,
    enabled: tab === "report",
  });

  const inc = detailQ.data?.incident;
  const impact = obj(inc?.impact);
  const evidence = arr(inc?.evidence);
  const sev = inc ? sevClass(inc.severity) : "unknown";

  // perf(7): payload-array chains (entries/slice/reverse/map) memoized with
  // deps = the exact incident arrays they read — identical rows, no rebuild
  // while the payload identity holds (tab switches reuse the memo too).
  const impactRows = useMemo(
    () =>
      Object.entries(impact)
        .slice(0, 16)
        .map(([k, v]) => <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />),
    [impact],
  );
  const evidenceRows = useMemo(
    () =>
      arr(inc?.evidence)
        .slice(0, 12)
        .map((e, i) => (
          <details key={i}>
            <summary className="tiny">
              {evKind(e) || `evidence ${i + 1}`} · {formatDateTime(evAt(e) || null)}
            </summary>
            <div style={{ marginTop: 4 }}>
              <JsonBlock value={e} maxChars={1500} />
            </div>
          </details>
        )),
    [inc?.evidence],
  );
  const valueTraceRows = useMemo(
    () =>
      arr(inc?.value_traces).map((t: Row, i: number) => (
        <details key={i}>
          <summary className="small">
            <b>{str(t.field) ?? "field"}</b> · <span className="muted tiny">{str(t.source) ?? "—"}</span>
          </summary>
          <div style={{ marginTop: 4 }}>
            <JsonBlock value={t.hops ?? t} maxChars={1200} />
          </div>
        </details>
      )),
    [inc?.value_traces],
  );

  return (
    <Drawer title={`Incident ${incidentId}`} onClose={onClose}>
      <div className="inc-dossier">
        <div className={`inc-dossier__head inc-sev-${sev}`}>
          {detailQ.isPending ? null : inc ? (
            <>
              <SeverityBadge severity={inc.severity} />
              <StatusChip status={inc.status} />
              {str(inc.root_cause_status) ? <StatusPill status={inc.root_cause_status} /> : null}
              {bool(inc.is_regression) ? <span className="inc-card__x">REGRESSION</span> : null}
            </>
          ) : (
            <span className="inc-dossier__id">{incidentId}</span>
          )}
          <span className="inc-dossier__actions">
            <a className="btn small" href={incidentZipHref(incidentId)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              ⤓ evidence zip (href)
            </a>
          </span>
        </div>

        <div className="inc-dossier__tabs" role="tablist" aria-label="Incident sections">
          {(["detail", "timeline", "traces", "report"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              className={`btn small ${tab === t ? "primary" : "ghost"}`}
              aria-pressed={tab === t}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>

        {detailQ.isPending ? (
          <Skeleton count={4} />
        ) : detailQ.isError ? (
          <ErrorState
            message={detailQ.error instanceof Error ? detailQ.error.message : "incident detail unavailable"}
            onRetry={() => void detailQ.refetch()}
          />
        ) : detailQ.data?.available === false || !inc ? (
          <EmptyState message="incident not found" hint={str(detailQ.data?.error) ?? "the store answered available:false"} />
        ) : (
          <>
            {tab === "detail" && (
              <div className="inc-dossier" style={{ gap: 10 }}>
                <div className="grid cols-4">
                  <MetricCard label="severity" value={<SeverityBadge severity={inc.severity} />} />
                  <MetricCard label="status" value={<StatusBadge status={inc.status} />} />
                  <MetricCard label="root cause" value={<StatusPill status={inc.root_cause_status} />} sub={inc.root_cause ?? "not yet attributed"} />
                  <MetricCard
                    label="repeated"
                    value={formatNumber(inc.repeated_count ?? 1, 0)}
                    tone={bool(inc.is_regression) ? "neg" : "dim"}
                    sub={inc.related_bug_id ? `linked ${inc.related_bug_id}` : "no bug linkage"}
                  />
                </div>

                <Panel title="What happened" tight accent>
                  <dl className="kv" style={{ padding: "10px 14px" }}>
                    <InfoRow label="component / operation" value={`${inc.component ?? "—"} / ${inc.operation ?? "—"}`} />
                    <InfoRow label="category" value={inc.category ?? "—"} />
                    <InfoRow label="correlation id" value={<span className="inline-mono tiny">{inc.correlation_id ?? "—"}</span>} />
                    <InfoRow label="fingerprint" value={<span className="inline-mono tiny">{inc.fingerprint ?? "—"}</span>} />
                  </dl>
                </Panel>

                <Panel title="When" tight accent>
                  <dl className="kv" style={{ padding: "10px 14px" }}>
                    <InfoRow label="detected" value={formatDateTime(inc.detected_at)} />
                    <InfoRow label="first / last seen" value={`${formatDateTime(inc.first_seen_at)} → ${formatDateTime(inc.last_seen_at)}`} />
                  </dl>
                </Panel>

                <Panel title="Impact (backend analyzer)" tight accent>
                  {Object.keys(impact).length === 0 ? (
                    <EmptyState message="no impact payload" />
                  ) : (
                    <dl className="kv" style={{ padding: "10px 14px" }}>{impactRows}</dl>
                  )}
                  <div className="tiny muted" style={{ margin: "6px 14px 10px" }}>
                    affected: {arr(inc.affected_records).length} records · {arr(inc.affected_models).length} models · tags{" "}
                    {(inc.tags ?? []).join(", ") || "—"}
                  </div>
                </Panel>

                <Panel title={`Evidence (${evidence.length})`} tight accent>
                  {evidence.length === 0 ? (
                    <div style={{ padding: "10px 14px" }}>
                      <EmptyState message="No evidence items attached." />
                    </div>
                  ) : (
                    <div style={{ display: "grid", gap: 6, padding: "10px 14px" }}>
                      {evidenceRows}
                    </div>
                  )}
                </Panel>

                <Panel title="Recovery plan" tight accent>
                  <div style={{ padding: "10px 14px" }}>
                    <JsonBlock value={inc.recovery_plan} maxChars={1500} />
                  </div>
                </Panel>

                <Panel title="Refs" tight accent>
                  <dl className="kv" style={{ padding: "10px 14px" }}>
                    <InfoRow label="recommended action" value={inc.recommended_action ?? "—"} />
                    <InfoRow label="fix / regression test" value={`${inc.fix_commit || "—"} · ${inc.regression_test || "—"}`} />
                    <InfoRow label="resolved without evidence" value={bool(inc.resolved_without_evidence) ? "YES (flagged)" : "no"} />
                  </dl>
                </Panel>
              </div>
            )}

            {tab === "timeline" && (
              <Panel title="Incident timeline" tight accent>
                <div style={{ padding: "8px 6px" }}>
                  <TimelineRail inc={inc} />
                </div>
                <div className="section-title" style={{ margin: "10px 14px 4px" }}>
                  quarantine entries
                </div>
                {arr(inc.quarantine_entries).length === 0 ? (
                  <div style={{ padding: "0 14px 10px" }}>
                    <EmptyState message="Nothing quarantined." />
                  </div>
                ) : (
                  <div style={{ padding: "0 14px 10px" }}>
                    <JsonBlock value={inc.quarantine_entries} maxChars={2000} />
                  </div>
                )}
              </Panel>
            )}

            {tab === "traces" && (
              <Panel title="Value traces (how each number got here)" tight accent>
                {arr(inc.value_traces).length === 0 ? (
                  <div style={{ padding: "10px 14px" }}>
                    <EmptyState message="No value traces recorded for this incident." />
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: 8, padding: "10px 14px" }}>
                    {valueTraceRows}
                  </div>
                )}
              </Panel>
            )}

            {tab === "report" && (
              <div className="inc-dossier" style={{ gap: 10 }}>
                <Panel title="Report export (secret-masked)" tight accent>
                  <div style={{ padding: "10px 14px" }}>
                    {reportQ.isPending ? (
                      <Skeleton count={2} />
                    ) : reportQ.data?.available === false ? (
                      <EmptyState message={str(reportQ.data?.error) ?? "report unavailable"} />
                    ) : (
                      <>
                        <div className="tiny muted" style={{ marginBottom: 6 }}>
                          incident_json + incident_markdown produced server-side (secret-masked).
                        </div>
                        <details>
                          <summary className="small">markdown report</summary>
                          <pre
                            tabIndex={0}
                            className="inline-mono tiny"
                            style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}
                          >
                            {reportQ.data?.markdown ?? "—"}
                          </pre>
                        </details>
                      </>
                    )}
                  </div>
                </Panel>
                <Panel title="Evidence bundle" tight accent>
                  <div style={{ padding: "10px 14px" }}>
                    {zipQ.isPending ? (
                      <Skeleton />
                    ) : zipQ.data?.available === true ? (
                      <dl className="kv">
                        <InfoRow label="zip_path" value={<span className="inline-mono tiny">{zipQ.data.zip_path ?? "—"}</span>} />
                        <InfoRow
                          label="size"
                          value={num(zipQ.data.size_bytes) === null ? "—" : `${formatNumber(num(zipQ.data.size_bytes)!, 0)} bytes`}
                        />
                        <InfoRow label="note" value={zipQ.data.note ?? "—"} />
                      </dl>
                    ) : (
                      <EmptyState message="zip export not available" hint="the export route answered available:false" />
                    )}
                  </div>
                </Panel>
              </div>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
