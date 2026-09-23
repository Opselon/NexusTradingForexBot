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
import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";

export function TypedConfirmModal({
  title,
  body,
  impact,
  requireText = null,
  confirmLabel,
  cancelLabel,
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
  const t = useI18n((s) => s.t);
  const [typed, setTyped] = useState("");
  const armed = requireText === null || typed === requireText;
  const cancelText = cancelLabel ?? t("ux.confirm.cancel", "Cancel");
  const confirmText = confirmLabel ?? t("ux.confirm.ok", "Confirm");
  const inputRef = useRef<HTMLInputElement>(null);
  const resolveRef = useRef(onResolve);
  resolveRef.current = onResolve;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) resolveRef.current(false);
    };
    window.addEventListener("keydown", onKey);
    const focusTimer = window.setTimeout(() => {
      (requireText ? inputRef.current : null)?.focus();
    }, 30);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(focusTimer);
    };
  }, [busy, requireText]);

  return (
    <div className="modal-overlay tc-overlay" role="presentation">
      <div
        className={`modal tc-modal ${requireText ? "tc-typed" : ""}`}
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
                {t("command-center.confirm.type_prefix", "Type")}{" "}
                <span className="inline-mono tc-token-ref">{requireText}</span>{" "}
                {t("command-center.confirm.type_suffix", "to enable confirmation")}
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
                  {t("command-center.confirm.exact_hint", "exact match required — a LIVE switch is never confirmed by click alone")}
                </span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={() => resolveRef.current(false)}>
            {cancelText} <kbd>esc</kbd>
          </button>
          <button
            className={`btn ${requireText ? "danger" : "primary"}`}
            disabled={busy || !armed}
            title={armed ? undefined : t("command-center.confirm.type_first", "type {w} first", { w: requireText ?? "" })}
            onClick={() => resolveRef.current(true)}
          >
            {busy ? t("command-center.confirm.sending", "sending…") : confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Same contract as the store's `t` (see stores/i18nStore) — callers pass
 *  `useI18n.getState().t` (or the render-time `t`) so the spec translates. */
export type TranslateFn = (key: string, fallback: string, vars?: Record<string, string | number>) => string;
/** Default when a caller has no translator at hand: English source stays. */
const identityTranslate: TranslateFn = (_key, fallback) => fallback;

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
export function modeChangeSpec(fromMode: string, toMode: string, t: TranslateFn = identityTranslate) {
  const to = (toMode || "").toUpperCase();
  const toLive = to === "LIVE";
  const impact =
    to === "PAPER"
      ? t("command-center.mode.impact_paper", "Simulated fills only — no real orders reach the broker.")
      : to === "SHADOW"
        ? t("command-center.mode.impact_shadow", "Signals are computed but never dispatched as orders.")
        : to === "LIVE"
          ? t("command-center.mode.impact_live", "The engine will dispatch REAL orders to the connected broker account.")
          : MODE_IMPACT[to] ?? to;
  return {
    title: t("ux.mode.title", "Switch execution mode: {from} → {to}?", { from: fromMode, to }),
    body: t("ux.mode.body", "This changes how the engine executes orders."),
    impact: (
      <>
        <b>{t("ux.mode.impact_label", "What changes")}</b>: {impact}
        {toLive && (
          <div className="tc-live-warn">
            {t("ux.mode.live_warning", "Real money is at risk. This affects your live broker account.")}
          </div>
        )}
      </>
    ),
    requireText: toLive ? "LIVE" : null,
    confirmLabel: toLive ? t("ux.mode.confirm_live", "Arm LIVE execution") : t("ux.confirm.ok", "Confirm"),
  } as const;
}
