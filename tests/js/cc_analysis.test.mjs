/**
 * CC analysis derivations — pins the viz-kit contract for the BUG-312
 * analysis wave: backend values flow through unmodified, missing data is
 * EXCLUDED/UNKNOWN (never a fabricated zero), ratios derive only from
 * reported values.
 *
 * Run: node --test tests/js/cc_analysis.test.mjs
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const featureDir = join(here, "..", "..", "frontend", "src", "features", "command-center");

// Vite-style extensionless relative imports ("./model") need a resolve hook
// under plain node --test; self-register so CI's bare runner works too.
register("./_ts_ext_resolve.mjs", import.meta.url);

// Node 24 runs .ts directly (type stripping) — import the pure module.
// Windows: dynamic import needs a file:// URL, not a bare C:\ path.
const { gateOutcomes, pipelineFunnel, histogram, evidenceRings, eligibilityCounts, lifecycleSegments, fleetConfidenceSamples } = await import(
  pathToFileURL(join(featureDir, "analysis.ts")).href
);

/* ---------- gateOutcomes ---------- */

test("gateOutcomes: backend counts flow through unchanged", () => {
  const overview = {
    evaluation_metrics: {
      BACKTEST: { pass: 496, fail: 2, running: 0, inconclusive: 0, total: 498, pass_rate: 0.996, fail_rate: 0.004 },
      WALK_FORWARD: { pass: 7, fail: 489, running: 0, inconclusive: 0, total: 496, pass_rate: 0.014, fail_rate: 0.986 },
    },
  };
  const rows = gateOutcomes(overview);
  assert.equal(rows.length, 2);
  const wf = rows.find((r) => r.gate === "WALK_FORWARD");
  assert.equal(wf.pass, 7);
  assert.equal(wf.fail, 489);
  assert.equal(wf.total, 496);
  // eval order preserved (BACKTEST before WALK_FORWARD)
  assert.equal(rows[0].gate, "BACKTEST");
});

test("gateOutcomes: a gate absent from the payload is EXCLUDED, not zero-filled", () => {
  const rows = gateOutcomes({ evaluation_metrics: { OOS: { pass: 64, fail: 0, running: 0, inconclusive: 0, total: 64 } } });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].gate, "OOS");
  assert.ok(!rows.some((r) => r.gate === "ROBUSTNESS"), "missing gate must not appear as a 0-count row");
});

test("gateOutcomes: total=0 renders rate UNKNOWN (null), not a fabricated 0%", () => {
  const rows = gateOutcomes({ evaluation_metrics: { SCORE: { pass: 0, fail: 0, running: 0, inconclusive: 0, total: 0, pass_rate: 0.0 } } });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].pass_rate, null, "empty gate must be UNKNOWN, not 0.0");
  assert.equal(rows[0].total, 0);
});

test("gateOutcomes: null/undefined overview → empty list (charts show EmptyState)", () => {
  assert.deepEqual(gateOutcomes(undefined), []);
  assert.deepEqual(gateOutcomes(null), []);
});

/* ---------- pipelineFunnel ---------- */

test("pipelineFunnel: tested/passed counts come from evaluation_pipeline only", () => {
  const overview = {
    total_strategies: 500,
    evaluation_pipeline: {
      BACKTEST_RUN: 498,
      WALK_FORWARD_TESTED: 496,
      WALK_FORWARD_PASSED: 7,
      OOS_TESTED: 496,
      OOS_PASSED: 64,
      ROBUSTNESS_TESTED: 471,
      ROBUSTNESS_PASSED: 471,
      SCORING_COMPLETED: 496,
    },
  };
  const stages = pipelineFunnel(overview);
  const wf = stages.find((s) => s.label === "walk-forward passed");
  assert.equal(wf.base, 496, "base = WALK_FORWARD_TESTED");
  assert.equal(wf.value, 7, "value = WALK_FORWARD_PASSED");
  assert.ok(wf.conversion !== null && Math.abs(wf.conversion - 7 / 496) < 1e-9, "conversion derives from passed/tested");
  const disc = stages[0];
  assert.equal(disc.label, "registry");
  assert.equal(disc.value, 500);
  assert.equal(disc.base, 500);
});

test("pipelineFunnel: stages nobody entered are excluded, never zero-filled", () => {
  const stages = pipelineFunnel({ total_strategies: 500, evaluation_pipeline: { BACKTEST_RUN: 498 } });
  assert.ok(!stages.some((s) => s.label === "walk-forward passed"), "untested gate must not appear as a 0-value row");
  const backtest = stages.find((s) => s.label === "backtest run");
  assert.equal(backtest.value, 498);
  assert.equal(backtest.base, 500, "conversion base for backtest = registry total");
});

test("pipelineFunnel: passed>tested guard keeps conversion honest (null, never >100%)", () => {
  const stages = pipelineFunnel({
    total_strategies: 10,
    evaluation_pipeline: { BACKTEST_RUN: 9, WALK_FORWARD_TESTED: 5, WALK_FORWARD_PASSED: 9 },
  });
  const wf = stages.find((s) => s.label === "walk-forward passed");
  assert.equal(wf.conversion, null, "passed>tested is backend-inconsistent → UNKNOWN, never 180%");
  assert.equal(wf.value, 9, "raw reported counts still shown");
});

/* ---------- histogram ---------- */

test("histogram: buckets count values, missing goes to `missing` not bin 0", () => {
  const h = histogram([0.1, 0.2, 0.2, null, undefined, NaN, 0.15], { lo: 0, hi: 1, bins: 5 });
  assert.equal(h.missing, 3, "null/undefined/NaN are excluded from bins");
  const counted = h.buckets.reduce((a, b) => a + b.count, 0);
  assert.equal(counted, 4, "only real values land in buckets (0.1, 0.2, 0.2, 0.15)");
  // width 0.2: 0.1/0.15 → bin0, 0.2/0.2 → bin1
  assert.equal(h.buckets[0].count, 2);
  assert.equal(h.buckets[1].count, 2);
});

test("histogram: out-of-range values counted below/above, not silently dropped", () => {
  const h = histogram([-1, 0.5, 2], { lo: 0, hi: 1, bins: 2 });
  assert.equal(h.below, 1);
  assert.equal(h.above, 1);
  assert.equal(h.buckets.reduce((a, b) => a + b.count, 0), 1);
});

/* ---------- evidenceRings ---------- */

test("evidenceRings: PASS count per row lands in the right ring", () => {
  const fleet = {
    available: true,
    count: 4,
    rows: [
      { evidence: { backtest_status: "PASS", walkforward_status: "PASS", oos_status: "FAIL", robustness_status: "PASS" } }, // 3
      { evidence: { backtest_status: "PASS", walkforward_status: "PASS", oos_status: "PASS", robustness_status: "PASS" } }, // 4
      { evidence: { backtest_status: "FAIL", walkforward_status: "FAIL", oos_status: "FAIL", robustness_status: "FAIL" } }, // 0
      {}, // missing evidence object → missing counter
    ],
  };
  const r = evidenceRings(fleet);
  assert.equal(r.buckets[3], 1);
  assert.equal(r.buckets[4], 1);
  assert.equal(r.buckets[0], 1);
  assert.equal(r.missing, 1);
  assert.equal(r.buckets.length, 5, "rings 0..4");
});

/* ---------- eligibilityCounts ---------- */

test("eligibilityCounts: authoritative states in policy order, others appended", () => {
  const fleet = {
    available: true,
    count: 5,
    rows: [
      { eligibility_state: "YES" },
      { eligibility_state: "YES" },
      { eligibility_state: "BLOCKED" },
      { eligibility_state: "MYSTERY" },
      {}, // → UNKNOWN bucket
    ],
  };
  const out = eligibilityCounts(fleet);
  assert.deepEqual(
    out.map((r) => [r.state, r.count]),
    [
      ["YES", 2],
      ["BLOCKED", 1],
      ["UNKNOWN", 1],
      ["MYSTERY", 1],
    ],
    "policy order first, unlisted states appended by count desc; absent states excluded"
  );
  assert.equal(
    out.reduce((a, b) => a + b.count, 0),
    5
  );
});

/* ---------- lifecycleSegments ---------- */

test("lifecycleSegments: pipeline+terminal merged, zero counts dropped, shares sum to 1", () => {
  const o = {
    by_lifecycle: { SHADOW: 300, DISCOVERED: 199, ACTIVE: 1, RETIRED: 0 },
    terminal: { REJECTED: 150, DEGRADED: 44, RETIRED: 6 },
  };
  const segs = lifecycleSegments(o);
  const byKey = Object.fromEntries(segs.map((s) => [s.key, s]));
  assert.equal(byKey.SHADOW.count, 300);
  assert.equal(byKey.REJECTED.count, 150);
  assert.equal(byKey.RETIRED.count, 6, "terminal RETIRED wins over by_lifecycle 0");
  assert.ok(!("DEGRADED" in byKey) || byKey.DEGRADED.count === 44);
  assert.ok(segs.every((s) => s.count > 0), "zero-count segments are dropped");
  const sum = segs.reduce((a, s) => a + s.share, 0);
  assert.ok(Math.abs(sum - 1) < 1e-9, `shares must sum to 1, got ${sum}`);
});

/* ---------- fleetConfidenceSamples ---------- */

test("fleetConfidenceSamples: non-numeric/missing confidence excluded as missing", () => {
  const out = fleetConfidenceSamples({
    available: true,
    count: 5,
    rows: [{ confidence: 0.7 }, { confidence: "oops" }, { confidence: null }, { confidence: 0.9 }, {}],
  });
  assert.equal(out.length, 5, "one entry per row (bucketing counts missing separately)");
  assert.equal(out.filter((v) => v !== null).length, 2, "only real confidence values survive");
  assert.equal(out.filter((v) => v === null).length, 3);
});

/* ---------- file audit: derivations stay presentation-pure ---------- */

test("analysis.ts imports no runtime data-layer modules", () => {
  const src = readFileSync(join(featureDir, "analysis.ts"), "utf8");
  const importLines = src.split("\n").filter((l) => l.startsWith("import "));
  for (const line of importLines) {
    assert.ok(
      !line.includes('"./api"') && !line.includes('"./useCases"') && !line.includes('"@tanstack'),
      `analysis.ts must stay pure (found: ${line})`
    );
  }
});
