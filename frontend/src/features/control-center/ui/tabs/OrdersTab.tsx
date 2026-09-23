/**
 * PURPOSE:  ORDERS tab — dispatch evidence (audit_orders) with latency percentiles.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ../useCases (controlCenterQueries.orders — existing GET), ../model,
 *           @/components/primitives, @/lib/format, ../../../../research/ui/lane5Kit (StatusPill)
 * PROVIDES: OrdersTab
 * INVARIANTS: latency renders NOT RECORDED when the backend sent no latency block
 *             (never 0); rows keep backend order; skeleton / error+retry /
 *             ledger-unavailable states are all visible.
 * EXTEND:   new column = a field the orders Row already carries (verify server-side).
 */
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { StatusPill } from "../../../research/ui/lane5Kit";
import { arr, num, str } from "../../model";
import { controlCenterQueries } from "../../useCases";

export function OrdersTab() {
  const ordersQ = useQuery({
    queryKey: ["control-center", "orders"],
    queryFn: ({ signal }) => controlCenterQueries.orders(signal),
    refetchInterval: 30_000,
    retry: false,
  });

  return (
    <Panel title="Dispatch evidence (audit_orders) + latency" tight>
      {ordersQ.isPending ? (
        <Skeleton count={5} />
      ) : ordersQ.isError ? (
        <ErrorState message={ordersQ.error instanceof Error ? ordersQ.error.message : "orders failed"} onRetry={() => void ordersQ.refetch()} />
      ) : ordersQ.data?.available === false ? (
        <EmptyState message="ledger unavailable" />
      ) : (
        <>
          <div style={{ marginBottom: 8 }}>
            <MetricCard
              label="latency p50 / p95 / p99 (ms)"
              value={
                ordersQ.data?.latency
                  ? `${formatNumber(ordersQ.data.latency.p50_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p95_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p99_ms ?? NaN, 1)}`
                  : "NOT RECORDED"
              }
              sub={`n=${String(ordersQ.data?.latency?.n ?? 0)} · ${String(ordersQ.data?.count ?? 0)} rows`}
            />
          </div>
          <DataTable
            headers={[
              { label: "ts" },
              { label: "ticket", num: true },
              { label: "action" },
              { label: "price", num: true },
              { label: "vol", num: true },
              { label: "latency", num: true },
              { label: "mode" },
              { label: "reason" },
            ]}
          >
            {arr(ordersQ.data?.rows).map((o, i) => (
              <tr key={i}>
                <td className="tiny">{formatDateTime(str(o.timestamp))}</td>
                <td className="num tiny">{num(o.ticket) ?? "—"}</td>
                <td>
                  <StatusPill status={str(o.action)} />
                </td>
                <td className="num tiny">{formatPrice(num(o.price), 2)}</td>
                <td className="num tiny">{num(o.volume) ?? "—"}</td>
                <td className="num tiny">{num(o.latency) === null ? "—" : `${formatNumber(num(o.latency)!, 0)}ms`}</td>
                <td className="tiny">{str(o.execution_mode) ?? "—"}</td>
                <td className="tiny muted">{str(o.reason) ?? ""}</td>
              </tr>
            ))}
          </DataTable>
        </>
      )}
    </Panel>
  );
}
