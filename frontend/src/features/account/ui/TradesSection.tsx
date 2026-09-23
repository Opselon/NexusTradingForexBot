/**
 * Closed trades table (forensic drawer split out — see TradeForensicsDrawer).
 *
 * Rows come from /api/account/trades (broker-reconstructed, ledger fallback —
 * the backend decides the producer). The drawer chunk loads on first ticket
 * open behind a local Suspense boundary; row leaves are memoized so the 45 s
 * trades poll re-renders do not touch unchanged rows.
 */

import { Suspense, lazy, memo, useCallback, useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { useAccountTrades } from "../hooks";
import type { TradeVM } from "../model";
import { DASH, FreshnessNote, errorProps, moneyOrDash } from "./shared";

const PAGE = 25;

const TradeForensicsDrawer = lazy(() => import("./TradeForensicsDrawer"));

/** One closed-trade row — memo leaf: identical trade data renders identically. */
const TradeRow = memo(function TradeRow({ t, onOpen }: { t: TradeVM; onOpen: (ticket: number) => void }) {
  return (
    <tr>
      <td>
        {t.numericId !== null ? (
          <button className="btn small ghost" onClick={() => onOpen(Number(t.numericId))}>
            #{t.id}
          </button>
        ) : (
          t.id
        )}
      </td>
      <td>{t.symbol}</td>
      <td>
        <span className={`badge ${t.direction === "BUY" ? "good" : t.direction === "SELL" ? "bad" : "unknown"}`}>{t.direction}</span>
      </td>
      <td className="num">{formatNumber(t.volume)}</td>
      <td className="inline-mono">
        {t.entryPrice !== null ? formatPrice(t.entryPrice) : DASH} → {t.exitPrice !== null ? formatPrice(t.exitPrice) : DASH}
      </td>
      <td className={`num ${t.netPnl === null ? "" : t.netPnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>{moneyOrDash(t.netPnl, true)}</td>
      <td className="tiny faint">{t.realizedR === null ? DASH : `${t.realizedR.toFixed(2)}R`}</td>
      <td>{t.status}</td>
      <td>{t.closedAt ? formatDateTime(t.closedAt) : DASH}</td>
    </tr>
  );
});

export function TradesSection() {
  const [offset, setOffset] = useState(0);
  const [ticket, setTicket] = useState<number | null>(null);
  const trades = useAccountTrades(PAGE, offset);

  const rows = trades.data?.trades ?? [];

  /* Stable callbacks — inline closures would defeat the memoized rows and
   * drawer on every 45 s trades-poll re-render. */
  const openTicket = useCallback((t: number) => setTicket(t), []);
  const closeTicket = useCallback(() => setTicket(null), []);

  return (
    <Panel
      title={`Closed trades (${rows.length})`}
      right={
        <>
          <button className="btn small ghost" onClick={() => setOffset((o) => Math.max(0, o - PAGE))} disabled={offset === 0 || trades.isFetching}>
            ← newer
          </button>
          <span className="timestamp-note">offset {offset}</span>
          <button className="btn small ghost" onClick={() => setOffset((o) => o + PAGE)} disabled={rows.length < PAGE || trades.isFetching}>
            older →
          </button>
          <FreshnessNote updatedAtMs={trades.dataUpdatedAt ?? null} label="trades" staleAfterMs={180_000} />
        </>
      }
    >
      {trades.isPending ? (
        <Skeleton count={6} height={20} />
      ) : trades.isError ? (
        <ErrorState {...errorProps(trades.error)} onRetry={() => trades.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message="No historical trades found yet." hint="Broker history sync pending — no rows are invented in the meantime." />
      ) : (
        <DataTable
          headers={[
            { label: "ticket" },
            { label: "symbol" },
            { label: "dir" },
            { label: "vol", num: true },
            { label: "entry → exit" },
            { label: "net PnL", num: true },
            { label: "R", num: false },
            { label: "status" },
            { label: "closed" },
          ]}
        >
          {rows.map((t, i) => (
            <TradeRow key={`${t.id}-${i}`} t={t} onOpen={openTicket} />
          ))}
        </DataTable>
      )}
      {ticket !== null && (
        <Suspense fallback={<Skeleton count={5} height={40} />}>
          <TradeForensicsDrawer ticket={ticket} onClose={closeTicket} />
        </Suspense>
      )}
    </Panel>
  );
}
