/**
 * PURPOSE:  Recent executions panel (v1 audit_executions): paginated table
 *           with the guardian-state footer and the explicit "no manual order
 *           placement" notice. Extracted from the original TradingPage.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: execution history query (V1Page<ExecutionHistoryRow>), SectionState,
 *           StatusBadge, format* helpers, EngineSnapshot health/mode for the
 *           footer.
 * PROVIDES: default ExecutionsPanel component (props: execQuery, snapshot,
 *           currentMode, page, onPage).
 * INVARIANTS: no fake order buttons — placement/cancel has no backend route
 *             and the notice says so; pagination keeps its exact behavior.
 * EXTEND:   new columns read ExecutionHistoryRow fields only.
 */
import type { EngineSnapshot, ExecutionHistoryRow, V1Page } from "@/types/domain";
import type { QueryLike } from "@/pages/_shared/SectionState";
import { SectionState } from "@/pages/_shared/SectionState";
import { Panel, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";

interface Props {
  execQuery: QueryLike<V1Page<ExecutionHistoryRow>>;
  snapshot: EngineSnapshot;
  currentMode: string;
  page: number;
  onPage: (next: number) => void;
}

export default function ExecutionsPanel({ execQuery, snapshot, currentMode, page, onPage }: Props) {
  return (
    <Panel
      title="Recent executions (audit_executions)"
      right={
        <>
          <span className="small faint">manual order placement / cancel: NO backend route — no fake buttons here (BUG-242 INV-004)</span>
          <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <button aria-label="Previous page" className="btn small" disabled={page <= 1} onClick={() => onPage(Math.max(1, page - 1))}>‹</button>
            <span className="small faint inline-mono">p{page}</span>
            <button aria-label="Next page" className="btn small" disabled={!execQuery.data?.has_more} onClick={() => onPage(page + 1)}>›</button>
          </span>
        </>
      }
      tight
    >
      <SectionState
        query={execQuery}
        emptyMessage="No execution rows yet."
        emptyHint="audit_executions fills as the OrderLifecycleManager dispatches."
        errorFallback="Execution history endpoint failed."
        emptyWhen={(d) => d.items.length === 0}
      >
        {(d) => (
          <div tabIndex={0} className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th scope="col">#</th><th scope="col">Order id</th><th scope="col">Symbol</th><th scope="col">Type</th><th scope="col">Volume</th><th scope="col">Price</th><th scope="col">Status</th><th scope="col">Executed</th></tr>
              </thead>
              <tbody>
                {d.items.map((r, i) => (
                  <tr key={String(r.id ?? `${r.order_id}-${i}`)}>
                    <td>{String(r.id ?? "—")}</td>
                    <td className="small">{r.order_id ?? "—"}</td>
                    <td>{r.symbol ?? "—"}</td>
                    <td>{r.order_type ?? "—"}</td>
                    <td className="num">{formatNumber(r.volume)}</td>
                    <td className="num">{formatPrice(r.price)}</td>
                    <td><StatusBadge status={r.status ?? null} /></td>
                    <td>{r.executed_at ? formatDateTime(r.executed_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionState>
      <div className="small faint" style={{ padding: "8px 12px" }}>
        Guardian state: <StatusBadge status={String(snapshot.health.subsystems.engine ?? "UNKNOWN")} /> (engine) · mode <span className="inline-mono">{currentMode || "—"}</span> · positions &amp;
        close actions live on the Positions page; model proposals on the Dashboard.
      </div>
    </Panel>
  );
}
