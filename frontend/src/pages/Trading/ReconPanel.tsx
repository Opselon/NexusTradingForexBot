/**
 * PURPOSE:  Virtual ⇄ real reconciliation: engine ledger OPEN rows matched by
 *           ticket against the broker positions read, with per-row state chips
 *           and an unreconciled-drift warning that is never auto-hidden.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: ledgerOpen + mt5 query results, ReconRow from ./tradingTypes,
 *           SortableTable, InfoChip, PositionSideBadge, Skeleton /
 *           ErrorState / EmptyState, ApiError, format* helpers.
 * PROVIDES: default ReconPanel component (props: ledgerOpenQuery, mt5Query,
 *           recon).
 * INVARIANTS: drift stays visible as drift — ENGINE_ONLY / BROKER_ONLY rows are
 *             labeled, the empty case is stated as "a clean, consistent EMPTY",
 *             and no verdict is computed beyond the ticket match itself.
 * EXTEND:   matching rules live in useTradingQueries; new columns read
 *           existing ReconRow fields only.
 */
import type { AuditLedgerRow, MT5Status } from "@/types/domain";
import type { QueryLike } from "@/pages/_shared/SectionState";
import { InfoChip, SortableTable } from "@/pages/_shared/widgets";
import { EmptyState, ErrorState, Panel, PositionSideBadge, Skeleton } from "@/components/primitives";
import type { ReconRow } from "./tradingTypes";
import { ApiError } from "@/types/api";
import { formatNumber, formatPrice } from "@/lib/format";

interface Props {
  ledgerOpenQuery: QueryLike<AuditLedgerRow[]>;
  mt5Query: QueryLike<MT5Status>;
  recon: ReconRow[];
}

export default function ReconPanel({ ledgerOpenQuery, mt5Query, recon }: Props) {
  const drift = recon.filter((r) => r.state !== "MATCHED");
  const matched = recon.length - drift.length;

  return (
    <Panel
      title="Virtual ⇄ real reconciliation"
      right={
        <>
          <InfoChip k="matched" v={matched} tone={drift.length === 0 && matched > 0 ? "good" : ""} />
          <InfoChip k="drift" v={drift.length} tone={drift.length > 0 ? "bad" : ""} />
        </>
      }
      tight
    >
      <div className="l4-note" style={{ padding: "8px 12px 0" }}>
        Engine ledger OPEN rows (/api/account/trades?status=OPEN) matched by ticket against broker positions (/api/mt5/status).
        {mt5Query.isPending ? " broker read pending…" : mt5Query.isError ? " ⚠ broker read FAILED — positions below fall back to the canonical snapshot." : ""}
      </div>
      {ledgerOpenQuery.isPending ? (
        <div style={{ padding: 12 }}><Skeleton count={3} /></div>
      ) : ledgerOpenQuery.isError ? (
        <ErrorState
          message={ledgerOpenQuery.error instanceof ApiError ? ledgerOpenQuery.error.message : "Engine ledger unavailable"}
          requestId={ledgerOpenQuery.error instanceof ApiError ? ledgerOpenQuery.error.requestId : null}
          onRetry={() => void ledgerOpenQuery.refetch()}
        />
      ) : recon.length === 0 ? (
        <EmptyState message="Nothing to reconcile — no open ledger rows and no broker positions." hint="A clean, consistent EMPTY. Not a hidden drift." />
      ) : (
        <SortableTable
          columns={[
            { key: "ticket", label: "Ticket", sortValue: (r) => r.ticket, render: (r) => r.ticket },
            {
              key: "engine",
              label: "Engine ledger",
              sortValue: (r) => r.engine?.symbol ?? null,
              render: (r) =>
                r.engine ? `${r.engine.symbol ?? "—"} ${r.engine.direction ?? "?"} ${formatNumber(r.engine.volume)}` : <span className="faint">absent</span>,
            },
            {
              key: "broker",
              label: "Broker position",
              sortValue: (r) => r.broker?.symbol ?? null,
              render: (r) =>
                r.broker ? (
                  <span className="trd-recon-broker">
                    {r.broker.symbol ?? "—"} <PositionSideBadge type={r.broker.type} /> {formatNumber(r.broker.volume)} @ {formatPrice(r.broker.price_open)}
                  </span>
                ) : (
                  <span className="faint">absent</span>
                ),
            },
            {
              key: "state",
              label: "State",
              sortValue: (r) => r.state,
              render: (r) => (
                <span className={`l4-chip ${r.state === "MATCHED" ? "good" : "warn"}`}>
                  {r.state === "MATCHED" ? "✓ ticket+symbol" : r.state === "ENGINE_ONLY" ? "ENGINE ONLY (not on broker)" : "BROKER ONLY (untracked)"}
                </span>
              ),
            },
          ]}
          rows={recon}
          rowKey={(r) => r.ticket}
          emptyMessage="No rows."
          maxHeight={320}
        />
      )}
      {drift.length > 0 && (
        <div className="confirm-box trd-drift" style={{ marginInline: 12, marginBlock: 12 }}>
          <span>
            <b>{drift.length} unreconciled row(s).</b> ENGINE ONLY usually means the broker rejected/closed without the ledger catching the deal yet;
            BROKER ONLY means a position the engine did not open (manual terminal action or restart gap). Investigate before enabling new risk.
          </span>
        </div>
      )}
    </Panel>
  );
}
