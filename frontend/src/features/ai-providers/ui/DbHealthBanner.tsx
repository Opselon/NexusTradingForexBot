/**
 * features/ai-providers/ui/DbHealthBanner.tsx — the PostgreSQL health banner
 * for the AI Providers console (wave §C1/§C2, brief §26).
 *
 * WHAT THIS IS: an ADDITIVE inline banner that renders ONLY when the
 * PostgreSQL backend is in a known bad state. It exists so a misconfigured
 * database yields a clear, actionable health state instead of the page
 * spinning on a route that keeps raising, or surfacing an opaque "request
 * failed".
 *
 * WHAT THIS NEVER DOES:
 *   - render a password, a DSN or any credential material. The password is
 *     represented ONLY as "configured: yes/no" (contract law 3 / Section 37).
 *   - invent a verdict. DEGRADED comes from the backend's own classification
 *     (the 503 body) or from the health service's connected:false; every
 *     other state is the honest UNKNOWN, rendered as such.
 *   - zero-fill. A field the payload did not carry renders "—" or the word
 *     UNKNOWN, never 0 / "" / localhost.
 *   - restructure or restyle the page. When the database is healthy this
 *     component renders nothing and the page is byte-identical to before.
 *
 * Presentation: the page's own .aipage-banner family (theme tokens only;
 * additive classes in ai-providers.css, no shared stylesheet edits).
 */

import { useCallback, type ReactElement } from "react";

import {
  type DbHealthState,
  type DbSafeDiagnostics,
  databaseStateLabel,
  databaseStateRemediation,
} from "./dbHealth";
import { useRefetchDbHealth } from "./useDbHealth";

/** Render one safe diagnostic field, or "—" when the backend did not send it. */
function Fact({ label, value }: { label: string; value: string | null | undefined }): ReactElement {
  return (
    <span className="aipage-dbh-fact">
      <span className="aipage-dbh-fact-k">{label}</span>
      <span className="aipage-dbh-fact-v inline-mono">{value && value !== "" ? value : "—"}</span>
    </span>
  );
}

/** The safe connection facts: host / port / database / user, never a password. */
function SafeFacts({ diagnostics }: { diagnostics: DbSafeDiagnostics | undefined }): ReactElement {
  const port =
    typeof diagnostics?.port === "number"
      ? String(diagnostics.port)
      : typeof diagnostics?.port === "string" && diagnostics.port !== ""
        ? diagnostics.port
        : null;
  return (
    <div className="aipage-dbh-facts">
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
 * The inline PostgreSQL health banner. Render it only when
 * `shouldRenderDbHealth(state)` is true (the parent decides, so the loading
 * phase never flickers a banner on mount).
 */
export function DbHealthBanner({ state }: { state: DbHealthState }): ReactElement | null {
  const refetch = useRefetchDbHealth();
  const onCheckAgain = useCallback(() => refetch(), [refetch]);

  // UNKNOWN is its own honest state: the health surface could not answer, so
  // the banner says so rather than implying a database failure.
  const isUnknown = state.phase === "UNKNOWN";
  const tone = isUnknown ? "warn" : "bad";
  const title = isUnknown
    ? "PostgreSQL: health state unavailable"
    : `PostgreSQL: ${databaseStateLabel(state.database_state)}`;

  const remediation = isUnknown
    ? "The engine's database health probe did not return a state — check the Database page, or probe again."
    : databaseStateRemediation(state.database_state);

  return (
    <section
      className={`aipage-dbh ${tone === "bad" ? "aipage-dbh-bad" : "aipage-dbh-warn"}`}
      role="alert"
      aria-labelledby="aipage-dbh-title"
    >
      <div className="aipage-dbh-head">
        <span className="aipage-dbh-glyph" aria-hidden="true">
          {tone === "bad" ? "✕" : "?"}
        </span>
        <h2 id="aipage-dbh-title" className="aipage-dbh-title">
          {title}
        </h2>
        <span className="aipage-dbh-tag inline-mono">{state.phase}</span>
      </div>
      {!isUnknown ? <SafeFacts diagnostics={state.diagnostics} /> : null}
      {state.detail ? (
        <p className="aipage-dbh-detail">
          <span className="aipage-dbh-detail-k">driver reason</span>
          <span className="aipage-dbh-detail-v inline-mono">{state.detail}</span>
        </p>
      ) : null}
      <p className="aipage-dbh-remedy">{remediation}</p>
      {Array.isArray(state.hints) && state.hints.length > 0 ? (
        <ul className="aipage-dbh-hints">
          {state.hints.map((hint, i) => (
            <li key={i} className="aipage-dbh-hint">
              {hint}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="aipage-dbh-actions">
        <button type="button" className="btn small aipage-dbh-check" onClick={onCheckAgain}>
          Check again
        </button>
        <span className="aipage-dbh-note small muted">
          state from <span className="inline-mono">/api/db/manage/status</span> — the page itself does not probe the database
        </span>
      </div>
    </section>
  );
}
