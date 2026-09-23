/**
 * PURPOSE:  Pending broker orders panel: MT5 open orders table with honest
 *           skeleton / error+retry / empty states and side-toned type cells.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: mt5 status query (MT5Status.orders / OrderRow), Skeleton /
 *           ErrorState / EmptyState, formatNumber / formatPrice / formatTime.
 * PROVIDES: default PendingOrdersPanel component (props: mt5Query).
 * INVARIANTS: renders only OrderRow fields the endpoint returned; side tone is
 *             derived from the existing type string (BUY/SELL), never from a
 *             number the payload does not carry.
 * EXTEND:   new columns read existing OrderRow fields only.
 */
import type { MT5Status, OrderRow } from "@/types/domain";
import type { QueryLike } from "@/pages/_shared/SectionState";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatNumber, formatPrice, formatTime } from "@/lib/format";

interface Props {
  mt5Query: QueryLike<MT5Status>;
}

/** Side tone derived from the backend's own type word (BUY / SELL / …). */
function sideTone(type: OrderRow["type"]): string {
  const s = String(type ?? "").toUpperCase();
  if (s.includes("BUY")) return "buy";
  if (s.includes("SELL")) return "sell";
  return "";
}

export default function PendingOrdersPanel({ mt5Query }: Props) {
  const orders = mt5Query.data?.orders;

  if (orders && orders.length > 0) {
    return (
      <Panel title="Pending orders (broker)" tight>
        <div tabIndex={0} className="table-wrap">
          <table className="data-table trd-pending">
            <thead>
              <tr><th scope="col">Ticket</th><th scope="col">Type</th><th scope="col">Volume</th><th scope="col">Price</th><th scope="col">State</th><th scope="col">Setup</th></tr>
            </thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={o.ticket ?? i} data-side={sideTone(o.type) || undefined}>
                  <td>{o.ticket ?? "—"}</td>
                  <td>
                    <span className={`trd-side-chip ${sideTone(o.type)}`}>
                      {sideTone(o.type) === "buy" ? "▲" : sideTone(o.type) === "sell" ? "▼" : ""}
                      {String(o.type ?? "—")}
                    </span>
                  </td>
                  <td className="num">{formatNumber(o.volume_current)}</td>
                  <td className="num">{formatPrice(o.price_open)}</td>
                  <td>{String(o.state ?? "—")}</td>
                  <td>{formatTime(o.time_setup)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    );
  }
  return (
    <Panel title="Pending orders (broker)" tight>
      {mt5Query.isPending ? (
        <div style={{ padding: 14 }}><Skeleton count={3} /></div>
      ) : mt5Query.isError ? (
        <ErrorState message="Pending orders unavailable (MT5 status endpoint failed)." onRetry={() => void mt5Query.refetch()} />
      ) : (
        <EmptyState message="No pending orders on the broker account." />
      )}
    </Panel>
  );
}
