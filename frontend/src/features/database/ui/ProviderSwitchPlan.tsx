/**
 * Provider-switch readiness checklist — pure render over switchReadiness().
 *
 * OWNER: Lane C (db-provider-pro) — this lane owns future edits.
 * CONSUMES: SwitchReadiness + providerTruth from ../uiLogic (both derived from
 *   /api/db/manage/status, /api/db/manage/report and the client-held
 *   test-connection answer), DbManageStatus from ../api, targetProvider (the
 *   label the operator picked).
 * PROVIDES: ProviderSwitchPlan (named + default export).
 * INVARIANTS: every row restates a backend field verbatim; the component
 *   computes NO health verdict of its own; a missing payload renders "—" or
 *   UNAVAILABLE, never a fabricated pass; all styling comes from the dbcsw-
 *   stylesheet (switch-provider.css) with theme tokens only.
 * EXTEND: add rows by extending switchReadiness() in uiLogic.ts — this
 *   component must stay a dumb renderer of `checklist`.
 */
import { type JSX } from "react";
import type { DbManageStatus } from "../api";
import { providerTruth, type SwitchReadiness } from "../uiLogic";

export function ProviderSwitchPlan(props: {
  readiness: SwitchReadiness;
  manage: DbManageStatus | null | undefined;
  targetProvider: string | null;
}): JSX.Element {
  const { readiness, manage, targetProvider } = props;
  const truth = providerTruth(manage);

  return (
    <div className="dbcsw-plan">
      {/* configured vs effective truth — providerTruth labels itself "backend"
          (measured) or "derived" (narrow client claim); both words restate. */}
      <div className="dbcsw-truth">
        <div className="dbcsw-truth-item">
          <span className="dbcsw-k">configured</span>
          <span className="dbcsw-v">{truth?.configured ?? "—"}</span>
        </div>
        <div className="dbcsw-truth-item">
          <span className="dbcsw-k">effective</span>
          <span className="dbcsw-v">{truth?.effective ?? "UNAVAILABLE"}</span>
        </div>
        <div className="dbcsw-truth-item">
          <span className="dbcsw-k">switch target</span>
          <span className="dbcsw-v">{(targetProvider ?? "").trim() === "" ? "—" : String(targetProvider)}</span>
        </div>
        <div className="dbcsw-truth-item">
          <span className="dbcsw-k">truth source</span>
          <span className="dbcsw-v">{truth?.source ?? "—"}</span>
        </div>
      </div>
      {truth?.note ? <p className="dbcsw-note">{truth.note}</p> : null}

      {!manage && (
        <p className="dbcsw-note" data-tone="warn">
          /api/db/manage/status has not answered — readiness is UNAVAILABLE and the switch stays disabled.
        </p>
      )}

      <ul className="dbcsw-list">
        {readiness.checklist.map((row) => (
          <li key={row.key} className="dbcsw-row" data-state={row.state}>
            <span className="dbcsw-state">{row.state}</span>
            <span className="dbcsw-label">{row.label}</span>
          </li>
        ))}
      </ul>

      <p className="dbcsw-foot">
        every row restates a backend field: /api/db/manage/status (provider, domain health, password reference,
        psycopg presence), /api/db/manage/report (provider_switch_ready — set only when the migration finished
        COMPLETE with validation PASSED) and the client-held connection test. this panel adds no verdict of its
        own: BLOCK is the backend saying no; WARN asks you to type an acknowledgement.
      </p>
    </div>
  );
}

export default ProviderSwitchPlan;
