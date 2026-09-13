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
import { ConfirmModal, DataTable, EmptyState, LoadingState, ErrorState, Panel } from "@/components/primitives";
import { SlTpEditor } from "@/components/pro/SlTpEditor";
import { formatDateTime, formatNumber, formatPnl, formatPrice } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { ApiError } from "@/types/api";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

interface CloseDialog {
  ticket: number;
}

export default function PositionsPage({ snapshot }: Props) {
  const t = useI18n((s) => s.t);
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
        <ConfirmModal
          title={t("alt.pos.confirm_close_title", "Close position #{ticket}", { ticket: closeDialog.ticket })}
          confirmLabel={t("alt.pos.confirm_close_label", "Confirm close")}
          busy={closeCmd.state.running}
          onConfirm={() => void confirmClose()}
          onCancel={() => setCloseDialog(null)}
        >
          <div>
            {t("alt.pos.close_body1", "Close position")} <b className="inline-mono">#{closeDialog.ticket}</b>{" "}
            {t("alt.pos.close_body2", "at market via the OrderLifecycleManager. The backend may refuse (guardian, state, connectivity) — the response decides.")}
          </div>
          {closeCmd.state.lastMessage && (
            <div className={`cmd-result ${closeCmd.state.lastResult ? "ok" : "fail"}`}>
              {closeCmd.state.lastResult ? "✓" : "✕"} {closeCmd.state.lastMessage}
            </div>
          )}
        </ConfirmModal>
      )}

      <Panel
        title={t("alt.pos.panel_open", "Open positions ({count})", { count: livePositions.length })}
        right={<span className="timestamp-note">{snapshot ? t("alt.pos.snapshot_note", "snapshot v{v}", { v: snapshot.state_version }) : "—"}</span>}
        tight
      >
        {positionsQuery.isPending && livePositions.length === 0 ? (
          <LoadingState label={t("alt.pos.loading_adapter", "Reading broker adapter…")} />
        ) : positionsQuery.isError && livePositions.length === 0 ? (
          <ErrorState
            message={positionsQuery.error instanceof ApiError ? positionsQuery.error.message : t("alt.pos.err_positions", "Position endpoint unavailable")}
            requestId={positionsQuery.error instanceof ApiError ? positionsQuery.error.requestId : null}
            onRetry={() => positionsQuery.refetch()}
          />
        ) : livePositions.length === 0 ? (
          <EmptyState message={t("alt.pos.empty_positions", "No open positions.")} hint={t("alt.pos.empty_positions_hint", "Broker adapter snapshot is empty — nothing is hidden or estimated.")} />
        ) : (
          <DataTable
            headers={[
              { label: t("alt.common.col_ticket", "Ticket") },
              { label: t("alt.common.col_symbol", "Symbol") },
              { label: t("alt.common.col_side", "Side") },
              { label: t("alt.common.col_volume", "Volume"), num: true },
              { label: t("alt.common.col_entry", "Entry"), num: true },
              { label: t("alt.common.col_current", "Current"), num: true },
              { label: t("alt.common.col_sl", "SL"), num: true },
              { label: t("alt.common.col_tp", "TP"), num: true },
              { label: t("alt.common.col_pnl", "PnL"), num: true },
              { label: t("alt.common.col_swap", "Swap"), num: true },
              { label: t("alt.common.col_opened", "Opened") },
              { label: "SL/TP" },
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
                <td>{p.ticket !== null && <SlTpEditor position={p} priceDigits={snapshot?.price_digits ?? null} />}</td>
                <td>
                  {p.ticket !== null && (
                    <button className="btn small danger" onClick={() => setCloseDialog({ ticket: p.ticket as number })}>
                      {t("alt.pos.close_btn", "Close")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Panel>

      <Panel title={t("alt.pos.panel_ledger", "Closed-trade ledger (broker-reconstructed)")} tight>
        {historyQuery.isPending ? (
          <LoadingState label={t("alt.pos.loading_ledger", "Reading audit ledger…")} />
        ) : historyQuery.isError ? (
          <ErrorState
            message={historyQuery.error instanceof ApiError ? historyQuery.error.message : t("alt.audit.err_ledger", "Ledger unavailable")}
            requestId={historyQuery.error instanceof ApiError ? historyQuery.error.requestId : null}
            onRetry={() => historyQuery.refetch()}
          />
        ) : (historyQuery.data?.length ?? 0) === 0 ? (
          <EmptyState message={t("alt.pos.empty_ledger", "No closed trades in the ledger yet.")} />
        ) : (
          <DataTable
            headers={[
              { label: t("alt.common.col_ticket", "Ticket") },
              { label: t("alt.common.col_symbol", "Symbol") },
              { label: t("alt.common.col_dir", "Dir") },
              { label: t("alt.common.col_volume", "Volume"), num: true },
              { label: t("alt.common.col_entry", "Entry"), num: true },
              { label: t("alt.common.col_status", "Status") },
              { label: t("alt.common.col_pnl", "PnL"), num: true },
              { label: t("alt.common.col_closed", "Closed") },
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
