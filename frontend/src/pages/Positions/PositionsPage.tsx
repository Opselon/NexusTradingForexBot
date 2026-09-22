/**
 * Positions — live backend position state + close command.
 *
 * Data: canonical snapshot positions (engine-merged) cross-checked with the
 * v1 adapter endpoint. Close goes through the OrderLifecycleManager
 * (backend-authoritative); results are NEVER assumed — backend response
 * decides, and a confirmation dialog protects the destructive action.
 *
 * Table upgrade (parity bar): sortable columns, ticket/symbol filter, sticky
 * header (theme), a floating-PnL summary header derived by plain arithmetic
 * over backend per-position values only, and a broker-vs-adapter
 * cross-check count so a drift between the two reads is visible, not hidden.
 * The closed-trade ledger gained its own filter, pagination slice, and a
 * client-side CSV export of exactly the rows the backend returned.
 */

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { positionsApi } from "@/api/positionsApi";
import { tradingApi } from "@/api/tradingApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import type { AuditLedgerRow, EngineSnapshot, Position } from "@/types/domain";
import {
  ConfirmModal,
  EmptyState,
  ErrorState,
  LoadingState,
  MetricCard,
  Panel,
  PositionSideBadge,
} from "@/components/primitives";
import { AgeNote } from "@/pages/_shared/SectionState";
import { SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime, formatNumber, formatPnl, formatPrice } from "@/lib/format";
import { ApiError } from "@/types/api";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

interface CloseDialog {
  ticket: number;
  summary: string;
}

interface ModifyDialog {
  ticket: number;
  summary: string;
  sl: string;
  tp: string;
}

function posKey(p: Position, i: number): string {
  return String(p.ticket ?? `${p.symbol}-${i}`);
}

function ledgerMatches(a: { ticket: number | null }, b: Position): boolean {
  return a.ticket !== null && a.ticket === b.ticket;
}

export default function PositionsPage({ snapshot }: Props) {
  const queryClient = useQueryClient();
  const closeCmd = useMutationFeedback();
  const modifyCmd = useMutationFeedback();
  const [closeDialog, setCloseDialog] = useState<CloseDialog | null>(null);
  const [modifyDialog, setModifyDialog] = useState<ModifyDialog | null>(null);
  const [ledgerStatus, setLedgerStatus] = useState("");

  const positionsQuery = useQuery({
    queryKey: ["v1-positions"],
    queryFn: ({ signal }) => positionsApi.openPositions(signal),
    refetchInterval: 10_000,
    retry: 1,
  });

  const historyQuery = useQuery({
    queryKey: ["ledger-history", ledgerStatus],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 200, status: ledgerStatus || undefined }, signal),
    refetchInterval: 30_000,
    retry: 1,
  });

  const livePositions: Position[] = positionsQuery.data?.positions ?? snapshot?.positions ?? [];
  const adapterSource = positionsQuery.data ? "v1 adapter" : snapshot ? "canonical snapshot (v1 endpoint pending)" : "—";
  // Cross-check: broker positions carried inside the canonical snapshot vs
  // the v1 endpoint — a visible count, not a silent preference.
  const snapshotCount = snapshot?.positions?.length ?? null;
  const v1Count = positionsQuery.data?.positions.length ?? null;
  const crossMismatch = snapshotCount !== null && v1Count !== null && snapshotCount !== v1Count;

  const totals = useMemo(() => {
    let floating = 0;
    let proven = true;
    let wins = 0;
    let losses = 0;
    let volume = 0;
    for (const p of livePositions) {
      if (typeof p.profit === "number" && Number.isFinite(p.profit)) {
        floating += p.profit;
        if (p.profit > 0) wins += 1;
        else if (p.profit < 0) losses += 1;
      } else {
        proven = false;
      }
      if (typeof p.volume === "number" && Number.isFinite(p.volume)) volume += p.volume;
    }
    return { floating, proven, wins, losses, volume, count: livePositions.length };
  }, [livePositions]);

  const columns = useMemo<Array<Column<Position>>>(
    () => [
      { key: "ticket", label: "Ticket", sortValue: (p) => p.ticket, render: (p) => p.ticket ?? "—" },
      { key: "symbol", label: "Symbol", sortValue: (p) => p.symbol, render: (p) => p.symbol ?? "—" },
      { key: "side", label: "Side", sortValue: (p) => (Number(p.type) === 0 ? "BUY" : Number(p.type) === 1 ? "SELL" : "—"), render: (p) => <PositionSideBadge type={p.type} /> },
      { key: "volume", label: "Volume", num: true, sortValue: (p) => p.volume, render: (p) => formatNumber(p.volume) },
      { key: "entry", label: "Entry", num: true, sortValue: (p) => p.price_open, render: (p) => formatPrice(p.price_open, snapshot?.price_digits ?? 2) },
      { key: "current", label: "Current", num: true, sortValue: (p) => p.price_current, render: (p) => formatPrice(p.price_current, snapshot?.price_digits ?? 2) },
      { key: "sl", label: "SL", num: true, sortValue: (p) => p.sl, render: (p) => (p.sl ? formatPrice(p.sl) : "—") },
      { key: "tp", label: "TP", num: true, sortValue: (p) => p.tp, render: (p) => (p.tp ? formatPrice(p.tp) : "—") },
      {
        key: "pnl",
        label: "PnL",
        num: true,
        sortValue: (p) => p.profit,
        render: (p) => <span className={(p.profit ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>{formatPnl(p.profit)}</span>,
      },
      { key: "swap", label: "Swap", num: true, sortValue: (p) => p.swap, render: (p) => formatNumber(p.swap) },
      { key: "time", label: "Opened", sortValue: (p) => (typeof p.time === "number" ? p.time : p.time), render: (p) => formatDateTime(p.time) },
      {
        key: "actions",
        label: "",
        render: (p) =>
          p.ticket !== null ? (
            <span className="l4-row-actions" onClick={(e) => e.stopPropagation()}>
              <button
                className="btn small"
                onClick={() =>
                  setModifyDialog({
                    ticket: p.ticket as number,
                    summary: `${p.symbol ?? "?"} ${Number(p.type) === 0 ? "BUY" : Number(p.type) === 1 ? "SELL" : "?"} ${formatNumber(p.volume)} lots @ ${formatPrice(p.price_open)}`,
                    sl: p.sl !== null && p.sl !== undefined && p.sl !== 0 ? String(p.sl) : "",
                    tp: p.tp !== null && p.tp !== undefined && p.tp !== 0 ? String(p.tp) : "",
                  })
                }
              >
                SL/TP
              </button>
              <button
                className="btn small danger"
                onClick={() =>
                  setCloseDialog({
                    ticket: p.ticket as number,
                    summary: `${p.symbol ?? "?"} ${Number(p.type) === 0 ? "BUY" : Number(p.type) === 1 ? "SELL" : "?"} ${formatNumber(p.volume)} lots @ ${formatPrice(p.price_open)} · floating ${formatPnl(p.profit)}`,
                  })
                }
              >
                Close
              </button>
            </span>
          ) : (
            <span className="faint small">no ticket</span>
          ),
      },
    ],
    [snapshot?.price_digits],
  );

  const confirmClose = async (): Promise<void> => {
    if (!closeDialog) return;
    const ok = await closeCmd.run(() => tradingApi.closePosition(closeDialog.ticket));
    if (ok) {
      setCloseDialog(null);
      void queryClient.invalidateQueries({ queryKey: ["v1-positions"] });
      void queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
    }
  };

  /** SL/TP edit — the backend OrderLifecycleManager owns validation (and may
   *  refuse a level that violates the enforce_stop_loss gate). */
  const confirmModify = async (): Promise<void> => {
    if (!modifyDialog) return;
    const sl = modifyDialog.sl.trim() === "" ? 0 : Number(modifyDialog.sl);
    const tp = modifyDialog.tp.trim() === "" ? 0 : Number(modifyDialog.tp);
    if (!Number.isFinite(sl) || !Number.isFinite(tp)) {
      // local guard only — not a validation verdict, just "this can't be sent"
      return;
    }
    const ok = await modifyCmd.run(() => tradingApi.modifyPosition({ ticket: modifyDialog.ticket, stop_loss: sl, take_profit: tp }));
    if (ok) {
      setModifyDialog(null);
      void queryClient.invalidateQueries({ queryKey: ["v1-positions"] });
      void queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
    }
  };

  const ledgerCols = useMemo<Array<Column<AuditLedgerRow>>>(
    () => [
      { key: "ticket", label: "Ticket", sortValue: (r) => r.ticket, render: (r) => r.ticket ?? "—" },
      { key: "symbol", label: "Symbol", sortValue: (r) => r.symbol, render: (r) => r.symbol ?? "—" },
      { key: "dir", label: "Dir", sortValue: (r) => r.direction, render: (r) => r.direction ?? "—" },
      { key: "vol", label: "Volume", num: true, sortValue: (r) => r.volume, render: (r) => formatNumber(r.volume) },
      { key: "entry", label: "Entry", num: true, sortValue: (r) => r.entry_price, render: (r) => formatPrice(r.entry_price) },
      { key: "status", label: "Status", sortValue: (r) => r.status, render: (r) => r.status ?? "—" },
      {
        key: "pnl",
        label: "PnL",
        num: true,
        sortValue: (r) => r.pnl,
        render: (r) => <span className={(r.pnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>{formatPnl(r.pnl)}</span>,
      },
      { key: "time", label: "Closed", sortValue: (r) => r.timestamp, render: (r) => formatDateTime(r.timestamp) },
    ],
    [],
  );

  return (
    <div>
      {/* PnL summary header — arithmetic over backend per-position values */}
      <div className="grid cols-4">
        <MetricCard
          label="Floating PnL"
          value={totals.count === 0 ? "—" : totals.proven ? formatPnl(totals.floating) : "PARTIAL"}
          tone={totals.count === 0 ? "dim" : totals.floating >= 0 ? "pos" : "neg"}
          sub={totals.proven ? "Σ of backend profit fields" : "some rows carry no profit value — sum withheld"}
        />
        <MetricCard label="Open positions" value={totals.count} sub={`volume ${formatNumber(totals.volume)} lots`} tone="dim" />
        <MetricCard label="Winners / losers" value={`${totals.wins} / ${totals.losses}`} sub="by backend floating sign (display only)" tone="dim" />
        <MetricCard
          label="Adapter cross-check"
          value={crossMismatch ? "MISMATCH" : v1Count !== null ? "MATCH" : "—"}
          tone={crossMismatch ? "neg" : "dim"}
          sub={`snapshot ${snapshotCount ?? "—"} vs v1 ${v1Count ?? "—"} positions`}
        />
      </div>

      {closeDialog && (
        <ConfirmModal
          title={`Close position #${closeDialog.ticket}`}
          confirmLabel="Confirm close"
          busy={closeCmd.state.running}
          onConfirm={() => void confirmClose()}
          onCancel={() => setCloseDialog(null)}
        >
          <div>
            Close position <b className="inline-mono">#{closeDialog.ticket}</b> at market via the OrderLifecycleManager.
            <div className="small" style={{ marginTop: 6 }}>{closeDialog.summary}</div>
            <div className="small muted" style={{ marginTop: 6 }}>
              The backend may refuse (guardian, state, connectivity) — the response decides, this dialog only prevents mis-clicks.
            </div>
          </div>
          {closeCmd.state.lastMessage && (
            <div className={`cmd-result ${closeCmd.state.lastResult ? "ok" : "fail"}`}>
              {closeCmd.state.lastResult ? "✓" : "✕"} {closeCmd.state.lastMessage}
            </div>
          )}
        </ConfirmModal>
      )}

      {modifyDialog && (
        <ConfirmModal
          title={`Modify SL/TP · position #${modifyDialog.ticket}`}
          danger={false}
          confirmLabel="Send modify"
          busy={modifyCmd.state.running}
          onConfirm={() => void confirmModify()}
          onCancel={() => setModifyDialog(null)}
        >
          <div>
            <div className="small">{modifyDialog.summary}</div>
            <div className="row" style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <label className="l4-replay__field" style={{ display: "grid", gap: 3 }}>
                <span className="l4-note">stop loss (0 = clear)</span>
                <input className="input" style={{ inlineSize: 130 }} inputMode="decimal" value={modifyDialog.sl} onChange={(e) => setModifyDialog((d) => (d ? { ...d, sl: e.target.value } : d))} />
              </label>
              <label className="l4-replay__field" style={{ display: "grid", gap: 3 }}>
                <span className="l4-note">take profit (0 = clear)</span>
                <input className="input" style={{ inlineSize: 130 }} inputMode="decimal" value={modifyDialog.tp} onChange={(e) => setModifyDialog((d) => (d ? { ...d, tp: e.target.value } : d))} />
              </label>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>
              Sent as {`{ticket, stop_loss, take_profit}`} to POST /api/positions/modify — the OrderLifecycleManager validates against the broker symbol spec and the
              enforce_stop_loss gate and may refuse; the reply decides.
            </div>
            {modifyCmd.state.lastMessage && (
              <div className={`cmd-result ${modifyCmd.state.lastResult ? "ok" : "fail"}`}>
                {modifyCmd.state.lastResult ? "✓" : "✕"} {modifyCmd.state.lastMessage}
              </div>
            )}
          </div>
        </ConfirmModal>
      )}

      <Panel
        title={`Open positions (${livePositions.length})`}
        right={
          <>
            <span className="timestamp-note">source: {adapterSource}{snapshot ? ` · snapshot v${snapshot.state_version}` : ""}</span>
            <button aria-label="Refresh positions" className="btn small ghost" onClick={() => void positionsQuery.refetch()} disabled={positionsQuery.isFetching}>
              ⟳
            </button>
          </>
        }
        tight
      >
        {positionsQuery.isPending && livePositions.length === 0 ? (
          <LoadingState label="Reading broker adapter…" />
        ) : positionsQuery.isError && livePositions.length === 0 ? (
          <ErrorState
            message={positionsQuery.error instanceof ApiError ? positionsQuery.error.message : "Position endpoint unavailable"}
            requestId={positionsQuery.error instanceof ApiError ? positionsQuery.error.requestId : null}
            onRetry={() => void positionsQuery.refetch()}
          />
        ) : livePositions.length === 0 ? (
          <EmptyState message="No open positions." hint="Broker adapter snapshot is empty — nothing is hidden or estimated." />
        ) : (
          <SortableTable
            columns={columns}
            rows={livePositions}
            rowKey={posKey}
            initialSort={{ key: "time", dir: "desc" }}
            filter={(p, q) =>
              String(p.ticket ?? "").includes(q) || (p.symbol ?? "").toLowerCase().includes(q) || (p.type === 0 || String(p.type).toUpperCase().includes("BUY") ? "buy" : "sell").includes(q)
            }
            emptyMessage="No open positions."
          />
        )}
        {crossMismatch && (
          <div className="confirm-box" style={{ marginInline: 12, marginBlockEnd: 12, borderColor: "rgba(235,161,63,0.5)" }}>
            <span>
              The v1 adapter endpoint and the canonical snapshot disagree on open-position count ({String(v1Count)} vs {String(snapshotCount)}). Usually a
              refresh-timing gap — re-pull both; if it persists, check the reconciliation on the Trading page before trusting either.
            </span>
          </div>
        )}
      </Panel>

      <Panel
        title="Closed-trade ledger (broker-reconstructed)"
        right={
          <>
            <select className="select" value={ledgerStatus} onChange={(e) => setLedgerStatus(e.target.value)} aria-label="ledger status filter">
              <option value="">all statuses</option>
              <option value="OPEN">OPEN</option>
              <option value="CLOSED">CLOSED</option>
            </select>
            <AgeNote label="age" ageSec={historyQuery.dataUpdatedAt ? (Date.now() - historyQuery.dataUpdatedAt) / 1000 : null} />
          </>
        }
        tight
      >
        {historyQuery.isPending ? (
          <LoadingState label="Reading audit ledger…" />
        ) : historyQuery.isError ? (
          <ErrorState
            message={historyQuery.error instanceof ApiError ? historyQuery.error.message : "Ledger unavailable"}
            requestId={historyQuery.error instanceof ApiError ? historyQuery.error.requestId : null}
            onRetry={() => void historyQuery.refetch()}
          />
        ) : (historyQuery.data?.length ?? 0) === 0 ? (
          <EmptyState message="No closed trades in the ledger yet." hint="Rows appear once trades close and broker history (or the engine ledger fallback) is read." />
        ) : (
          <>
            <div className="l4-toolbar" style={{ padding: "8px 12px", justifyContent: "flex-end" }}>
              <button
                className="btn small ghost"
                onClick={() =>
                  downloadCsv({
                    filename: `nse-ledger-${stampForFilename()}.csv`,
                    headers: ["ticket", "symbol", "direction", "volume", "entry_price", "status", "pnl", "timestamp"],
                    rows: (historyQuery.data ?? []).map((r) => [r.ticket, r.symbol, r.direction, r.volume, r.entry_price, r.status, r.pnl, r.timestamp]),
                  })
                }
                title="exports exactly the rows returned by /api/account/trades — no re-query, no added values"
              >
                ⇩ export CSV
              </button>
            </div>
            <SortableTable
              columns={ledgerCols}
              rows={(historyQuery.data ?? []).filter((r) => livePositions.every((p) => !ledgerMatches(r, p)))}
              rowKey={(r, i) => `${r.ticket ?? "x"}-${i}`}
              initialSort={{ key: "time", dir: "desc" }}
              filter={(r, q) => String(r.ticket ?? "").includes(q) || (r.symbol ?? "").toLowerCase().includes(q)}
              emptyMessage="No ledger rows."
            />
            <div className="l4-note" style={{ padding: "6px 12px" }}>
              Ledger rows still present in the open-position table are hidden here (status filter aside) so a position is never counted twice on one screen.
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
