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
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { StatusPill } from "../../../research/ui/lane5Kit";
import { arr, num, str, type Row } from "../../model";
import { controlCenterQueries } from "../../useCases";
import { useI18n } from "@/stores/i18nStore";

/** Stable row key: backend identity first (ticket), then timestamp, then slot —
 *  never a bare index, so a shifted row keeps its DOM across polls. */
const orderKey = (o: Row, i: number): string => `${num(o.ticket) ?? "t"}-${str(o.timestamp) ?? ""}-${i}`;

export function OrdersTab() {
  const t = useI18n((s) => s.t);
  const ordersQ = useQuery({
    queryKey: ["control-center", "orders"],
    queryFn: ({ signal }) => controlCenterQueries.orders(signal),
    refetchInterval: 30_000,
    retry: false,
  });

  // perf: row elements derive once per payload — the parent page re-renders on
  // every 15s summary tick while this 30s payload is unchanged.
  const rows = useMemo(() => arr(ordersQ.data?.rows), [ordersQ.data]);
  const rowEls = useMemo(
    () =>
      rows.map((o, i) => (
        <tr key={orderKey(o, i)}>
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
      )),
    [rows],
  );

  return (
    <Panel title={t("control-center.panel.orders", "Dispatch evidence (audit_orders) + latency")} tight>
      {ordersQ.isPending ? (
        <Skeleton count={5} />
      ) : ordersQ.isError ? (
        <ErrorState message={ordersQ.error instanceof Error ? ordersQ.error.message : t("control-center.err.orders", "orders failed")} onRetry={() => void ordersQ.refetch()} />
      ) : ordersQ.data?.available === false ? (
        <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} />
      ) : (
        <>
          <div className="ctl-metric">
            <MetricCard
              label={t("control-center.kpi.latency", "latency p50 / p95 / p99 (ms)")}
              value={
                ordersQ.data?.latency
                  ? `${formatNumber(ordersQ.data.latency.p50_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p95_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p99_ms ?? NaN, 1)}`
                  : t("control-center.truth.not_recorded", "NOT RECORDED")
              }
              sub={t("control-center.kpi.n_rows", "n={n} · {rows} rows", { n: String(ordersQ.data?.latency?.n ?? 0), rows: String(ordersQ.data?.count ?? 0) })}
            />
          </div>
          {rows.length === 0 ? (
            <EmptyState message={t("control-center.empty.no_orders", "No dispatch rows in this window.")} />
          ) : (
            <DataTable
              headers={[
                { label: t("control-center.th.ts", "ts") },
                { label: t("control-center.th.ticket", "ticket"), num: true },
                { label: t("control-center.th.action", "action") },
                { label: t("control-center.th.price", "price"), num: true },
                { label: t("control-center.th.vol", "vol"), num: true },
                { label: t("control-center.th.latency", "latency"), num: true },
                { label: t("control-center.th.mode", "mode") },
                { label: t("control-center.th.reason", "reason") },
              ]}
            >
              {rowEls}
            </DataTable>
          )}
        </>
      )}
    </Panel>
  );
}
