/**
 * PURPOSE:  Positions — live backend position state + close / SL-TP commands,
 *           upgraded to a trade-style blotter (side rails, magnitude-ramped
 *           P&L, derived aggregate strip, density toggle, keyboard rows).
 * OWNER:    uiux-wave5-positions  (future edits to this file belong to this lane)
 * CONSUMES: positionsApi (v1 open positions; ledger history lives in
 *           ./LedgerPanel), tradingApi
 *           (close/modify via the OrderLifecycleManager), EngineSnapshot prop,
 *           kit primitives, pages/_shared widgets, positions.css.
 * PROVIDES: default PositionsPage (routed at /positions by AppShell), which
 *           composes ./AggregateStrip, ./BlotterTable and ./LedgerPanel.
 * INVARIANTS: honest empty/loading/error states, no fabricated data; the
 *             backend response decides every command outcome (confirmation
 *             dialogs only prevent mis-clicks); derived figures are labeled
 *             "derived"; no user-visible control or string may stop working.
 * EXTEND:   new columns go in the `columns` useMemo and must read fields that
 *           exist on Position/AuditLedgerRow; new aggregates belong in
 *           AggregateStrip with a proven flag; styles live in positions.css
 *           under the pos- prefix (this file keeps the shared kit read-only).
 *
 * Data: canonical snapshot positions (engine-merged) cross-checked with the
 * v1 adapter endpoint. The floating-PnL summary is plain arithmetic over
 * backend per-position values only, and a broker-vs-adapter cross-check count
 * keeps drift between the two reads visible, not hidden. The closed-trade
 * ledger keeps its status filter, pagination slice, and a client-side CSV
 * export of exactly the rows the backend returned.
 */

import { useMemo, useState, type CSSProperties } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { positionsApi } from "@/api/positionsApi";
import { tradingApi } from "@/api/tradingApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import type { EngineSnapshot, Position } from "@/types/domain";
import {
  ConfirmModal,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  PositionSideBadge,
  Skeleton,
} from "@/components/primitives";
import { type Column } from "@/pages/_shared/widgets";
import { formatDateTime, formatNumber, formatPnl, formatPrice, positionSide } from "@/lib/format";
import { ApiError } from "@/types/api";
import BlotterTable from "./BlotterTable";
import AggregateStrip from "./AggregateStrip";
import LedgerPanel from "./LedgerPanel";
import { readDensity, writeDensity, type Density } from "./density";
import "@/pages/_shared/pages.css";
import "@/pages/Positions/positions.css";

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

/** Stable initial-sort object — memoized BlotterTable compares it by reference. */
const POS_SORT: { key: string; dir: "asc" | "desc" } = { key: "time", dir: "desc" };

function posKey(p: Position, i: number): string {
  return String(p.ticket ?? `${p.symbol}-${i}`);
}

/** Row rail class — restates Position.type (0=BUY/long, 1=SELL/short). */
function posRowClass(p: Position): string | undefined {
  const side = positionSide(p.type);
  return side === "BUY" ? "pos-row--long" : side === "SELL" ? "pos-row--short" : undefined;
}

/** Same predicate the table filter has always used — ticket / symbol / side
 *  text; `q` arrives lower-cased exactly as before. */
function matchPosFilter(p: Position, q: string): boolean {
  return (
    String(p.ticket ?? "").includes(q) ||
    (p.symbol ?? "").toLowerCase().includes(q) ||
    (p.type === 0 || String(p.type).toUpperCase().includes("BUY") ? "buy" : "sell").includes(q)
  );
}

export default function PositionsPage({ snapshot }: Props) {
  const queryClient = useQueryClient();
  const closeCmd = useMutationFeedback();
  const modifyCmd = useMutationFeedback();
  const [closeDialog, setCloseDialog] = useState<CloseDialog | null>(null);
  const [modifyDialog, setModifyDialog] = useState<ModifyDialog | null>(null);
  // Display-only preferences (client state, persisted under the lane key).
  const [density, setDensity] = useState<Density>(readDensity);
  const [posFilter, setPosFilter] = useState("");

  const applyDensity = (d: Density): void => {
    setDensity(d);
    writeDensity(d);
  };

  const positionsQuery = useQuery({
    queryKey: ["v1-positions"],
    queryFn: ({ signal }) => positionsApi.openPositions(signal),
    refetchInterval: 10_000,
    retry: 1,
  });

  const livePositions: Position[] = positionsQuery.data?.positions ?? snapshot?.positions ?? [];
  const adapterSource = positionsQuery.data ? "v1 adapter" : snapshot ? "canonical snapshot (v1 endpoint pending)" : "—";
  // Cross-check: broker positions carried inside the canonical snapshot vs
  // the v1 endpoint — a visible count, not a silent preference.
  const snapshotCount = snapshot?.positions?.length ?? null;
  const v1Count = positionsQuery.data?.positions.length ?? null;
  const crossMismatch = snapshotCount !== null && v1Count !== null && snapshotCount !== v1Count;

  /** Rows currently visible in the blotter (page-level filter) — the strip
   *  aggregates THESE rows, so it always matches what is on screen. */
  const visiblePositions = useMemo(() => {
    const q = posFilter.trim().toLowerCase();
    return q ? livePositions.filter((p) => matchPosFilter(p, q)) : livePositions;
  }, [livePositions, posFilter]);

  /** Largest |profit| on screen — the P&L colour ramp keys its intensity to
   *  it (derived display logic; no displayed value is changed by this). */
  const maxAbsPnl = useMemo(() => {
    let m = 0;
    for (const p of visiblePositions) {
      if (typeof p.profit === "number" && Number.isFinite(p.profit)) m = Math.max(m, Math.abs(p.profit));
    }
    return m;
  }, [visiblePositions]);

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
      {
        key: "side",
        label: "Side",
        sortValue: (p) => (Number(p.type) === 0 ? "BUY" : Number(p.type) === 1 ? "SELL" : "—"),
        render: (p) => {
          const side = positionSide(p.type);
          const label = side === "BUY" ? "long" : side === "SELL" ? "short" : "—";
          const dataSide = side === "BUY" ? "long" : side === "SELL" ? "short" : "unknown";
          return (
            <span className="pos-side-cell">
              <PositionSideBadge type={p.type} />
              <span className="pos-side-cell__sub" data-side={dataSide} title={`${label} — trade reading of the ${side} side field`}>
                {label}
              </span>
            </span>
          );
        },
      },
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
        render: (p) => {
          const v = p.profit;
          if (v === null || v === undefined || !Number.isFinite(v)) {
            return (
              <span className="faint" title="no numeric profit value in the payload — nothing is rendered in its place">
                —
              </span>
            );
          }
          // Signed ramp: hue from the sign, alpha from |value| vs the largest
          // |P&L| currently on screen (55%..100% of the theme token).
          const intensity = maxAbsPnl > 0 ? Math.sqrt(Math.abs(v) / maxAbsPnl) : 0;
          const mix = Math.round((55 + 45 * intensity) * 100) / 100;
          return (
            <span
              className={`pos-pnl ${v >= 0 ? "pos-pnl--up" : "pos-pnl--down"}${intensity >= 0.8 ? " pos-pnl--hot" : ""}`}
              style={{ "--pos-pnl-mix": `${mix}%` } as CSSProperties}
              title={`backend profit field — sign colours it, intensity ${Math.round(intensity * 100)}% of the largest |P&L| on screen (derived display)`}
            >
              {formatPnl(v)}
            </span>
          );
        },
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
    [snapshot?.price_digits, maxAbsPnl],
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
            <span className="pos-density" role="group" aria-label="row density">
              <button
                className="btn small ghost pos-density__btn"
                aria-pressed={density === "comfortable"}
                title="comfortable row padding (theme default)"
                onClick={() => applyDensity("comfortable")}
              >
                Comfortable
              </button>
              <button
                className="btn small ghost pos-density__btn"
                aria-pressed={density === "compact"}
                title="compact row padding — cells and columns are never hidden"
                onClick={() => applyDensity("compact")}
              >
                Compact
              </button>
            </span>
            <button aria-label="Refresh positions" className="btn small ghost" onClick={() => void positionsQuery.refetch()} disabled={positionsQuery.isFetching}>
              ⟳
            </button>
          </>
        }
        tight
      >
        {positionsQuery.isPending && livePositions.length === 0 ? (
          <div className="pos-loading">
            <span className="pos-loading__label">Reading broker adapter…</span>
            <Skeleton count={5} />
          </div>
        ) : positionsQuery.isError && livePositions.length === 0 ? (
          <ErrorState
            message={positionsQuery.error instanceof ApiError ? positionsQuery.error.message : "Position endpoint unavailable"}
            requestId={positionsQuery.error instanceof ApiError ? positionsQuery.error.requestId : null}
            onRetry={() => void positionsQuery.refetch()}
          />
        ) : livePositions.length === 0 ? (
          <EmptyState message="No open positions." hint="Broker adapter snapshot is empty — nothing is hidden or estimated." />
        ) : (
          <>
            {/* Derived strip over the rows currently on screen (hides itself at 0). */}
            <AggregateStrip rows={visiblePositions} />
            <BlotterTable
              columns={columns}
              rows={visiblePositions}
              totalCount={livePositions.length}
              rowKey={posKey}
              initialSort={POS_SORT}
              query={posFilter}
              onQueryChange={setPosFilter}
              rowClassName={posRowClass}
              density={density}
              emptyMessage="No open positions."
            />
          </>
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

      <LedgerPanel openPositions={livePositions} density={density} />
    </div>
  );
}
