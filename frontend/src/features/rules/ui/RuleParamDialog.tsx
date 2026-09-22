/**
 * RuleParamDialog — modal editor for a rule's stored parameter set.
 *
 * Contract (unchanged from the inline panel it replaced):
 *  - kinds are inherited from the ORIGINAL backend value (a number never
 *    becomes free text) via model.paramSpecs/validateParamEdits/paramPayload;
 *  - an invalid payload is blocked client-side and never reaches the wire;
 *  - Save arms the page-level ConfirmModal (double confirmation) and only
 *    then runs POST /api/rules/toggle — the backend verdict decides.
 *
 * UX additions: Esc / backdrop close (suppressed while the confirm modal is
 * open above), focus trapped at dialog level, per-field errors surfaced after
 * the first rejected save and kept live while editing, "N edited" dirty tag.
 */

import { useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { FieldRow, NumberField, TextField } from "@/features/config/ui/kit";
import { firstError, type FieldErrors } from "@/features/config/validation";
import { catTone, validateParamEdits, type RuleVO } from "../model";

export type RuleDraft = Record<string, string>;

export function RuleParamDialog({
  rule,
  draft,
  busy,
  locked,
  showErrors,
  onChange,
  onClose,
  onSave,
}: {
  rule: RuleVO;
  draft: RuleDraft;
  busy: boolean;
  /** True while the ConfirmModal is stacked above — suppress Esc/backdrop. */
  locked: boolean;
  showErrors: boolean;
  onChange: (key: string, value: string) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const errors: FieldErrors = validateParamEdits(rule, draft);
  const changed = rule.params.filter((p) => (draft[p.key] ?? "") !== p.value).length;

  // Focus the dialog on open so Esc and Tab start here.
  useEffect(() => {
    boxRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !locked) {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [locked, onClose]);

  const onBackdrop = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (!locked && e.target === e.currentTarget && !busy) onClose();
  };

  return (
    <div className="rl-overlay" onMouseDown={onBackdrop}>
      <div
        className="rl-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Parameters — ${rule.name}`}
        ref={boxRef}
        tabIndex={-1}
      >
        <div className="rl-dialog-head">
          <span className="ico" aria-hidden="true">§</span>
          <span className="t">
            <b>Parameters</b>
            <span className="who">{rule.name}</span>
          </span>
          <span className={`rl-tag rl-tone-${catTone(rule.category)}`} title={rule.category}>
            <span className="lbl">{rule.category}</span>
          </span>
          <span className="spacer" />
          {changed > 0 && <span className="rl-dirty-tag">{changed} edited</span>}
          <button className="rl-x" onClick={onClose} disabled={busy} aria-label="Close parameter editor">
            ✕
          </button>
        </div>

        <div className="rl-dialog-body">
          <div className="rl-form-grid">
            {rule.params.map((p) => {
              const err = showErrors ? firstError(errors, p.key) : null;
              return (
                <FieldRow
                  key={p.key}
                  label={p.key}
                  hint={p.threshold ? `${p.kind} · threshold` : p.kind}
                  error={err}
                >
                  {p.kind === "number" ? (
                    <NumberField
                      value={draft[p.key] ?? ""}
                      step="any"
                      error={err}
                      onChange={(v) => onChange(p.key, v)}
                    />
                  ) : (
                    <TextField
                      value={draft[p.key] ?? ""}
                      error={err}
                      onChange={(v) => onChange(p.key, v)}
                    />
                  )}
                </FieldRow>
              );
            })}
          </div>
          <div className="rl-dialog-note">
            numeric parameters must be ≥ 0 · kinds follow the stored backend value · an invalid
            payload is rejected here and never sent
          </div>
        </div>

        <div className="rl-dialog-foot">
          <span className="hint">
            saving re-confirms, then re-reads the table from the backend
          </span>
          <button className="btn ghost" onClick={onClose} disabled={busy}>
            Cancel <kbd>esc</kbd>
          </button>
          <button className="btn primary" onClick={onSave} disabled={busy}>
            Save parameters…
          </button>
        </div>
      </div>
    </div>
  );
}
