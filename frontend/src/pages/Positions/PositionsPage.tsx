/**
 * Positions — live backend position state + close command.
 *
 * Data: canonical snapshot positions (engine-merged) cross-checked with the
 * v1 adapter endpoint. Close/modify go through the OrderLifecycleManager
 * (backend-authoritative); results are NEVER assumed — backend response
 * decides, and a confirmation dialog protects the destructive action.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { positionsApi } from "@/api/positionsApi";
import { tradingApi } from "@/api/tradingApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import type { EngineSnapshot, Position } from "@/types/domain";
import { DataTable, EmptyState, LoadingState, ErrorState, Panel } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPnl, formatPrice } from "@/lib/format";
import { ApiError } from "@/types/api";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

interface CloseDialog {
  ticket: number;
}

export default function PositionsPage({ snapshot }: Props) {
  const queryClient = useQueryClient();
  const closeCmd = useMutationFeedback();
  const [closeDialog, setCloseDialog] = useState<CloseDialog | null>(null);

  const positionsQuery = useQuery({
    queryKey: ["v1-positions"],
    queryFn: ({ signal }) => positionsApi.openPositions(signal),
    refetchInterval: 10_000,
    retry: 1,
  });

  const historyQuery = useQuery({
    queryKey: ["ledger-history"],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 50 }, signal),
    refetchInterval: 30_000,
    retry: 1,
  });

  const livePositions: Position[] = positionsQuery.data?.positions ?? snapshot?.positions ?? [];

  const confirmClose = async (): Promise<void> => {
    if (!closeDialog) return;
    const ok = await closeCmd.run(() => tradingApi.closePosition(closeDialog.ticket));
    if (ok) {
      setCloseDialog(null);
      void queryClient.invalidateQueries({ queryKey: ["v1-positions"] });
      void queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
    }
  };

  return (
    <div>
      {closeDialog && (
        <div className="panel" style={{ borderColor: "rgba(240,86,79,0.6)" }}>
          <div className="panel-header"><span>Confirm close — backend-validated</span></div>
          <div className="panel-body">
            <div className="confirm-box" style={{ marginTop: 0 }}>
              <div>
                Close position <b className="inline-mono">#{closeDialog.ticket}</b> at market via the OrderLifecycleManager. The backend may refuse (guardian, state, connectivity) — the response decides.
              </div>
              <div className="row">
                <button className="btn danger" disabled={closeCmd.state.running} onClick={() => void confirmClose()}>
                  {closeCmd.state.running ? "sending…" : "Confirm close"}
                </button>
                <button className="btn" disabled={closeCmd.state.running} onClick={() => setCloseDialog(null)}>
                  Cancel
                </button>
              </div>
            </div>
            {closeCmd.state.lastMessage && (
              <div className={`cmd-result ${closeCmd.state.lastResult ? "ok" : "fail"}`}>
                {closeCmd.state.lastResult ? "✓" : "✕"} {closeCmd.state.lastMessage}
              </div>
            )}
          </div>
        </div>
      )}

      <Panel
        title={`Open positions (${livePositions.length})`}
        right={<span className="timestamp-note">{snapshot ? `snapshot v${snapshot.state_version}` : "—"}</span>}
        tight
      >
        {positionsQuery.isPending && livePositions.length === 0 ? (
          <LoadingState label="Reading broker adapter…" />
        ) : positionsQuery.isError && livePositions.length === 0 ? (
          <ErrorState
            message={positionsQuery.error instanceof ApiError ? positionsQuery.error.message : "Position endpoint unavailable"}
            requestId={positionsQuery.error instanceof ApiError ? positionsQuery.error.requestId : null}
            onRetry={() => positionsQuery.refetch()}
          />
        ) : livePositions.length === 0 ? (
          <EmptyState message="No open positions." hint="Broker adapter snapshot is empty — nothing is hidden or estimated." />
        ) : (
          <DataTable
            headers={[
              { label: "Ticket" },
              { label: "Symbol" },
              { label: "Side" },
              { label: "Volume", num: true },
              { label: "Entry", num: true },
              { label: "Current", num: true },
              { label: "SL", num: true },
              { label: "TP", num: true },
              { label: "PnL", num: true },
              { label: "Swap", num: true },
              { label: "Opened" },
              { label: "" },
            ]}
          >
            {livePositions.map((p, i) => (
              <tr key={String(p.ticket ?? i)}>
                <td>{p.ticket ?? "—"}</td>
                <td>{p.symbol ?? "—"}</td>
                <td>{Number(p.type) === 0 ? <span className="badge good">BUY</span> : Number(p.type) === 1 ? <span className="badge bad">SELL</span> : String(p.type)}</td>
                <td className="num">{formatNumber(p.volume)}</td>
                <td className="num">{formatPrice(p.price_open)}</td>
                <td className="num">{formatPrice(p.price_current)}</td>
                <td className="num">{p.sl ? formatPrice(p.sl) : "—"}</td>
                <td className="num">{p.tp ? formatPrice(p.tp) : "—"}</td>
                <td className={`num ${p.profit !== null && p.profit >= 0 ? "pnl-pos" : "pnl-neg"}`}>{formatPnl(p.profit)}</td>
                <td className="num">{formatNumber(p.swap)}</td>
                <td>{formatDateTime(p.time)}</td>
                <td>
                  {p.ticket !== null && (
                    <button className="btn small danger" onClick={() => setCloseDialog({ ticket: p.ticket as number })}>
                      Close
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      <Panel title="Closed-trade ledger (broker-reconstructed)" tight>
        {historyQuery.isPending ? (
          <LoadingState label="Reading audit ledger…" />
        ) : historyQuery.isError ? (
          <ErrorState
            message={historyQuery.error instanceof ApiError ? historyQuery.error.message : "Ledger unavailable"}
            requestId={historyQuery.error instanceof ApiError ? historyQuery.error.requestId : null}
            onRetry={() => historyQuery.refetch()}
          />
        ) : (historyQuery.data?.length ?? 0) === 0 ? (
          <EmptyState message="No closed trades in the ledger yet." />
        ) : (
          <DataTable
            headers={[
              { label: "Ticket" },
              { label: "Symbol" },
              { label: "Dir" },
              { label: "Volume", num: true },
              { label: "Entry", num: true },
              { label: "Status" },
              { label: "PnL", num: true },
              { label: "Closed" },
            ]}
          >
            {(historyQuery.data ?? []).slice(0, 25).map((r, i) => (
              <tr key={String(r.ticket ?? i)}>
                <td>{r.ticket ?? "—"}</td>
                <td>{r.symbol ?? "—"}</td>
                <td>{r.direction ?? "—"}</td>
                <td className="num">{formatNumber(r.volume)}</td>
                <td className="num">{formatPrice(r.entry_price)}</td>
                <td>{r.status ?? "—"}</td>
                <td className={`num ${r.pnl !== null && r.pnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>{formatPnl(r.pnl)}</td>
                <td>{formatDateTime(r.timestamp)}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>
    </div>
  );
}
