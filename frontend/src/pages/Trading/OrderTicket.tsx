/**
 * PURPOSE:  Order-ticket inspector for a dispatched audit order: side-colored
 *           header (BUY/SELL from the action string), monospace price /
 *           volume / SL / TP hierarchy over fields that exist on
 *           OperatorOrderRow, plus a read-only notice — placement and cancel
 *           have no backend route.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: OperatorOrderRow from pages/_shared/contracts, format* helpers,
 *           trd-ticket classes from trading.css (green/red theme tokens).
 * PROVIDES: default OrderTicket component (props: row | null, priceDigits).
 * INVARIANTS: read-only — it renders exactly the fields the backend returned,
 *             an unselected ticket shows an honest "select a row" empty state,
 *             and it never pretends an order can be placed or cancelled from
 *             here (no fake buttons).
 * EXTEND:   new ticket lines read existing OperatorOrderRow fields only;
 *           never add an input — this is an inspector, not an order form.
 */
import type { OperatorOrderRow } from "@/pages/_shared/contracts";
import { EmptyState } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";

interface Props {
  row: OperatorOrderRow | null;
  priceDigits: number;
}

/** Side from the backend's own action word — never guessed. */
function side(action: string | null): "buy" | "sell" | "" {
  const a = (action ?? "").toUpperCase();
  if (a.includes("BUY")) return "buy";
  if (a.includes("SELL")) return "sell";
  return "";
}

export default function OrderTicket({ row, priceDigits }: Props) {
  if (!row) {
    return (
      <div className="trd-ticket trd-ticket--empty" role="status">
        <EmptyState
          message="No ticket selected — click an order-flow row to inspect it."
          hint="Read-only: manual order placement / cancel has no backend route, so this desk never fakes an order form."
        />
      </div>
    );
  }

  const s = side(row.action);
  return (
    <div className={`trd-ticket ${s}`} data-side={s || "unknown"} role="group" aria-label={`Order ticket ${row.ticket ?? row.id}`}>
      <div className="trd-ticket__head">
        <span className="trd-ticket__side">{row.action ?? "UNKNOWN"}</span>
        <span className="trd-ticket__symbol">{row.symbol ?? "—"}</span>
        <span className="trd-ticket__id">#{String(row.ticket ?? row.id)}</span>
        <span className="trd-ticket__time">{row.timestamp ? formatDateTime(row.timestamp) : "—"}</span>
      </div>

      <div className="trd-ticket__grid">
        <div className="trd-ticket__cell trd-ticket__cell--hero">
          <span className="trd-ticket__lab">price</span>
          <span className="trd-ticket__price">{formatPrice(row.price, priceDigits)}</span>
        </div>
        <div className="trd-ticket__cell">
          <span className="trd-ticket__lab">volume</span>
          <span className="trd-ticket__num">{formatNumber(row.volume)}</span>
        </div>
        <div className="trd-ticket__cell">
          <span className="trd-ticket__lab">stop loss</span>
          <span className="trd-ticket__num trd-ticket__sl">{row.stop_loss ? formatPrice(row.stop_loss, priceDigits) : "—"}</span>
        </div>
        <div className="trd-ticket__cell">
          <span className="trd-ticket__lab">take profit</span>
          <span className="trd-ticket__num trd-ticket__tp">{row.take_profit ? formatPrice(row.take_profit, priceDigits) : "—"}</span>
        </div>
      </div>

      <div className="trd-ticket__meta">
        <span className="trd-ticket__meta-item" title="backend-measured dispatch latency">
          latency <b>{typeof row.latency === "number" ? `${row.latency.toFixed(1)} ms` : "—"}</b>
        </span>
        <span className="trd-ticket__meta-item">
          mode <b>{row.execution_mode ?? "—"}</b>
        </span>
        <span className="trd-ticket__meta-item">
          order_id <b className="inline-mono">{row.order_id ?? "—"}</b>
        </span>
        <span className="trd-ticket__meta-item">
          execution_id <b className="inline-mono">{row.execution_id ?? "—"}</b>
        </span>
      </div>

      {row.reason && (
        <div className="trd-ticket__reason" title={row.reason}>
          {row.reason}
        </div>
      )}

      <div className="trd-ticket__foot">
        read-only audit ticket — manual placement / cancel has no backend route (BUG-242 INV-004)
      </div>
    </div>
  );
}
