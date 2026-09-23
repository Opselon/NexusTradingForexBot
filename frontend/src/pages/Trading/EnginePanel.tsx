/**
 * PURPOSE:  Engine start/stop panel: the two buttons, the mutation-feedback
 *           line and the recovery note. Extracted from the original
 *           TradingPage so that file stays a composition only.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: engineApi.toggleEngine, useMutationFeedback, ConfirmModal, Panel,
 *           t() from i18nStore.
 * PROVIDES: default EnginePanel component (props: running, cmd).
 * INVARANTS: the backend response still decides the outcome; the confirmation
 *             modal is unchanged; only presentational classes change.
 * EXTEND:   New engine commands call the same cmd.run() relay.
 */
import { useState } from "react";
import { engineApi } from "@/api/engineApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { ConfirmModal, Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";

interface Props {
  running: boolean;
  cmd: ReturnType<typeof useMutationFeedback>;
}

export default function EnginePanel({ running, cmd }: Props) {
  const t = useI18n((s) => s.t);
  const [stopConfirm, setStopConfirm] = useState(false);

  const toggleEngine = async (active: boolean): Promise<void> => {
    const ok = await cmd.run(() => engineApi.toggleEngine(active));
    if (ok) {
      // No local state change — the next authoritative snapshot carries it.
    }
  };

  return (
    <Panel title="Engine commands" accent>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn primary" disabled={cmd.state.running || running} onClick={() => void toggleEngine(true)}>
          ▶ Start engine
        </button>
        <button className="btn danger" disabled={cmd.state.running || !running} onClick={() => setStopConfirm(true)}>
          ■ Stop engine
        </button>
      </div>
      {cmd.state.lastMessage && (
        <div className={`cmd-result ${cmd.state.lastResult ? "ok" : "fail"}`}>
          {cmd.state.lastResult ? "✓" : "✕"} {cmd.state.lastMessage}
        </div>
      )}
      <div className="small muted" style={{ marginTop: 10 }}>
        Result comes from the backend response — the UI never assumes a command succeeded before confirmation, and the authoritative engine state on the left updates from the next snapshot.
      </div>

      {stopConfirm && (
        <ConfirmModal
          title={t("ux.confirm.title", "Confirm action") + " — STOP ENGINE"}
          confirmLabel="■ Stop engine"
          busy={cmd.state.running}
          onCancel={() => setStopConfirm(false)}
          onConfirm={() => {
            setStopConfirm(false);
            void toggleEngine(false);
          }}
        >
          <div>
            <b>Impact:</b> the engine loop stops — no new proposals, no new executions. Open positions stay on the broker until you act there.
            <div className="small muted" style={{ marginTop: 8 }}>
              Recovery: Start engine re-attaches the loop; the backend refuses the command if the runtime state forbids it.
            </div>
          </div>
        </ConfirmModal>
      )}
    </Panel>
  );
}
