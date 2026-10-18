/**
 * features/provisioning/ui/DbHealthBadge.tsx — the PostgreSQL health badge for
 * the Model Setup console (wave §C1/§C2, brief §26).
 *
 * WHAT THIS IS: an ADDITIVE inline badge that renders ONLY when the PostgreSQL
 * backend is in a known bad state (or its health state is honestly UNKNOWN).
 * A misconfigured database must not look like a training-stack problem on this
 * page, so the badge names the database condition and its safe connection
 * facts instead.
 *
 * WHAT THIS NEVER DOES:
 *   - render a password, a DSN or any credential material. The password is
 *     represented ONLY as "configured: yes/no" (contract law 3 / Section 37).
 *   - invent a verdict. DEGRADED comes from the health service's own
 *     connected:false + driver reason; every other state is the honest
 *     UNKNOWN, labelled as such.
 *   - zero-fill. A field the payload did not carry renders "—", never 0 / "" /
 *     localhost.
 *   - restructure or restyle the page. When the database is healthy this
 *     renders nothing and the page is unchanged.
 *
 * Presentation: the feature's own .pv-banner family + the shared
 * .badge/.inline-mono/.small/.muted vocabulary (theme tokens only; additive
 * classes in provisioning.css, no shared stylesheet edits).
 */

import { useCallback, type ReactElement } from "react";
import { useI18n } from "@/stores/i18nStore";
import type { DbHealthSnapshot, DbSafeDiagnostics } from "../dbHealth";
import { dbHealthStateLabel, dbHealthStateRemediation } from "../dbHealth";
import { useRefetchDbHealth } from "./useDbHealth";
import { classNames } from "./kit";

/** Render one safe diagnostic field, or "—" when the backend did not send it. */
function Fact({ label, value }: { label: string; value: string | null | undefined }): ReactElement {
  return (
    <span className="pv-dbh-fact">
      <span className="pv-dbh-fact-k">{label}</span>
      <span className="pv-dbh-fact-v inline-mono">{value && value !== "" ? value : "—"}</span>
    </span>
  );
}

/** The safe connection facts: provider / host / port / database / user. */
function SafeFacts({ diagnostics }: { diagnostics: DbSafeDiagnostics | undefined }): ReactElement {
  const port =
    typeof diagnostics?.port === "number"
      ? String(diagnostics.port)
      : typeof diagnostics?.port === "string" && diagnostics.port !== ""
        ? diagnostics.port
        : null;
  return (
    <div className="pv-dbh-facts">
      <Fact label="provider" value={diagnostics?.provider} />
      <Fact label="host" value={diagnostics?.host} />
      <Fact label="port" value={port} />
      <Fact label="database" value={diagnostics?.database} />
      <Fact label="user" value={diagnostics?.username} />
      <Fact
        label="password"
        value={
          typeof diagnostics?.password_configured === "boolean"
            ? diagnostics.password_configured
              ? "configured (value never sent to the UI)"
              : "NOT configured"
            : null
        }
      />
    </div>
  );
}

/**
 * The inline PostgreSQL health badge. Render it only when
 * `shouldRenderDbHealth(state)` is true (the parent decides, so the loading
 * phase never flickers a badge on mount).
 */
export function DbHealthBadge({ state }: { state: DbHealthSnapshot }): ReactElement | null {
  const t = useI18n((s) => s.t);
  const refetch = useRefetchDbHealth();
  const onCheckAgain = useCallback(() => refetch(), [refetch]);

  const isUnknown = state.phase === "UNKNOWN";
  const tone = isUnknown ? "warn" : "bad";
  const title = isUnknown
    ? t("provisioning.dbHealth.unknown_title", "PostgreSQL: health state unavailable")
    : t("provisioning.dbHealth.title", "PostgreSQL: {state}", {
        state: dbHealthStateLabel(state.database_state),
      });

  const remediation = isUnknown
    ? t(
        "provisioning.dbHealth.unknown_remedy",
        "The engine's database health probe did not return a state — check the Database page, or probe again.",
      )
    : t(
        `provisioning.dbHealth.remedy_${(state.database_state ?? "unknown").toLowerCase()}`,
        dbHealthStateRemediation(state.database_state),
      );

  return (
    <div
      className={classNames("pv-banner", tone === "bad" ? "pv-dbh-bad" : "pv-dbh-warn")}
      role="alert"
    >
      <div className="pv-dbh-head">
        <span className="pv-dbh-glyph" aria-hidden="true">
          {tone === "bad" ? "✕" : "?"}
        </span>
        <span className="pv-dbh-title">{title}</span>
        <span className="pv-dbh-tag inline-mono">{state.phase}</span>
      </div>
      {!isUnknown ? <SafeFacts diagnostics={state.diagnostics} /> : null}
      {state.detail ? (
        <p className="pv-dbh-detail">
          <span className="pv-dbh-detail-k">{t("provisioning.dbHealth.reason", "driver reason")}</span>
          <span className="pv-dbh-detail-v inline-mono">{state.detail}</span>
        </p>
      ) : null}
      <p className="pv-dbh-remedy">{remediation}</p>
      {Array.isArray(state.hints) && state.hints.length > 0 ? (
        <ul className="pv-dbh-hints">
          {state.hints.map((hint, i) => (
            <li key={i} className="pv-dbh-hint">
              {hint}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="pv-dbh-actions">
        <button
          type="button"
          className="btn small pv-dbh-check"
          onClick={onCheckAgain}
          aria-label={t("provisioning.dbHealth.check_again", "Check database health again")}
        >
          {t("provisioning.dbHealth.check_again", "Check again")}
        </button>
        <span className="pv-dbh-note small muted">
          {t(
            "provisioning.dbHealth.provenance",
            "state from /api/db/manage/status — this page does not probe the database",
          )}
        </span>
      </div>
    </div>
  );
}
