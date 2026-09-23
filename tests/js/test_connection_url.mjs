/**
 * Connection URL — client parse/build/mask matrix (contract §3.1).
 *
 * Pins the client-side mirror against the server-side shape: the accepted
 * scheme set {postgresql, postgres, pgsql}, every rejection reason, the
 * round-trip (parse → build), the password-exclusion rule on build, and the
 * display mask.  Runs with plain `node` (repo convention for tests/js).
 *
 * Run: node tests/js/test_connection_url.mjs
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const featureDir = join(here, "..", "..", "frontend", "src", "features", "database", "ui");

// Vite-style extensionless relative imports need a resolve hook under plain
// node; self-register so a bare `node`/`node --test` run works too.
// Windows: the specifier must be a file:// URL, not a bare C:\ path.
register(pathToFileURL(join(here, "_ts_ext_resolve.mjs")).href, pathToFileURL(here).href);

// Node strips types and runs the .ts directly.  Windows: dynamic import needs
// a file:// URL, not a bare C:\ path.  The module is pure (no `@/` runtime
// imports), so nothing else is required.
const mod = await import(pathToFileURL(join(featureDir, "connectionUrl.ts")).href);
const { parsePgUrl, buildPgUrl, maskUrl, isUrlParseFailure, PG_URL_SCHEMES } = mod;

const OK = "postgresql://nse_user@db.internal:6432/nse_audit";
const OK_SSL = "postgresql://nse_user@db.internal:6432/nse_audit?sslmode=require";

function parsed(raw) {
  const out = parsePgUrl(raw);
  assert.ok(!isUrlParseFailure(out), `expected a successful parse for ${raw}, got: ${out.reason}`);
  return out;
}

function refused(raw) {
  const out = parsePgUrl(raw);
  assert.ok(isUrlParseFailure(out), `expected ${raw} to be refused`);
  return out;
}

/* ---------- parse: the happy paths ---------- */

test("parse: postgres-family schemes are all accepted", () => {
  for (const scheme of ["postgresql", "postgres", "pgsql"]) {
    const cfg = parsed(`${scheme}://nse_user@db.internal:6432/nse_audit`);
    assert.equal(cfg.host, "db.internal");
    assert.equal(cfg.port, 6432);
    assert.equal(cfg.database, "nse_audit");
    assert.equal(cfg.username, "nse_user");
    assert.equal(cfg.ssl_mode, "");
    assert.equal(cfg.password, undefined, "no password was carried");
  }
});

test("parse: PG_URL_SCHEMES is exactly the postgres family", () => {
  assert.deepEqual([...PG_URL_SCHEMES].sort(), ["pgsql", "postgres", "postgresql"]);
});

test("parse: port defaults to 5432 when absent", () => {
  const cfg = parsed("postgresql://nse_user@db.internal/nse_audit");
  assert.equal(cfg.port, 5432);
});

test("parse: no userinfo is fine", () => {
  const cfg = parsed("postgresql://db.internal:5432/nse_audit");
  assert.equal(cfg.username, "");
  assert.equal(cfg.password, undefined);
});

test("parse: sslmode query is folded into ssl_mode", () => {
  const cfg = parsed("postgresql://nse_user@db.internal:5432/nse_audit?sslmode=require");
  assert.equal(cfg.ssl_mode, "require");
});

test("parse: ssl_mode query key is accepted too", () => {
  const cfg = parsed("postgresql://nse_user@db.internal:5432/nse_audit?ssl_mode=verify-full");
  assert.equal(cfg.ssl_mode, "verify-full");
});

test("parse: an unknown sslmode value passes through untouched", () => {
  const cfg = parsed("postgresql://nse_user@db.internal:5432/nse_audit?sslmode=custom-mode");
  assert.equal(cfg.ssl_mode, "custom-mode");
});

test("parse: an unknown query key is ignored", () => {
  const cfg = parsed("postgresql://nse_user@db.internal:5432/nse_audit?application_name=probe&sslmode=require");
  assert.equal(cfg.ssl_mode, "require");
  assert.equal(cfg.host, "db.internal");
});

test("parse: percent-encoded path and userinfo are decoded", () => {
  const cfg = parsed("postgresql://nse%5Fuser@db.internal:5432/db%20name?sslmode=require");
  assert.equal(cfg.username, "nse_user");
  assert.equal(cfg.database, "db name");
  assert.equal(cfg.ssl_mode, "require");
});

test("parse: username without a password", () => {
  const cfg = parsed("postgresql://nse_user@db.internal:5432/nse_audit");
  assert.equal(cfg.username, "nse_user");
  assert.equal(cfg.password, undefined);
});

test("parse: password is carried onto the client shape only", () => {
  const raw = "postgresql://nse_user:" + "s3cret" + "@db.internal:6432/nse_audit";
  const cfg = parsed(raw);
  assert.equal(cfg.host, "db.internal");
  assert.equal(cfg.username, "nse_user");
  assert.equal(cfg.password, "s3cret");
});

test("parse: an IPv6 host literal parses", () => {
  const cfg = parsed("postgresql://nse_user@[::1]:6432/nse_audit");
  assert.equal(cfg.host, "::1");
  assert.equal(cfg.port, 6432);
});

test("parse: an IPv6 host without a port keeps the default port", () => {
  const cfg = parsed("postgresql://nse_user@[fd00::7]/nse_audit");
  assert.equal(cfg.host, "fd00::7");
  assert.equal(cfg.port, 5432);
});

test("parse: never throws on hostile input", () => {
  for (const raw of ["", "   ", "not a url", "://", "postgres://", "postgresql://@", "postgresql://user@:5432/db", "%%%%%"]) {
    assert.ok(isUrlParseFailure(parsePgUrl(raw)), `expected ${JSON.stringify(raw)} to be refused, not thrown`);
  }
});

test("parse: a non-string input is refused, never thrown", () => {
  for (const bad of [null, undefined, 42, {}]) {
    assert.ok(isUrlParseFailure(parsePgUrl(bad)), `expected ${JSON.stringify(bad)} to be refused`);
  }
});

/* ---------- parse: the rejection rules (contract §3.1) ---------- */

test("parse: a scheme outside the postgres family is refused with the scheme named", () => {
  for (const raw of ["mysql://u@h:5432/db", "http://h:5432/db", "ftp://h:5432/db", "jdbc:postgresql://h/db"]) {
    const out = refused(raw);
    assert.match(out.reason, /must use the postgresql scheme/i);
  }
});

test("parse: an empty scheme is refused as (none)", () => {
  const out = refused("nse_user@db.internal");
  assert.equal(out.reason, "Connection URL must use the postgresql scheme (postgresql, postgres or pgsql); got '(none)'.");
});

test("parse: a scheme-only string names its scheme", () => {
  const out = refused("mysql:");
  assert.match(out.reason, /got 'mysql'\./);
});

test("parse: a missing host is refused", () => {
  assert.equal(refused("postgresql://:5432/nse_audit").reason, "Connection URL is missing its host.");
});

test("parse: a non-numeric port is refused", () => {
  assert.match(refused("postgresql://h:notaport/db").reason, /port must be a number between 1 and 65535/);
});

test("parse: a port below 1 is refused", () => {
  assert.match(refused("postgresql://h:0/db").reason, /port must be a number between 1 and 65535/);
});

test("parse: a port above 65535 is refused", () => {
  assert.match(refused("postgresql://h:65536/db").reason, /port must be a number between 1 and 65535/);
});

test("parse: an empty path (no database name) is refused", () => {
  assert.equal(refused("postgresql://h:5432/").reason, "Connection URL is missing its database name.");
});

test("parse: an authority with no path at all is refused as a missing database", () => {
  assert.equal(refused("postgresql://h:5432").reason, "Connection URL is missing its database name.");
});

test("parse: an empty URL is refused with a required sentence", () => {
  assert.equal(refused("").reason, "A connection URL is required.");
});

test("parse: a bare scheme with nothing after the separator is refused", () => {
  assert.equal(refused("postgresql://").reason, "Connection URL is missing its host and database name.");
});

test("parse: an unterminated IPv6 literal is refused", () => {
  assert.match(refused("postgresql://[::1:5432/db").reason, /unterminated IPv6 host literal/);
});

/* ---------- build ---------- */

test("build: is the inverse of parse for a full URL", () => {
  const cfg = parsed(OK_SSL);
  assert.equal(buildPgUrl(cfg), OK_SSL);
});

test("build: always emits the postgresql scheme", () => {
  for (const scheme of ["postgres", "pgsql"]) {
    const cfg = parsed(`${scheme}://nse_user@db.internal:6432/nse_audit`);
    assert.ok(buildPgUrl(cfg).startsWith("postgresql://"));
  }
});

test("build: NEVER writes the password into the emitted URL", () => {
  const cfg = parsed("postgresql://nse_user:" + "s3cret" + "@db.internal:6432/nse_audit");
  const out = buildPgUrl(cfg);
  assert.equal(out, "postgresql://nse_user@db.internal:6432/nse_audit");
  assert.ok(!out.includes("s3cret"), "buildPgUrl leaked the password");
  assert.ok(!out.includes("s3cret@"), "buildPgUrl leaked a credential");
});

test("build: a password on the input shape is dropped even when parse did not supply it", () => {
  const out = buildPgUrl({ host: "db.internal", port: 6432, database: "nse_audit", username: "nse_user", ssl_mode: "prefer", password: "leak" });
  assert.ok(!out.includes("leak"));
  assert.equal(out, "postgresql://nse_user@db.internal:6432/nse_audit?sslmode=prefer");
});

test("build: an empty username emits no userinfo", () => {
  const out = buildPgUrl({ host: "db.internal", port: 5432, database: "nse_audit", username: "", ssl_mode: "" });
  assert.equal(out, "postgresql://db.internal:5432/nse_audit");
});

test("build: an unset or invalid port falls back to 5432", () => {
  assert.equal(
    buildPgUrl({ host: "db.internal", port: 0, database: "nse_audit", username: "", ssl_mode: "" }),
    "postgresql://db.internal:5432/nse_audit",
  );
  assert.equal(
    buildPgUrl({ host: "db.internal", port: NaN, database: "nse_audit", username: "", ssl_mode: "" }),
    "postgresql://db.internal:5432/nse_audit",
  );
});

test("build: an IPv6 host is bracketed", () => {
  const out = buildPgUrl({ host: "::1", port: 6432, database: "nse_audit", username: "", ssl_mode: "" });
  assert.equal(out, "postgresql://[::1]:6432/nse_audit");
});

test("build: an already-bracketed host is not double-wrapped", () => {
  const out = buildPgUrl({ host: "[::1]", port: 6432, database: "nse_audit", username: "", ssl_mode: "" });
  assert.equal(out, "postgresql://[::1]:6432/nse_audit");
});

test("build: ssl_mode is normalized but never invented", () => {
  assert.equal(
    buildPgUrl({ host: "db.internal", port: 5432, database: "nse_audit", username: "", ssl_mode: "REQUIRE" }),
    "postgresql://db.internal:5432/nse_audit?sslmode=require",
  );
  assert.equal(
    buildPgUrl({ host: "db.internal", port: 5432, database: "nse_audit", username: "", ssl_mode: "" }),
    "postgresql://db.internal:5432/nse_audit",
  );
});

/* ---------- maskUrl ---------- */

test("maskUrl: replaces a password with ***", () => {
  assert.equal(maskUrl("postgresql://nse_user:***@db.internal:6432/nse_audit"), "postgresql://nse_user:***@db.internal:6432/nse_audit");
});

test("maskUrl: never includes the raw password", () => {
  const out = maskUrl("postgresql://nse_user:***@db.internal:6432/nse_audit");
  assert.ok(!out.includes("s3cret"));
});

test("maskUrl: leaves a URL without a password untouched", () => {
  assert.equal(maskUrl(OK_SSL), OK_SSL);
});

test("maskUrl: leaves a URL without credentials untouched", () => {
  const plain = "postgresql://db.internal:6432/nse_audit";
  assert.equal(maskUrl(plain), plain);
});

test("maskUrl: keeps the original scheme spelling", () => {
  assert.equal(maskUrl("pgsql://u:p@h:5432/db"), "pgsql://u:***@h:5432/db");
});

test("maskUrl: a non-string or schemeless string passes through untouched", () => {
  assert.equal(maskUrl("not a url"), "not a url");
  assert.equal(maskUrl(""), "");
});

test("maskUrl: never throws", () => {
  for (const raw of ["postgresql://host/db", "postgres://u@h/db", "://", "%%%%"]) {
    assert.doesNotThrow(() => maskUrl(raw));
  }
});

/* ---------- round-trip / parity with the server shape ---------- */

test("round-trip: parse → build → parse is stable", () => {
  for (const raw of [OK, "postgres://u@h:5432/db", "pgsql://h/db", "postgresql://u@[::1]:6432/db?sslmode=require"]) {
    const once = buildPgUrl(parsed(raw));
    const twice = buildPgUrl(parsed(once));
    assert.equal(once, twice);
  }
});

test("parity: the failure shape is exactly { reason }", () => {
  const out = parsePgUrl("mysql://h/db");
  assert.ok(isUrlParseFailure(out));
  assert.deepEqual(Object.keys(out).sort(), ["reason"]);
  assert.equal(typeof out.reason, "string");
  assert.ok(out.reason.length > 0);
  assert.ok(!out.reason.includes("at "), `reason must not be an exception trace: ${out.reason}`);
});

test("parity: the success shape never carries a password over the wire-shaped build", () => {
  const cfg = parsed("postgresql://nse_user:" + "s3cret" + "@db.internal:6432/nse_audit");
  assert.deepEqual(Object.keys(cfg).sort(), ["database", "host", "password", "port", "ssl_mode", "username"]);
  assert.ok(!buildPgUrl(cfg).includes("s3cret"));
});
