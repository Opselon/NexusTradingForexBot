/**
 * PURPOSE:  Closed-trade ledger panel — broker-reconstructed history with a
 *           status filter, data-age note, dedup against the open-position
 *           blotter, and a client-side CSV export of the backend rows.
 * OWNER:    uiux-wave5-positions  (future edits to this file belong to this lane)
 * CONSUMES: positionsApi.ledgerHistory via react-query (same query key and
 *           cadence as before), openPositions for dedup, SortableTable, kit
 *           states, AgeNote, Density from ./density, positions.css.
 * PROVIDES: default LedgerPanel component.
 * INVARIANTS: honest loading/error/empty states; rows still open in the
 *             blotter are hidden here so a position is never counted twice;
 *             the CSV exports exactly the rows the backend returned — no
 *             re-query, no added values.
 * EXTEND:   new columns go in ledgerCols and must read fields that exist on
 *           AuditLedgerRow; filters stay client-side display state.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { positionsApi } from "@/api/positionsApi";
import type { AuditLedgerRow, Position } from "@/types/domain";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { AgeNote } from "@/pages/_shared/SectionState";
import { SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { formatDateTime, formatNumber, formatPnl, formatPrice } from "@/lib/format";
import { ApiError } from "@/types/api";
import { useI18n } from "@/stores/i18nStore";
import type { Density } from "./density";
import "@/pages/_shared/pages.css";
import "@/pages/Positions/positions.css";

function ledgerMatches(a: { ticket: number | null }, b: Position): boolean {
  return a.ticket !== null && a.ticket === b.ticket;
}

export default function LedgerPanel({ openPositions, density }: { openPositions: Position[]; density: Density }) {
  const t = useI18n((s) => s.t);
  const [ledgerStatus, setLedgerStatus] = useState("");

  const historyQuery = useQuery({
    queryKey: ["ledger-history", ledgerStatus],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 200, status: ledgerStatus || undefined }, signal),
    refetchInterval: 30_000,
    retry: 1,
  });

  const ledgerCols = useMemo<Array<Column<AuditLedgerRow>>>(
    () => [
      { key: "ticket", label: t("positions.th.ticket", "Ticket"), sortValue: (r) => r.ticket, render: (r) => r.ticket ?? "—" },
      { key: "symbol", label: t("positions.th.symbol", "Symbol"), sortValue: (r) => r.symbol, render: (r) => r.symbol ?? "—" },
      { key: "dir", label: t("positions.th.dir", "Dir"), sortValue: (r) => r.direction, render: (r) => r.direction ?? "—" },
      { key: "vol", label: t("positions.th.volume", "Volume"), num: true, sortValue: (r) => r.volume, render: (r) => formatNumber(r.volume) },
      { key: "entry", label: t("positions.th.entry", "Entry"), num: true, sortValue: (r) => r.entry_price, render: (r) => formatPrice(r.entry_price) },
      { key: "status", label: t("positions.th.status", "Status"), sortValue: (r) => r.status, render: (r) => r.status ?? "—" },
      {
        key: "pnl",
        label: t("positions.th.pnl", "PnL"),
        num: true,
        sortValue: (r) => r.pnl,
        render: (r) => <span className={(r.pnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>{formatPnl(r.pnl)}</span>,
      },
      { key: "time", label: t("positions.th.closed", "Closed"), sortValue: (r) => r.timestamp, render: (r) => formatDateTime(r.timestamp) },
    ],
    [t],
  );

  return (
    <Panel
      title={t("positions.panel.ledger", "Closed-trade ledger (broker-reconstructed)")}
      right={
        <>
          <select className="select" value={ledgerStatus} onChange={(e) => setLedgerStatus(e.target.value)} aria-label={t("positions.filter.ledger_aria", "ledger status filter")}>
            <option value="">{t("positions.filter.all_statuses", "all statuses")}</option>
            <option value="OPEN">OPEN</option>
            <option value="CLOSED">CLOSED</option>
          </select>
          <AgeNote label={t("positions.age.label", "age")} ageSec={historyQuery.dataUpdatedAt ? (Date.now() - historyQuery.dataUpdatedAt) / 1000 : null} />
        </>
      }
      tight
    >
      {historyQuery.isPending ? (
        <div className="pos-loading">
          <span className="pos-loading__label">{t("positions.loading.ledger", "Reading audit ledger…")}</span>
          <Skeleton count={4} />
        </div>
      ) : historyQuery.isError ? (
        <ErrorState
          message={historyQuery.error instanceof ApiError ? historyQuery.error.message : t("positions.error.ledger", "Ledger unavailable")}
          requestId={historyQuery.error instanceof ApiError ? historyQuery.error.requestId : null}
          onRetry={() => void historyQuery.refetch()}
        />
      ) : (historyQuery.data?.length ?? 0) === 0 ? (
        <EmptyState message={t("positions.empty.ledger", "No closed trades in the ledger yet.")} hint={t("positions.empty.ledger_hint", "Rows appear once trades close and broker history (or the engine ledger fallback) is read.")} />
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
              title={t("positions.csv.title", "exports exactly the rows returned by /api/account/trades — no re-query, no added values")}
            >
              {t("positions.csv.export", "⇩ export CSV")}
            </button>
          </div>
          <SortableTable
            columns={ledgerCols}
            rows={(historyQuery.data ?? []).filter((r) => openPositions.every((p) => !ledgerMatches(r, p)))}
            rowKey={(r, i) => `${r.ticket ?? "x"}-${i}`}
            initialSort={{ key: "time", dir: "desc" }}
            filter={(r, q) => String(r.ticket ?? "").includes(q) || (r.symbol ?? "").toLowerCase().includes(q)}
            emptyMessage={t("positions.empty.rows_ledger", "No ledger rows.")}
            dense={density === "compact"}
          />
          <div className="l4-note" style={{ padding: "6px 12px" }}>
            {t("positions.ledger.note", "Ledger rows still present in the open-position table are hidden here (status filter aside) so a position is never counted twice on one screen.")}
          </div>
        </>
      )}
    </Panel>
  );
}
