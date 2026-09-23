/**
 * switchReadiness derivation matrix — readiness-gated provider switch (Lane C).
 *
 * Run:  node tests/js/test_switch_readiness.mjs
 *   or: node --test tests/js/test_switch_readiness.mjs
 *
 * Imports the REAL frontend module frontend/src/features/database/uiLogic.ts
 * (node 24 strips the TS types; the only non-erasable import in that file is
 * `import type`, removed before resolution, so the `@/` alias never has to
 * resolve here). Same convention as database_console.test.js.
 *
 * What these tests pin:
 *  - canSwitch is EXACTLY "no BLOCK item" — the frontend computes no verdict
 *    of its own, and it never turns a missing input into a pass.
 *  - the only BLOCKs are the backend's own negative signals: driver missing
 *    while the target is postgresql, password_set false while the target is
 *    postgresql, a failed connection probe, and an absent switch target.
 *  - the migration WARN is always present unless the backend itself said
 *    provider_switch_ready, and it forces requiresAcknowledgement.
 *  - backend words are restated verbatim in the checklist labels.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { switchReadiness } from "../../frontend/src/features/database/uiLogic.ts";

/** The happy path: everything the backend reported was positive. */
const GREEN_MANAGE = {
  success: true,
  provider: "postgresql",
  supported_providers: ["sqlite", "postgresql"],
  overall: "Healthy",
  domains: { audit: { connected: true } },
  postgres: { host: "localhost", port: 5432, database: "nse_audit", username: "nse_user" },
  password_set: true,
  postgresql_driver_available: true,
  hints: [],
};

const GREEN_REPORT = {
  status: "COMPLETE",
  source: "sqlite",
  destination: "postgresql",
  tables_migrated: 12,
  rows_migrated: 1000,
  rows_failed: 0,
  duration_ms: 1234.5,
  validation: "PASSED",
  provider_switch_ready: true,
};

const states = (r) => r.checklist.map((i) => [i.key, i.state]);
const item = (r, key) => r.checklist.find((i) => i.key === key);

test("green path: no BLOCK, no acknowledgement required, all checklist states PASS", () => {
  const r = switchReadiness(GREEN_MANAGE, GREEN_REPORT, { connected: true });
  assert.equal(r.canSwitch, true);
  assert.equal(r.requiresAcknowledgement, false);
  assert.deepEqual(
    states(r),
    [
      ["target-provider", "INFO"],
      ["pg-driver", "PASS"],
      ["pg-password", "PASS"],
      ["connection-test", "PASS"],
      ["migration-validated", "PASS"],
      ["domain-health", "INFO"],
    ],
    "every input was a positive backend signal — no WARN, no BLOCK",
  );
});

test("driver missing while targeting postgresql is the only BLOCK", () => {
  const r = switchReadiness(
    { ...GREEN_MANAGE, postgresql_driver_available: false },
    GREEN_REPORT,
    { connected: true },
  );
  assert.equal(r.canSwitch, false, "a missing driver must stop the switch");
  assert.equal(r.requiresAcknowledgement, false);
  assert.deepEqual(item(r, "pg-driver").state, "BLOCK");
  assert.deepEqual(
    states(r),
    [
      ["target-provider", "INFO"],
      ["pg-driver", "BLOCK"],
      ["pg-password", "PASS"],
      ["connection-test", "PASS"],
      ["migration-validated", "PASS"],
      ["domain-health", "INFO"],
    ],
  );
});

test("password_set false while targeting postgresql is a BLOCK", () => {
  const r = switchReadiness({ ...GREEN_MANAGE, password_set: false }, GREEN_REPORT, {
    connected: true,
  });
  assert.equal(r.canSwitch, false);
  assert.deepEqual(item(r, "pg-password").state, "BLOCK");
  assert.ok(
    item(r, "pg-password").label.includes("password is missing"),
    "the label must say what the backend said",
  );
});

test("a failed connection probe is a BLOCK (the probe said no)", () => {
  const r = switchReadiness(GREEN_MANAGE, GREEN_REPORT, { connected: false });
  assert.equal(r.canSwitch, false);
  assert.deepEqual(item(r, "connection-test").state, "BLOCK");
});

test("no connection probe yet is an INFO, never a pass and never a silent block", () => {
  const r = switchReadiness(GREEN_MANAGE, GREEN_REPORT, null);
  assert.equal(r.canSwitch, true, "an unrun probe blocks nothing on its own");
  assert.deepEqual(item(r, "connection-test").state, "INFO");
  assert.equal(r.requiresAcknowledgement, false);
});

test("provider_switch_ready false -> WARN + requiresAcknowledgement, switch still allowed", () => {
  const r = switchReadiness(GREEN_MANAGE, { ...GREEN_REPORT, provider_switch_ready: false }, {
    connected: true,
  });
  assert.equal(r.canSwitch, true, "the operator may insist — the UI just will not be silent");
  assert.equal(r.requiresAcknowledgement, true);
  assert.deepEqual(item(r, "migration-validated").state, "WARN");
  assert.ok(
    item(r, "migration-validated").label.includes("not validated"),
    "the WARN restates the backend's own words",
  );
  assert.ok(item(r, "migration-validated").label.includes("COMPLETE"));
});

test("provider_switch_ready absent (no report yet) is the WARN that demands acknowledgement", () => {
  const r = switchReadiness(GREEN_MANAGE, null, { connected: true });
  assert.equal(r.canSwitch, true);
  assert.equal(r.requiresAcknowledgement, true);
  assert.deepEqual(item(r, "migration-validated").state, "WARN");
  assert.ok(
    item(r, "migration-validated").label.includes("UNAVAILABLE"),
    "missing data is named, never zero-filled into a pass",
  );
});

test("a FAILED migration report is a WARN (still requires acknowledgement), not a BLOCK", () => {
  const r = switchReadiness(
    GREEN_MANAGE,
    { status: "FAILED", validation: "FAILED", provider_switch_ready: false, errors: ["boom"] },
    { connected: true },
  );
  assert.equal(r.canSwitch, true);
  assert.equal(r.requiresAcknowledgement, true);
  assert.deepEqual(item(r, "migration-validated").state, "WARN");
  assert.ok(item(r, "migration-validated").label.includes("FAILED"));
});

test("switching to sqlite never BLOCKs on postgres-only negatives (driver/password are INFO)", () => {
  const r = switchReadiness(
    {
      ...GREEN_MANAGE,
      provider: "sqlite",
      password_set: false,
      postgresql_driver_available: false,
    },
    null,
    null,
  );
  assert.equal(r.canSwitch, true, "back to sqlite needs no postgres prerequisites");
  assert.deepEqual(
    states(r),
    [
      ["target-provider", "INFO"],
      ["pg-driver", "INFO"],
      ["pg-password", "INFO"],
      ["connection-test", "INFO"],
      ["migration-validated", "WARN"],
      ["domain-health", "INFO"],
    ],
    "postgres-only negatives downgrade to INFO when postgres is not the target",
  );
  assert.equal(r.requiresAcknowledgement, true);
});

test("no manage payload at all: UNAVAILABLE target is a BLOCK, nothing is invented", () => {
  const r = switchReadiness(null, null, null);
  assert.equal(r.canSwitch, false, "without a reported provider there is no switch to greenlight");
  assert.equal(r.requiresAcknowledgement, true);
  assert.deepEqual(
    states(r),
    [
      ["target-provider", "BLOCK"],
      ["pg-driver", "INFO"],
      ["pg-password", "INFO"],
      ["connection-test", "INFO"],
      ["migration-validated", "WARN"],
      ["domain-health", "INFO"],
    ],
  );
  assert.ok(item(r, "target-provider").label.includes("UNAVAILABLE"));
  assert.ok(item(r, "domain-health").label.includes("UNAVAILABLE"));
});

test("an older backend that does not report driver presence stays INFO, never claims installed", () => {
  const older = { ...GREEN_MANAGE };
  delete older.postgresql_driver_available;
  const r = switchReadiness(older, GREEN_REPORT, { connected: true });
  assert.equal(r.canSwitch, true, "tri-state: unreported is neither installed nor a blocker");
  assert.deepEqual(item(r, "pg-driver").state, "INFO");
  assert.ok(item(r, "pg-driver").label.includes("UNAVAILABLE"));
});

test("an older backend that does not report password_set stays INFO, never claims stored", () => {
  const older = { ...GREEN_MANAGE };
  delete older.password_set;
  const r = switchReadiness(older, GREEN_REPORT, { connected: true });
  assert.equal(r.canSwitch, true);
  assert.deepEqual(item(r, "pg-password").state, "INFO");
  assert.ok(item(r, "pg-password").label.includes("UNAVAILABLE"));
});

test("domain-health is always INFO: the backend word is context, never a client verdict", () => {
  for (const overall of ["Healthy", "Warning", "Error", "DISCONNECTED"]) {
    const r = switchReadiness({ ...GREEN_MANAGE, overall }, GREEN_REPORT, { connected: true });
    assert.deepEqual(item(r, "domain-health").state, "INFO");
    assert.ok(item(r, "domain-health").label.includes(overall), "restate the backend word verbatim");
  }
});

test("the checklist restate backend words only — no client-invented verdict words", () => {
  const labels = switchReadiness(GREEN_MANAGE, GREEN_REPORT, { connected: true })
    .checklist.map((i) => i.label)
    .join(" ");
  for (const word of ["postgresql", "COMPLETE", "PASSED", "Healthy"]) {
    assert.ok(labels.includes(word), `backend word ${word} must appear verbatim`);
  }
  // the client must not mint its own health verdict on top of the payload
  assert.ok(!/"DEGRADED"|"UNHEALTHY"/.test(labels));
});

test("multiple BLOCKs are all reported and canSwitch stays false until every one clears", () => {
  const r = switchReadiness(
    { ...GREEN_MANAGE, postgresql_driver_available: false, password_set: false },
    null,
    { connected: false },
  );
  assert.equal(r.canSwitch, false);
  const blocks = r.checklist.filter((i) => i.state === "BLOCK").map((i) => i.key);
  assert.deepEqual(blocks, ["pg-driver", "pg-password", "connection-test"]);
  assert.equal(r.requiresAcknowledgement, true);
  // clearing two of three still leaves the switch blocked
  const r2 = switchReadiness(
    { ...GREEN_MANAGE, password_set: true, postgresql_driver_available: true },
    null,
    { connected: false },
  );
  assert.equal(r2.canSwitch, false);
  assert.deepEqual(
    r2.checklist.filter((i) => i.state === "BLOCK").map((i) => i.key),
    ["connection-test"],
  );
});

test("provider casing does not change the derivation (postgresql is the only matched target)", () => {
  const r = switchReadiness(
    { ...GREEN_MANAGE, provider: "PostgreSQL" },
    { ...GREEN_REPORT, provider_switch_ready: false },
    { connected: true },
  );
  assert.deepEqual(item(r, "pg-driver").state, "PASS");
  assert.deepEqual(item(r, "pg-password").state, "PASS");
  assert.deepEqual(item(r, "migration-validated").state, "WARN");
});

test("canSwitch is a function of the checklist, not a second verdict", () => {
  const cases = [
    [GREEN_MANAGE, GREEN_REPORT, { connected: true }],
    [{ ...GREEN_MANAGE, postgresql_driver_available: false }, GREEN_REPORT, { connected: true }],
    [{ ...GREEN_MANAGE, password_set: false }, GREEN_REPORT, { connected: true }],
    [GREEN_MANAGE, GREEN_REPORT, { connected: false }],
    [{ ...GREEN_MANAGE, provider: "sqlite" }, null, null],
  ];
  for (const [m, rep, tr] of cases) {
    const r = switchReadiness(m, rep, tr);
    assert.equal(
      r.canSwitch,
      !r.checklist.some((i) => i.state === "BLOCK"),
      "canSwitch must be exactly 'no BLOCK item'",
    );
    assert.equal(
      r.requiresAcknowledgement,
      r.checklist.some((i) => i.state === "WARN"),
      "requiresAcknowledgement must be exactly 'a WARN item exists'",
    );
  }
});
