/**
 * ML / model pro-visualization math — backend-truth regression suite.
 *
 * Run:  node tests/js/pro_ml_viz.test.mjs
 *   or: node --test tests/js/pro_ml_viz.test.mjs
 *
 * Imports the REAL frontend module (frontend/src/lib/mlVizMath.ts) so the
 * assertions pin shipped code, not a copy. Node 24 strips the erasable TS
 * types; `import type { … } from "@/types/domain"` disappears before
 * resolution, but mlVizMath does have two VALUE imports through the `@/`
 * alias (lib/format, lib/signal), so a tiny resolve hook maps `@/…` onto
 * frontend/src/… for this process only. No bundler, no deps, no network.
 *
 * Contract under test (the same truth rules the console follows):
 *   - a status / disagreement class is the backend's own string, verbatim;
 *   - a missing status is UNKNOWN, never "ok"/"VALID";
 *   - a metric the backend did not send stays null (never 0, never a residual);
 *   - ranking/clamping is geometry only — displayed numbers stay unmodified;
 *   - reason wording comes from lib/signal REASONS, unknown codes verbatim.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const SRC_URL = new URL("../../frontend/src/", import.meta.url);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (typeof specifier === "string" && specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), SRC_URL);
      for (const ext of [".ts", ".tsx"]) {
        const candidate = fileURLToPath(new URL(base.href + ext));
        if (existsSync(candidate)) {
          return { url: base.href + ext, shortCircuit: true, format: "module-typescript" };
        }
      }
      throw new Error(`pro_ml_viz test: cannot resolve alias ${specifier}`);
    }
    return nextResolve(specifier, context);
  },
});

const {
  AGREEMENT_CLASS,
  BACKEND_FEATURE_STATUSES,
  DISAGREEMENT_CLASSES,
  UNKNOWN,
  agreementRateText,
  disagreementBadgeLevel,
  disagreementTally,
  featureStatusTally,
  featureStatusTone,
  finiteOrNull,
  formatAgeMsText,
  formatAgeSec,
  latencySplit,
  msText,
  probPctText,
  probTriple,
  reasonCopy,
  shadowRows,
  statusText,
  tallyFromCounts,
  topNContributions,
} = await import("../../frontend/src/lib/mlVizMath.ts");

const { formatAgeMs } = await import("../../frontend/src/lib/format.ts");
const { REASONS } = await import("../../frontend/src/lib/signal.ts");

// ---------------------------------------------------------------------------
// status / number passthrough primitives
// ---------------------------------------------------------------------------

test("statusText: backend status strings pass through VERBATIM", () => {
  for (const s of BACKEND_FEATURE_STATUSES) {
    assert.equal(statusText(s), s);
  }
  assert.equal(statusText("SHADOW_DEGRADED"), "SHADOW_DEGRADED");
});

test("statusText: missing/blank collapses to UNKNOWN, never 'ok'", () => {
  assert.equal(statusText(undefined), UNKNOWN);
  assert.equal(statusText(null), UNKNOWN);
  assert.equal(statusText(""), UNKNOWN);
  assert.equal(statusText("   "), UNKNOWN);
  assert.notEqual(statusText(undefined), "ok");
  assert.notEqual(statusText(""), "VALID");
  assert.equal(statusText(0), "0"); // a real (if odd) value is still echoed
});

test("finiteOrNull: only honest numbers; NaN/Infinity/null/bool -> null", () => {
  assert.equal(finiteOrNull(1.5), 1.5);
  assert.equal(finiteOrNull(0), 0);
  assert.equal(finiteOrNull(-3), -3);
  assert.equal(finiteOrNull("2.5"), 2.5);
  assert.equal(finiteOrNull(NaN), null);
  assert.equal(finiteOrNull(Infinity), null);
  assert.equal(finiteOrNull(null), null);
  assert.equal(finiteOrNull(undefined), null);
  assert.equal(finiteOrNull(true), null);
  assert.equal(finiteOrNull("abc"), null);
  assert.equal(finiteOrNull(""), null);
});

test("msText / probPctText / age helpers keep null visible", () => {
  assert.equal(msText(4.234), "4.2 ms");
  assert.equal(msText(null), "—");
  assert.equal(msText(undefined), "—");
  assert.equal(msText(NaN), "—");
  assert.equal(probPctText(0.6234), "62.3%");
  assert.equal(probPctText(null), UNKNOWN);
  // freshness age formatting is REUSED from lib/format, not reimplemented
  assert.equal(formatAgeMsText(5000), formatAgeMs(5000));
  assert.equal(formatAgeSec(5), formatAgeMs(5000));
  assert.equal(formatAgeSec(null), "—");
  assert.equal(formatAgeSec(undefined), "—");
});

// ---------------------------------------------------------------------------
// topNContributions — |value| ranking with status passthrough
// ---------------------------------------------------------------------------

const FEATURES = [
  { index: 0, name: "atr_ratio", value: 0.4, status: "VALID" },
  { index: 1, name: "spread_points", value: -1.2, status: "VALID" },
  { index: 2, name: "rsi_14", value: 3.5, status: "VALID" },
  { index: 3, name: "htf_liquidity_score", value: NaN, status: "NAN" },
  { index: 4, name: "bsl_distance_atr", value: null, status: "UNAVAILABLE" },
  { index: 5, name: "eql_strength", value: -3.5, status: "VALID" },
];

test("topNContributions: ranked by |value|, descending", () => {
  const rows = topNContributions(FEATURES, 3);
  assert.deepEqual(
    rows.map((r) => r.name),
    ["rsi_14", "eql_strength", "spread_points"],
  );
  assert.deepEqual(
    rows.map((r) => r.magnitude),
    [3.5, 3.5, 1.2],
  );
});

test("topNContributions: raw value is untouched (no scaling, no sign loss)", () => {
  const [top] = topNContributions(FEATURES, 1);
  assert.equal(top.value, 3.5);
  const neg = topNContributions(FEATURES, 6).find((r) => r.name === "eql_strength");
  assert.equal(neg.value, -3.5);
  assert.equal(neg.direction, "neg");
});

test("topNContributions: status passes through per feature; missing -> UNKNOWN", () => {
  const rows = topNContributions(
    [
      { index: 0, name: "a", value: 1, status: "VALID" },
      { index: 1, name: "b", value: 2 }, // backend sent no status at all
      { index: 2, name: "c", value: 3, status: "" }, // blank status
    ],
    5,
  );
  const byName = Object.fromEntries(rows.map((r) => [r.name, r.status]));
  assert.equal(byName.a, "VALID");
  assert.equal(byName.b, UNKNOWN);
  assert.equal(byName.c, UNKNOWN);
  assert.notEqual(byName.b, "ok");
});

test("topNContributions: NaN/null entries rank last and stay visible, not 0", () => {
  const rows = topNContributions(FEATURES, 6);
  assert.equal(rows.length, 6);
  assert.deepEqual(
    rows.slice(-2).map((r) => r.name),
    ["htf_liquidity_score", "bsl_distance_atr"],
  );
  const nan = rows.find((r) => r.name === "htf_liquidity_score");
  assert.equal(nan.value, null); // NaN sanitized away -> no honest number
  assert.equal(nan.status, "NAN");
  assert.equal(nan.renderable, false);
  assert.equal(nan.fraction, 0);
  assert.equal(nan.direction, "unknown");
});

test("topNContributions: fraction normalizes against the largest shown magnitude", () => {
  const rows = topNContributions(FEATURES, 3);
  assert.equal(rows[0].fraction, 1);
  assert.ok(Math.abs(rows[2].fraction - 1.2 / 3.5) < 1e-12);
  assert.ok(rows.every((r) => r.fraction >= 0 && r.fraction <= 1));
});

test("topNContributions: n bounds the list; degenerate n yields no rows", () => {
  assert.equal(topNContributions(FEATURES, 0).length, 0);
  assert.equal(topNContributions(FEATURES, -5).length, 0);
  assert.equal(topNContributions(FEATURES, 99).length, 6);
  assert.equal(topNContributions(undefined, 5).length, 0);
  assert.equal(topNContributions(null, 5).length, 0);
  assert.equal(topNContributions([], 5).length, 0);
});

test("topNContributions: label falls back to backend index, never a fake name", () => {
  const rows = topNContributions([{ index: 7, value: 1, status: "VALID" }], 1);
  assert.equal(rows[0].label, "feature_7");
  assert.equal(rows[0].index, 7);
  assert.equal(rows[0].name, null);
  const anonymous = topNContributions([{ value: 1 }], 1);
  assert.equal(anonymous[0].label, "feature_0");
  assert.equal(anonymous[0].index, null);
});

test("topNContributions: missing_features flag comes from the backend list only", () => {
  const rows = topNContributions(FEATURES, 6, {
    missingFeatures: ["spread_points", "not_a_real_feature"],
  });
  const flagged = rows.filter((r) => r.missing).map((r) => r.name);
  assert.deepEqual(flagged, ["spread_points"]);
});

test("topNContributions: equal magnitudes keep backend index order", () => {
  const rows = topNContributions(
    [
      { index: 1, name: "b", value: -2, status: "VALID" },
      { index: 0, name: "a", value: 2, status: "VALID" },
    ],
    2,
  );
  assert.deepEqual(
    rows.map((r) => r.name),
    ["a", "b"],
  );
});

test("featureStatusTone maps only the backend's own status words", () => {
  assert.equal(featureStatusTone("VALID"), "ok");
  assert.equal(featureStatusTone("NAN"), "bad");
  assert.equal(featureStatusTone("INF"), "bad");
  assert.equal(featureStatusTone("NON_NUMERIC"), "bad");
  assert.equal(featureStatusTone("UNAVAILABLE"), "warn");
  assert.equal(featureStatusTone("WHATEVER"), "unknown");
  assert.equal(featureStatusTone(undefined), "unknown");
});

test("featureStatusTally counts real statuses without inventing buckets", () => {
  assert.deepEqual(featureStatusTally(FEATURES), { VALID: 4, NAN: 1, UNAVAILABLE: 1 });
  assert.deepEqual(featureStatusTally([]), {});
  assert.deepEqual(featureStatusTally([{ value: 1 }]), { UNKNOWN: 1 });
});

// ---------------------------------------------------------------------------
// probTriple
// ---------------------------------------------------------------------------

test("probTriple: values verbatim, order NO_TRADE/BUY/SELL", () => {
  const t = probTriple({ available: true, no_trade: 0.5, buy: 0.3, sell: 0.2 });
  assert.deepEqual(
    t.rows.map((r) => r.key),
    ["no_trade", "buy", "sell"],
  );
  assert.deepEqual(
    t.rows.map((r) => r.value),
    [0.5, 0.3, 0.2],
  );
  assert.deepEqual(
    t.rows.map((r) => r.valueText),
    ["0.5", "0.3", "0.2"],
  );
  assert.equal(t.available, true);
  assert.ok(Math.abs(t.sum - 1) < 1e-12);
});

test("probTriple: nulls are PRESERVED as null (never coerced to 0)", () => {
  const t = probTriple({ available: true, no_trade: null, buy: 0.3, sell: null });
  assert.equal(t.rows[0].value, null);
  assert.equal(t.rows[0].valueText, UNKNOWN);
  assert.equal(t.rows[0].pctText, UNKNOWN);
  assert.equal(t.rows[0].fraction, 0);
  assert.equal(t.rows[2].value, null);
  assert.equal(t.sum, null); // partial triple -> no honest sum to show
});

test("probTriple: available=false keeps values but marks them not real", () => {
  const t = probTriple({ available: false, no_trade: 0.9, buy: 0.1, sell: 0 });
  assert.equal(t.available, false);
  assert.ok(t.rows.every((r) => r.real === false));
});

test("probTriple: missing payload / all-null payload is honest", () => {
  for (const payload of [undefined, null, {}]) {
    const t = probTriple(payload);
    assert.equal(t.available, false);
    assert.ok(t.rows.every((r) => r.value === null && r.valueText === UNKNOWN));
    assert.equal(t.sum, null);
    assert.equal(t.inferenceTimestamp, null);
  }
});

test("probTriple: no renormalization even when the backend triple is skewed", () => {
  const t = probTriple({ available: true, no_trade: 0.9, buy: 0.9, sell: 0.9 });
  assert.deepEqual(
    t.rows.map((r) => r.value),
    [0.9, 0.9, 0.9],
  );
  assert.ok(Math.abs(t.sum - 2.7) < 1e-12);
  // geometry clamps, the number does not
  assert.ok(t.rows.every((r) => r.fraction <= 1));
});

test("probTriple: out-of-range fraction is clamped for geometry only", () => {
  const t = probTriple({ available: true, no_trade: 1.4, buy: -0.2, sell: 0.5 });
  assert.equal(t.rows[0].fraction, 1);
  assert.equal(t.rows[1].fraction, 0);
  assert.equal(t.rows[0].value, 1.4);
});

test("probTriple: timestamp passthrough", () => {
  const t = probTriple({ available: true, no_trade: 1, buy: 0, sell: 0, inference_timestamp: "2026-09-13T03:00:00+00:00" });
  assert.equal(t.inferenceTimestamp, "2026-09-13T03:00:00+00:00");
});

// ---------------------------------------------------------------------------
// latencySplit
// ---------------------------------------------------------------------------

const MODEL_BASE = {
  available: true,
  model_id: "m",
  model_version: "v",
  architecture: "mlp",
  artifact_path: "/p",
  feature_schema_id: "scalp_v3",
  feature_dimension: 70,
  scaler_ready: true,
  latency_ms: null,
  latency_breakdown: null,
};

test("latencySplit: flattened ModelMeta fields are read straight", () => {
  const s = latencySplit({ ...MODEL_BASE, feature_ms: 1.1, model_forward_ms: 2.2, e2e_ms: 4.4, latency_ms: 4.4 });
  assert.equal(s.feature_ms, 1.1);
  assert.equal(s.model_forward_ms, 2.2);
  assert.equal(s.e2e_ms, 4.4);
  assert.equal(s.latency_ms, 4.4);
  assert.equal(s.hasData, true);
  assert.equal(s.partial, false);
});

test("latencySplit: nulls are KEPT (a missing stage is not 0 ms)", () => {
  const s = latencySplit({ ...MODEL_BASE, feature_ms: null, model_forward_ms: null, e2e_ms: null });
  assert.equal(s.feature_ms, null);
  assert.equal(s.model_forward_ms, null);
  assert.equal(s.e2e_ms, null);
  assert.equal(s.hasData, false);
});

test("latencySplit: breakdown fallback, incl. tracer key model_ms -> model_forward_ms", () => {
  const s = latencySplit({
    ...MODEL_BASE,
    latency_breakdown: { feature_ms: 0.7, model_ms: 1.9, e2e_ms: 5.0, scaling_ms: 0.2, queue_ms: null },
  });
  assert.equal(s.feature_ms, 0.7);
  assert.equal(s.model_forward_ms, 1.9);
  assert.equal(s.e2e_ms, 5.0);
  const extras = Object.fromEntries(s.stages.map((x) => [x.key, x.value]));
  assert.equal(extras.scaling_ms, 0.2);
  assert.equal(extras.queue_ms, null);
});

test("latencySplit: flattened field wins over the breakdown", () => {
  const s = latencySplit({
    ...MODEL_BASE,
    model_forward_ms: 3.0,
    latency_breakdown: { model_ms: 1.0, e2e_ms: 6.0 },
  });
  assert.equal(s.model_forward_ms, 3.0);
  assert.equal(s.e2e_ms, 6.0);
});

test("latencySplit: partial payload flags itself instead of hiding the hole", () => {
  const s = latencySplit({ ...MODEL_BASE, feature_ms: 1.0, model_forward_ms: null, e2e_ms: 5.0 });
  assert.equal(s.partial, true);
  assert.equal(s.hasData, true);
});

test("latencySplit: absent model / empty breakdown never fabricates", () => {
  for (const model of [undefined, null, {}]) {
    const s = latencySplit(model);
    assert.deepEqual([s.feature_ms, s.model_forward_ms, s.e2e_ms, s.latency_ms], [null, null, null, null]);
    assert.equal(s.hasData, false);
    assert.equal(s.partial, false);
    assert.deepEqual(s.stages, []);
  }
});

test("latencySplit: non-numeric breakdown values are dropped, not zeroed", () => {
  const s = latencySplit({ ...MODEL_BASE, latency_breakdown: { feature_ms: "n/a", e2e_ms: null } });
  assert.equal(s.feature_ms, null);
  assert.equal(s.e2e_ms, null);
});

// ---------------------------------------------------------------------------
// disagreement taxonomy
// ---------------------------------------------------------------------------

const OBS = (over = {}) => ({
  observation_id: "o",
  timestamp: "2026-09-13T03:00:00+00:00",
  champion_action: "NO_TRADE",
  shadow_action: "NO_TRADE",
  champion_confidence: 0.9,
  shadow_confidence: 0.8,
  disagreement: "AGREEMENT",
  regime: "RANGE",
  news_state: "QUIET",
  liquidity_state: "BALANCED",
  outcome: "PENDING",
  ...over,
});

test("disagreementTally: counts by the backend string ONLY", () => {
  const tally = disagreementTally([
    OBS(),
    OBS(),
    OBS({ disagreement: "ACTION_DISAGREEMENT" }),
    OBS({ disagreement: "BUY_VS_SELL" }),
    OBS({ disagreement: "BUY_VS_SELL" }),
  ]);
  const map = Object.fromEntries(tally.buckets.map((b) => [b.class, b.count]));
  assert.deepEqual(map, { AGREEMENT: 2, BUY_VS_SELL: 2, ACTION_DISAGREEMENT: 1 });
  assert.equal(tally.total, 5);
  assert.equal(tally.agreements, 2);
  assert.equal(tally.disagreements, 3);
  assert.equal(tally.unclassified, 0);
});

test("disagreementTally: blank/missing class lands in UNKNOWN, not a bucket", () => {
  const tally = disagreementTally([
    OBS({ disagreement: "" }),
    OBS({ disagreement: undefined }),
    OBS({ disagreement: AGREEMENT_CLASS }),
  ]);
  const map = Object.fromEntries(tally.buckets.map((b) => [b.class, b.count]));
  assert.equal(map[UNKNOWN], 2);
  assert.equal(map[AGREEMENT_CLASS], 1);
  assert.equal(tally.disagreements, 0); // unknown is neither agree nor disagree
  assert.equal(tally.unclassified, 2);
});

test("disagreementTally: an out-of-taxonomy class stays verbatim + unknown level", () => {
  const tally = disagreementTally([OBS({ disagreement: "SOMETHING_NEW" })]);
  assert.equal(tally.buckets[0].class, "SOMETHING_NEW");
  assert.equal(tally.buckets[0].level, "unknown");
});

test("disagreementTally: empty / null payload has no rate to report", () => {
  for (const payload of [[], null, undefined]) {
    const tally = disagreementTally(payload);
    assert.deepEqual(tally.buckets, []);
    assert.equal(tally.total, 0);
    assert.equal(agreementRateText(tally), "—");
  }
});

test("tallyFromCounts: normalizes the backend store.disagreement_counts histogram", () => {
  const tally = tallyFromCounts({
    AGREEMENT: 40,
    CONFIDENCE_DIVERGENCE: 3,
    "": 1,
    HIGH_CONFIDENCE_DISAGREEMENT: 2,
    broken: "n/a",
  });
  assert.equal(tally.total, 46); // non-numeric entry excluded, not counted as 0
  assert.equal(tally.agreements, 40);
  assert.equal(tally.unclassified, 1);
  assert.equal(agreementRateText(tally), `${((40 / 46) * 100).toFixed(1)}%`);
  assert.deepEqual(
    tally.buckets.map((b) => b.class),
    ["AGREEMENT", "CONFIDENCE_DIVERGENCE", "HIGH_CONFIDENCE_DISAGREEMENT", UNKNOWN],
  );
  assert.deepEqual(tallyFromCounts(null).buckets, []);
});

test("disagreementBadgeLevel: every backend class maps to an existing badge level", () => {
  const levels = new Set(["good", "warn", "bad", "neutral", "unknown"]);
  assert.equal(disagreementBadgeLevel(AGREEMENT_CLASS), "good");
  assert.equal(disagreementBadgeLevel("BUY_VS_SELL"), "bad");
  assert.equal(disagreementBadgeLevel("CONFIDENCE_DIVERGENCE"), "warn");
  assert.equal(disagreementBadgeLevel("NOT_IN_TAXONOMY"), "unknown");
  assert.equal(disagreementBadgeLevel(undefined), "unknown");
  for (const klass of DISAGREEMENT_CLASSES) {
    assert.ok(levels.has(disagreementBadgeLevel(klass)), `class ${klass}`);
  }
});

test("shadowRows: verbatim strings, null confidences preserved, limit applied", () => {
  const rows = shadowRows(
    [
      OBS({ observation_id: "a" }),
      OBS({
        observation_id: "b",
        champion_action: "BUY_MARKET",
        shadow_action: "NO_TRADE",
        disagreement: "CHAMPION_BUYS_SHADOW_NO_TRADE",
        champion_confidence: null,
        shadow_confidence: null,
        news_state: "",
      }),
    ],
    5,
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[1].championAction, "BUY_MARKET");
  assert.equal(rows[1].shadowAction, "NO_TRADE");
  assert.equal(rows[1].actionDiffers, true);
  assert.equal(rows[0].actionDiffers, false);
  assert.equal(rows[1].championConfidence, null);
  assert.equal(rows[1].newsState, UNKNOWN);
  assert.equal(rows[1].disagreement, "CHAMPION_BUYS_SHADOW_NO_TRADE");
  assert.equal(rows[1].level, "bad");
  assert.equal(rows[1].timeText, "2026-09-13T03:00:00");
  assert.equal(shadowRows([OBS(), OBS()], 1).length, 1);
  assert.deepEqual(shadowRows(null, 3), []);
});

test("agreementRateText: rate over what the backend classified only", () => {
  const tally = disagreementTally([OBS(), OBS(), OBS({ disagreement: "BUY_VS_SELL" })]);
  assert.equal(agreementRateText(tally), "66.7%");
});

// ---------------------------------------------------------------------------
// reason wording — reused from lib/signal, invented nowhere
// ---------------------------------------------------------------------------

test("reasonCopy: known codes reuse the REASONS map", () => {
  for (const code of Object.keys(REASONS)) {
    const copy = reasonCopy(code);
    assert.equal(copy.known, true, code);
    assert.equal(copy.simple, REASONS[code].simple, code);
    assert.equal(copy.detail, REASONS[code].detail, code);
    assert.equal(copy.code, code);
  }
});

test("reasonCopy: unknown code falls back to the raw code, never prose", () => {
  const copy = reasonCopy("SOME_BACKEND_CODE_NOT_MAPPED");
  assert.equal(copy.known, false);
  assert.equal(copy.simple, null);
  assert.equal(copy.detail, "SOME_BACKEND_CODE_NOT_MAPPED");
});

test("reasonCopy: blank/missing reason renders UNKNOWN", () => {
  for (const raw of [null, undefined, ""]) {
    const copy = reasonCopy(raw);
    assert.equal(copy.simple, null);
    assert.equal(copy.detail, UNKNOWN);
  }
});

// ---------------------------------------------------------------------------
// module-level safety invariants (source scan of the shipped files)
// ---------------------------------------------------------------------------

const readSource = async (rel) => {
  const { readFile } = await import("node:fs/promises");
  return readFile(new URL(`../../frontend/src/${rel}`, import.meta.url), "utf8");
};

test("SAFETY: mlVizMath imports nothing that can reach the network", async () => {
  const src = await readSource("lib/mlVizMath.ts");
  for (const banned of ["fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "axios"]) {
    assert.ok(!src.includes(banned), `mlVizMath must not use ${banned}`);
  }
  const imports = src.match(/^import .*$/gm) ?? [];
  for (const line of imports) {
    assert.ok(
      /from "@\/(types\/domain|lib\/(format|signal))"/.test(line),
      `unexpected import: ${line}`,
    );
  }
});

test("SAFETY: MLViz.tsx is props-only — no I/O, no timers, no verdict verbs", async () => {
  const src = await readSource("components/pro/MLViz.tsx");
  for (const banned of [
    "fetch(",
    "XMLHttpRequest",
    "useQuery",
    "useMutation",
    "mlApi",
    "setTimeout",
    "setInterval",
    "EventSource",
    "localStorage",
    "sessionStorage",
  ]) {
    assert.ok(!src.includes(banned), `MLViz must not use ${banned}`);
  }
  // the suite describes state; it must never NAME a health verdict of its own
  for (const verb of [">HEALTHY<", ">SAFE<", ">BLOCKED<", ">READY<"]) {
    assert.ok(!src.includes(verb), `MLViz must not hardcode ${verb}`);
  }
});

test("SAFETY: pro-ml.css introduces no external font/CDN reference", async () => {
  const { readFile } = await import("node:fs/promises");
  const css = await readFile(
    new URL("../../frontend/src/components/pro/pro-ml.css", import.meta.url),
    "utf8",
  );
  assert.ok(!/https?:\/\//.test(css), "no remote URLs in pro-ml.css");
  assert.ok(!/@import/.test(css), "no CSS @import (theme vars are inherited)");
});
