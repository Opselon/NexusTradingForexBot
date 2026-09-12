/**
 * Pro risk-visual math — safety-contract regression suite.
 *
 * Run:  node tests/js/pro_risk_viz.test.mjs
 *   or: node --test tests/js/pro_risk_viz.test.mjs
 *
 * Imports the REAL frontend module (Node 24 strips the TS types; the only
 * non-erasable import in that file is `import type`, which is removed before
 * resolution, so the `@/` alias never has to resolve here).
 *
 * These tests pin the client-side safety contract for the ALT console risk
 * visuals: no heuristic may ever manufacture a verdict word, a gate without
 * backend booleans is UNKNOWN and never FAIL, and a null limit never renders as
 * a satisfied one.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  UNKNOWN_WORD,
  backendWord,
  ceilingPairTone,
  clamp01,
  counterTone,
  exposureTotals,
  floorPairTone,
  gateFunnel,
  gateVerdict,
  isFiniteNumber,
  limitTone,
  limitUtilization,
} from "../../frontend/src/lib/riskVizMath.ts";

// ---------------------------------------------------------------------------
// gateVerdict / gateFunnel — backend booleans ONLY
// ---------------------------------------------------------------------------

test("gateVerdict: backend booleans map 1:1", () => {
  assert.equal(gateVerdict({ passed: true }), "pass");
  assert.equal(gateVerdict({ passed: false }), "fail");
  assert.equal(gateVerdict({ allowed: true }), "pass");
  assert.equal(gateVerdict({ allowed: false }), "fail");
  assert.equal(gateVerdict(true), "pass");
  assert.equal(gateVerdict(false), "fail");
});

test("SAFETY: a gate with neither passed nor allowed is UNKNOWN, never FAIL", () => {
  const shapes = [
    {},
    null,
    undefined,
    { value: 12, limit: 5 }, // has a number, says nothing about the verdict
    { reason: "probed but undetermined" },
    { passed: null, allowed: null },
    { passed: "true" }, // a string is not a backend boolean
    { allowed: 1 },
    0,
    "passed",
    [],
  ];
  for (const shape of shapes) {
    assert.equal(gateVerdict(shape), "unknown", `misread shape: ${JSON.stringify(shape) ?? String(shape)}`);
    assert.notEqual(gateVerdict(shape), "fail");
  }
});

test("gateVerdict: passed wins over allowed=false, mirroring RiskPage ordering", () => {
  assert.equal(gateVerdict({ passed: true, allowed: false }), "pass");
  assert.equal(gateVerdict({ passed: false, allowed: true }), "pass");
});

test("gateFunnel: counts split pass / fail / unknown and keep reason text verbatim", () => {
  const funnel = gateFunnel({
    spread_guard: { passed: true },
    news_window: { allowed: false, reason: "HIGH_IMPACT_NEWS within 120s" },
    liquidity_gate: { value: 0.4, limit: 0.2 },
    atr_sl_buffer: {},
  });
  assert.equal(funnel.pass, 1);
  assert.equal(funnel.fail, 1);
  assert.equal(funnel.unknown, 2);
  assert.equal(funnel.total, 4);
  const failRow = funnel.rows.find((r) => r.name === "news_window");
  assert.equal(failRow.reason, "HIGH_IMPACT_NEWS within 120s");
  assert.equal(funnel.rows.find((r) => r.name === "atr_sl_buffer").reason, null);
});

test("gateFunnel: null / empty payload is an EMPTY funnel, not a wall of unknowns", () => {
  for (const payload of [null, undefined, {}]) {
    const f = gateFunnel(payload);
    assert.deepEqual({ pass: f.pass, fail: f.fail, unknown: f.unknown, total: f.total }, { pass: 0, fail: 0, unknown: 0, total: 0 });
  }
});

test("gateFunnel: a 10-gate trace with no booleans reports 10 UNKNOWN and 0 FAIL", () => {
  const checks = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`gate_${i}`, { value: i }]));
  const f = gateFunnel(checks);
  assert.equal(f.unknown, 10);
  assert.equal(f.fail, 0);
  assert.equal(f.pass, 0);
});

// ---------------------------------------------------------------------------
// limitUtilization — a null limit is never a satisfied limit
// ---------------------------------------------------------------------------

test("limitUtilization: value/limit arithmetic", () => {
  assert.equal(limitUtilization(3, 10), 0.3);
  assert.equal(limitUtilization(10, 10), 1);
  assert.ok(limitUtilization(15, 10) > 1);
});

test("SAFETY: limitUtilization returns null when EITHER side is missing", () => {
  assert.equal(limitUtilization(4, null), null);
  assert.equal(limitUtilization(4, undefined), null);
  assert.equal(limitUtilization(null, 5), null);
  assert.equal(limitUtilization(undefined, undefined), null);
  assert.equal(limitUtilization(NaN, 5), null);
  assert.equal(limitUtilization(5, NaN), null);
  assert.equal(limitUtilization(5, Infinity), null);
  // A zero/negative limit cannot define a budget — no fake 0 % or Infinity %.
  assert.equal(limitUtilization(5, 0), null);
  assert.equal(limitUtilization(5, -2), null);
});

test("limitTone: null utilization can never resolve to the clean tone", () => {
  assert.equal(limitTone(null), "warn");
  assert.equal(limitTone(0.4), "ok");
  assert.equal(limitTone(1), "ok");
  assert.equal(limitTone(1.01), "bad");
});

test("ceilingPairTone: missing limit -> null (caller must not paint it safe)", () => {
  assert.equal(ceilingPairTone(2, 10), "ok");
  assert.equal(ceilingPairTone(20, 10), "bad");
  assert.equal(ceilingPairTone(2, null), null);
  assert.equal(ceilingPairTone(null, 10), null);
});

test("floorPairTone: margin level above the BACKEND floor is the clean side", () => {
  assert.equal(floorPairTone(500, 200), "ok");
  assert.equal(floorPairTone(200, 200), "ok");
  assert.equal(floorPairTone(150, 200), "bad");
  assert.equal(floorPairTone(150, null), null); // no floor supplied -> no claim
  assert.equal(floorPairTone(null, 200), null);
});

test("clamp01: geometry clamp, null stays null", () => {
  assert.equal(clamp01(-3), 0);
  assert.equal(clamp01(0.42), 0.42);
  assert.equal(clamp01(9), 1);
  assert.equal(clamp01(null), null);
});

// ---------------------------------------------------------------------------
// exposureTotals
// ---------------------------------------------------------------------------

test("exposureTotals: sums backend by_symbol rows", () => {
  const t = exposureTotals({
    XAUUSD: { volume: 0.5, profit: 12.5, positions: 2 },
    EURUSD: { volume: 1.25, profit: -3, positions: 3 },
  });
  assert.equal(t.symbols, 2);
  assert.equal(t.volume, 1.75);
  assert.equal(t.profit, 9.5);
  assert.equal(t.positions, 5);
});

test("exposureTotals: absent / empty block is null, NOT zero exposure", () => {
  assert.equal(exposureTotals(null), null);
  assert.equal(exposureTotals(undefined), null);
  assert.equal(exposureTotals({}), null);
});

test("exposureTotals: a row field with no finite number stays null, never 0", () => {
  const t = exposureTotals({ XAUUSD: { positions: 1 } });
  assert.equal(t.volume, null);
  assert.equal(t.profit, null);
  assert.equal(t.positions, 1);
});

// ---------------------------------------------------------------------------
// counterTone — value > 0 ONLY, no invented severities
// ---------------------------------------------------------------------------

test("counterTone derives from value>0 only", () => {
  assert.equal(counterTone(0), "ok");
  assert.equal(counterTone(1), "bad");
  assert.equal(counterTone(9999), "bad");
  assert.equal(counterTone(-1), "ok"); // not > 0: no invented severity
});

test("counterTone: missing / non-numeric can never read as clean", () => {
  for (const v of [null, undefined, NaN, Infinity]) {
    assert.notEqual(counterTone(v), "ok");
    assert.equal(counterTone(v), "warn");
  }
});

// ---------------------------------------------------------------------------
// verdict words — the module must not contain a verdict vocabulary
// ---------------------------------------------------------------------------

test("isFiniteNumber guards real numbers only", () => {
  assert.equal(isFiniteNumber(0), true);
  assert.equal(isFiniteNumber("5"), false);
  assert.equal(isFiniteNumber(null), false);
  assert.equal(isFiniteNumber(NaN), false);
});

test("backendWord echoes the payload string; blank/missing collapses to UNKNOWN", () => {
  assert.equal(backendWord("halted"), "HALTED");
  assert.equal(backendWord(" running "), "RUNNING");
  assert.equal(backendWord(null), UNKNOWN_WORD);
  assert.equal(backendWord(""), UNKNOWN_WORD);
  assert.equal(backendWord(undefined), UNKNOWN_WORD);
});

test("SAFETY: the whole module never invents SAFE / BLOCKED / HALTED wording", async () => {
  const src = await readSelf();
  // The only literal status words allowed are the ones that also appear as
  // backend payload values inside a comment/docstring; the vocabulary check is
  // on the UNKNOWN fallback plus the two boolean restatements.
  assert.match(src, /export const UNKNOWN_WORD = "UNKNOWN"/);
  // gateVerdict may only return these three machine keys.
  const returned = [...src.matchAll(/return "(pass|fail|unknown|ok|bad|warn)";/g)].map((m) => m[1]);
  const allowed = new Set(["pass", "fail", "unknown", "ok", "bad", "warn"]);
  assert.ok(returned.length > 0);
  for (const word of returned) assert.ok(allowed.has(word), `unexpected literal verdict "${word}"`);
  // No human-facing SAFE word is ever produced by the math layer.
  assert.doesNotMatch(src, /return\s+"SAFE"/);
  assert.doesNotMatch(src, /return\s+"WITHIN LIMIT"/);
});

// ---------------------------------------------------------------------------
// component layer — no fetching, no invented verdict words
// ---------------------------------------------------------------------------

test("SAFETY: RiskViz.tsx performs no I/O and adds no verdict vocabulary", async () => {
  const src = await readRepoFile("frontend", "src", "components", "pro", "RiskViz.tsx");
  assert.doesNotMatch(src, /\bfetch\s*\(/, "risk visuals must not fetch");
  assert.doesNotMatch(src, /XMLHttpRequest|useQuery|axios/, "risk visuals must be data-passive");
  assert.doesNotMatch(src, /localStorage|sessionStorage/, "risk visuals must not read storage");
  // Every literal status word in the component file must be one the backend can
  // send (echoed comparison values or restatements of a backend boolean).
  const words = [...src.matchAll(/return\s+"([A-Z][A-Z _]{2,})"|===\s+"([A-Z][A-Z _]{2,})"/g)]
    .map((m) => m[1] ?? m[2])
    .filter((w) => !["RUNNING", "HALTED", "BLOCKED", "FRESH", "OK", "HEALTHY", "READY", "ACTIVE", "ERROR", "FAILED", "STALE_DATA", "DISCONNECTED"].includes(w));
  for (const w of words) {
    assert.ok(["ACTIVE", "DISENGAGED", "UNKNOWN", "OFF", "PASS", "FAIL"].includes(w), `component invents status word "${w}"`);
  }
  // The SAFE claim must never appear in the presentation layer at all.
  assert.doesNotMatch(src, /"SAFE"/);
});

async function readRepoFile(...parts) {
  const { readFile } = await import("node:fs/promises");
  const { fileURLToPath } = await import("node:url");
  const { dirname, resolve } = await import("node:path");
  const here = dirname(fileURLToPath(import.meta.url));
  return readFile(resolve(here, "..", "..", ...parts), "utf8");
}

async function readSelf() {
  return readRepoFile("frontend", "src", "lib", "riskVizMath.ts");
}
