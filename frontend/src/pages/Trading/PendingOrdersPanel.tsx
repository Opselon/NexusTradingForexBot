/**
 * PURPOSE:  Pending broker orders panel: MT5 open orders table with honest
 *           skeleton / error+retry / empty states. Extracted from the
 *           original TradingPage so that file stays a composition only.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: mt5 status query (MT5Status.orders / OrderRow), Skeleton /
 *           ErrorState / EmptyState, formatNumber / formatPrice / formatTime.
 * PROVIDES: default PendingOrdersPanel component (props: mt5Query).
 * INVARIANTS: renders only OrderRow fields the endpoint returned; no tone,
 *             meter or derived value is added here.
 * EXTEND:   new columns read existing OrderRow fields only.
 */
import type { MT5Status } from "@/types/domain";
import type { QueryLike } from "@/pages/_shared/SectionState";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatNumber, formatPrice, formatTime } from "@/lib/format";

interface Props {
  mt5Query: QueryLike<MT5Status>;
}

export default function PendingOrdersPanel({ mt5Query }: Props) {
  return (
    <Panel title="Pending orders (broker)" tight>
      {mt5Query.data?.orders && mt5Query.data.orders.length > 0 ? (
        <div tabIndex={0} className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th scope="col">Ticket</th><th scope="col">Type</th><th scope="col">Volume</th><th scope="col">Price</th><th scope="col">State</th><th scope="col">Setup</th></tr>
            </thead>
            <tbody>
              {mt5Query.data.orders.map((o, i) => (
                <tr key={o.ticket ?? i}>
                  <td>{o.ticket ?? "—"}</td>
                  <td>{String(o.type ?? "—")}</td>
                  <td className="num">{formatNumber(o.volume_current)}</td>
                  <td className="num">{formatPrice(o.price_open)}</td>
                  <td>{String(o.state ?? "—")}</td>
                  <td>{formatTime(o.time_setup)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : mt5Query.isPending ? (
        <div style={{ padding: 14 }}><Skeleton count={3} /></div>
      ) : mt5Query.isError ? (
        <ErrorState message="Pending orders unavailable (MT5 status endpoint failed)." onRetry={() => void mt5Query.refetch()} />
      ) : (
        <EmptyState message="No pending orders on the broker account." />
      )}
    </Panel>
  );
}
