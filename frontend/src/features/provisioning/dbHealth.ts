/**
 * features/provisioning/model.ts — PostgreSQL health state (wave §C1/§C2,
 * brief §26). See the sibling block in features/ai-providers/ui/dbHealth.ts
 * for the full contract notes; the house convention is one typed surface per
 * feature, so this is the provisioning feature's own view of the same shared
 * health snapshot.
 *
 * ONE PROBE: the engine's existing centralized health surface
 * (GET /api/db/manage/status — DatabaseHealthService.snapshot() +
 * load_ui_config()). The provisioning page does NOT open its own connection to
 * PostgreSQL; it reads the same cached snapshot the Database page already
 * polls, so the pages share one server-side probe. The health service
 * memoizes a healthy result (never a failure), so a broken database is always
 * re-read live.
 *
 * SECRETS: the password is represented ONLY as a boolean (contract law 3 /
 * Section 37). No credential value is ever received, held or rendered.
 *
 * HONESTY: every field is optional. A field the backend did not send renders
 * as "—" / UNKNOWN, never a zero-filled plausible value.
 */

/** The backend's own classification tokens (legacy_errors.DatabaseState). */
export type DbHealthState =
  | "AUTH_FAILED"
  | "UNREACHABLE"
  | "DATABASE_NOT_FOUND"
  | "PERMISSION_DENIED"
  | "UNKNOWN";

const KNOWN_STATES: readonly DbHealthState[] = [
  "AUTH_FAILED",
  "UNREACHABLE",
  "DATABASE_NOT_FOUND",
  "PERMISSION_DENIED",
  "UNKNOWN",
];

export function isKnownDbHealthState(value: unknown): value is DbHealthState {
  return typeof value === "string" && (KNOWN_STATES as readonly string[]).includes(value);
}

/** Safe connection diagnostics — no credential material, ever. */
export interface DbSafeDiagnostics {
  provider?: string | null;
  host?: string | null;
  port?: number | null;
  database?: string | null;
  username?: string | null;
  password_configured?: boolean | null;
}

/** One domain snapshot from DatabaseHealthService.check_domain(). */
export interface DbDomainHealth {
  domain?: string;
  provider?: string;
  /** CONNECTED | DISCONNECTED | DRIVER_UNAVAILABLE | UNKNOWN. */
  status?: string;
  connected?: boolean;
  database?: string;
  /** "host:port" for postgresql, "Local" for sqlite. */
  server?: string;
  /** Healthy | Warning | Error. */
  health?: string;
  /** The driver's own connection reason (HEALTH-DBREASON), verbatim or "". */
  error?: string;
}

/** GET /api/db/manage/status — the fields this feature reads. */
export interface DbManageStatus {
  success?: boolean;
  provider?: string;
  overall?: string;
  domains?: Record<string, DbDomainHealth>;
  postgres?: DbSafeDiagnostics | null;
  password_set?: boolean;
  /** Backend-computed remediation guidance, rendered verbatim. */
  hints?: string[];
}

/**
 * The rendered verdict, derived ONLY from payload fields.
 *
 * - "HEALTHY"  — the audit domain reports connected (no badge; the page
 *                renders exactly as it does today).
 * - "DEGRADED" — a database failure the health service reported.
 * - "LOADING"  — no payload yet (honest "checking" state, never a guess).
 * - "UNKNOWN"  — the snapshot could not answer; rendered as UNKNOWN, never as
 *                healthy and never as an error.
 */
export type DbHealthPhase = "LOADING" | "HEALTHY" | "DEGRADED" | "UNKNOWN";

/** The shape the badge component consumes. */
export interface DbHealthSnapshot {
  phase: DbHealthPhase;
  /** Verbatim classification token when phase is DEGRADED. */
  database_state?: DbHealthState;
  /** Safe connection facts (host/port/database/user; never a password). */
  diagnostics?: DbSafeDiagnostics;
  /** The driver's own reason, verbatim when the backend sent one. */
  detail?: string;
  /** Backend-computed remediation hints, rendered verbatim. */
  hints?: string[];
}

/**
 * Derive the rendered state from a resolved manage/status payload.
 *
 * A snapshot that cannot answer (no domains, no audit entry) is UNKNOWN rather
 * than DEGRADED: this UI must not claim a database failure the health service
 * did not report.
 */
export function dbHealthFromStatus(payload: DbManageStatus | null | undefined): DbHealthSnapshot {
  if (!payload) return { phase: "LOADING" };
  const audit = payload.domains?.["audit"];
  if (audit && typeof audit === "object") {
    if (audit.connected === true) return { phase: "HEALTHY" };
    if (audit.connected === false) {
      return {
        phase: "DEGRADED",
        database_state: classifyDbFailure(audit.error, audit.status, payload.provider),
        diagnostics: mergeSafeDiagnostics(payload.postgres, audit),
        detail: audit.error || undefined,
        hints: Array.isArray(payload.hints) ? payload.hints.slice() : undefined,
      };
    }
  }
  return { phase: "UNKNOWN" };
}

/**
 * Best-effort classification of a health-service failure reason.
 *
 * Mirrors the backend's own substring probes (`legacy_errors._PATTERNS`) so the
 * badge names the same condition the API boundary would classify. An
 * unrecognised reason falls back to UNKNOWN — the driver reason is still
 * rendered verbatim alongside the label, so nothing is hidden.
 */
export function classifyDbFailure(
  reason: string | undefined,
  status: string | undefined,
  provider: string | null | undefined,
): DbHealthState {
  const text = `${reason ?? ""} ${status ?? ""}`.toLowerCase();
  if (!text.trim() && provider !== "postgresql") return "UNKNOWN";
  if (["password authentication failed", "no password supplied", "authentication failed", "invalid password", "fe_sendauth"].some((m) => text.includes(m))) {
    return "AUTH_FAILED";
  }
  if (["connection refused", "could not connect to server", "connection timed out", "timeout expired", "getaddrinfo", "name or service not known", "network is unreachable", "too many clients"].some((m) => text.includes(m))) {
    return "UNREACHABLE";
  }
  if (['database "', "does not exist"].some((m) => text.includes(m))) {
    return "DATABASE_NOT_FOUND";
  }
  if (["permission denied", "must have privilege"].some((m) => text.includes(m))) {
    return "PERMISSION_DENIED";
  }
  return "UNKNOWN";
}

/**
 * Prefer the health snapshot's own target (it reports what was actually
 * probed); fall back to the persisted safe config when the snapshot omits it.
 */
function mergeSafeDiagnostics(
  pg: DbSafeDiagnostics | null | undefined,
  audit: DbDomainHealth,
): DbSafeDiagnostics {
  const server = typeof audit.server === "string" ? audit.server : "";
  const [host, portText] = server.split(":");
  const port = portText ? Number(portText) : NaN;
  return {
    provider: typeof audit.provider === "string" && audit.provider ? audit.provider : (pg?.provider ?? null),
    host: host && host !== "Local" ? host : (pg?.host ?? null),
    port: Number.isFinite(port) ? port : (pg?.port ?? null),
    database: typeof audit.database === "string" && audit.database ? audit.database : (pg?.database ?? null),
    username: pg?.username ?? null,
    password_configured: pg?.password_configured ?? null,
  };
}

/** The human word an operator reads for a classified state. */
export function dbHealthStateLabel(state: DbHealthState | string | undefined): string {
  switch (state) {
    case "AUTH_FAILED":
      return "Authentication Failed";
    case "UNREACHABLE":
      return "Unreachable";
    case "DATABASE_NOT_FOUND":
      return "Not Found";
    case "PERMISSION_DENIED":
      return "Permission Denied";
    default:
      return "Unavailable";
  }
}

/**
 * A short remediation DIRECTION — one sentence per condition, naming the
 * fixing surface. It points at the right knob; it never claims to know the
 * operator's value.
 */
export function dbHealthStateRemediation(state: DbHealthState | string | undefined): string {
  switch (state) {
    case "AUTH_FAILED":
      return "Rotate or re-enter the PostgreSQL credential in the secure store (Database → PostgreSQL config); the stored password is what the server rejected.";
    case "UNREACHABLE":
      return "Check that the PostgreSQL service is running and reachable at the configured host and port, then re-probe.";
    case "DATABASE_NOT_FOUND":
      return "The configured database does not exist on the server — create it or run the migration from the Database page.";
    case "PERMISSION_DENIED":
      return "The configured PostgreSQL role lacks permission for this data — grant the required privileges or switch to a role that holds them.";
    default:
      return "The database is unavailable for a reason the server did not classify — see the driver reason and the Database page.";
  }
}
