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
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { num, type OperatorDecisionRow } from "../../model";
import { controlCenterQueries, controlCenterUseCases } from "../../useCases";

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
  const decisionsQ = useQuery({
    queryKey: ["control-center", "decisions", hours, actionFilter, search],
    queryFn: ({ signal }) =>
      controlCenterQueries.decisions({ hours, action: actionFilter || undefined, search: search || undefined, limit: 100 }, signal),
    retry: false,
  });

  // perf: rows derivation (identity guard on the payload array) + the row
  // map chain memoized together — the parent page re-renders on every 15s
  // summary tick while this payload is unchanged, and the chain is the hot
  // path (up to 100 rows x decisionKey + formatters).
  const rows = useMemo(() => decisionsQ.data?.rows ?? [], [decisionsQ.data]);
  const rowEls = useMemo(
    () =>
      rows.map((r: OperatorDecisionRow) => (
        <tr key={controlCenterUseCases.decisionKey(r)}>
          <td className="num tiny">{r.id ?? "—"}</td>
          <td className="small">{r.symbol ?? "—"}</td>
          <td>
            <StatusBadge status={r.action} />
          </td>
          <td className="num tiny">{r.confidence == null ? "NOT RECORDED" : formatNumber(r.confidence, 3)}</td>
          <td className="tiny">{r.decision_stage ?? "—"}</td>
          <td className="tiny">{r.blocked_by ?? ""}</td>
          <td className="tiny muted" title={r.reason_code ?? ""}>
            {(r.reason_code ?? "—").slice(0, 20)}
          </td>
          <td className="tiny">{formatDateTime(r.generated_at)}</td>
          <td>
            <button className="btn small ghost" disabled={r.payload_ok === false} onClick={() => onInspect(num(r.id) ?? null)}>
              {r.payload_ok === false ? "payload ✗" : "inspect"}
            </button>
          </td>
        </tr>
      )),
    [rows, onInspect],
  );

  return (
    <Panel
      title="Decision observatory (audit_signals, read-only)"
      right={
        <div style={{ display: "flex", gap: 6 }}>
          <input
            className="input"
            style={{ width: 150 }}
            aria-label="Search decisions"
            placeholder="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
          <select
            aria-label="Action filter"
            className="select"
            style={{ width: 120 }}
            value={actionFilter}
            onChange={(e) => onActionFilter(e.target.value)}
          >
            <option value="">action: any</option>
            {["BUY", "SELL", "NO_TRADE"].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            aria-label="Window (hours)"
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
          message={decisionsQ.error instanceof Error ? decisionsQ.error.message : "decisions failed"}
          onRetry={() => void decisionsQ.refetch()}
        />
      ) : decisionsQ.data?.available === false ? (
        <EmptyState message="ledger unavailable" />
      ) : rows.length === 0 ? (
        <EmptyState message="No decisions match the filters." />
      ) : (
        <DataTable
          headers={[
            { label: "id", num: true },
            { label: "symbol" },
            { label: "action" },
            { label: "conf", num: true },
            { label: "stage" },
            { label: "gate" },
            { label: "reason" },
            { label: "at" },
            { label: "" },
          ]}
        >
          {rowEls}
        </DataTable>
      )}
      <div className="tiny faint" style={{ marginTop: 6 }}>
        rows with unparseable payload are kept and flagged (never silently dropped) — inspect disabled for them by the backend contract.
      </div>
    </Panel>
  );
}
