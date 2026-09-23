/**
 * Incidents — incident record drawer: full detail + report export + evidence
 * zip link (href to the backend export route) + timeline/value traces.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, SeverityBadge, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { incidentZipHref } from "../api";
import { arr, bool, num, obj, str, type Row } from "../model";
import { incidentsQueries } from "../useCases";
import { Drawer, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";

export default function IncidentDrawer({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const [tab, setTab] = useState<"detail" | "timeline" | "report" | "traces">("detail");
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

  return (
    <Drawer title={`Incident ${incidentId}`} onClose={onClose}>
      <div className="inc-drawer-tabs">
        {(["detail", "timeline", "traces", "report"] as const).map((t) => (
          <button key={t} className={`btn small ${tab === t ? "primary" : "ghost"}`} aria-pressed={tab === t} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
        <a className="btn small" href={incidentZipHref(incidentId)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
          ⤓ evidence zip (href)
        </a>
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
      ) : tab === "detail" ? (
        <div style={{ display: "grid", gap: 10 }}>
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
          <Panel title="Where it happened" tight>
            <dl className="kv">
              <InfoRow label="component / operation" value={`${inc.component ?? "—"} / ${inc.operation ?? "—"}`} />
              <InfoRow label="category" value={inc.category ?? "—"} />
              <InfoRow label="correlation id" value={<span className="inline-mono tiny">{inc.correlation_id ?? "—"}</span>} />
              <InfoRow label="detected" value={formatDateTime(inc.detected_at)} />
              <InfoRow label="first / last seen" value={`${formatDateTime(inc.first_seen_at)} → ${formatDateTime(inc.last_seen_at)}`} />
              <InfoRow label="fingerprint" value={<span className="inline-mono tiny">{inc.fingerprint ?? "—"}</span>} />
              <InfoRow label="recovery state" value={<StatusPill status={inc.recovery_status} />} />
              <InfoRow label="recommended action" value={inc.recommended_action ?? "—"} />
              <InfoRow
                label="fix / regression test"
                value={`${inc.fix_commit || "—"} · ${inc.regression_test || "—"}`}
              />
              <InfoRow
                label="resolved without evidence"
                value={bool(inc.resolved_without_evidence) ? "YES (flagged)" : "no"}
              />
            </dl>
          </Panel>
          <Panel title="Impact (backend analyzer)" tight>
            {Object.keys(impact).length === 0 ? (
              <EmptyState message="no impact payload" />
            ) : (
              <dl className="kv">
                {Object.entries(impact).slice(0, 16).map(([k, v]) => (
                  <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                ))}
              </dl>
            )}
            <div className="tiny muted" style={{ marginTop: 6 }}>
              affected: {arr(inc.affected_records).length} records · {arr(inc.affected_models).length} models · tags{" "}
              {(inc.tags ?? []).join(", ") || "—"}
            </div>
          </Panel>
          <Panel title={`Evidence (${arr(inc.evidence).length})`} tight>
            {arr(inc.evidence).length === 0 ? (
              <EmptyState message="No evidence items attached." />
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {arr(inc.evidence)
                  .slice(0, 12)
                  .map((e, i) => (
                    <details key={i} className="inc-ev">
                      <summary className="tiny">
                        {str(e.kind) ?? str(e.label) ?? `evidence ${i + 1}`} · {formatDateTime(str(e.timestamp) ?? str(e.at))}
                      </summary>
                      <div style={{ marginTop: 4 }}>
                        <JsonBlock value={e} maxChars={1500} />
                      </div>
                    </details>
                  ))}
              </div>
            )}
          </Panel>
          <Panel title="Recovery plan" tight>
            <JsonBlock value={inc.recovery_plan} maxChars={1500} />
          </Panel>
        </div>
      ) : tab === "timeline" ? (
        <Panel title="Incident timeline" tight>
          {arr(inc.timeline).length === 0 ? (
            <EmptyState message="No timeline events recorded." />
          ) : (
            <DataTable headers={[{ label: "at" }, { label: "event" }, { label: "detail" }]}>
              {arr(inc.timeline)
                .slice()
                .reverse()
                .slice(0, 100)
                .map((t: Row, i: number) => (
                  <tr key={i}>
                    <td className="tiny">{formatDateTime(str(t.timestamp))}</td>
                    <td className="small">{str(t.event) ?? str(t.event_type) ?? "—"}</td>
                    <td className="tiny muted">{str(t.detail) ?? str(t.message) ?? ""}</td>
                  </tr>
                ))}
            </DataTable>
          )}
          <div className="section-title" style={{ marginTop: 10 }}>
            quarantine entries
          </div>
          {arr(inc.quarantine_entries).length === 0 ? (
            <EmptyState message="Nothing quarantined." />
          ) : (
            <JsonBlock value={inc.quarantine_entries} maxChars={2000} />
          )}
        </Panel>
      ) : tab === "traces" ? (
        <Panel title="Value traces (how each number got here)" tight>
          {arr(inc.value_traces).length === 0 ? (
            <EmptyState message="No value traces recorded for this incident." />
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {arr(inc.value_traces).map((t: Row, i: number) => (
                <div key={i} className="inc-trace">
                  <div className="small">
                    <b>{str(t.field) ?? "field"}</b> · <span className="muted tiny">{str(t.source) ?? "—"}</span>
                  </div>
                  <JsonBlock value={t.hops ?? t} maxChars={1200} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <Panel title="Report export (secret-masked)" tight>
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
                  <pre tabIndex={0} className="inline-mono tiny" style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>
                    {reportQ.data?.markdown ?? "—"}
                  </pre>
                </details>
              </>
            )}
          </Panel>
          <Panel title="Evidence bundle" tight>
            {zipQ.isPending ? (
              <Skeleton />
            ) : zipQ.data?.available === true ? (
              <dl className="kv">
                <InfoRow label="zip_path" value={<span className="inline-mono tiny">{zipQ.data.zip_path ?? "—"}</span>} />
                <InfoRow label="size" value={num(zipQ.data.size_bytes) === null ? "—" : `${formatNumber(num(zipQ.data.size_bytes)!, 0)} bytes`} />
                <InfoRow label="note" value={zipQ.data.note ?? "—"} />
              </dl>
            ) : (
              <EmptyState message="zip export not available" hint="the export route answered available:false" />
            )}
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
