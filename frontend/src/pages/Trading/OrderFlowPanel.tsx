/**
 * PURPOSE:  Dispatch order-flow section (audit_orders + backend latency stats):
 *           sortable table of the dispatched orders with CSV export of exactly
 *           the rows the backend returned, latency stat chips, skeleton /
 *           unavailable / empty states. Extracted from the original
 *           TradingPage so that file stays a composition only.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: operator orders query result (OperatorOrdersResponse from
 *           pages/_shared/contracts), SortableTable + InfoChip, downloadCsv,
 *           AgeNote, Skeleton/EmptyState/StatusBadge, EngineSnapshot
 *           price_digits, format*.
 * PROVIDES: default OrderFlowPanel (props: query, nowMs, priceDigits).
 * INVARIANTS: the action chip tone is derived from the existing `action`
 *             string only (contains BUY → good, SELL → bad); latency chips
 *             render only the backend's own p50/p95/p99; export ships the raw
 *             rows — no re-query, no invented value.
 * EXTEND:   New columns read fields that exist on OperatorOrderRow.
 */
import { useMemo } from "react";
import type { OperatorOrderRow, OperatorOrdersResponse } from "@/pages/_shared/contracts";
import type { QueryLike } from "@/pages/_shared/SectionState";
import { AgeNote } from "@/pages/_shared/SectionState";
import { InfoChip, SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { EmptyState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";

interface Props {
  query: QueryLike<OperatorOrdersResponse>;
  nowMs: number;
  priceDigits: number;
}

export default function OrderFlowPanel({ query, nowMs, priceDigits }: Props) {
  const data = query.data;

  const orderCols = useMemo<Array<Column<OperatorOrderRow>>>(
    () => [
      { key: "time", label: "Time", sortValue: (r) => r.timestamp, render: (r) => (r.timestamp ? formatDateTime(r.timestamp) : "—") },
      { key: "ticket", label: "Ticket", sortValue: (r) => r.ticket, render: (r) => r.ticket ?? "—" },
      { key: "symbol", label: "Symbol", sortValue: (r) => r.symbol, render: (r) => r.symbol ?? "—" },
      { key: "action", label: "Action", sortValue: (r) => r.action, render: (r) => <span className={`l4-chip ${(r.action ?? "").includes("BUY") ? "good" : (r.action ?? "").includes("SELL") ? "bad" : ""}`}>{r.action ?? "—"}</span> },
      { key: "vol", label: "Vol", num: true, sortValue: (r) => r.volume, render: (r) => formatNumber(r.volume) },
      { key: "price", label: "Price", num: true, sortValue: (r) => r.price, render: (r) => formatPrice(r.price, priceDigits) },
      { key: "sl", label: "SL", num: true, sortValue: (r) => r.stop_loss, render: (r) => (r.stop_loss ? formatPrice(r.stop_loss) : "—") },
      { key: "tp", label: "TP", num: true, sortValue: (r) => r.take_profit, render: (r) => (r.take_profit ? formatPrice(r.take_profit) : "—") },
      { key: "lat", label: "Latency ms", num: true, sortValue: (r) => r.latency, render: (r) => (typeof r.latency === "number" ? r.latency.toFixed(1) : "—") },
      { key: "mode", label: "Mode", sortValue: (r) => r.execution_mode, render: (r) => <StatusBadge status={String(r.execution_mode ?? null)} /> },
      { key: "reason", label: "Reason", render: (r) => <span className="small muted" title={r.reason ?? undefined}>{r.reason?.slice(0, 42) ?? "—"}</span> },
    ],
    [priceDigits],
  );

  return (
    <Panel
      title="Dispatch order flow (audit_orders)"
      right={
        <>
          <AgeNote label="age" ageSec={query.dataUpdatedAt ? Math.max(0, (nowMs - query.dataUpdatedAt) / 1000) : null} />
          {(data?.rows?.length ?? 0) > 0 && (
            <button
              className="btn small ghost"
              title="exports exactly the rows the backend returned (client-side, no re-query)"
              onClick={() =>
                downloadCsv({
                  filename: `nse-order-flow-${stampForFilename()}.csv`,
                  headers: ["timestamp", "id", "ticket", "order_id", "symbol", "action", "volume", "price", "stop_loss", "take_profit", "latency", "execution_mode", "reason", "execution_id"],
                  rows: (data?.rows ?? []).map((r) => [r.timestamp, r.id, r.ticket, r.order_id, r.symbol, r.action, r.volume, r.price, r.stop_loss, r.take_profit, r.latency, r.execution_mode, r.reason, r.execution_id]),
                })
              }
            >
              ⇩ CSV
            </button>
          )}
        </>
      }
      tight
    >
      {query.isPending && !data ? (
        <div style={{ padding: 12 }}><Skeleton count={4} /></div>
      ) : data?.available === false ? (
        <EmptyState message="Order flow unavailable." hint={data.reason ?? "Ledger store not reachable — nothing inferred."} />
      ) : (data?.rows?.length ?? 0) === 0 ? (
        <EmptyState message="No dispatched orders recorded yet." hint="audit_orders rows appear when the engine sends a proposal to the broker/simulation adapter." />
      ) : (
        <>
          <div className="l4-toolbar" style={{ padding: "8px 12px 0" }}>
            {data?.latency ? (
              <>
                <InfoChip k="n" v={data.latency.n ?? "—"} />
                <InfoChip k="p50" v={`${formatNumber(data.latency.p50_ms, 1)} ms`} tone="accent" />
                <InfoChip k="p95" v={`${formatNumber(data.latency.p95_ms, 1)} ms`} />
                <InfoChip k="p99" v={`${formatNumber(data.latency.p99_ms, 1)} ms`} />
              </>
            ) : (
              <span className="l4-note">no numeric latency values in the returned rows yet</span>
            )}
            <span className="timestamp-note" style={{ marginInlineStart: "auto" }}>stats computed by the backend over these rows</span>
          </div>
          <SortableTable
            columns={orderCols}
            rows={data?.rows ?? []}
            rowKey={(r) => String(r.id)}
            initialSort={{ key: "time", dir: "desc" }}
            filter={(r, q) => String(r.ticket ?? "").includes(q) || (r.symbol ?? "").toLowerCase().includes(q) || (r.action ?? "").toLowerCase().includes(q)}
            emptyMessage="No order-flow rows."
          />
        </>
      )}
    </Panel>
  );
}
