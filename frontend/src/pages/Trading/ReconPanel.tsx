/**
 * PURPOSE  — the virtual vs. real reconciliation section of the Trading page:
 *            engine ledger OPEN rows (/api/account/trades?status=OPEN) matched
 *            by ticket against broker positions (/api/mt5/status). A drift is
 *            shown as drift, never auto-hidden.
 * OWNER    — Trading page (pages/Trading). Extracted verbatim from
 *            TradingPage.tsx by the wave-7 integrator (line law). The queries,
 *            the ReconRow type, the reconciliation memo, the drift/matched
 *            counts and the whole Panel are byte-identical to the original
 *            block; only the props seam is new.
 * CONSUMES — ledgerOpenQuery + the shared mt5 status query (both moved here)
 *            and the canonical snapshot as the broker fallback.
 * PROVIDES — <ReconPanel snapshot={snapshot} mt5Query={mt5Query} />.
 * INVARANTS- matched/drift counts and every rendered value are unchanged; a
 *            MATCHED row stays MATCHED; mt5Query stays owned by the page
 *            (the Pending-orders panel reads it too), so it is passed in.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { positionsApi } from "@/api/positionsApi";
import { EmptyState, ErrorState, Panel, PositionSideBadge, Skeleton } from "@/components/primitives";
import { InfoChip, SortableTable } from "@/pages/_shared/widgets";
import { formatNumber, formatPrice } from "@/lib/format";
import { ApiError } from "@/types/api";
import type { EngineSnapshot, Position } from "@/types/domain";

type ReconRow = {
ticket: string;
engine: { symbol: string | null; direction: string | null; volume: number | null } | null;
broker: Position | null;
state: "MATCHED" | "ENGINE_ONLY" | "BROKER_ONLY";
};

type Mt5StatusQuery = ReturnType<
  typeof useQuery<Awaited<ReturnType<typeof engineApi.mt5Status>>>
>;

interface Props {
  snapshot: EngineSnapshot;
  mt5Query: Mt5StatusQuery;
}

export function ReconPanel({ snapshot, mt5Query }: Props) {
  const ledgerOpenQuery = useQuery({
    queryKey: ["ledger-open"],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 100, status: "OPEN" }, signal),
    refetchInterval: 20_000,
    retry: 1,
  });
  // ---- reconciliation (engine ledger OPEN vs broker positions) ------------
  const recon = useMemo<ReconRow[]>(() => {
    const engineRows = ledgerOpenQuery.data ?? [];
    const brokerRows: Position[] = mt5Query.data?.positions ?? snapshot?.positions ?? [];
    const byTicket = new Map<string, ReconRow>();
    for (const e of engineRows) {
  if (e.ticket === null) continue;
  byTicket.set(String(e.ticket), {
    ticket: String(e.ticket),
    engine: { symbol: e.symbol, direction: e.direction, volume: e.volume },
    broker: null,
    state: "ENGINE_ONLY",
  });
    }
    for (const b of brokerRows) {
  if (b.ticket === null) continue;
  const key = String(b.ticket);
  const prev = byTicket.get(key);
  if (prev) {
    byTicket.set(key, { ...prev, broker: b, state: "MATCHED" });
  } else {
    byTicket.set(key, { ticket: key, engine: null, broker: b, state: "BROKER_ONLY" });
  }
    }
    return [...byTicket.values()].sort((a, b) => Number(b.state === "MATCHED") - Number(a.state === "MATCHED"));
}, [ledgerOpenQuery.data, mt5Query.data, snapshot?.positions]);
  const drift = recon.filter((r) => r.state !== "MATCHED");
  const matched = recon.length - drift.length;
  // Virtual ↔ real reconciliation (verbatim block, extracted for the line law).
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
                    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
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
          <div className="confirm-box" style={{ marginInline: 12, marginBlock: 12, borderColor: "rgba(235,161,63,0.5)" }}>
            <span>
              <b>{drift.length} unreconciled row(s).</b> ENGINE ONLY usually means the broker rejected/closed without the ledger catching the deal yet;
              BROKER ONLY means a position the engine did not open (manual terminal action or restart gap). Investigate before enabling new risk.
            </span>
          </div>
        )}
      </Panel>
  );

}
