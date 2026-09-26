/**
 * TraceDecisions — the decision feed / historical search (§39-42, §80).
 *
 * Rows come only from the backend `/decisions` endpoint (each row is a real
 * decision record). Search/filter are client-side views over those rows —
 * a row that does not match renders nothing, it is never fabricated.
 * Double-click stages a row for the A/B compare (§37).
 */

import { memo, useCallback, useMemo } from "react";
import { EmptyState, ErrorState, LoadingState, Segmented } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { DecisionRow } from "../types";
import type { TraceFilter } from "../store";

interface Props {
  rows: DecisionRow[];
  total: number;
  loading: boolean;
  filter: TraceFilter;
  search: string;
  onFilter: (f: TraceFilter) => void;
  onSearch: (q: string) => void;
  onSelect: (row: DecisionRow) => void;
  selectedId: string | null;
  onComparePair?: (pair: [DecisionRow, DecisionRow] | null) => void;
  /** Backend's own words for a failed /decisions fetch (never "no decisions"). */
  error?: string | null;
  /** request_id the backend attached to that failure — audit trail. */
  requestId?: string | null;
}

const FILTERS: Array<{ id: TraceFilter; label: string }> = [
  { id: "ALL", label: "All" },
  { id: "ACTIVE", label: "Active" },
  { id: "PASSED", label: "Passed" },
  { id: "REJECTED", label: "Rejected" },
  { id: "ERROR", label: "Error" },
];

/** Static switch so the gate sees every key as a literal. */
function filterLabel(t: (key: string, fallback: string, vars?: Record<string, string | number>) => string, id: TraceFilter): string {
  switch (id) {
    case "ALL": return t("trace.filter.all", "All");
    case "ACTIVE": return t("trace.filter.active", "Active");
    case "PASSED": return t("trace.filter.passed", "Passed");
    case "REJECTED": return t("trace.filter.rejected", "Rejected");
    case "ERROR": return t("trace.filter.error", "Error");
    default: return id;
  }
}

/** Static switch so the gate sees every key as a literal. Unknown tokens
 *  fall back to the verbatim backend value (technical identifiers stay raw). */
function decisionStatusLabel(t: (key: string, fallback: string, vars?: Record<string, string | number>) => string, status: string): string {
  switch (status) {
    case "ERROR": return t("trace.dstatus.error", "ERROR");
    case "FAILED": return t("trace.dstatus.failed", "FAILED");
    case "APPROVED": return t("trace.dstatus.approved", "APPROVED");
    case "EXECUTED": return t("trace.dstatus.executed", "EXECUTED");
    case "DISPATCHED": return t("trace.dstatus.dispatched", "DISPATCHED");
    case "REJECTED": return t("trace.dstatus.rejected", "REJECTED");
    case "NO_TRADE": return t("trace.dstatus.no_trade", "NO_TRADE");
    case "PENDING": return t("trace.dstatus.pending", "PENDING");
    case "UNKNOWN": return t("trace.marker.unknown", "UNKNOWN");
    default: return status;
  }
}

const Row = memo(function Row({
  row,
  selected,
  onSelect,
  onDouble,
}: {
  row: DecisionRow;
  selected: boolean;
  onSelect: (r: DecisionRow) => void;
  onDouble: (r: DecisionRow) => void;
}) {
  const t = useI18n((s) => s.t);
  const status = (row.status ?? "UNKNOWN").toUpperCase();
  const tone =
    status === "ERROR" || status === "FAILED"
      ? "error"
      : status === "APPROVED" || status === "EXECUTED" || status === "DISPATCHED"
        ? "pass"
        : status === "REJECTED" || status === "NO_TRADE"
          ? "reject"
          : "neutral";
  const reason = row.rejection_reason ?? row.reason_code ?? row.reason;
  return (
    <button
      className={`dt-d-row ${tone} ${selected ? "selected" : ""}`}
      onClick={() => onSelect(row)}
      onDoubleClick={() => onDouble(row)}
      title={`${row.decision_id ?? "?"} · ${row.symbol ?? "—"} · ${row.model_id ?? t("trace.marker.unknown", "UNKNOWN")} — ${t("trace.decisions.row_hint", "double-click for compare")}`}
    >
      <span className="dt-d-time">{shortTime(row.recorded_at)}</span>
      <span className={`dt-d-status ${tone}`}>{decisionStatusLabel(t, status)}</span>
      <span className="dt-d-sym">{row.symbol ?? "—"}</span>
      <span className="dt-d-model" title={row.model_id ?? undefined}>{row.model_id ?? t("trace.marker.unknown", "UNKNOWN")}</span>
      <span className="dt-d-contract">{row.contract ?? t("trace.marker.unknown", "UNKNOWN")}</span>
      <span className="dt-d-regime">{row.regime ?? t("trace.marker.unknown", "UNKNOWN")}</span>
      <span className="dt-d-reason" title={reason ?? ""}>{reason ?? ""}</span>
    </button>
  );
});

function shortTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return (
      String(d.getHours()).padStart(2, "0") +
      ":" +
      String(d.getMinutes()).padStart(2, "0") +
      ":" +
      String(d.getSeconds()).padStart(2, "0")
    );
  } catch {
    return ts;
  }
}

export function TraceDecisions({
  rows,
  total,
  loading,
  filter,
  search,
  onFilter,
  onSearch,
  onSelect,
  selectedId,
  onComparePair,
  error,
  requestId,
}: Props) {
  const t = useI18n((s) => s.t);
  /** Stable identity so memo(Row) is not defeated by a fresh closure per row. */
  const handleCompare = useCallback(
    (row: DecisionRow) => {
      onComparePair?.([row, row]);
    },
    [onComparePair],
  );
  /** Filter labels re-localize only when the locale changes, not per render. */
  const filterOptions = useMemo(
    () => FILTERS.map((f) => ({ id: f.id, label: filterLabel(t, f.id) })),
    [t],
  );
  return (
    <section className="dt-decisions" aria-label={t("trace.decisions.region", "Decision feed")}>
      <header className="dt-dec-head">
        <span className="dt-dec-title">{t("trace.decisions.title", "Decisions")}</span>
        <span className="dt-dec-count">{t("trace.decisions.count", "{total} recorded · showing {shown}", { total, shown: rows.length })}</span>
        <input
          className="dt-dec-search input"
          placeholder={t("trace.decisions.search_placeholder", "search id / symbol / model / reason…")}
          value={search}
          aria-label={t("trace.decisions.search_aria", "Search decisions")}
          onChange={(e) => onSearch(e.target.value)}
        />
        <div className="dt-dec-filter">
          <Segmented options={filterOptions} value={filter} onChange={(f) => onFilter(f)} />
        </div>
      </header>
      {loading ? (
        <LoadingState label={t("trace.decisions.loading", "Loading recorded decisions…")} />
      ) : error ? (
        <ErrorState message={error} requestId={requestId} />
      ) : !rows.length ? (
        <EmptyState
          message={t("trace.decisions.empty_message", "No decisions recorded in this session.")}
          hint={t("trace.decisions.empty_hint", "Decisions appear as the engine evaluates market conditions against the model.")}
        />
      ) : (
        <div className="dt-d-list">
          {rows.map((row, i) => (
            <Row
              key={row.decision_id ?? row.trace_id ?? `${row.recorded_at ?? "row"}-${i}`}
              row={row}
              selected={selectedId === row.decision_id}
              onSelect={onSelect}
              onDouble={handleCompare}
            />
          ))}
        </div>
      )}
    </section>
  );
}
