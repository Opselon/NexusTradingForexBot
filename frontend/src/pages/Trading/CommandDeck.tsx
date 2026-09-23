/**
 * CommandDeck — engine + execution-mode controls for /alt/trading (lane B).
 * Lane contract: presentation only. All state is passed in; never fetches
 * and never mutates anything itself — it only calls the callbacks handed to
 * it. Baseline keeps ALL original controls verbatim; restyle, never delete.
 */
import { Panel } from "@/components/primitives";
import type { EngineSnapshot } from "@/types/domain";
import { LIVE_CONFIRM_TEXT, MODE_IMPACT } from "@/pages/Trading/tradingConsts";
import "./commandDeck.css";

type TFn = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

export interface CommandDeckProps {
  snapshot: EngineSnapshot;
  engineCmd: { state: { running: boolean; lastMessage?: string | null; lastResult?: boolean | null } };
  modeCmd: { state: { running: boolean; lastMessage?: string | null; lastResult?: boolean | null } };
  onStart: () => void;
  onStop: () => void;
  onApplyMode: () => void;
  modeTarget: string;
  setModeTarget: (v: string) => void;
  liveConfirm: string;
  setLiveConfirm: (v: string) => void;
  showLiveConfirm: boolean;
  setShowLiveConfirm: (v: boolean) => void;
  currentMode?: string | null;
  t: TFn;
}

export default function CommandDeck(props: CommandDeckProps) {
  const { snapshot, engineCmd, modeCmd, onStart, onStop, onApplyMode, modeTarget, setModeTarget, liveConfirm, setLiveConfirm, showLiveConfirm, setShowLiveConfirm, currentMode, t } = props;
  const running = snapshot.engine_running;
  return (
    <div className="tr-deck">
      <div className="tr-deck-grid">
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title="Engine commands" accent>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={engineCmd.state.running || running} onClick={onStart}>
              ▶ Start engine
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={onStop}>
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

        <Panel title="Execution mode (PAPER ⇄ LIVE)" accent>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label="Target execution mode" className="select" value={modeTarget} onChange={(e) => { setModeTarget(e.target.value); setShowLiveConfirm(e.target.value === "LIVE"); }}>
              <option value="">select mode…</option>
              <option value="PAPER">PAPER (simulation adapter)</option>
              <option value="SHADOW">SHADOW (no execution)</option>
              <option value="LIVE">LIVE (real capital)</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
              onClick={onApplyMode}
            >
              Apply mode
            </button>
            <span className="l4-chip">current {currentMode || "—"}</span>
          </div>
          {modeTarget && MODE_IMPACT[modeTarget] && (
            <div className="l4-note" style={{ marginTop: 8 }}>{MODE_IMPACT[modeTarget]}</div>
          )}
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
                  aria-label="LIVE confirmation phrase" placeholder={t("ux.confirm.type", "Type {w} to enable confirmation", { w: LIVE_CONFIRM_TEXT })}
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


      </div>
    </div>
  );
}
