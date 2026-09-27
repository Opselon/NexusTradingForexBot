/**
 * The advanced `DatabaseConfig` knobs the backend already owned but had no UI
 * for (contract §3.3): command_timeout_sec, connect_timeout_sec,
 * migrate_on_startup, pooling_enabled, plus the config's domain scope.
 *
 * OWNER: lane B (db-provider-pro) — this lane owns future edits.
 * CONSUMES: `model.ts` (`optionsFromStatus`, `domainOptions`,
 *   `advancedSpecsWithDomains`, `optionRowState`) — every value shown here
 *   derives from a named backend field; `connectionUrl.ts` never (these are
 *   not URL fields).  The parent's `values`/`set` hold the knobs in the SAME
 *   form the discrete fields use, so Save persists them through the existing
 *   config endpoint (POST /api/db/manage/config replaces the row — the
 *   payload carries every knob).
 * PROVIDES: `ProviderOptions` — the knob grid for `ConnectionPanel`.
 * INVARIANTS:
 *   - every figure traces to a named backend field; a knob absent from the
 *     payload renders the explicit word UNAVAILABLE and its control stays
 *     inert (never a zero-filled or invented default);
 *   - the domain select offers only backend-reported domains;
 *   - an out-of-range knob is refused client-side before any POST
 *     (bounds mirror the backend: command 0..600, connect 1..120);
 *   - no `any`, no `@ts-expect-error`.
 * EXTEND: a new knob — add it to `advancedSpecs()` in model.ts first (bounds
 *   mirror `settings/provider_options.py`), then render it here.
 */

import { firstError, type FieldErrors, type FieldValues } from "@/features/config/validation";
import { CheckField, FieldRow, NumberField, SelectField } from "@/features/config/ui/kit";
import type { DbManageStatus } from "../api";
import {
  OPTION_UNAVAILABLE,
  advancedSpecsWithDomains,
  domainOptions,
  optionRowState,
  optionsFromStatus,
} from "../model";

export function ProviderOptions({
  manage,
  values,
  set,
  errors,
}: {
  manage: DbManageStatus | null | undefined;
  values: FieldValues;
  set: (k: string, v: string | boolean) => void;
  errors: FieldErrors;
}) {
  const opts = optionsFromStatus(manage);
  const domains = domainOptions(manage);
  const specs = advancedSpecsWithDomains(domains);

  return (
    <div className="dbcp-options">
      <div className="dbcp-options-head">
        <span className="dbcp-section-title" style={{ margin: 0 }}>
          provider options
        </span>
        <span className={`dbcp-options-state ${opts.live ? "live" : ""}`}>
          {opts.live ? "live values" : OPTION_UNAVAILABLE}
        </span>
        <span className="dbcp-hint">
          {opts.database !== null ? (
            <>
              database: <span className="dbcp-mono">{opts.database}</span>
            </>
          ) : (
            <>
              database: {OPTION_UNAVAILABLE}
            </>
          )}
        </span>
      </div>

      {specs.map((spec) => {
        const err = firstError(errors, spec.key);

        /* domain: available exactly when the backend reported a list. */
        if (spec.key === "domain") {
          const current = String(values.domain ?? "");
          if (domains.length === 0) {
            return (
              <FieldRow
                key={spec.key}
                label={spec.label ?? spec.key}
                hint={`${OPTION_UNAVAILABLE} — this backend build reports no domains`}
                error={err}
              >
                <span className="dbcp-hint">{OPTION_UNAVAILABLE}</span>
              </FieldRow>
            );
          }
          const options = current && !domains.includes(current) ? [...domains, current] : domains;
          return (
            <FieldRow
              key={spec.key}
              label={spec.label ?? spec.key}
              hint={spec.hint ?? spec.key}
              error={err}
              dirty={opts.domain !== null && current !== opts.domain}
            >
              <SelectField value={current} onChange={(x) => set(spec.key, x)} options={options} error={err} label={spec.key} />
            </FieldRow>
          );
        }

        const row = optionRowState(opts, spec.key);
        const reportedValue = String(values[spec.key] ?? "");

        if (row.unavailable) {
          return (
            <FieldRow
              key={spec.key}
              label={spec.label ?? spec.key}
              hint={`${OPTION_UNAVAILABLE} — this backend build does not report this knob`}
              error={err}
            >
              <span className="dbcp-hint">{OPTION_UNAVAILABLE}</span>
            </FieldRow>
          );
        }

        if (spec.kind === "integer") {
          /* The optional pool knobs render blank when the operator never set
           * one — that blank is "the engine default applies", NOT a 0, and
           * saving it back sends nothing (advancedPayload omits an empty
           * control so the stored value survives untouched). */
          return (
            <FieldRow
              key={spec.key}
              label={spec.label ?? spec.key}
              hint={row.unavailable ? `${OPTION_UNAVAILABLE} — this backend build does not report this knob` : `${spec.hint ?? spec.key} · reported: ${row.reported}`}
              error={err}
              dirty={reportedValue !== row.reported}
            >
              <NumberField
                value={reportedValue}
                onChange={(x) => set(spec.key, x)}
                error={err}
                step="1"
                spec={spec.label}
              />
            </FieldRow>
          );
        }

        /* boolean knobs: CheckField shows the CURRENT form state; the
         * reported value rides the hint so a divergence is visible. */
        return (
          <FieldRow
            key={spec.key}
            label={spec.label ?? spec.key}
            hint={`${spec.hint ?? spec.key} · reported: ${row.reported}`}
            error={err}
            dirty={reportedValue !== row.reported}
          >
            <CheckField
              checked={reportedValue === "true"}
              onChange={(x) => set(spec.key, x)}
              label={spec.label ?? spec.key}
            />
          </FieldRow>
        );
      })}
    </div>
  );
}
