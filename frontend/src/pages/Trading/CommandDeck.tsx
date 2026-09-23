/**
 * PURPOSE:  Pro command deck for /alt/trading — engine Start/Stop controls and
 *           the execution-mode target console (segmented dual-panel deck,
 *           mode segmented control, typed-LIVE danger zone, backend-result
 *           toasts). Presentation only: no fetch, no mutation, no verdict.
 * OWNER:    ui/tr-b  (future edits belong to this lane)
 * CONSUMES: snapshot.engine_running (engine RUNNING/STOPPED badge, dot and
 *           the Start/Stop disabled flags); engineCmd.state.running /
 *           .lastMessage / .lastResult (in-flight chip + result toast);
 *           modeCmd.state.running / .lastMessage / .lastResult; currentMode
 *           (page-derived from snapshot.runtime_mode ?? execution_mode — the
 *           "current" chip, the mode-tone and the Apply disabled flag);
 *           modeTarget, liveConfirm, showLiveConfirm (page-owned state);
 *           LIVE_CONFIRM_TEXT + MODE_IMPACT from ./tradingConsts (verbatim);
 *           t() for ux.mode.live_warning, ux.mode.body, ux.confirm.type,
 *           ux.mode.impact_label, ux.mode.danger_zone. Deck-head route chips
 *           restate the backend-verified routes documented in TradingPage.tsx
 *           (POST /api/engine/toggle, POST /api/engine/mode).
 * PROVIDES: CommandDeckProps (frozen orchestrator shape) + default component
 *           with its own local markup only; no new query, mutation or state
 *           beyond rendering the props it is handed.
 * INVARIANTS: backend-guarantee — raw backend words restate only
 *             (RUNNING/STOPPED, PAPER/SHADOW/LIVE, backend lastMessage in the
 *             toasts); every command result renders only after the backend
 *             reply (engineCmd/modeCmd state), never optimistically; missing
 *             mode -> em dash; LIVE_CONFIRM_TEXT/MODE_IMPACT imported, never
 *             redefined; baseline controls, options, disabled expressions and
 *             copy kept verbatim; reduced-motion honored.
 * EXTEND:   New deck affordances must reuse the existing props/mutations and
 *           render only backend payload fields (cite them in CONSUMES).
 */
import { StatusBadge } from "@/components/primitives";
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

/** Segmented mode picker — descriptions are the verbatim fragments of the
 *  baseline <select> option labels ("PAPER (simulation adapter)" etc.). */
const MODE_SEGMENTS = [
  { id: "PAPER", desc: "simulation adapter" },
  { id: "SHADOW", desc: "no execution" },
  { id: "LIVE", desc: "real capital" },
] as const;

export default function CommandDeck(props: CommandDeckProps) {
  const { snapshot, engineCmd, modeCmd, onStart, onStop, onApplyMode, modeTarget, setModeTarget, liveConfirm, setLiveConfirm, showLiveConfirm, setShowLiveConfirm, currentMode, t } = props;
  const running = snapshot.engine_running;

  /** Identical transition to the baseline <select> onChange — the same two
   *  setters with the same values; segments and select stay in lockstep. */
  const pickMode = (v: string): void => {
    setModeTarget(v);
    setShowLiveConfirm(v === "LIVE");
  };

  /** "current" chip tone — restate of the backend mode word only (theme
   *  semantics: LIVE red / SHADOW amber / PAPER blue), never a verdict. */
  const modeTone = currentMode === "LIVE" ? "bad" : currentMode === "PAPER" ? "accent" : currentMode === "SHADOW" ? "warn" : "";

  /** Mechanical mirror of the two disabled expressions below — explains the
   *  local button availability (pure local view state), adds no verdict. */
  const engineStartLocked = engineCmd.state.running || running;
  const engineStopLocked = engineCmd.state.running || !running;
  const applyHint = !modeTarget
    ? "no target selected"
    : modeCmd.state.running
      ? "command in flight"
      : modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT
        ? `type ${LIVE_CONFIRM_TEXT} to arm`
        : modeTarget === currentMode
          ? "target equals current mode"
          : "ready to apply";

  return (
    <div className="tr-deck" role="region" aria-label="Command deck">
      <div className="tr-deck-frame">
        <div className="tr-deck-head">
          <span className="tr-deck-kicker">Command deck</span>
          <span className="tr-deck-prov">
            {/* backend-verified routes (documented in TradingPage.tsx) */}
            <span className="l4-prov">POST /api/engine/toggle</span>
            <span className="l4-prov">POST /api/engine/mode</span>
          </span>
        </div>

        <div className="tr-deck-grid">
          {/* ---------------------------- Engine ---------------------------- */}
          <section className="tr-deck-card tr-deck-card--engine" aria-labelledby="tr-deck-engine-title">
            <div className="tr-deck-card-head">
              <h2 className="tr-deck-card-title" id="tr-deck-engine-title">
                <span className={`tr-deck-dot${running ? " is-live" : ""}`} aria-hidden="true" />
                Engine commands
              </h2>
              <span className="tr-deck-head-tools">
                {engineCmd.state.running && (
                  <span className="tr-deck-busy" role="status">
                    sending…
                  </span>
                )}
                {/* raw value: snapshot.engine_running */}
                <StatusBadge status={running ? "RUNNING" : "STOPPED"} />
              </span>
            </div>
            <div className="tr-deck-card-body">
              <div className="tr-deck-actions">
                <button className="btn primary tr-deck-btn tr-deck-btn--start" disabled={engineCmd.state.running || running} onClick={onStart}>
                  ▶ Start engine
                </button>
                <button className="btn danger tr-deck-btn tr-deck-btn--stop" disabled={engineCmd.state.running || !running} onClick={onStop}>
                  ■ Stop engine
                </button>
              </div>
              <div className="tr-deck-hint">
                <span className={engineStartLocked ? "is-locked" : "is-ready"}>start {engineStartLocked ? "locked" : "ready"}</span>
                <span aria-hidden="true">·</span>
                <span className={engineStopLocked ? "is-locked" : "is-armed"}>stop {engineStopLocked ? "locked" : "armed"}</span>
              </div>
              {engineCmd.state.lastMessage && (
                <div className={`cmd-result tr-deck-toast ${engineCmd.state.lastResult ? "ok" : "fail"}`}>{engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}</div>
              )}
              <div className="small muted tr-deck-note">
                Result comes from the backend response — the UI never assumes a command succeeded before confirmation, and the authoritative engine state on the left updates from the next snapshot.
              </div>
            </div>
          </section>

          {/* ------------------------ Execution mode ------------------------ */}
          <section className="tr-deck-card tr-deck-card--mode" aria-labelledby="tr-deck-mode-title">
            <div className="tr-deck-card-head">
              <h2 className="tr-deck-card-title" id="tr-deck-mode-title">
                <span className="tr-deck-dot tr-deck-dot--mode" aria-hidden="true" />
                Execution mode (PAPER ⇄ LIVE)
              </h2>
              <span className="tr-deck-head-tools">
                {modeCmd.state.running && (
                  <span className="tr-deck-busy" role="status">
                    sending…
                  </span>
                )}
                <span className={`l4-chip tr-deck-current${modeTone ? ` ${modeTone}` : ""}`}>current {currentMode || "—"}</span>
              </span>
            </div>
            <div className="tr-deck-card-body">
              {/* segmented quick-target — same two setters as the select below */}
              <div className="tr-deck-segs" role="group" aria-label="Target execution mode quick picker">
                {MODE_SEGMENTS.map((m) => {
                  const active = modeTarget === m.id;
                  return (
                    <button key={m.id} type="button" className={`tr-deck-seg tr-deck-seg--${m.id.toLowerCase()}${active ? " is-active" : ""}`} aria-pressed={active} onClick={() => pickMode(m.id)}>
                      <span className="tr-deck-seg-name">{m.id}</span>
                      <span className="tr-deck-seg-desc">{m.desc}</span>
                      {currentMode === m.id && <span className="tr-deck-seg-current">current</span>}
                    </button>
                  );
                })}
              </div>

              <div className="tr-deck-controls">
                <span className="tr-deck-field">
                  <span className="tr-deck-field-label">target mode</span>
                  <select aria-label="Target execution mode" className="select tr-deck-select" value={modeTarget} onChange={(e) => { setModeTarget(e.target.value); setShowLiveConfirm(e.target.value === "LIVE"); }}>
                    <option value="">select mode…</option>
                    <option value="PAPER">PAPER (simulation adapter)</option>
                    <option value="SHADOW">SHADOW (no execution)</option>
                    <option value="LIVE">LIVE (real capital)</option>
                  </select>
                </span>
                <button
                  className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"} tr-deck-apply`}
                  disabled={!modeTarget || modeCmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
                  onClick={onApplyMode}
                >
                  Apply mode
                </button>
              </div>
              <div className="tr-deck-hint">
                <span className={applyHint === "ready to apply" ? "is-ready" : "is-locked"}>{applyHint}</span>
              </div>

              {modeTarget && MODE_IMPACT[modeTarget] && (
                <div className="l4-note tr-deck-impact">
                  <span className="tr-deck-impact-tag">{t("ux.mode.impact_label", "What changes")}</span>
                  <span>{MODE_IMPACT[modeTarget]}</span>
                </div>
              )}

              {showLiveConfirm && modeTarget === "LIVE" && (
                <div className="confirm-box tr-deck-danger">
                  <div className="tr-deck-danger-head">
                    <span className="tr-deck-danger-badge">⚠ {t("ux.mode.danger_zone", "DANGER ZONE")}</span>
                  </div>
                  <div className="tr-deck-danger-body">
                    <div className="tr-deck-danger-text">
                      <b>{t("ux.mode.live_warning", "Real money is at risk. This affects your live broker account.")}</b>{" "}
                      {t("ux.mode.body", "This changes how the engine executes orders.")}{" "}
                      <span className="muted">{MODE_IMPACT.LIVE}</span>
                    </div>
                    <div className="row">
                      <input
                        className="input tr-deck-confirm-input"
                        aria-label="LIVE confirmation phrase"
                        placeholder={t("ux.confirm.type", "Type {w} to enable confirmation", { w: LIVE_CONFIRM_TEXT })}
                        value={liveConfirm}
                        onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                      />
                      <span className="note">confirmation is relayed with the command; backend validation still applies</span>
                    </div>
                  </div>
                </div>
              )}

              {modeCmd.state.lastMessage && (
                <div className={`cmd-result tr-deck-toast ${modeCmd.state.lastResult ? "ok" : "fail"}`}>{modeCmd.state.lastResult ? "✓" : "✕"} {modeCmd.state.lastMessage}</div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
