/**
 * MarketReadout — market state + pending orders + SMC/ICT overlays (lane C).
 * Lane contract: presentation only. Reads the query objects already wired by
 * the page (same query keys, same cache); never issues new fetches, never
 * mutates state. Baseline keeps ALL original rows verbatim; restyle only.
 */
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { AgeNote } from "@/pages/_shared/SectionState";
import { formatNumber, formatPct, formatPrice, formatTime } from "@/lib/format";
import type { EngineSnapshot, OrderRow } from "@/types/domain";
import "./readout.css";

export interface MarketReadoutProps {
  snapshot: EngineSnapshot;
  mt5Query: { data?: { orders?: OrderRow[] }; isPending: boolean; isError: boolean; refetch: () => void };
  ordersQuery: { data?: unknown; isPending: boolean; isError: boolean; refetch: () => void };
}

export default function MarketReadout(props: MarketReadoutProps) {
  const { snapshot, mt5Query } = props;
  return (
    <div className="tr-readout">
      <div className="tr-readout-grid">
      <div className="grid cols-2">
        <Panel
          title="Market / execution state"
          right={<AgeNote label="tick age" ageSec={snapshot.diagnostics.tick_age_sec} />}
        >
          <dl className="kv">
            <dt>symbol</dt>
            <dd>{snapshot.symbol ?? "—"}</dd>
            <dt>bid / ask</dt>
            <dd>{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} / {formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</dd>
            <dt>spread</dt>
            <dd>{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</dd>
            <dt>tick stale</dt>
            <dd>{snapshot.tick_stale ? <span className="badge warn">STALE</span> : <span className="badge good">FRESH</span>}</dd>
            <dt>regime</dt>
            <dd>{snapshot.regime ?? "—"}</dd>
            <dt>AI proposal</dt>
            <dd>{snapshot.ai_decision ?? "—"} {snapshot.ai_confidence !== null ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            <dt>proposal blocked by</dt>
            <dd>{snapshot.ai_reason ?? "—"}</dd>
            <dt>proposal age</dt>
            <dd>{snapshot.diagnostics.proposal_age_sec === null ? "—" : `${snapshot.diagnostics.proposal_age_sec.toFixed(1)}s`}</dd>
          </dl>
        </Panel>

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
      </div>


      </div>
    </div>
  );
}
