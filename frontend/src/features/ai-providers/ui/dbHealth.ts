/**
 * features/ai-providers/ui/dbHealth.ts — PostgreSQL health state for the
 * AI Providers console (wave §C1/§C2, brief §26).
 *
 * WHY A SEPARATE PROBE: the page's own GET /api/ai-providers call may surface
 * a DB infrastructure failure as an opaque 500/exception text (the legacy
 * route family is not path-guarded by the /api/v1 error handlers). When that
 * happens the operator must still be able to see WHICH database condition
 * they are looking at — auth failure, unreachable server, missing database,
 * denied permission — instead of a spinning page or a generic "request
 * failed". This module resolves that state from the ONE centralized health
 * surface the engine already exposes, so a page never hammers PostgreSQL
 * itself.
 *
 * TWO SOURCES, ONE VERDICT (both additive; neither is invented here):
 *
 *  1. The structured 503 DEGRADED body the API boundary returns for a known
 *     infrastructure failure (Lane B contract):
 *
 *       { status: "DEGRADED",
 *         database_state: "AUTH_FAILED" | "UNREACHABLE"
 *                       | "DATABASE_NOT_FOUND" | "PERMISSION_DENIED" | "UNKNOWN",
 *         provider_state: "unavailable",
 *         detail: "<safe driver reason>",
 *         safe_diagnostics: { provider, host, port, database, username,
 *                             password_configured: boolean } }
 *
 *     The middleware throws on a non-2xx, so this shape is read off the
 *     ApiError, never off a resolved body. Only `status === "DEGRADED"` is
 *     treated as a classified database state — any other failure stays a
 *     transport error and the page renders it as it does today.
 *
 *  2. The engine's existing centralized health snapshot
 *     (GET /api/db/manage/status — DatabaseHealthService.snapshot() +
 *     load_ui_config, never a new probe): `domains.audit` carries
 *     connected/health/error and the postgres block carries the safe
 *     connection fields. PREFERRED for the steady state — it is memoized
 *     server-side (failures never memoized), so the two pages that read it
 *     cost one shared probe, not one per page.
 *
 * SECRETS: the password is represented ONLY as `password_configured: boolean`
 * (contract law 3). This module never receives, holds or renders a credential
 * value; `safe_diagnostics` is forwarded verbatim and any unknown key in it is
 * ignored rather than dumped.
 *
 * HONESTY: every field below is optional on purpose. A field the backend did
 * not send renders as UNKNOWN/— at the call site, never a zero-filled
 * plausible value (brief: "Missing fields -> render the word UNKNOWN/—, never
 * zero-filled").
 */

import { getLegacy } from "@/api/client";
import { ApiError } from "@/types/api";

/** The backend's own classification tokens (legacy_errors.DatabaseState). */
export type DatabaseState =
  | "AUTH_FAILED"
  | "UNREACHABLE"
  | "DATABASE_NOT_FOUND"
  | "PERMISSION_DENIED"
  | "UNKNOWN";

/** The database_state values this UI knows how to label; anything else is
 *  reported verbatim as an UNKNOWN-class condition (never collapsed). */
const KNOWN_STATES: readonly DatabaseState[] = [
  "AUTH_FAILED",
  "UNREACHABLE",
  "DATABASE_NOT_FOUND",
  "PERMISSION_DENIED",
  "UNKNOWN",
];

export function isKnownDatabaseState(value: unknown): value is DatabaseState {
  return typeof value === "string" && (KNOWN_STATES as readonly string[]).includes(value);
}

/** Safe diagnostics — no credential material, ever. All fields optional. */
export interface DbSafeDiagnostics {
  provider?: string | null;
  host?: string | null;
  port?: number | null;
  database?: string | null;
  username?: string | null;
  password_configured?: boolean | null;
}

/** The structured DEGRADED body the API boundary returns (Lane B contract). */
export interface DbDegradedPayload {
  status: "DEGRADED";
  database_state: DatabaseState;
  provider_state?: string;
  detail?: string;
  safe_diagnostics?: DbSafeDiagnostics;
}

/**
 * Pull the structured DEGRADED payload off a thrown ApiError.
 *
 * Returns `null` for anything that is not a classified database degradation —
 * a 500, a network failure, a 401 and a thrown non-ApiError are all left
 * alone so the page's existing error handling keeps owning them (no-regression
 * rule). The middleware's `apiErrorFromResponse` already maps the body's
 * `detail` onto `ApiError.message`, so a DEGRADED 503 also reads sanely as a
 * plain error when this returns null.
 */
export function degradedPayloadOf(err: unknown): DbDegradedPayload | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status !== 503) return null;
  // The middleware consumed the body to build the error; re-read it off the
  // captured response when available, else fall back to the ApiError fields.
  const body = errorBody(err) as Partial<DbDegradedPayload> | null;
  const status = body?.status ?? (err.code === "DEGRADED" ? "DEGRADED" : null);
  if (status !== "DEGRADED") return null;
  const rawState = body?.database_state;
  // An unrecognised token is still a real classification from the server, but
  // not one this UI can label precisely — report it under the UNKNOWN banner
  // (verbatim token reaches the banner via the detail/driver reason) rather
  // than silently treating the payload as a transport error.
  const database_state = isKnownDatabaseState(rawState) ? rawState : "UNKNOWN";
  return {
    status: "DEGRADED",
    database_state,
    provider_state: typeof body?.provider_state === "string" ? body.provider_state : undefined,
    detail: typeof body?.detail === "string" ? body.detail : undefined,
    safe_diagnostics: shapeDiagnostics(body?.safe_diagnostics),
  };
}

/** Read the JSON body the middleware captured for this error, if any. */
function errorBody(err: ApiError): Record<string, unknown> | null {
  const holder = err as ApiError & { body?: unknown };
  if (holder.body && typeof holder.body === "object") {
    return holder.body as Record<string, unknown>;
  }
  return null;
}

/** Keep only the documented safe fields; drop anything else without dumping. */
function shapeDiagnostics(value: unknown): DbSafeDiagnostics | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;
  const port = raw["port"];
  return {
    provider: typeof raw["provider"] === "string" ? raw["provider"] : null,
    host: typeof raw["host"] === "string" ? raw["host"] : null,
    port: typeof port === "number" ? port : null,
    database: typeof raw["database"] === "string" ? raw["database"] : null,
    username: typeof raw["username"] === "string" ? raw["username"] : null,
    password_configured: typeof raw["password_configured"] === "boolean" ? raw["password_configured"] : null,
  };
}

/* ---------------------------------------------------------------------------
 * Centralized health snapshot (GET /api/db/manage/status)
 *
 * The engine's existing health surface. `domains.<name>` is the
 * DatabaseHealthService per-domain snapshot (never raises; a failed probe
 * reports `connected:false` + the driver's own reason in `error`). The
 * `postgres` block is `load_ui_config()`'s safe connection view. This module
 * types only the fields it renders — the endpoint returns more, and every
 * extra key stays accessible to the Database page that owns it.
 * ------------------------------------------------------------------------- */

/** One domain snapshot from DatabaseHealthService.check_domain(). */
export interface DbDomainSnapshot {
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
  /** The driver's own connection reason (HEALTH-DBREASON); verbatim or "". */
  error?: string;
}

/** GET /api/db/manage/status — the fields this feature reads. */
export interface DbManageStatusPayload {
  success?: boolean;
  provider?: string;
  overall?: string;
  domains?: Record<string, DbDomainSnapshot>;
  postgres?: DbSafeDiagnostics | null;
  password_set?: boolean;
  /** Backend-computed guidance for a broken provider state. */
  hints?: string[];
}

/** Endpoint to read (defined once; the Database feature uses the same one). */
const MANAGE_STATUS_PATH = "/api/db/manage/status";

/** Fetch the centralized DB health snapshot (the page's single probe). */
export async function fetchDbManageStatus(signal?: AbortSignal): Promise<DbManageStatusPayload> {
  return getLegacy<DbManageStatusPayload>(MANAGE_STATUS_PATH, signal);
}

/**
 * The rendered health verdict, derived ONLY from payload fields.
 *
 * - "HEALTHY"     — the audit domain reports connected (no banner; the page
 *                   renders exactly as it does today).
 * - "DEGRADED"    — a classified database state (from a 503 body or from a
 *                   domain snapshot that reports not connected).
 * - "LOADING"     — no payload and no error yet (honest empty state).
 * - "UNKNOWN"     — the snapshot exists but does not carry the fields this
 *                   UI needs to state anything; rendered as UNKNOWN, never
 *                   as healthy and never as an error.
 */
export type DbHealthPhase = "LOADING" | "HEALTHY" | "DEGRADED" | "UNKNOWN";

/** The shape the banner component consumes. */
export interface DbHealthState {
  phase: DbHealthPhase;
  /** Verbatim classification token when phase is DEGRADED. */
  database_state?: DatabaseState;
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
 * A snapshot that cannot answer (success false, no domains, no audit entry)
 * is UNKNOWN rather than DEGRADED: this UI must not claim a database failure
 * the health service did not report.
 */
export function healthFromManageStatus(payload: DbManageStatusPayload | null | undefined): DbHealthState {
  if (!payload) return { phase: "LOADING" };
  const audit = payload.domains?.["audit"];
  if (audit && typeof audit === "object") {
    if (audit.connected === true) return { phase: "HEALTHY" };
    if (audit.connected === false) {
      return {
        phase: "DEGRADED",
        // The health service reports a driver reason, not a classification —
        // classify it conservatively so the banner names the condition.
        database_state: classifyDomainError(audit.error, audit.status, payload.provider),
        diagnostics: mergeDiagnostics(payload.postgres, audit),
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
 * banner names the same condition the API boundary would. Unknown reasons fall
 * back to the UNKNOWN token rather than being forced into a wrong class — the
 * driver reason is still rendered verbatim alongside the label.
 */
export function classifyDomainError(
  reason: string | undefined,
  status: string | undefined,
  provider: string | null | undefined,
): DatabaseState {
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
function mergeDiagnostics(
  pg: DbSafeDiagnostics | null | undefined,
  audit: DbDomainSnapshot,
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

/** The human label for a classified state (the word an operator reads). */
export function databaseStateLabel(state: DatabaseState | string | undefined): string {
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
 * fixing surface (the secure store / the service / provisioning / grants).
 * Deliberately generic: it points at the right knob, it never claims to know
 * the operator's value.
 */
export function databaseStateRemediation(state: DatabaseState | string | undefined): string {
  switch (state) {
    case "AUTH_FAILED":
      return "Rotate or re-enter the PostgreSQL credential in the secure store (Database → PostgreSQL config); the stored password is what the server rejected.";
    case "UNREACHABLE":
      return "Check that the PostgreSQL service is running and reachable at the configured host and port, then let this page re-probe.";
    case "DATABASE_NOT_FOUND":
      return "The configured database does not exist on the server — create it or run the migration from the Database page.";
    case "PERMISSION_DENIED":
      return "The configured PostgreSQL role lacks permission for this data — grant the required privileges or switch to a role that holds them.";
    default:
      return "The database is unavailable for a reason the server did not classify — see the driver reason and the Database page.";
  }
}
