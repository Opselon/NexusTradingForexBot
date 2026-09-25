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
 * visuals: no heuristic may ever manufacture a verdict word, a gate whose
 * limit is absent is UNKNOWN and never FAIL, and a null limit never renders as
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
  isFiniteNumber,
  limitTone,
  limitUtilization,
} from "../../frontend/src/lib/riskVizMath.ts";
import {
  comparePair,
  directionWord,
  gateEvidence,
  verdictWord,
} from "../../frontend/src/lib/riskGateTrace.ts";

// ---------------------------------------------------------------------------
// comparePair / gateEvidence — value vs its OWN backend limit
//
// `proposal.risk_checks` (signals/policy.py) is a flat EVIDENCE record: a
// metric value and its limit live in SIBLING keys (`rr` / `min_rr`,
// `spread_atr_ratio` / `max_spread_atr_ratio`, ...). It is NOT a map of gate
// booleans, so a verdict is the arithmetic restatement of those two numbers
// in the producer's own direction — never an invented word.
// ---------------------------------------------------------------------------

test("comparePair: floor gates pass at the limit and above, fail below it", () => {
  assert.equal(comparePair(0.7, 0.7, "ge"), "pass");
  assert.equal(comparePair(0.9, 0.7, "ge"), "pass");
  assert.equal(comparePair(0.69, 0.7, "ge"), "fail");
});

test("comparePair: ceiling gates pass at the limit and below, fail above it", () => {
  assert.equal(comparePair(0.18, 0.18, "le"), "pass");
  assert.equal(comparePair(0.1, 0.18, "le"), "pass");
  assert.equal(comparePair(0.19, 0.18, "le"), "fail");
});

test("SAFETY: a pair with a missing, non-finite, or unusable limit is UNKNOWN, never FAIL", () => {
  const unusable = [
    [0.5, null],
    [0.5, undefined],
    [null, 0.7],
    [undefined, 0.7],
    [NaN, 0.7],
    [0.5, NaN],
    [0.5, Infinity],
    [Infinity, 0.7],
    ["0.5", 0.7],
    [0.5, "0.7"],
  ];
  for (const [value, limit] of unusable) {
    assert.equal(comparePair(value, limit, "ge"), "unknown", `floor pair misread: ${String(value)} vs ${String(limit)}`);
    assert.equal(comparePair(value, limit, "le"), "unknown", `ceiling pair misread: ${String(value)} vs ${String(limit)}`);
  }
});

test("SAFETY: a ceiling with a non-positive limit stays UNKNOWN (no fake headroom, no divide-by-zero reading)", () => {
  assert.equal(comparePair(0.1, 0, "le"), "unknown");
  assert.equal(comparePair(0.1, -1, "le"), "unknown");
  // A floor of zero is a real, satisfiable threshold — it must not be collapsed
  // to the same unknown (this is the one direction where 0 is a legitimate limit).
  assert.equal(comparePair(0.1, 0, "ge"), "pass");
  assert.equal(comparePair(-0.1, 0, "ge"), "fail");
});

test("gateEvidence: REAL backend payload pairs into pass/fail/unknown (no invented verdicts)", () => {
  // Verbatim shape returned by /api/v1/risk/status for a live NO_TRADE proposal.
  const checks = {
    zone_quality: 0,
    min_zone_quality: 0.7,
    rr: 1,
    min_rr: 2.2,
    model_confidence: 0,
    confidence_source: "DIRECTIONAL_NORMALIZED",
    base_threshold: 0.4,
    range_penalty: 0.1,
    survival_mode_adjustment: 0,
    effective_threshold: 0.5,
    spread_usd: 0.24,
    spread_atr_ratio: 0.2444,
    max_spread_atr_ratio: 0.18,
    spread_tp_ratio: 0,
    tp_distance_usd: null,
    max_spread_pct_of_tp: 0.15,
    spread_session_percentile_value: null,
    spread_session_percentile: 70,
    expected_symbol: "XAUUSD",
    expected_magic: 888101,
  };
  const ev = gateEvidence(checks);
  // 20 payload keys -> 14 rows: 7 declared value→limit pairs (one row each)
  // plus the 7 keys that are only LIMITS or threshold components; the 6 limit
  // keys (min_zone_quality, min_rr, effective_threshold as a limit,
  // max_spread_atr_ratio, max_spread_pct_of_tp, spread_session_percentile,
  // max_spread_points) are consumed BY their pair and never double-counted.
  assert.equal(ev.total, 14, "every payload key becomes at most one row");
  const byName = Object.fromEntries(ev.rows.map((r) => [r.name, r]));
  // Floors the decision failed:
  assert.equal(byName.zone_quality.verdict, "fail");
  assert.equal(byName.rr.verdict, "fail");
  assert.equal(byName.model_confidence.verdict, "fail");
  // Ceiling the decision breached:
  assert.equal(byName.spread_atr_ratio.verdict, "fail");
  // Ceiling that passed:
  assert.equal(byName.spread_tp_ratio.verdict, "pass");
  // A pair whose VALUE is null must not be read as a failure:
  assert.equal(byName.spread_session_percentile_value.verdict, "unknown");
  assert.equal(byName.spread_session_percentile_value.value, null);
  // The percentile key itself is consumed AS that pair's limit (value 70) and
  // must not also appear as its own row — one metric, one row.
  assert.ok(!("spread_session_percentile" in byName), "a limit key is consumed by its pair, never double-counted");
  // Cross-unit pairing is forbidden: spread_usd (dollars) is NOT compared
  // against max_spread_points (broker points) — the units differ by 100.
  assert.equal(byName.spread_usd.verdict, "unknown", "cross-unit pair must stay unknown");
  assert.equal(byName.spread_usd.limit, null, "no limit may be fabricated for an unpaired value");
  // Threshold arithmetic is evidence, not gates:
  assert.equal(byName.base_threshold.verdict, "unknown");
  assert.equal(byName.range_penalty.verdict, "unknown");
  assert.equal(byName.survival_mode_adjustment.verdict, "unknown");
  // effective_threshold is consumed AS the model_confidence pair's limit, so
  // it must not also appear as a row — one metric, one row.
  assert.ok(!("effective_threshold" in byName), "effective_threshold is the confidence pair's limit, not its own row");
  assert.equal(byName.model_confidence.limit, 0.5, "the confidence gate's limit is the effective threshold");
  assert.ok(!("min_rr" in byName), "min_rr is consumed as the rr pair's limit, never a row of its own");
  // Non-numeric identity evidence is reported, never silently dropped:
  assert.equal(byName.confidence_source.value, null);
  assert.ok(byName.model_confidence.detail.includes("DIRECTIONAL_NORMALIZED"), "backend string context is echoed verbatim");
  // The funnel counts must agree with the rows themselves.
  assert.equal(ev.pass + ev.fail + ev.unknown, ev.total);
  assert.ok(ev.fail > 0, "the payload carried real breaches — the matrix must not report zero of everything");
});

test("gateEvidence: null / empty payload is an EMPTY trace, not a wall of unknowns", () => {
  for (const payload of [null, undefined, {}]) {
    const ev = gateEvidence(payload);
    assert.deepEqual({ pass: ev.pass, fail: ev.fail, unknown: ev.unknown, total: ev.total }, { pass: 0, fail: 0, unknown: 0, total: 0 });
  }
});

test("SAFETY: gateEvidence never manufactures a verdict for an unpaired metric", () => {
  // A payload of bare numbers with no sibling limits — the old bug shape. The
  // layer must report each one UNKNOWN with its value still visible, and must
  // never upgrade a bare number to a FAIL.
  const checks = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`metric_${i}`, i]));
  const ev = gateEvidence(checks);
  assert.equal(ev.total, 10);
  assert.equal(ev.unknown, 10);
  assert.equal(ev.fail, 0);
  assert.equal(ev.pass, 0);
  for (const row of ev.rows) assert.ok(row.value !== null, `value for ${row.name} is still displayed`);
});

test("gateEvidence: the other decision paths' shapes stay safe", () => {
  // TICK_SWEEP / PREDICTIVE_LIMIT paths stamp a NESTED structural gate object
  // instead of scalar pairs. It is not the scalar-pair model, so every entry
  // must stay UNKNOWN rather than being misread as a pass/fail.
  const ev = gateEvidence({
    decision_path: "TICK_SWEEP",
    confidence_gate_applied: true,
    sweep_conf_threshold: 0.45,
    model_confidence_verdict: "SWEEP_PATH_THRESHOLD",
    structural_gate: { price_pierced_liquidity: true, reversal_detected: true },
  });
  assert.equal(ev.fail, 0, "nested shapes carry no scalar limit to compare against");
  assert.equal(ev.pass, 0);
  assert.equal(ev.total, 5);
  for (const row of ev.rows) assert.equal(row.verdict, "unknown");
});

test("verdictWord / directionWord: only restatements, never new vocabulary", () => {
  assert.equal(verdictWord("pass"), "PASS");
  assert.equal(verdictWord("fail"), "FAIL");
  assert.equal(verdictWord("unknown"), UNKNOWN_WORD);
  assert.equal(directionWord("ge"), "min");
  assert.equal(directionWord("le"), "max");
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

test("SAFETY: the derivation modules never invent SAFE / BLOCKED / HALTED wording", async () => {
  // Both derivation layers are checked: riskVizMath owns UNKNOWN_WORD, and
  // riskGateTrace owns the value-vs-limit pairing that replaced the boolean
  // funnel. Neither may emit a human-facing verdict word the backend did not.
  const src = (await readRepoFile("frontend", "src", "lib", "riskVizMath.ts")) + "\n" + (await readSelf());
  assert.match(src, /export const UNKNOWN_WORD = "UNKNOWN"/);
  // A verdict may only be one of these machine keys.
  const returned = [
    ...src.matchAll(/return\s+"(pass|fail|unknown|ok|bad|warn)";/g),
    ...src.matchAll(/return\s+(?:value\s+)?(?:>=|<=)\s+\S+\s*\?\s*"(pass|fail|unknown|ok|bad|warn)"/g),
  ].map((m) => m[1]);
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
  return readRepoFile("frontend", "src", "lib", "riskGateTrace.ts");
}
