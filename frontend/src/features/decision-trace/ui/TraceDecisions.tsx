/**
 * TraceDecisions — the decision feed / historical search (§39-42, §80).
 *
 * Rows come only from the backend `/decisions` endpoint (each row is a real
 * decision record). Search/filter are client-side views over those rows —
 * a row that does not match renders nothing, it is never fabricated.
 * Double-click stages a row for the A/B compare (§37).
 */

import { memo } from "react";
import { EmptyState, LoadingState, Segmented } from "@/components/primitives";
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
}

const FILTERS: Array<{ id: TraceFilter; label: string }> = [
  { id: "ALL", label: "All" },
  { id: "ACTIVE", label: "Active" },
  { id: "PASSED", label: "Passed" },
  { id: "REJECTED", label: "Rejected" },
  { id: "ERROR", label: "Error" },
];

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
      title={`${row.decision_id ?? "?"} · ${row.symbol ?? "—"} · ${row.model_id ?? "UNKNOWN"} — double-click for compare`}
    >
      <span className="dt-d-time">{shortTime(row.recorded_at)}</span>
      <span className={`dt-d-status ${tone}`}>{status}</span>
      <span className="dt-d-sym">{row.symbol ?? "—"}</span>
      <span className="dt-d-model" title={row.model_id ?? undefined}>{row.model_id ?? "UNKNOWN"}</span>
      <span className="dt-d-contract">{row.contract ?? "UNKNOWN"}</span>
      <span className="dt-d-regime">{row.regime ?? "UNKNOWN"}</span>
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
}: Props) {
  return (
    <section className="dt-decisions" aria-label="Decision feed">
      <header className="dt-dec-head">
        <span className="dt-dec-title">Decisions</span>
        <span className="dt-dec-count">{total} recorded · showing {rows.length}</span>
        <input
          className="dt-dec-search input"
          placeholder="search id / symbol / model / reason…"
          value={search}
          aria-label="Search decisions"
          onChange={(e) => onSearch(e.target.value)}
        />
        <div className="dt-dec-filter">
          <Segmented options={FILTERS} value={filter} onChange={(f) => onFilter(f)} />
        </div>
      </header>
      {loading ? (
        <LoadingState label="Loading recorded decisions…" />
      ) : !rows.length ? (
        <EmptyState
          message="No decisions recorded in this session."
          hint="Decisions appear as the engine evaluates market conditions against the model."
        />
      ) : (
        <div className="dt-d-list">
          {rows.map((row) => (
            <Row
              key={row.decision_id ?? row.trace_id ?? Math.random()}
              row={row}
              selected={selectedId === row.decision_id}
              onSelect={onSelect}
              onDouble={(r) => {
                if (!onComparePair) return;
                onComparePair([r, r]);
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}
