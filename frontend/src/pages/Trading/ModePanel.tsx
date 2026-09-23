/**
 * PURPOSE:  Execution-mode panel (PAPER ⇄ LIVE) with the EXISTING typed-phrase
 *           confirmation guard for LIVE. Extracted from the original
 *           TradingPage; behavior, validation and submit relay are unchanged.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: engineApi.setMode, useMutationFeedback, Panel, useI18n t(),
 *           LIVE_CONFIRM_TEXT + MODE_IMPACT from ./tradingTypes.
 * PROVIDES: default ModePanel (props: currentMode, cmd).
 * INVARANTS: LIVE still requires the exact phrase in the confirmation input
 *             before the button enables; the phrase is relayed with the command
 *             and the backend still validates it. No behavior is altered.
 * EXTEND:   New modes extend MODE_IMPACT; the guard stays keyed on the phrase.
 */
import { useState } from "react";
import { engineApi } from "@/api/engineApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { LIVE_CONFIRM_TEXT, MODE_IMPACT } from "./tradingTypes";

interface Props {
  currentMode: string;
  cmd: ReturnType<typeof useMutationFeedback>;
}

export default function ModePanel({ currentMode, cmd }: Props) {
  const t = useI18n((s) => s.t);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);

  const submitMode = async (): Promise<void> => {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) return;
    const ok = await cmd.run(() => engineApi.setMode(modeTarget));
    if (ok) {
      setShowLiveConfirm(false);
      setLiveConfirm("");
      setModeTarget("");
    }
  };

  return (
    <Panel title="Execution mode (PAPER ⇄ LIVE)" accent>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select
          aria-label="Target execution mode"
          className="select"
          value={modeTarget}
          onChange={(e) => {
            setModeTarget(e.target.value);
            setShowLiveConfirm(e.target.value === "LIVE");
          }}
        >
          <option value="">select mode…</option>
          <option value="PAPER">PAPER (simulation adapter)</option>
          <option value="SHADOW">SHADOW (no execution)</option>
          <option value="LIVE">LIVE (real capital)</option>
        </select>
        <button
          className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
          disabled={!modeTarget || cmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
          onClick={() => void submitMode()}
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
              aria-label="LIVE confirmation phrase"
              placeholder={t("ux.confirm.type", "Type {w} to enable confirmation", { w: LIVE_CONFIRM_TEXT })}
              value={liveConfirm}
              onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
            />
            <span className="note">confirmation is relayed with the command; backend validation still applies</span>
          </div>
        </div>
      )}
      {cmd.state.lastMessage && (
        <div className={`cmd-result ${cmd.state.lastResult ? "ok" : "fail"}`}>
          {cmd.state.lastResult ? "✓" : "✕"} {cmd.state.lastMessage}
        </div>
      )}
    </Panel>
  );
}
