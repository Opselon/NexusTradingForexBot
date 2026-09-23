/**
 * TypedConfirmModal — React port of the legacy typed-confirmation escalation
 * (Web/ux.js NX.confirmTyped / NX.confirmModeChange, repo law invariant #4:
 * "typed-confirm LIVE escalation semantics must survive porting").
 *
 * Contract, identical to legacy:
 *  - resolves TRUE only on an explicit confirm click;
 *  - when `requireText` is set, the confirm button stays DEAD until the operator
 *    types the EXACT token (used for any transition into LIVE);
 *  - a typed gate is BLOCKING: backdrop click never dismisses it, Esc only
 *    aborts (resolves false) — a money-path escalation can never be dismissed
 *    accidentally, and it can never be silently downgraded to a plain confirm.
 *
 * It ships from the command-center lane so any feature (control-center mode
 * switch, research commands, ...) can reuse one implementation.
 */

import { useEffect, useRef, useState } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import type { ReactNode } from "react";

export function TypedConfirmModal({
  title,
  body,
  impact,
  requireText = null,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  busy = false,
  onResolve,
}: {
  title: string;
  body?: ReactNode;
  impact?: ReactNode;
  /** Exact token that arms the confirm button (e.g. "LIVE"). null → plain confirm. */
  requireText?: string | null;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  /** true = operator explicitly confirmed; false = aborted/never armed. */
  onResolve: (confirmed: boolean) => void;
}) {
  const [typed, setTyped] = useState("");
  const armed = requireText === null || typed === requireText;
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const resolveRef = useRef(onResolve);
  resolveRef.current = onResolve;

  // Legacy escalation contract preserved: Esc only aborts (resolves false)
  // while NOT busy — the shared hook adds focus-in/trap/restore + the
  // stacked-dialog focus guard around it.
  useDialogA11y(boxRef, () => resolveRef.current(false), { escEnabled: !busy });

  useEffect(() => {
    const focusTimer = window.setTimeout(() => {
      (requireText ? inputRef.current : null)?.focus();
    }, 30);
    return () => window.clearTimeout(focusTimer);
  }, [requireText]);

  return (
    <div className="modal-overlay tc-overlay" role="presentation">
      <div
        className={`modal tc-modal ${requireText ? "tc-typed" : ""}`}
        ref={boxRef}
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-header tc-header">
          <span aria-hidden="true">⚠</span> {title}
        </div>
        <div className="modal-body">
          {body ? <div className="small tc-body">{body}</div> : null}
          {impact ? <div className="tc-impact">{impact}</div> : null}
          {requireText ? (
            <div className="tc-type-row">
              <label className="tiny" htmlFor="tc-token">
                Type <span className="inline-mono tc-token-ref">{requireText}</span> to enable confirmation
              </label>
              <div className="row">
                <input
                  id="tc-token"
                  ref={inputRef}
                  className="input tc-input"
                  autoComplete="off"
                  spellCheck={false}
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder={requireText}
                  aria-describedby="tc-hint"
                />
                <span id="tc-hint" className="tiny faint">
                  exact match required — a LIVE switch is never confirmed by click alone
                </span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={() => resolveRef.current(false)}>
            {cancelLabel} <kbd>esc</kbd>
          </button>
          <button
            className={`btn ${requireText ? "danger" : "primary"}`}
            disabled={busy || !armed}
            title={armed ? undefined : `type ${requireText} first`}
            onClick={() => resolveRef.current(true)}
          >
            {busy ? "sending…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** What actually changes per target mode — copy parity with legacy MODE_IMPACT. */
export const MODE_IMPACT: Record<string, string> = {
  PAPER: "Simulated fills only — no real orders reach the broker.",
  SHADOW: "Signals are computed but never dispatched as orders.",
  LIVE: "The engine will dispatch REAL orders to the connected broker account.",
};

/**
 * Spec for a mode-change confirmation (legacy NX.confirmModeChange semantics):
 * anything → LIVE requires typing "LIVE"; PAPER ↔ SHADOW gets a single impact
 * confirm with no typing. Never infers or mutates anything — the caller sends
 * the command ONLY when this resolves true, and the backend answer decides.
 */
export function modeChangeSpec(fromMode: string, toMode: string) {
  const to = (toMode || "").toUpperCase();
  const toLive = to === "LIVE";
  return {
    title: `Switch execution mode: ${fromMode} → ${to}?`,
    body: "This changes how the engine executes orders.",
    impact: (
      <>
        <b>What changes</b>: {MODE_IMPACT[to] ?? to}
        {toLive && <div className="tc-live-warn">Real money is at risk. This affects your live broker account.</div>}
      </>
    ),
    requireText: toLive ? "LIVE" : null,
    confirmLabel: toLive ? "Arm LIVE execution" : "Confirm",
  } as const;
}
