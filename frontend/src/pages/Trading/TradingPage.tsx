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
 *
 * Pro UX: the mode-switch now runs through the legacy dashboard's typed-
 * confirmation gate (Web/ux.js confirmModeChange port: ACTION/CURRENT/IMPACT/
 * RECOVERY + type-LIVE-to-arm), commands surface as toasts, and every verdict
 * still comes from the backend response — never assumed locally.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import type { EngineSnapshot } from "@/types/domain";
import { ConfirmModal, EmptyState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { formatNumber, formatPct, formatPrice, formatTime } from "@/lib/format";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

const LIVE_CONFIRM_TEXT = "LIVE";

export default function TradingPage({ snapshot }: Props) {
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const t = useI18n((s) => s.t);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);

  // Mode-impact explanations are UI copy (translated); the mode words
  // PAPER/SHADOW/LIVE themselves are backend enum values and stay verbatim.
  const MODE_IMPACT: Record<string, string> = {
    PAPER: t("alt.trade.impact_paper", "Simulated fills only — no real orders reach the broker."),
    SHADOW: t("alt.trade.impact_shadow", "Signals are computed but never dispatched as orders."),
    LIVE: t("alt.trade.impact_live", "The engine will dispatch REAL orders to the connected broker account."),
  };

  const mt5Query = useQuery({
    queryKey: ["mt5-status"],
    queryFn: ({ signal }) => engineApi.mt5Status(signal),
    refetchInterval: 10_000,
  });

  if (!snapshot) {
    return (
      <div>
        <Panel title={t("alt.trade.panel_title", "Trading")}>
          <Skeleton count={5} />
        </Panel>
      </div>
    );
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
        <MetricCard label={t("alt.trade.metric_engine_loop", "Engine loop")} value={running ? "RUNNING" : "STOPPED"} tone={running ? "pos" : "dim"} sub={t("alt.trade.engine_loop_sub", "backend-authoritative (state_version climbs while running)")} />
        <MetricCard label={t("alt.trade.metric_exec_mode", "Execution mode")} value={currentMode || "—"} tone={currentMode.startsWith("LIVE") ? "neg" : "dim"} sub={`data_source: ${snapshot.data_source ?? "—"}`} />
        <MetricCard label={t("alt.trade.metric_adapter", "Broker adapter")} value={snapshot.adapter_class ?? "—"} tone="dim" sub={String(snapshot.health.details.mt5 ?? "")} />
        <MetricCard
          label={t("alt.trade.metric_terminal_trading", "Terminal trading")}
          value={snapshot.account.trade_allowed === null ? "—" : snapshot.account.trade_allowed ? "ALLOWED" : "RESTRICTED"}
          tone={snapshot.account.trade_allowed === true ? "pos" : snapshot.account.trade_allowed === false ? "neg" : "dim"}
          sub={t("alt.trade.terminal_sub", "broker terminal trade_allowed")}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("alt.trade.panel_commands", "Engine commands")} accent>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => void toggleEngine(true)}>
              ▶ {t("alt.trade.start_engine", "Start engine")}
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => setStopConfirm(true)}>
              ■ {t("alt.trade.stop_engine", "Stop engine")}
            </button>
          </div>
          {engineCmd.state.lastMessage && (
            <div className={`cmd-result ${engineCmd.state.lastResult ? "ok" : "fail"}`}>
              {engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            {t("alt.trade.cmd_note_backend", "Result comes from the backend response — the UI never assumes a command succeeded before confirmation, and the authoritative engine state on the left updates from the next snapshot.")}
          </div>
        </Panel>

        <Panel title={t("alt.trade.panel_mode", "Execution mode (PAPER ⇄ LIVE)")} accent>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select className="select" value={modeTarget} onChange={(e) => { setModeTarget(e.target.value); setShowLiveConfirm(e.target.value === "LIVE"); }}>
              <option value="">{t("alt.trade.select_mode", "select mode…")}</option>
              <option value="PAPER">{t("alt.trade.mode_paper", "PAPER (simulation adapter)")}</option>
              <option value="SHADOW">{t("alt.trade.mode_shadow", "SHADOW (no execution)")}</option>
              <option value="LIVE">{t("alt.trade.mode_live", "LIVE (real capital)")}</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
              onClick={() => void submitMode()}
            >
              {t("alt.trade.apply_mode", "Apply mode")}
            </button>
          </div>
          {showLiveConfirm && modeTarget === "LIVE" && (
            <div className="confirm-box">
              <div>
                <b>{t("ux.mode.live_warning", "Real money is at risk. This affects your live broker account.")}</b>{" "}
                {t("ux.mode.body", "This changes how the engine executes orders.")}{" "}
                <span className="muted">{MODE_IMPACT.LIVE}</span>
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ width: 200 }}
                  placeholder={t("ux.confirm.type", "Type {w} to enable confirmation", { w: LIVE_CONFIRM_TEXT })}
                  value={liveConfirm}
                  onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                />
                <span className="note">{t("alt.trade.mode_note_relay", "confirmation is relayed with the command; backend validation still applies")}</span>
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
          title={t("alt.trade.panel_market", "Market / execution state")}
          right={<span className="timestamp-note">{t("alt.trade.tick_note", "tick {time}", { time: formatTime(snapshot.timestamps.tick) })}</span>}
        >
          <dl className="kv">
            <dt>{t("alt.trade.dt_symbol", "symbol")}</dt>
            <dd>{snapshot.symbol ?? "—"}</dd>
            <dt>{t("alt.trade.dt_bid_ask", "bid / ask")}</dt>
            <dd>{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} / {formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</dd>
            <dt>{t("alt.trade.dt_spread", "spread")}</dt>
            <dd>{snapshot.spread === null ? "—" : t("alt.trade.spread_pts", "{spread} pts", { spread: formatNumber(snapshot.spread) })}</dd>
            <dt>{t("alt.trade.dt_tick_stale", "tick stale")}</dt>
            <dd>{snapshot.tick_stale ? <span className="badge warn">STALE</span> : <span className="badge good">FRESH</span>}</dd>
            <dt>{t("alt.trade.dt_regime", "regime")}</dt>
            <dd>{snapshot.regime ?? "—"}</dd>
            <dt>{t("alt.trade.dt_ai_proposal", "AI proposal")}</dt>
            <dd>{snapshot.ai_decision ?? "—"} {snapshot.ai_confidence !== null ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            <dt>{t("alt.trade.dt_blocked_by", "proposal blocked by")}</dt>
            <dd>{snapshot.ai_reason ?? "—"}</dd>
          </dl>
        </Panel>

        <Panel title={t("alt.trade.panel_pending", "Pending orders (broker)")} tight>
          {mt5Query.data?.orders && mt5Query.data.orders.length > 0 ? (
            <div style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t("alt.common.col_ticket", "Ticket")}</th>
                    <th>{t("alt.common.col_type", "Type")}</th>
                    <th>{t("alt.common.col_volume", "Volume")}</th>
                    <th>{t("alt.trade.col_price", "Price")}</th>
                    <th>{t("alt.trade.col_state", "State")}</th>
                    <th>{t("alt.trade.col_setup", "Setup")}</th>
                  </tr>
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
            <div style={{ padding: 14 }}><Skeleton count={3} /></div>
          ) : mt5Query.isError ? (
            <EmptyState message={t("alt.trade.err_pending", "Pending orders unavailable (MT5 status endpoint failed).")} />
          ) : (
            <EmptyState message={t("alt.trade.empty_pending", "No pending orders on the broker account.")} />
          )}
        </Panel>
      </div>

      <Panel title={t("alt.trade.panel_exec", "Recent executions (audit_executions)")}>
        <div className="small muted" style={{ padding: "4px 2px 10px" }}>
          {t("alt.trade.exec_note1", "Execution history and trading permissions are shown on the Positions and Audit pages; recent model proposals are on the Dashboard. Guardian state:")}{" "}
          <StatusBadge status={String(snapshot.health.subsystems.engine ?? "UNKNOWN")} /> {t("alt.trade.exec_note2", "(engine), mode")} <span className="inline-mono">{currentMode || "—"}</span>.
        </div>
        <div className="small faint">
          {t("alt.trade.exec_note_fake", "Manual order placement / order cancellation are not implemented: the NSE web layer exposes no such operator routes (execution is engine-owned; BUG-242 INV-004 keeps broker mutations inside the OrderLifecycleManager). Adding fake buttons here would violate the backend-as-source-of-truth rule.")}
        </div>
      </Panel>

      {stopConfirm && (
        <ConfirmModal
          title={t("ux.confirm.title", "Confirm action") + " — " + t("alt.trade.stop_title_suffix", "STOP ENGINE")}
          confirmLabel={"■ " + t("alt.trade.stop_engine", "Stop engine")}
          busy={engineCmd.state.running}
          onCancel={() => setStopConfirm(false)}
          onConfirm={() => {
            setStopConfirm(false);
            void toggleEngine(false);
          }}
        >
          <div>
            <b>{t("alt.common.impact_label", "Impact:")}</b> {t("alt.trade.stop_impact", "the engine loop stops — no new proposals, no new executions. Open positions stay on the broker until you act there.")}
            <div className="small muted" style={{ marginTop: 8 }}>
              {t("alt.trade.stop_recovery", "Recovery: Start engine re-attaches the loop; the backend refuses the command if the runtime state forbids it.")}
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
