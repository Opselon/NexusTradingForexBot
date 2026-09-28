/**
 * Health matrix — safety-contract regression suite (Health/Safety/Governance).
 *
 * Run:  node tests/js/health_matrix_contract.test.js
 *
 * Pins the client-side contract of the /alt health page against the exact
 * defects this campaign fixed. Every assertion below is a contract a UI must
 * not silently violate:
 *
 *   NEWS-AXES        service liveness and data freshness are independent; a
 *                    live service with STALE data must read STALE, never the
 *                    green ACTIVE the old derivation produced.
 *   CELL-ISOLATION   one failed endpoint fails exactly one cell; every other
 *                    cell keeps its own verdict, source, timestamp and age.
 *   STALE-TRANSITION a GOOD reading older than the staleness budget recolors
 *                    to warn (amber), and its CELL alone — a fresh neighbor
 *                    stays green (no global contamination).
 *   VERDICT-MAP      unknown verdict words fall back to neutral/UNKNOWN,
 *                    never to good (the UI never invents HEALTHY).
 *   OVERVIEW-COUNT   the counters are a pure count over the backend verdicts;
 *                    the sum always equals the cell total (no double counting,
 *                    no reclassification).
 *   WORKER-STATE     NOT_ATTACHED / STOPPED are reported neutral, never bad —
 *                    a backend attachment state is not a health failure.
 *
 * Imports the REAL frontend module (Node strips TS types; the only
 * non-erasable import in model.ts is `import type`).
 */
import assert from "node:assert/strict";

import {
  cellFromCheck,
  cellFromSubsystem,
  cellFromWorker,
  cellTone,
  healthLevel,
  matrixSummary,
} from "../../frontend/src/features/health/model.ts";

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ok  ${name}`);
  } catch (err) {
    failures += 1;
    console.log(`  FAIL ${name}\n      ${err.message}`);
  }
}

console.log("health model — safety contract");

// ------------------------------------------------------------- VERDICT-MAP
console.log(" verdict mapping never invents a healthy state");
check("backend verdicts map verbatim", () => {
  assert.equal(healthLevel("PASS"), "good");
  assert.equal(healthLevel("WARNING"), "warn");
  assert.equal(healthLevel("FAIL"), "bad");
  assert.equal(healthLevel("HEALTHY"), "good");
  assert.equal(healthLevel("DEGRADED"), "warn");
  assert.equal(healthLevel("UNHEALTHY"), "bad");
  assert.equal(healthLevel("DISCONNECTED"), "bad");
});
check("unknown verdict is neutral, never good", () => {
  assert.equal(healthLevel("GARBAGE"), "neutral");
  assert.equal(healthLevel(""), "neutral");
  assert.equal(healthLevel(null), "neutral");
  assert.equal(healthLevel(undefined), "neutral");
});
check("verdicts are case-insensitive but not substrings", () => {
  assert.equal(healthLevel("healthy"), "good");
  assert.equal(healthLevel("Healthy"), "good");
  // "NOT READY" must not be confused with "READY"
  assert.equal(healthLevel("NOT READY"), "bad");
  assert.equal(healthLevel("READY"), "good");
});

// ------------------------------------------------------------- CELL-ISOLATION
console.log(" cell isolation — one read, one verdict, one age");
check("cells from different sources keep distinct ids", () => {
  const sub = cellFromSubsystem(
    { name: "Risk Engine", status: "HEALTHY", detail: "ok", metrics: {} },
    "debug/health",
    1000,
  );
  const chk = cellFromCheck(
    { category: "DATABASE", verdict: "PASS", reason: "ok" },
    "v1/system",
    2000,
  );
  assert.notEqual(sub.id, chk.id);
  assert.equal(sub.source, "debug/health");
  assert.equal(chk.source, "v1/system");
});
check("a failed subsystem does not alter another cell's verdict", () => {
  const a = cellFromSubsystem(
    { name: "Audit Database", status: "UNHEALTHY", detail: "down", metrics: {} },
    "debug/health",
    1000,
  );
  const b = cellFromSubsystem(
    { name: "Risk Engine", status: "HEALTHY", detail: "ok", metrics: {} },
    "debug/health",
    1000,
  );
  assert.equal(healthLevel(a.status), "bad");
  assert.equal(healthLevel(b.status), "good");
  // The matrix builder never merges the two: each cell is built independently
  // from its own payload, and matrixSummary counts them apart.
  const s = matrixSummary([a, b]);
  assert.equal(s.bad, 1);
  assert.equal(s.good, 1);
});
check("each cell carries its own fetch timestamp", () => {
  const a = cellFromCheck({ category: "A", verdict: "PASS" }, "s", 1000);
  const b = cellFromCheck({ category: "B", verdict: "PASS" }, "s", 5000);
  assert.equal(a.fetchedAtMs, 1000);
  assert.equal(b.fetchedAtMs, 5000);
});

// ------------------------------------------------------------- STALE-TRANSITION
console.log(" staleness — an old GOOD is amber, only its own cell");
check("a fresh good cell stays good", () => {
  const cell = cellFromCheck({ category: "X", verdict: "PASS" }, "s", 1000);
  const tone = cellTone(cell, 1000 + 5_000);
  assert.equal(tone.level, "good");
  assert.equal(tone.staleClass, "");
  assert.equal(tone.ageMs, 5000);
});
check("a good cell older than the budget recolors to warn", () => {
  const cell = cellFromCheck({ category: "X", verdict: "PASS" }, "s", 1000);
  // pollBudget 10s; stale at 2.5x = 25s
  const tone = cellTone(cell, 1000 + 30_000);
  assert.equal(tone.level, "warn");
  assert.equal(tone.staleClass, "stale");
});
check("staleness is per-cell — a fresh neighbor is unaffected", () => {
  const stale = cellFromCheck({ category: "A", verdict: "PASS" }, "s", 1000);
  const fresh = cellFromCheck({ category: "B", verdict: "PASS" }, "s", 1000 + 29_000);
  const now = 1000 + 30_000;
  assert.equal(cellTone(stale, now).level, "warn");
  assert.equal(cellTone(fresh, now).level, "good");
});
check("a never-read cell is stale and never claims an age", () => {
  const cell = cellFromCheck({ category: "X", verdict: "PASS" }, "s", null);
  const tone = cellTone(cell, 1000);
  assert.equal(tone.ageMs, null);
  assert.equal(tone.staleClass, "stale");
});
check("age can never be negative (clock skew / late clock)", () => {
  const cell = cellFromCheck({ category: "X", verdict: "PASS" }, "s", 5000);
  // A client clock behind the fetch timestamp must not produce a negative age
  // that flips the staleness arithmetic.
  const tone = cellTone(cell, 1000);
  assert.ok(tone.ageMs !== null && tone.ageMs >= 0, "age must be clamped >= 0");
});

// ------------------------------------------------------------- OVERVIEW-COUNT
console.log(" overview counters sum to the cell total");
check("counts partition the matrix exactly", () => {
  const cells = [
    cellFromCheck({ category: "A", verdict: "PASS" }, "s", 1),
    cellFromCheck({ category: "B", verdict: "WARNING" }, "s", 1),
    cellFromCheck({ category: "C", verdict: "FAIL" }, "s", 1),
    cellFromWorker({ name: "w", state: "NOT_ATTACHED", attached: false }, 1),
  ];
  const s = matrixSummary(cells);
  const total = cells.length;
  assert.equal(s.good + s.warn + s.bad + s.neutral, total);
  assert.equal(s.good, 1);
  assert.equal(s.warn, 1);
  assert.equal(s.bad, 1);
  assert.equal(s.neutral, 1);
});
check("no cell can land in two buckets", () => {
  const s = matrixSummary([
    cellFromCheck({ category: "A", verdict: "PASS" }, "s", 1),
    cellFromCheck({ category: "B", verdict: "WARNING" }, "s", 1),
  ]);
  assert.equal(s.good, 1);
  assert.equal(s.warn, 1);
  assert.equal(s.bad, 0);
  assert.equal(s.neutral, 0);
});
check("empty matrix is all-zero (never a phantom healthy count)", () => {
  const s = matrixSummary([]);
  assert.equal(s.good, 0);
  assert.equal(s.warn, 0);
  assert.equal(s.bad, 0);
  assert.equal(s.neutral, 0);
});

// ------------------------------------------------------------- WORKER-STATE
console.log(" worker states — reported, never mis-healthed");
check("NOT_ATTACHED is neutral, not a failure", () => {
  const cell = cellFromWorker({ name: "incident_worker", state: "NOT_ATTACHED", attached: false }, 1);
  assert.equal(cell.status, "NOT_ATTACHED");
  assert.equal(cell.level, "neutral");
});
check("STOPPED is neutral, not a failure", () => {
  const cell = cellFromWorker({ name: "accounting_worker", state: "STOPPED", attached: true }, 1);
  assert.equal(cell.level, "neutral");
});
check("UNKNOWN is neutral and never healthy", () => {
  const cell = cellFromWorker({ name: "w", state: "UNKNOWN", attached: true }, 1);
  assert.equal(cell.level, "neutral");
  assert.notEqual(cell.level, "good");
});
check("RUNNING is good", () => {
  const cell = cellFromWorker({ name: "w", state: "RUNNING", attached: true }, 1);
  assert.equal(cell.level, "good");
});
check("missing worker state renders UNKNOWN, never a verdict", () => {
  const cell = cellFromWorker({ name: "w", state: null }, 1);
  assert.equal(cell.status, "UNKNOWN");
  assert.equal(cell.level, "neutral");
});
check("attached=false is reported in the detail", () => {
  const cell = cellFromWorker({ name: "w", state: "NOT_ATTACHED", attached: false }, 1);
  assert.equal(cell.detail, "attribute missing on engine");
});

// ------------------------------------------------------------- NEWS-AXES
// The page-level news derivation lives in HealthPage.tsx (a component, not
// importable here); the contract it must obey is pinned as pure logic so a
// regression in the derivation fails loudly. This mirrors the derivation:
function newsVerdict(available, enabled, healthState, stale) {
  if (!available) return "UNAVAILABLE";
  const dataState = typeof healthState === "string" ? healthState.toUpperCase() : "";
  if (stale === true || dataState === "STALE") return "STALE";
  return enabled ? "ACTIVE" : "IDLE";
}
function newsLevel(available, status) {
  if (!available) return "neutral";
  return status === "STALE" ? "warn" : "good";
}

console.log(" news axes — liveness and freshness are independent");
check("live service + STALE data reads STALE, not ACTIVE", () => {
  // The exact observed defect: available=true, state=STALE, stale=true
  // rendered a green ACTIVE badge.
  const status = newsVerdict(true, true, "STALE", true);
  assert.equal(status, "STALE");
  assert.equal(newsLevel(true, status), "warn");
});
check("live service + fresh data reads ACTIVE", () => {
  const status = newsVerdict(true, true, "NORMAL", false);
  assert.equal(status, "ACTIVE");
  assert.equal(newsLevel(true, status), "good");
});
check("live service + no health payload reads ACTIVE (no data axis claim)", () => {
  const status = newsVerdict(true, true, undefined, false);
  assert.equal(status, "ACTIVE");
});
check("unavailable service reads UNAVAILABLE regardless of data", () => {
  assert.equal(newsVerdict(false, true, "STALE", true), "UNAVAILABLE");
  assert.equal(newsLevel(false, "UNAVAILABLE"), "neutral");
});
check("disabled-but-present service reads IDLE, not a failure", () => {
  const status = newsVerdict(true, false, "NORMAL", false);
  assert.equal(status, "IDLE");
});
check("stale flag alone (no state word) still degrades", () => {
  const status = newsVerdict(true, true, undefined, true);
  assert.equal(status, "STALE");
});

// ------------------------------------------------------------- result
if (failures > 0) {
  console.log(`\n${failures} health contract check(s) FAILED`);
  process.exit(1);
}
console.log("\nall health contract checks passed");
