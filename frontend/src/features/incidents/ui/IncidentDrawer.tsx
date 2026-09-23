/**
 * Incidents — incident record drawer: full detail + report export + evidence
 * zip link (href to the backend export route) + timeline/value traces.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton, SeverityBadge, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { incidentZipHref } from "../api";
import { arr, bool, num, obj, str, type Row } from "../model";
import { incidentsQueries } from "../useCases";
import { Drawer, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";

export default function IncidentDrawer({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const [tab, setTab] = useState<"detail" | "timeline" | "report" | "traces">("detail");
  const t = useI18n((s) => s.t);
  const tabLabels: Record<string, string> = {
    detail: t("incidents.drawer.tab_detail", "detail"),
    timeline: t("incidents.drawer.tab_timeline", "timeline"),
    traces: t("incidents.drawer.tab_traces", "traces"),
    report: t("incidents.drawer.tab_report", "report"),
  };
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
    <Drawer title={t("incidents.drawer.title", "Incident {id}", { id: incidentId })} onClose={onClose}>
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        {(["detail", "timeline", "traces", "report"] as const).map((t) => (
          <button key={t} className={`btn small ${tab === t ? "primary" : "ghost"}`} aria-pressed={tab === t} onClick={() => setTab(t)}>
            {tabLabels[t]}
          </button>
        ))}
        <a className="btn small" href={incidentZipHref(incidentId)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
          {t("incidents.drawer.zip_link", "⤓ evidence zip (href)")}
        </a>
      </div>

      {detailQ.isPending ? (
        <Skeleton count={4} />
      ) : detailQ.isError ? (
              <ErrorState
                message={detailQ.error instanceof Error ? detailQ.error.message : t("incidents.drawer.detail_unavailable", "incident detail unavailable")}
                onRetry={() => void detailQ.refetch()}
              />
      ) : detailQ.data?.available === false || !inc ? (
        <EmptyState
          message={t("incidents.drawer.not_found", "incident not found")}
          hint={str(detailQ.data?.error) ?? t("incidents.drawer.not_found_hint", "the store answered available:false")}
        />
      ) : tab === "detail" ? (
        <div style={{ display: "grid", gap: 10 }}>
          <div className="grid cols-4">
            <MetricCard label={t("incidents.drawer.severity", "severity")} value={<SeverityBadge severity={inc.severity} />} />
            <MetricCard label={t("incidents.drawer.status", "status")} value={<StatusBadge status={inc.status} />} />
            <MetricCard
              label={t("incidents.drawer.root_cause", "root cause")}
              value={<StatusPill status={inc.root_cause_status} />}
              sub={inc.root_cause ?? t("incidents.drawer.no_attr", "not yet attributed")}
            />
            <MetricCard
              label={t("incidents.drawer.repeated", "repeated")}
              value={formatNumber(inc.repeated_count ?? 1, 0)}
              tone={bool(inc.is_regression) ? "neg" : "dim"}
              sub={
                inc.related_bug_id
                  ? t("incidents.drawer.linked_bug", "linked {id}", { id: inc.related_bug_id })
                  : t("incidents.drawer.no_bug", "no bug linkage")
              }
            />
          </div>
          <Panel title={t("incidents.drawer.where", "Where it happened")} tight>
            <dl className="kv">
              <InfoRow label={t("incidents.drawer.component_op", "component / operation")} value={`${inc.component ?? "—"} / ${inc.operation ?? "—"}`} />
              <InfoRow label={t("incidents.drawer.category", "category")} value={inc.category ?? "—"} />
              <InfoRow
                label={t("incidents.drawer.correlation", "correlation id")}
                value={<span className="inline-mono tiny">{inc.correlation_id ?? "—"}</span>}
              />
              <InfoRow label={t("incidents.drawer.detected", "detected")} value={formatDateTime(inc.detected_at)} />
              <InfoRow
                label={t("incidents.drawer.first_last", "first / last seen")}
                value={`${formatDateTime(inc.first_seen_at)} → ${formatDateTime(inc.last_seen_at)}`}
              />
              <InfoRow
                label={t("incidents.drawer.fingerprint", "fingerprint")}
                value={<span className="inline-mono tiny">{inc.fingerprint ?? "—"}</span>}
              />
              <InfoRow label={t("incidents.drawer.recovery_state", "recovery state")} value={<StatusPill status={inc.recovery_status} />} />
              <InfoRow label={t("incidents.drawer.recommended", "recommended action")} value={inc.recommended_action ?? "—"} />
              <InfoRow
                label={t("incidents.drawer.fix_test", "fix / regression test")}
                value={`${inc.fix_commit || "—"} · ${inc.regression_test || "—"}`}
              />
              <InfoRow
                label={t("incidents.drawer.resolved_wo", "resolved without evidence")}
                value={
                  bool(inc.resolved_without_evidence)
                    ? t("incidents.drawer.yes_flagged", "YES (flagged)")
                    : t("incidents.drawer.no", "no")
                }
              />
            </dl>
          </Panel>
          <Panel title={t("incidents.drawer.impact", "Impact (backend analyzer)")} tight>
            {Object.keys(impact).length === 0 ? (
              <EmptyState message={t("incidents.drawer.no_impact", "no impact payload")} />
            ) : (
              <dl className="kv">
                {Object.entries(impact).slice(0, 16).map(([k, v]) => (
                  <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                ))}
              </dl>
            )}
            <div className="tiny muted" style={{ marginTop: 6 }}>
              {t("incidents.drawer.affected", "affected: {records} records · {models} models · tags {tags}", {
                records: arr(inc.affected_records).length,
                models: arr(inc.affected_models).length,
                tags: (inc.tags ?? []).join(", ") || "—",
              })}
            </div>
          </Panel>
          <Panel title={t("incidents.drawer.evidence", "Evidence ({n})", { n: arr(inc.evidence).length })} tight>
            {arr(inc.evidence).length === 0 ? (
              <EmptyState message={t("incidents.drawer.no_evidence", "No evidence items attached.")} />
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {arr(inc.evidence)
                  .slice(0, 12)
                  .map((e, i) => (
                    <details key={i} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "4px 8px" }}>
                      <summary className="tiny">
                        {str(e.kind) ?? str(e.label) ?? t("incidents.drawer.evidence_n", "evidence {n}", { n: i + 1 })} ·{" "}
                        {formatDateTime(str(e.timestamp) ?? str(e.at))}
                      </summary>
                      <div style={{ marginTop: 4 }}>
                        <JsonBlock value={e} maxChars={1500} />
                      </div>
                    </details>
                  ))}
              </div>
            )}
          </Panel>
          <Panel title={t("incidents.drawer.recovery_plan", "Recovery plan")} tight>
            <JsonBlock value={inc.recovery_plan} maxChars={1500} />
          </Panel>
        </div>
      ) : tab === "timeline" ? (
        <Panel title={t("incidents.drawer.timeline", "Incident timeline")} tight>
          {arr(inc.timeline).length === 0 ? (
            <EmptyState message={t("incidents.drawer.no_timeline", "No timeline events recorded.")} />
          ) : (
            <DataTable
              headers={[
                { label: t("incidents.th.at", "at") },
                { label: t("incidents.th.event", "event") },
                { label: t("incidents.th.detail", "detail") },
              ]}
            >
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
            {t("incidents.drawer.quarantine", "quarantine entries")}
          </div>
          {arr(inc.quarantine_entries).length === 0 ? (
            <EmptyState message={t("incidents.drawer.quarantine_none", "Nothing quarantined.")} />
          ) : (
            <JsonBlock value={inc.quarantine_entries} maxChars={2000} />
          )}
        </Panel>
      ) : tab === "traces" ? (
        <Panel title={t("incidents.drawer.traces", "Value traces (how each number got here)")} tight>
          {arr(inc.value_traces).length === 0 ? (
            <EmptyState message={t("incidents.drawer.no_traces", "No value traces recorded for this incident.")} />
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {arr(inc.value_traces).map((tr: Row, i: number) => (
                <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "6px 8px" }}>
                  <div className="small">
                    <b>{str(tr.field) ?? t("incidents.drawer.field_fallback", "field")}</b> · <span className="muted tiny">{str(tr.source) ?? "—"}</span>
                  </div>
                  <JsonBlock value={tr.hops ?? tr} maxChars={1200} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <Panel title={t("incidents.drawer.report", "Report export (secret-masked)")} tight>
            {reportQ.isPending ? (
              <Skeleton count={2} />
            ) : reportQ.data?.available === false ? (
              <EmptyState message={str(reportQ.data?.error) ?? t("incidents.drawer.report_unavailable", "report unavailable")} />
            ) : (
              <>
                <div className="tiny muted" style={{ marginBottom: 6 }}>
                  {t("incidents.drawer.report_note", "incident_json + incident_markdown produced server-side (secret-masked).")}
                </div>
                <details>
                  <summary className="small">{t("incidents.drawer.markdown_report", "markdown report")}</summary>
                  <pre tabIndex={0} className="inline-mono tiny" style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>
                    {reportQ.data?.markdown ?? "—"}
                  </pre>
                </details>
              </>
            )}
          </Panel>
          <Panel title={t("incidents.drawer.bundle", "Evidence bundle")} tight>
            {zipQ.isPending ? (
              <Skeleton />
            ) : zipQ.data?.available === true ? (
              <dl className="kv">
                <InfoRow label="zip_path" value={<span className="inline-mono tiny">{zipQ.data.zip_path ?? "—"}</span>} />
                <InfoRow
                  label={t("incidents.drawer.size", "size")}
                  value={
                    num(zipQ.data.size_bytes) === null
                      ? "—"
                      : t("incidents.drawer.size_value", "{n} bytes", { n: formatNumber(num(zipQ.data.size_bytes)!, 0) })
                  }
                />
                <InfoRow label={t("incidents.drawer.note", "note")} value={zipQ.data.note ?? "—"} />
              </dl>
            ) : (
              <EmptyState
                message={t("incidents.drawer.zip_unavailable", "zip export not available")}
                hint={t("incidents.drawer.zip_unavailable_hint", "the export route answered available:false")}
              />
            )}
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
