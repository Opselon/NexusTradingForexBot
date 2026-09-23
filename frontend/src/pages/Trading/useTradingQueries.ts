/**
 * PURPOSE:  Every Trading-page data subscription in one hook: broker (MT5)
           status, operator order flow, engine ledger OPEN rows, paginated
           execution history — plus the virtual⇄real reconciliation rows
           derived by matching ledger tickets against broker positions.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: engineApi.mt5Status, positionsApi.ledgerHistory /
           executionHistory, operatorApi.orders, EngineSnapshot prop, and the
           ReconRow shape from ./tradingTypes.
 * PROVIDES: useTradingQueries(snapshot, execPage) → the four query results plus
            the derived recon row list.
 * INVARIANTS: No endpoint is added or changed — same keys, same intervals, same
             retry flags as before; reconciliation stays pure arithmetic over
             the two reads, and a ticket present in only one read is shown as
             drift, never hidden.
 * EXTEND:   A new section's query goes here next to its siblings; anything that
          must combine queries is a useMemo in this file, not in a component.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { positionsApi } from "@/api/positionsApi";
import { operatorApi } from "@/pages/_shared/edgeApi";
import type { EngineSnapshot, Position } from "@/types/domain";
import type { ReconRow } from "./tradingTypes";

export function useTradingQueries(snapshot: EngineSnapshot | undefined, execPage: number) {
  const mt5Query = useQuery({
    queryKey: ["mt5-status"],
    queryFn: ({ signal }) => engineApi.mt5Status(signal),
    refetchInterval: 10_000,
  });

  const ordersQuery = useQuery({
    queryKey: ["operator-orders"],
    queryFn: ({ signal }) => operatorApi.orders(80, signal),
    refetchInterval: 20_000,
    retry: false,
  });

  const ledgerOpenQuery = useQuery({
    queryKey: ["ledger-open"],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 100, status: "OPEN" }, signal),
    refetchInterval: 20_000,
    retry: 1,
  });

  const execQuery = useQuery({
    queryKey: ["execution-history", execPage],
    queryFn: ({ signal }) => positionsApi.executionHistory({ page: execPage, page_size: 15 }, signal),
    placeholderData: (prev) => prev,
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

  return { mt5Query, ordersQuery, ledgerOpenQuery, execQuery, recon };
}
