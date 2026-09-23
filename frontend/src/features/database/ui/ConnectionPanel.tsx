/**
 * The Manage tab's connection section: URL entry + the discrete PostgreSQL
 * form + the advanced provider options, in one panel (contract §0/§2 Lane B).
 *
 * OWNER: lane B (db-provider-pro) — this lane owns future edits.
 * CONSUMES: `ConnectionUrlField` (client-side URL parse, kept in sync with
 *   the discrete fields), `ProviderOptions` (advanced knobs), `model.ts`
 *   (specs/validation/baseline helpers), `DbManageStatus` from the frozen
 *   `api.ts` (the payload source of truth).  This file imports
 *   `ui/connection.css` (the `.dbcp-` stylesheet; `ui/database.css` stays
 *   untouched).
 * PROVIDES: the default-exported `ConnectionPanel` with EXACTLY the props
 *   signature the contract fixes (§3.5) — the orchestrator mounts it in
 *   `ManageTab.tsx`.
 * INVARIANTS:
 *   - the URL field and the discrete form stay in sync (edit either, the
 *     other follows); a malformed URL never reaches the backend;
 *   - every existing guard passes through untouched: blank password means
 *     "keep the stored secret", mismatched confirmation is refused client
 *     side, an invalid payload never POSTs (the parent's `errors` already
 *     combine the discrete + advanced specs);
 *   - no `any`, no `@ts-expect-error`; missing backend fields render `—` /
 *     UNAVAILABLE;
 *   - house style only: theme tokens via `connection.css`, logical
 *     properties, one `:focus-visible` rule, animations gated on
 *     `prefers-reduced-motion`.
 * EXTEND: a new section — add a child component and a `dbcp-` rule; never
 *   reach into `database.css`.
 */

import { type JSX } from "react";
import type { FieldErrors, FieldValues } from "@/features/config/validation";
import { FieldRow, NumberField, SelectField, TextField } from "@/features/config/ui/kit";
import type { DbManageStatus } from "../api";
import { PG_SSL_MODES, pgSpecs } from "../model";
import { ProviderOptions } from "./ProviderOptions";
import { ConnectionUrlField } from "./ConnectionUrlField";
import "./connection.css";

export function ConnectionPanel(props: {
  manage: DbManageStatus | null | undefined;
  values: FieldValues;
  set: (k: string, v: string | boolean) => void;
  errors: FieldErrors;
}): JSX.Element {
  const { manage, values, set, errors } = props;

  /* A URL-carried password lands in BOTH password fields at once, so the
   * existing cross-field rule stays satisfied and blank still means
   * "keep the stored secret". */
  const capturePassword = (v: string) => {
    set("password", v);
    set("confirm_password", v);
  };

  return (
    <div className="dbcp-panel">
      <div className="dbc-section-title">connection</div>

      <ConnectionUrlField values={values} set={set} onPassword={capturePassword} />

      <div className="dbcp-grid">
        {pgSpecs().map((spec) => {
          const err = errors[spec.key]?.[0] ?? null;
          const v = values[spec.key];
          return (
            <FieldRow key={spec.key} label={spec.label ?? spec.key} hint={spec.hint ?? spec.key} error={err}>
              {spec.kind === "integer" ? (
                <NumberField value={String(v ?? "")} onChange={(x) => set(spec.key, x)} error={err} step="1" spec={spec.label} />
              ) : spec.kind === "enum" ? (
                <SelectField
                  value={String(v ?? "")}
                  onChange={(x) => set(spec.key, x)}
                  options={PG_SSL_MODES}
                  error={err}
                  label={spec.key}
                />
              ) : (
                <TextField
                  value={String(v ?? "")}
                  onChange={(x) => set(spec.key, x)}
                  error={err}
                  placeholder={spec.secret ? "••• leave blank to keep the stored secret" : undefined}
                />
              )}
            </FieldRow>
          );
        })}
      </div>

      <p className="dbc-note" style={{ marginTop: 6 }}>
        a password typed for a test is held only for that probe (ephemeral key, removed right after) — a failed test
        never overwrites the stored secret.
      </p>

      <ProviderOptions manage={manage} values={values} set={set} errors={errors} />
    </div>
  );
}
