/**
 * PURPOSE:  DECISIONS tab — filterable decision observatory + drilldown handoff.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.decisions — existing GET), ../model,
 *           @/components/primitives, @/lib/format, ../useCases.decisionKey
 * PROVIDES: DecisionsTab
 * INVARIANTS: skeleton / error+retry / ledger-unavailable / empty-filter states are
 *             all visible and honest; payload_ok:false rows are kept, flagged and
 *             inspect-disabled; confidence never rendered when null (NOT RECORDED).
 * EXTEND:   new filter = a qs() field that api.ts already declares.
 */
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { num, type OperatorDecisionRow } from "../../model";
import { controlCenterQueries, controlCenterUseCases } from "../../useCases";
import { useI18n } from "@/stores/i18nStore";

interface DecisionsTabProps {
  hours: number | undefined;
  actionFilter: string;
  search: string;
  onHours: (h: number) => void;
  onActionFilter: (v: string) => void;
  onSearch: (v: string) => void;
  onInspect: (id: number | null) => void;
}

export function DecisionsTab({ hours, actionFilter, search, onHours, onActionFilter, onSearch, onInspect }: DecisionsTabProps) {
  const t = useI18n((s) => s.t);
  const decisionsQ = useQuery({
    queryKey: ["control-center", "decisions", hours, actionFilter, search],
    queryFn: ({ signal }) =>
      controlCenterQueries.decisions({ hours, action: actionFilter || undefined, search: search || undefined, limit: 100 }, signal),
    retry: false,
  });

  return (
    <Panel
      title={t("control-center.panel.decisions", "Decision observatory (audit_signals, read-only)")}
      right={
        <div style={{ display: "flex", gap: 6 }}>
          <input
            className="input"
            style={{ width: 150 }}
            aria-label={t("control-center.a11y.search_decisions", "Search decisions")}
            placeholder={t("control-center.action.search", "search")}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
          <select
            aria-label={t("control-center.a11y.action_filter", "Action filter")}
            className="select"
            style={{ width: 120 }}
            value={actionFilter}
            onChange={(e) => onActionFilter(e.target.value)}
          >
            <option value="">{t("control-center.filter.action_any", "action: any")}</option>
            {["BUY", "SELL", "NO_TRADE"].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            aria-label={t("control-center.a11y.window_hours", "Window (hours)")}
            className="select"
            style={{ width: 96 }}
            value={String(hours ?? 72)}
            onChange={(e) => onHours(Number(e.target.value))}
          >
            {[1, 24, 72, 168].map((h) => (
              <option key={h} value={h}>
                {h}h
              </option>
            ))}
          </select>
        </div>
      }
      tight
    >
      {decisionsQ.isPending ? (
        <Skeleton count={5} />
      ) : decisionsQ.isError ? (
        <ErrorState
          message={decisionsQ.error instanceof Error ? decisionsQ.error.message : t("control-center.err.decisions", "decisions failed")}
          onRetry={() => void decisionsQ.refetch()}
        />
      ) : decisionsQ.data?.available === false ? (
        <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} />
      ) : (decisionsQ.data?.rows ?? []).length === 0 ? (
        <EmptyState message={t("control-center.empty.no_decisions", "No decisions match the filters.")} />
      ) : (
        <DataTable
          headers={[
            { label: t("control-center.th.id", "id"), num: true },
            { label: t("control-center.th.symbol", "symbol") },
            { label: t("control-center.th.action", "action") },
            { label: t("control-center.th.conf", "conf"), num: true },
            { label: t("control-center.th.stage", "stage") },
            { label: t("control-center.th.gate", "gate") },
            { label: t("control-center.th.reason", "reason") },
            { label: t("control-center.th.at", "at") },
            { label: "" },
          ]}
        >
          {(decisionsQ.data?.rows ?? []).map((r: OperatorDecisionRow) => (
            <tr key={controlCenterUseCases.decisionKey(r)}>
              <td className="num tiny">{r.id ?? "—"}</td>
              <td className="small">{r.symbol ?? "—"}</td>
              <td>
                <StatusBadge status={r.action} />
              </td>
              <td className="num tiny">{r.confidence == null ? t("control-center.truth.not_recorded", "NOT RECORDED") : formatNumber(r.confidence, 3)}</td>
              <td className="tiny">{r.decision_stage ?? "—"}</td>
              <td className="tiny">{r.blocked_by ?? ""}</td>
              <td className="tiny muted" title={r.reason_code ?? ""}>
                {(r.reason_code ?? "—").slice(0, 20)}
              </td>
              <td className="tiny">{formatDateTime(r.generated_at)}</td>
              <td>
                <button className="btn small ghost" disabled={r.payload_ok === false} onClick={() => onInspect(num(r.id) ?? null)}>
                  {r.payload_ok === false ? t("control-center.action.payload_bad", "payload ✗") : t("control-center.action.inspect", "inspect")}
                </button>
              </td>
            </tr>
          ))}
        </DataTable>
      )}
      <div className="tiny faint" style={{ marginTop: 6 }}>
        {t("control-center.decisions.footnote", "rows with unparseable payload are kept and flagged (never silently dropped) — inspect disabled for them by the backend contract.")}
      </div>
    </Panel>
  );
}
