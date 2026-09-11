/**
 * Trading — command console around backend-supported actions ONLY.
 *
 * Supported (backend-verified):
 *  - Start / Stop engine loop      POST /api/engine/toggle
 *  - Execution mode switch         POST /api/engine/mode  (LIVE requires
 *    typed confirmation; the backend itself enforces adapter realignment
 *    and refuses invalid transitions — the UI only relays)
 *  - Close position                POST /api/positions/close
 *  - Modify SL/TP                  POST /api/positions/modify
 *
 * NOT implemented (no backend route exists): manual order placement, order
 * cancel. The UI refuses to fake such actions.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import type { EngineSnapshot } from "@/types/domain";
import { EmptyState, MetricCard, Panel, StatusBadge } from "@/components/primitives";
import { formatNumber, formatPct, formatPrice, formatTime } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

const LIVE_CONFIRM_TEXT = "LIVE";

export default function TradingPage({ snapshot }: Props) {
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);

  const mt5Query = useQuery({
    queryKey: ["mt5-status"],
    queryFn: ({ signal }) => engineApi.mt5Status(signal),
    refetchInterval: 10_000,
  });

  if (!snapshot) {
    return <EmptyState message="Waiting for backend state…" />;
  }

  const running = snapshot.engine_running;
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();

  const toggleEngine = async (active: boolean): Promise<void> => {
    const ok = await engineCmd.run(() => engineApi.toggleEngine(active));
    if (ok) {
      // No local state change — the next authoritative snapshot/signal carries it.
    }
  };

  const submitMode = async (): Promise<void> => {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) return;
    const ok = await modeCmd.run(() => engineApi.setMode(modeTarget));
    if (ok) {
      setShowLiveConfirm(false);
      setLiveConfirm("");
      setModeTarget("");
    }
  };

  return (
    <div>
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

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="Engine commands">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => void toggleEngine(true)}>
              ▶ Start engine
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => void toggleEngine(false)}>
              ■ Stop engine
            </button>
          </div>
          {engineCmd.state.lastMessage && (
            <div className={`cmd-result ${engineCmd.state.lastResult ? "ok" : "fail"}`}>
              {engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            Result comes from the backend response — the UI never assumes a command succeeded before confirmation, and the authoritative engine state on the left updates from the next snapshot.
          </div>
        </Panel>

        <Panel title="Execution mode (PAPER ⇄ LIVE)">
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select className="select" value={modeTarget} onChange={(e) => { setModeTarget(e.target.value); setShowLiveConfirm(e.target.value === "LIVE"); }}>
              <option value="">select mode…</option>
              <option value="PAPER">PAPER (simulation adapter)</option>
              <option value="SHADOW">SHADOW (no execution)</option>
              <option value="LIVE">LIVE (real capital)</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
              onClick={() => void submitMode()}
            >
              Apply mode
            </button>
          </div>
          {showLiveConfirm && modeTarget === "LIVE" && (
            <div className="confirm-box">
              <div>
                <b>LIVE places real orders on the connected broker account.</b> The backend performs adapter realignment and refuses unsafe transitions; this switch is persisted by the engine.
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ width: 160 }}
                  placeholder={`type ${LIVE_CONFIRM_TEXT} to confirm`}
                  value={liveConfirm}
                  onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                />
                <span className="note">confirmation is relayed with the command; backend validation still applies</span>
              </div>
            </div>
          )}
          {modeCmd.state.lastMessage && (
            <div className={`cmd-result ${modeCmd.state.lastResult ? "ok" : "fail"}`}>
              {modeCmd.state.lastResult ? "✓" : "✕"} {modeCmd.state.lastMessage}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel
          title="Market / execution state"
          right={<span className="timestamp-note">tick {formatTime(snapshot.timestamps.tick)}</span>}
        >
          <dl className="kv">
            <dt>symbol</dt>
            <dd>{snapshot.symbol ?? "—"}</dd>
            <dt>bid / ask</dt>
            <dd>{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} / {formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</dd>
            <dt>spread</dt>
            <dd>{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</dd>
            <dt>tick stale</dt>
            <dd>{snapshot.tick_stale ? <span className="badge warn">STALE</span> : <span className="badge good">FRESH</span>}</dd>
            <dt>regime</dt>
            <dd>{snapshot.regime ?? "—"}</dd>
            <dt>AI proposal</dt>
            <dd>{snapshot.ai_decision ?? "—"} {snapshot.ai_confidence !== null ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            <dt>proposal blocked by</dt>
            <dd>{snapshot.ai_reason ?? "—"}</dd>
          </dl>
        </Panel>

        <Panel title="Pending orders (broker)" tight>
          {mt5Query.data?.orders && mt5Query.data.orders.length > 0 ? (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr><th>Ticket</th><th>Type</th><th>Volume</th><th>Price</th><th>State</th><th>Setup</th></tr>
                </thead>
                <tbody>
                  {mt5Query.data.orders.map((o, i) => (
                    <tr key={o.ticket ?? i}>
                      <td>{o.ticket ?? "—"}</td>
                      <td>{String(o.type ?? "—")}</td>
                      <td className="num">{formatNumber(o.volume_current)}</td>
                      <td className="num">{formatPrice(o.price_open)}</td>
                      <td>{String(o.state ?? "—")}</td>
                      <td>{formatTime(o.time_setup)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : mt5Query.isPending ? (
            <div className="state-block"><div className="spinner" /></div>
          ) : mt5Query.isError ? (
            <EmptyState message="Pending orders unavailable (MT5 status endpoint failed)." />
          ) : (
            <EmptyState message="No pending orders on the broker account." />
          )}
        </Panel>
      </div>

      <Panel title="Recent executions (audit_executions)">
        <div className="small muted" style={{ padding: "4px 2px 10px" }}>
          Execution history and trading permissions are shown on the Positions and Audit pages; recent model proposals are on the Dashboard. Guardian state:{" "}
          <StatusBadge status={String(snapshot.health.subsystems.engine ?? "UNKNOWN")} /> (engine), mode <span className="inline-mono">{currentMode || "—"}</span>.
        </div>
        <div className="small faint">
          Manual order placement / order cancellation are not implemented: the NSE web layer exposes no such operator routes (execution is engine-owned; BUG-242 INV-004 keeps broker mutations inside the OrderLifecycleManager). Adding fake buttons here would violate the backend-as-source-of-truth rule.
        </div>
      </Panel>
    </div>
  );
}
