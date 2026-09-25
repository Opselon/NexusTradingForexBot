/**
 * Pure client-side mirror of the server connection-URL helpers (contract §3.1).
 *
 * OWNER: lane B (db-provider-pro) — this lane owns future edits.
 * CONSUMES: nothing at runtime (pure, no React, no fetch); the server authority
 *   is `src/nexus_scalp/database/connection_url.py` — mirrored BY CONTRACT,
 *   never imported (cross-lane dependency rule, contract §2.1).
 * PROVIDES: `parsePgUrl` / `buildPgUrl` / `maskUrl` + the `ParsedPgConfig` and
 *   `UrlParseFailure` shapes the connection panel and the URL field consume.
 * INVARIANTS:
 *   - the accepted scheme set is EXACTLY {postgresql, postgres, pgsql} — the
 *     same set `DatabaseProvider.parse` accepts;
 *   - NEVER throws at the UI boundary: a malformed URL returns a human
 *     sentence (`UrlParseFailure.reason`), so a bad string never reaches the
 *     backend and an exception text (which may embed credentials) is never
 *     surfaced;
 *   - `buildPgUrl` NEVER writes a password into the emitted URL — it is a
 *     round-trip of the discrete fields only, safe to display and to log;
 *   - `maskUrl` replaces any password with `***` (display echo only).
 *   - This module is unit-tested by `tests/js/test_connection_url.mjs`, so it
 *     must stay free of non-erased imports (node runs it directly with types
 *     stripped; no `@/` alias may resolve at runtime).
 * EXTEND: add a new parsed field by teaching `applyQueryArgs` and `buildPgUrl`
 *   about it, and mirror the change on the server side.
 */

export type ParsedPgConfig = {
  host: string;
  port: number;
  database: string;
  username: string;
  ssl_mode: string;
  /** Present ONLY when the URL carried it — the server-side echo excludes it
   *  (contract §3.2); the client keeps it transiently to populate the form's
   *  password field, and `buildPgUrl` never writes it back. */
  password?: string;
};

export type UrlParseFailure = {
  /** A human sentence; never an exception/traceback text. */
  reason: string;
};

/**
 * Schemes accepted on both sides of the wire (contract §3.1).
 * Mirrors `DatabaseProvider.parse`'s postgres-family set exactly.
 */
export const PG_URL_SCHEMES: ReadonlySet<string> = new Set(["postgresql", "postgres", "pgsql"]);

/** Query-string keys understood by the parser (lower-cased on read). */
const SSL_MODE_KEYS: ReadonlySet<string> = new Set(["sslmode", "ssl_mode"]);

/**
 * SQL-standard SSL mode values — used ONLY to normalize the case of a value
 * the operator already typed, never to invent one.
 */
const SSL_MODES: ReadonlySet<string> = new Set([
  "disable",
  "allow",
  "prefer",
  "require",
  "verify-ca",
  "verify-full",
]);

const DEFAULT_PG_PORT = 5432;

/** True when `parsePgUrl` refuses `raw` (never throws). */
export function isUrlParseFailure(v: ParsedPgConfig | UrlParseFailure): v is UrlParseFailure {
  return typeof (v as UrlParseFailure).reason === "string";
}

function fail(reason: string): UrlParseFailure {
  return { reason };
}

function failBadScheme(scheme: string): UrlParseFailure {
  const pretty = scheme.length > 0 ? scheme : "(none)";
  return fail(
    `Connection URL must use the postgresql scheme (postgresql, postgres or pgsql); got '${pretty}'.`,
  );
}

/** `port` as an int, or null when it is not a usable 1..65535 value. */
function parsePort(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined) return null;
  const text = raw.trim();
  if (text.length === 0) return null;
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  if (value < 1 || value > 65535) return null;
  return value;
}

function normalizeSslMode(value: string): string {
  const text = (value ?? "").trim();
  if (text.length === 0) return "";
  const lowered = text.toLowerCase();
  return SSL_MODES.has(lowered) ? lowered : text;
}

/** Fold the query string's sslmode into `fields` (in place). */
function applyQueryArgs(fields: Record<string, string>, query: string): void {
  if (query.length === 0) return;
  for (const pair of query.split("&")) {
    if (pair.length === 0) continue;
    const sep = pair.indexOf("=");
    const key = sep < 0 ? pair : pair.slice(0, sep);
    const value = sep < 0 ? "" : pair.slice(sep + 1);
    const name = decode(key).trim().toLowerCase();
    if (SSL_MODE_KEYS.has(name)) {
      fields.ssl_mode = decode(value).trim();
    }
  }
}

/** Percent-decode, tolerating malformed escapes (never throws). */
function decode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

export function parsePgUrl(raw: string): ParsedPgConfig | UrlParseFailure {
  if (typeof raw !== "string" || raw.trim().length === 0) {
    return fail("A connection URL is required.");
  }

  const text = raw.trim();
  if (!text.includes("://")) {
    const scheme = text.includes(":") ? (text.split(":", 1)[0] ?? "") : "";
    return failBadScheme(scheme);
  }

  const sep = text.indexOf("://");
  const scheme = text.slice(0, sep).trim().toLowerCase();
  const rest = text.slice(sep + 3);
  if (!PG_URL_SCHEMES.has(scheme)) return failBadScheme(scheme);
  if (rest.length === 0) {
    return fail("Connection URL is missing its host and database name.");
  }

  // Split off the query string before touching credentials/host.
  const q = rest.indexOf("?");
  const authorityPath = q < 0 ? rest : rest.slice(0, q);
  const query = q < 0 ? "" : rest.slice(q + 1);

  // rsplit semantics: an IPv6 host contains ':' but the userinfo '@' is the
  // real seam between credentials and host.
  const at = authorityPath.lastIndexOf("@");
  const userinfo = at < 0 ? "" : authorityPath.slice(0, at);
  const hostportPath = at < 0 ? authorityPath : authorityPath.slice(at + 1);

  const usernameRaw = userinfo.includes(":") ? userinfo.slice(0, userinfo.indexOf(":")) : userinfo;
  const passwordRaw = userinfo.includes(":") ? userinfo.slice(userinfo.indexOf(":") + 1) : "";
  const username = userinfo.length > 0 ? decode(usernameRaw).trim() : "";
  const password = passwordRaw.length > 0 ? decode(passwordRaw) : "";

  const slash = hostportPath.indexOf("/");
  const hostport = slash < 0 ? hostportPath : hostportPath.slice(0, slash);
  const path = slash < 0 ? "" : hostportPath.slice(slash);

  let host = "";
  let port = DEFAULT_PG_PORT;
  if (hostport.startsWith("[")) {
    // IPv6 literal: [::1]:5432
    const close = hostport.indexOf("]");
    if (close < 0) return fail("Connection URL has an unterminated IPv6 host literal.");
    host = hostport.slice(1, close);
    const tail = hostport.slice(close + 1);
    if (tail.length > 0) {
      const parsed = parsePort(tail.replace(/^:/, ""));
      if (parsed === null) return fail("Connection URL port must be a number between 1 and 65535.");
      port = parsed;
    }
  } else if (hostport.includes(":")) {
    const colon = hostport.indexOf(":");
    host = hostport.slice(0, colon).trim();
    const parsed = parsePort(hostport.slice(colon + 1));
    if (parsed === null) return fail("Connection URL port must be a number between 1 and 65535.");
    port = parsed;
  } else {
    host = hostport.trim();
  }

  if (host.length === 0) return fail("Connection URL is missing its host.");

  const database = decode(path.replace(/^\/+/, "")).trim();
  if (database.length === 0) return fail("Connection URL is missing its database name.");

  const fields: Record<string, string> = {
    host,
    port: String(port),
    database,
    username,
    ssl_mode: "",
  };
  applyQueryArgs(fields, query);
  const sslMode = normalizeSslMode(fields.ssl_mode ?? "");

  const cfg: ParsedPgConfig = { host, port, database, username, ssl_mode: sslMode };
  if (password.length > 0) cfg.password = password;
  return cfg;
}

export function buildPgUrl(cfg: ParsedPgConfig): string {
  const host = String(cfg.host ?? "");
  let hostPart = host;
  if (hostPart.includes(":") && !hostPart.startsWith("[")) hostPart = `[${hostPart}]`;
  const username = String(cfg.username ?? "");
  const userPart = username.length > 0 ? `${username}@` : "";
  const port = Number(cfg.port) > 0 ? Number(cfg.port) : DEFAULT_PG_PORT;
  const database = String(cfg.database ?? "");
  const sslMode = normalizeSslMode(String(cfg.ssl_mode ?? ""));
  const url = `postgresql://${userPart}${hostPart}:${port}/${database}`;
  return sslMode.length > 0 ? `${url}?sslmode=${sslMode}` : url;
}

export function maskUrl(raw: string): string {
  if (typeof raw !== "string" || !raw.includes("://")) return raw;
  const sep = raw.indexOf("://");
  const scheme = raw.slice(0, sep);
  const rest = raw.slice(sep + 3);
  const at = rest.lastIndexOf("@");
  if (at < 0) return raw;
  const creds = rest.slice(0, at);
  const tail = rest.slice(at + 1);
  const colon = creds.indexOf(":");
  if (colon < 0) return raw;
  const user = creds.slice(0, colon);
  return `${scheme}://${user}:***@${tail}`;
}
