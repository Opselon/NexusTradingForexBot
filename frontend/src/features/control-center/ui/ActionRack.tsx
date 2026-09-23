/**
 * PURPOSE:  Guarded action rack — grouped engine/mode controls with state chips,
 *           "requires confirm" affordances and the page's existing confirm flows.
 * OWNER:    uiux-wave5-control
 * CONSUMES: @/components/primitives (Panel, ConfirmModal), @/hooks/useMutationFeedback,
 *           ../useCases (existing engine mutations), ../../research/ui/lane5Kit
 *           (CommandResultLine), ./tones (GlowTone)
 * PROVIDES: ActionRack
 * INVARIANTS: NO new mutations — only controlCenterUseCases.toggleEngine/setMode
 *             on the already confirm-guarded canonical routes; the UI never fakes
 *             state (the backend verdict + next authoritative snapshot decide);
 *             chips re-state backend words only; button enable rules are the
 *             legacy rules, unchanged.
 * EXTEND:   new guarded control = another .ctl-rack-group (chip + guard chip)
 *           wired through an existing use case — never a new endpoint.
 */
import { useState } from "react";
import { ConfirmModal, Panel } from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { CommandResultLine } from "../../research/ui/lane5Kit";
import { controlCenterUseCases } from "../useCases";
import type { GlowTone } from "./tones";
import { useI18n } from "@/stores/i18nStore";

const LIVE_CONFIRM_TEXT = "LIVE";

interface ActionRackProps {
  /** legacy control semantics (engine field missing → false), never gated by chips */
  running: boolean;
  /** current backend execution-mode word (uppercased) */
  mode: string;
  /** false = snapshot not loaded / errored → chips read UNKNOWN, enable rules unchanged */
  engineKnown: boolean;
  modeTone: GlowTone;
  /** re-read the authoritative snapshots after a command settles */
  onSettled: () => void;
}

export function ActionRack({ running, mode, engineKnown, modeTone, onSettled }: ActionRackProps) {
  const t = useI18n((s) => s.t);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [startConfirm, setStartConfirm] = useState(false);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();

  const engineChip = engineKnown ? (running ? "ON" : "OFF") : "UNKNOWN";
  const engineChipTone: GlowTone = engineKnown ? (running ? "on" : "off") : "unknown";
  const armed = modeTarget !== "" && modeTarget.toUpperCase() !== mode;
  const typedGuard = modeTarget === "LIVE";

  async function applyMode(): Promise<void> {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) {
      setLiveConfirm("");
      return;
    }
    const ok = await modeCmd.run(() => controlCenterUseCases.setMode(modeTarget));
    if (ok) {
      setModeTarget("");
      setLiveConfirm("");
      onSettled();
    }
  }

  return (
    <>
      <Panel title={t("control-center.panel.rack_title", "Action rack — engine + mode (guarded)")} accent>
        <div className="ctl-rack">
          <div className="ctl-rack-group">
            <div className="ctl-rack-head">
              <span>{t("control-center.rack.engine", "ENGINE")}</span>
              <span className="sep" aria-hidden="true" />
              <span className={`ctl-chip ctl-chip--${engineChipTone}`}>{engineChip === "ON" ? t("control-center.engine.on", "ON") : engineChip === "OFF" ? t("control-center.engine.off", "OFF") : t("control-center.engine.unknown_chip", "UNKNOWN")}</span>
            </div>
            <div className="ctl-rack-actions">
              <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => setStartConfirm(true)}>
                {t("control-center.action.start_engine", "▶ Start engine")}
              </button>
              <span className="ctl-guard" title={t("control-center.rack.confirm_hint", "opens the existing confirmation dialog")}>
                {t("control-center.rack.requires_confirm", "⚠ requires confirm")}
              </span>
              <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => setStopConfirm(true)}>
                {t("control-center.action.stop_engine", "■ Stop engine (kill switch)")}
              </button>
              <span className="ctl-guard" title={t("control-center.rack.confirm_hint", "opens the existing confirmation dialog")}>
                ⚠ requires confirm
              </span>
            </div>
            <div className="ctl-rack-note">
              {t("control-center.rack.enable_rules", "Enable rules unchanged: Start while STOPPED, Stop while RUNNING — the snapshot decides, chips only re-state it.")}
            </div>
          </div>

          <div className="ctl-rack-group">
            <div className="ctl-rack-head">
              <span>{t("control-center.rack.exec_mode", "EXECUTION MODE")}</span>
              <span className="sep" aria-hidden="true" />
              <span className={`ctl-chip ctl-chip--${modeTone}`}>{mode}</span>
              {armed && <span className="ctl-chip ctl-chip--armed">{t("control-center.rack.armed", "ARMED")} · {modeTarget}</span>}
            </div>
            <div className="ctl-rack-actions">
              <select
                aria-label={t("control-center.rack.mode_aria", "Target execution mode")}
                className="select ctl-mode-select"
                value={modeTarget}
                onChange={(e) => setModeTarget(e.target.value)}
              >
                <option value="">{t("control-center.action.mode_switch", "mode switch…")}</option>
                {["PAPER", "LIVE", "SHADOW"].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button
                className={`btn ${modeTarget === "LIVE" ? "danger" : ""}`}
                disabled={!modeTarget || modeCmd.state.running || modeTarget.toUpperCase() === mode}
                onClick={() => void applyMode()}
              >
                {t("control-center.action.apply", "apply")}
              </button>
              {typedGuard && (
                <span className="ctl-guard" title={t("control-center.rack.live_guard_hint", "legacy typed LIVE confirmation phrase")}>
                  {t("control-center.rack.typed_live_guard", "⚠ typed LIVE guard")}
                </span>
              )}
            </div>
            <div className="ctl-rack-note">
              {t("control-center.panel.engine_commands_note", "Commands go to /api/engine/toggle + /api/engine/mode (BUG-148 hot-swap path). The UI never shows a locally-changed state — the next authoritative snapshot decides.")}
            </div>
          </div>
        </div>

        <div className="ctl-rack-foot">
          <CommandResultLine state={engineCmd.state} />
          <CommandResultLine state={modeCmd.state} />
        </div>
      </Panel>

      {(startConfirm || stopConfirm) && (
        <ConfirmModal
          title={stopConfirm ? t("control-center.confirm.stop_title", "STOP the engine (kill switch)") : t("control-center.confirm.start_title", "Start the engine")}
          danger={stopConfirm}
          confirmLabel={stopConfirm ? t("control-center.confirm.stop", "Stop engine") : t("control-center.confirm.start", "Start engine")}
          busy={engineCmd.state.running}
          onCancel={() => {
            setStopConfirm(false);
            setStartConfirm(false);
          }}
          onConfirm={async () => {
            const stopping = stopConfirm;
            setStopConfirm(false);
            setStartConfirm(false);
            await engineCmd.run(() => controlCenterUseCases.toggleEngine(!stopping));
            onSettled();
          }}
        >
          <div className="small">
            {stopConfirm
              ? t("control-center.confirm.stop_body", "Stops the engine loop: no new decisions or dispatches. Open positions remain under broker/exits — closing them is a separate explicit action on the Positions page.")
              : t("control-center.confirm.start_body", "Starts the engine loop via the canonical async start path (BUG-239). The backend response decides.")}
          </div>
        </ConfirmModal>
      )}

      {modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT && (
        <ConfirmModal
          title={t("control-center.live.title", "Switch execution mode to LIVE")}
          danger
          confirmLabel={t("control-center.live.abort", "abort switch")}
          busy={false}
          onCancel={() => setModeTarget("")}
          onConfirm={() => setModeTarget("")}
        >
          <div className="confirm-box">
            <div className="small">
              {t("control-center.live.type_hint", "LIVE dispatches real orders. Type “{w}” below the button to arm the switch (legacy parity guard).", { w: LIVE_CONFIRM_TEXT })}
            </div>
            <div className="row">
              <input
                className="input ctl-live-input"
                value={liveConfirm}
                onChange={(e) => setLiveConfirm(e.target.value)}
                aria-label={t("control-center.live.phrase_aria", "LIVE confirmation phrase")}
                placeholder={LIVE_CONFIRM_TEXT}
              />
              <button className="btn small danger" disabled={liveConfirm !== LIVE_CONFIRM_TEXT} onClick={() => void applyMode()}>
                {t("control-center.live.switch", "switch to LIVE")}
              </button>
            </div>
          </div>
        </ConfirmModal>
      )}
    </>
  );
}
