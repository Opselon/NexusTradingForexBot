/**
 * PURPOSE:  The four-card state rail at the top of the trade desk: engine loop,
           execution mode, broker adapter class, terminal trade permission —
           each value restating a backend field.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot (engine_running, runtime_mode/execution_mode,
           data_source, adapter_class, health, account.trade_allowed),
           MetricCard from components/primitives, currentModeOf.
 * PROVIDES: default DeskStrip component (the top grid of the Trading page).
 * INVARANTS: no fabricated value: every card maps one snapshot field; the LIVE
             tone is restated through the theme's existing neg token.
 * EXTEND:   A fifth card reads another existing snapshot field — never a new
          network call.
 */

import type { EngineSnapshot } from "@/types/domain";
import { MetricCard } from "@/components/primitives";
import { currentModeOf } from "./tradingTypes";

export default function DeskStrip({ snapshot }: { snapshot: EngineSnapshot }) {
  const running = snapshot.engine_running;
  const currentMode = currentModeOf(snapshot);

  return (
    <div className="grid cols-4">
      <MetricCard label="Engine loop" value={running ? "RUNNING" : "STOPPED"} tone={running ? "pos" : "dim"} sub="backend-authoritative (state_version climbs while running)" />
      <MetricCard label="Execution mode" value={currentMode || "—"} tone={currentMode.startsWith("LIVE") ? "neg" : "dim"} sub={`data_source: ${snapshot.data_source ?? "—"}`} />
      <MetricCard label="Broker adapter" value={snapshot.adapter_class ?? "—"} tone="dim" sub={String(snapshot.health.details.mt5 ?? "")} />
      <MetricCard
        label="Terminal trading"
        value={snapshot.account.trade_allowed === null ? "—" : snapshot.account.trade_allowed ? "ALLOWED" : "RESTRICTED"}
        tone={snapshot.account.trade_allowed === true ? "pos" : snapshot.account.trade_allowed === false ? "neg" : "dim"}
        sub="broker terminal trade_allowed"
      />
    </div>
  );
}
