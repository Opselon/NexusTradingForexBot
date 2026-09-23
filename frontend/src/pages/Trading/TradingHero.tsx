/**
 * TradingHero — hero header + KPI strip for /alt/trading (lane A).
 * Lane contract: presentation only. Every value comes verbatim from props;
 * never fetches, never derives state beyond formatting/labels. Baseline
 * keeps ALL original KPI facts (engine loop / execution mode / broker
 * adapter / terminal trading) — redesign them, never delete them.
 *
 * INVARIANTS (carried from baseline):
 *  - engine RUNNING/STOPPED, execution mode, adapter_class, trade_allowed,
 *    data_source, mt5 health detail, positions/pending counts, matched/drift
 *    recon counts and the session clock are all rendered VERBATIM — no value
 *    is computed, zero-filled or inferred here.
 *  - tone follows the baseline mapping only: LIVE (neg), RUNNING/ALLOWED
 *    (pos), STOPPED/RESTRICTED and the stateless adapter id (dim). The
 *    reconciliation tint restates the drift/matched props the page already
 *    chips below — no frontend verdicts.
 *  - time comes from the page's nowMs prop, formatted with
 *    Intl.DateTimeFormat (no date-fns in this repo).
 *  - refresh-all only calls the callback the page wired — no new fetches.
 *  - endpoint chips are provenance labels of the queries this page already
 *    runs (engineApi.snapshot = /api/status, engineApi.mt5Status,
 *    positionsApi.ledgerHistory) — never decoration, never new fetches.
 */
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

/** Engine-loop value tone — baseline mapping: RUNNING = pos, STOPPED = dim. */
function engineTone(running: boolean): "pos" | "dim" {
  return running ? "pos" : "dim";
}

export default function TradingHero({ snapshot, mt5Positions, pendingOrders, matched, drift, nowMs, refreshAll, anyFetching, engineCmd }: TradingHeroProps) {
  const running = snapshot.engine_running;
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
  const tradeAllowed = snapshot.account.trade_allowed;
  const reconTotal = matched + drift;
  const lastMessage = engineCmd.state.lastMessage;
  return (
    <section className="tr-hero">
      <span className="tr-hero-mesh" aria-hidden="true" />
      <div className="tr-hero-head">
        <div className="tr-hero-title">
          <span className="tr-kicker">
            <span className="tr-kicker-dot" aria-hidden="true" />
            Live control deck
            <span className="tr-kicker-rule" aria-hidden="true" />
          </span>
          <h1>Trading</h1>
          <div className="tr-sub">
            Engine readout, broker bridge, execution mode and forensic flow — every value is the backend reply.
            <span className="tr-fact"> engine {running ? "RUNNING" : "STOPPED"}</span>
            {lastMessage ? (
              <span className="tr-fact"> · last command: {lastMessage}</span>
            ) : null}
          </div>
          <div className="tr-hero-endpoints" aria-label="endpoints surfaced by this page">
            <span className="tr-ep" title="Provenance — backend endpoint /api/status (engine loop, mode, adapter, terminal flags rendered verbatim, never inferred here)">/api/status</span>
            <span className="tr-ep" title="Provenance — backend endpoint /api/mt5/status (broker positions + pending orders + MT5 health detail)">/api/mt5/status</span>
            <span className="tr-ep" title="Provenance — backend endpoint /api/account/trades?status=OPEN (reconciliation matched / drift by ticket)">/api/account/trades</span>
          </div>
        </div>
        <button
          type="button"
          className="tr-refresh tr-hero-refresh"
          onClick={refreshAll}
          disabled={anyFetching}
          aria-label="Refresh all trading panels"
          title="Refetch every trading query — the backend stays authoritative for every value."
        >
          {anyFetching ? (
            <span className="tr-hero-spin" aria-hidden="true" />
          ) : (
            <span className="tr-hero-refresh-ico" aria-hidden="true">⟳</span>
          )}
          <span>{anyFetching ? "Refreshing…" : "Refresh all"}</span>
        </button>
      </div>
      <div className="tr-hero-grid">
        {/* engine loop — fields: engine_running, state_version via sub */}
        <article className="tr-hero-card tr-hero-card--engine">
          <div className="tr-hero-card-head">
            <span className="tr-hero-ico" aria-hidden="true">◉</span>
            <span className="tr-hero-lab">Engine loop</span>
          </div>
          <div className={`tr-hero-val is-${engineTone(running)}`}>
            <span className="tr-hero-val-dot" aria-hidden="true" />
            {running ? "RUNNING" : "STOPPED"}
          </div>
          <div className="tr-hero-sub">backend-authoritative (state_version climbs while running)</div>
        </article>

        {/* execution mode — fields: runtime_mode / execution_mode, data_source */}
        <article className={`tr-hero-card${currentMode.startsWith("LIVE") ? " is-live" : ""}`}>
          <div className="tr-hero-card-head">
            <span className="tr-hero-ico" aria-hidden="true">▣</span>
            <span className="tr-hero-lab">Execution mode</span>
          </div>
          <div className={`tr-hero-val${currentMode.startsWith("LIVE") ? " is-neg" : " is-dim"}`}>
            {currentMode || "—"}
          </div>
          <div className="tr-hero-sub">{`data_source: ${snapshot.data_source ?? "—"}`}</div>
        </article>

        {/* broker adapter — fields: adapter_class, health.details.mt5 */}
        <article className="tr-hero-card">
          <div className="tr-hero-card-head">
            <span className="tr-hero-ico" aria-hidden="true">⇄</span>
            <span className="tr-hero-lab">Broker adapter</span>
          </div>
          <div className="tr-hero-val is-dim">{snapshot.adapter_class ?? "—"}</div>
          <div className="tr-hero-sub">{String(snapshot.health.details.mt5 ?? "")}</div>
        </article>

        {/* terminal trading — field: account.trade_allowed */}
        <article className="tr-hero-card">
          <div className="tr-hero-card-head">
            <span className="tr-hero-ico" aria-hidden="true">⚖</span>
            <span className="tr-hero-lab">Terminal trading</span>
          </div>
          <div className={`tr-hero-val${tradeAllowed === true ? " is-pos" : tradeAllowed === false ? " is-neg" : " is-dim"}`}>
            {tradeAllowed === null ? "—" : tradeAllowed ? "ALLOWED" : "RESTRICTED"}
          </div>
          <div className="tr-hero-sub">broker terminal trade_allowed</div>
        </article>
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
          <span className={`tr-kpi-value${drift > 0 ? " is-warn" : matched > 0 ? " is-pos" : ""}`}>
            {matched} / {reconTotal}
          </span>
          <span className="tr-kpi-cap">matched / total ledger rows</span>
        </div>
        <div className="tr-kpi">
          <span className="tr-kpi-label">Session clock</span>
          <span className="tr-kpi-value">
            {new Intl.DateTimeFormat(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(nowMs)}
          </span>
          <span className="tr-kpi-cap">local render time</span>
        </div>
      </div>
    </section>
  );
}
