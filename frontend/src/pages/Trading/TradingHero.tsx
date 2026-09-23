/**
 * TradingHero — hero header + KPI strip for /alt/trading (lane A).
 * Lane contract: presentation only. Every value comes verbatim from props;
 * never fetches, never derives state beyond formatting/labels. Baseline
 * keeps ALL original KPI facts (engine loop / execution mode / broker
 * adapter / terminal trading) — redesign them, never delete them.
 */
import { MetricCard } from "@/components/primitives";
import type { EngineSnapshot } from "@/types/domain";
import "./tradingHero.css";

export interface TradingHeroProps {
  snapshot: EngineSnapshot;
  mt5Positions: number;
  pendingOrders: number;
  matched: number;
  drift: number;
  nowMs: number;
  refreshAll: () => void;
  anyFetching: boolean;
  engineCmd: { state: { running: boolean; lastMessage?: string | null; lastResult?: boolean | null } };
}

export default function TradingHero({ snapshot, mt5Positions, pendingOrders, matched, drift, nowMs, refreshAll, anyFetching, engineCmd }: TradingHeroProps) {
  const running = snapshot.engine_running;
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
  return (
    <div className="tr-hero">
      <div className="tr-hero-head">
        <div className="tr-hero-title">
          <span className="tr-kicker">Live control deck</span>
          <h1>Trading</h1>
          <div className="tr-sub">
            Engine readout, broker bridge, execution mode and forensic flow — every value is the backend reply.
            <span className="tr-fact"> engine {running ? "RUNNING" : "STOPPED"}</span>
            {engineCmd.state.lastMessage ? (
              <span className="tr-fact"> · last command: {engineCmd.state.lastMessage}</span>
            ) : null}
          </div>
        </div>
        <button
          className="tr-refresh"
          onClick={refreshAll}
          disabled={anyFetching}
          aria-label="Refresh all trading panels"
        >
          <span className={`tr-refresh-spin ${anyFetching ? "tr-spinning" : ""}`} aria-hidden="true">&#8635;</span>
          {anyFetching ? "Refreshing…" : "Refresh all"}
        </button>
      </div>
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
      <div className="tr-kpis">
        <div className="tr-kpi">
          <span className="tr-kpi-label">Broker positions</span>
          <span className="tr-kpi-value">{mt5Positions}</span>
          <span className="tr-kpi-cap">open in MT5</span>
        </div>
        <div className="tr-kpi">
          <span className="tr-kpi-label">Pending orders</span>
          <span className="tr-kpi-value">{pendingOrders}</span>
          <span className="tr-kpi-cap">unfilled at the broker</span>
        </div>
        <div className="tr-kpi">
          <span className="tr-kpi-label">Reconciliation</span>
          <span className="tr-kpi-value">{matched} / {matched + drift}</span>
          <span className="tr-kpi-cap">matched / total ledger rows</span>
        </div>
        <div className="tr-kpi">
          <span className="tr-kpi-label">Session clock</span>
          <span className="tr-kpi-value">{new Intl.DateTimeFormat(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(nowMs)}</span>
          <span className="tr-kpi-cap">local render time</span>
        </div>
      </div>
    </div>
  );
}
