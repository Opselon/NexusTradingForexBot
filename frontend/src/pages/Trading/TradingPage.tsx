/**
 * PURPOSE:  Trading page — composition of the trade-desk components. Data
 *           subscriptions live in useTradingQueries; every section is a
 *           colocated component in this folder. This file wires nothing but
 *           props and keeps the page-level guard (no snapshot → skeleton).
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot + nowMs props, useTradingQueries, the colocated
 *           DeskStrip / EnginePanel / ModePanel / MarketPanel /
 *           PendingOrdersPanel / OrderFlowPanel / ReconPanel /
 *           SmcReadoutPanel / ExecutionsPanel components.
 * PROVIDES: default TradingPage (routed at /trading by AppShell).
 * INVARIANTS: every backend-supported action keeps its existing wiring —
 *             start/stop engine, execution-mode switch with the unchanged
 *             typed LIVE confirmation; manual order placement / cancel has no
 *             backend route and stays absent. Honest loading / empty / error
 *             states per section.
 * EXTEND:   new sections become a colocated component and get composed here;
 *           keep this file a composition, not a component implementation.
 */
import { useState } from "react";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { Panel, Skeleton } from "@/components/primitives";
import type { EngineSnapshot } from "@/types/domain";
import { useTradingQueries } from "./useTradingQueries";
import { currentModeOf } from "./tradingTypes";
import DeskStrip from "./DeskStrip";
import EnginePanel from "./EnginePanel";
import ModePanel from "./ModePanel";
import MarketPanel from "./MarketPanel";
import PendingOrdersPanel from "./PendingOrdersPanel";
import OrderFlowPanel from "./OrderFlowPanel";
import ReconPanel from "./ReconPanel";
import SmcReadoutPanel from "./SmcReadoutPanel";
import ExecutionsPanel from "./ExecutionsPanel";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

export default function TradingPage({ snapshot, nowMs }: Props) {
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const [execPage, setExecPage] = useState(1);
  const { mt5Query, ordersQuery, ledgerOpenQuery, execQuery, recon } = useTradingQueries(snapshot, execPage);

  if (!snapshot) {
    return (
      <div>
        <Panel title="Trading">
          <Skeleton count={5} />
        </Panel>
      </div>
    );
  }

  const running = snapshot.engine_running;
  const currentMode = currentModeOf(snapshot);

  return (
    <div>
      <DeskStrip snapshot={snapshot} />

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <EnginePanel running={running} cmd={engineCmd} />
        <ModePanel currentMode={currentMode} cmd={modeCmd} />
      </div>

      <div className="grid cols-2">
        <MarketPanel snapshot={snapshot} />
        <PendingOrdersPanel mt5Query={mt5Query} />
      </div>

      <OrderFlowPanel query={ordersQuery} nowMs={nowMs} priceDigits={snapshot.price_digits ?? 2} />

      <ReconPanel ledgerOpenQuery={ledgerOpenQuery} mt5Query={mt5Query} recon={recon} />

      <SmcReadoutPanel snapshot={snapshot} />

      <ExecutionsPanel execQuery={execQuery} snapshot={snapshot} currentMode={currentMode} page={execPage} onPage={setExecPage} />
    </div>
  );
}
