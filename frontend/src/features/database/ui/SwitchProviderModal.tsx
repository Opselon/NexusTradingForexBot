/**
 * Readiness-gated provider-switch modal — checklist, then typed acknowledgement.
 *
 * OWNER: Lane C (db-provider-pro) — this lane owns future edits.
 * CONSUMES: switchReadiness from ../uiLogic (backend fields only),
 *   TypedConfirmModal from @/features/config/ui/kit, useDialogA11y from
 *   @/components/useDialogA11y, ProviderSwitchPlan from ./ProviderSwitchPlan,
 *   the DbManageStatus / MigrationReport shapes from ../api (types only).
 * PROVIDES: SwitchProviderModal (named + default export, exact §3.5 props).
 * INVARIANTS: the modal NEVER posts — it reports the operator's decision
 *   through onConfirm(acknowledged) and the orchestrator wires the actual
 *   POST /api/db/manage/provider at harvest; while a BLOCK exists the confirm
 *   button is disabled; a WARN must be acknowledged by TYPING the target
 *   provider before onConfirm(true) fires; no health verdict is computed here.
 * EXTEND: new checklist rows belong in switchReadiness() (uiLogic.ts), not in
 *   this file — it renders what the derivation returns and nothing else.
 */
import { useRef, useState, type JSX } from "react";
import { useDialogA11y } from "@/components/useDialogA11y";
import { useI18n } from "@/stores/i18nStore";
import { TypedConfirmModal } from "@/features/config/ui/kit";
import type { DbManageStatus, MigrationReport } from "../api";
import { switchReadiness } from "../uiLogic";
import { ProviderSwitchPlan } from "./ProviderSwitchPlan";
import "./switch-provider.css";

export function SwitchProviderModal(props: {
  open: boolean;
  onClose: () => void;
  manage: DbManageStatus | null | undefined;
  report: MigrationReport | null | undefined;
  testResult: { connected: boolean } | null | undefined;
  targetProvider: string | null;
  onConfirm: (acknowledged: boolean) => void;
}): JSX.Element {
  // Hooks live in the inner dialog, which mounts only while `open` — so focus
  // enters on open, returns on close, and the ack stage resets per opening.
  if (!props.open) return <></>;
  return <SwitchProviderDialog {...props} />;
}

export default SwitchProviderModal;

function SwitchProviderDialog(props: Parameters<typeof SwitchProviderModal>[0]): JSX.Element {
  const { onClose, manage, report, testResult, targetProvider, onConfirm } = props;
  const t = useI18n((s) => s.t);
  const readiness = switchReadiness(manage, report, testResult);
  const [stage, setStage] = useState<"plan" | "ack">("plan");
  const boxRef = useRef<HTMLDivElement | null>(null);
  useDialogA11y(boxRef, onClose);

  const rawTarget = (targetProvider ?? "").trim();
  const name = rawTarget === "" ? "provider" : rawTarget;
  const ackWord = rawTarget === "" ? "SWITCH" : rawTarget.toUpperCase();
  const dataReady = manage != null;
  const canProceed = readiness.canSwitch && dataReady;
  const blocks = readiness.checklist.filter((row) => row.state === "BLOCK");

  return (
    <>
      <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div
          ref={boxRef}
          className="modal dbcsw-modal"
          role="dialog"
          aria-modal="true"
          aria-label={t("database.switch.aria", "switch active provider to {name}", { name })}
        >
          <div className="modal-header">{t("database.switch.title", "switch active provider → {name}", { name })}</div>
          <div className="modal-body">
            <ProviderSwitchPlan readiness={readiness} manage={manage} targetProvider={targetProvider} />

            {!canProceed && (
              <p className="dbcsw-gate" data-tone="bad" role="note">
                {dataReady
                  ? t("database.switch.blocked", "switch blocked — {blocks}", { blocks: blocks.map((b) => b.label).join(" · ") })
                  : t("database.switch.blocked_unavailable", "switch blocked — /api/db/manage/status has not answered, readiness is UNAVAILABLE.")}
              </p>
            )}
            {canProceed && readiness.requiresAcknowledgement && (
              <p className="dbcsw-gate" data-tone="warn" role="note">
                {t("database.switch.warn_ack", "a WARN remains — you will be asked to type {word} to acknowledge switching without a validated migration.", { word: ackWord })}
              </p>
            )}
            {canProceed && !readiness.requiresAcknowledgement && (
              <p className="dbcsw-gate" data-tone="good" role="note">
                {t("database.switch.ok", "all prerequisites reported by the backend are in place — confirm to switch to {name}. the selection is persisted and applies on the next restart; no data is moved.", { name })}
              </p>
            )}
          </div>
          <div className="modal-actions">
            <button className="btn" disabled={stage === "ack"} onClick={onClose}>
              {t("common.close", "Cancel")} <kbd>esc</kbd>
            </button>
            <button
              className="btn primary"
              disabled={!canProceed}
              onClick={() => (readiness.requiresAcknowledgement ? setStage("ack") : onConfirm(false))}
            >
              {t("database.switch.confirm", "switch to {name}", { name })}
            </button>
          </div>
        </div>
      </div>

      {/* Typed acknowledgement, stacked over the plan (WARN only): the plan
          stays mounted so focus and the Esc trap return to it on cancel. */}
      {stage === "ack" && (
        <TypedConfirmModal
          title={t("database.switch.ack_title", "Switch to {name} without a validated migration", { name })}
          word={ackWord}
          confirmLabel={t("database.switch.confirm", "switch to {name}", { name })}
          onCancel={() => setStage("plan")}
          onConfirm={() => onConfirm(true)}
          body={
            <>
              {t("database.switch.ack_body_pre", "The backend sets")} <span className="inline-mono">provider_switch_ready</span> {t("database.switch.ack_body_mid", "only when the last migration finished")} <span className="inline-mono">COMPLETE</span> {t("database.switch.ack_body_mid2", "with validation")}{" "}
              <span className="inline-mono">PASSED</span> {t("database.switch.ack_body_post", "(database/migrate_engine.py:354) — the report you have does not say that. Switching only persists the provider selection: no row is moved, copied or deleted, and it applies on the next restart. Type {word} to confirm you accept switching with that migration unvalidated.", { word: ackWord })}
            </>
          }
        />
      )}
    </>
  );
}
