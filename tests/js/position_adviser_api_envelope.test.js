/**
 * Position Adviser API envelope contract — regression gate for BUG-544.
 *
 * Run from the worktree ROOT:  node tests/js/position_adviser_api_envelope.test.js
 *
 * BUG-544 symptom: /position-adviser rendered a blank page with
 *   "Cannot read properties of undefined (reading 'join')"
 *   at TensorInspectorPanel (PositionAdviserPage chunk, col ~4435).
 *
 * Root cause: the tensor route answers a LEGACY wrapper —
 *   {"status": "OK", "tensor": {...}}
 * — it is NOT a v1 envelope, so `getLegacy` (which returns the raw body for
 * legacy routes, unwrapping nothing) handed the WHOLE envelope to a state
 * variable typed as the INNER payload. `tensor.tensor_shape` was therefore
 * undefined, and `.join(", ")` threw during render.
 *
 * This suite exercises the REAL production `api.ts` (no re-implemented copy)
 * through the same transport seam the page uses, and pins:
 *   1. the tensor fetcher UNWRAPS `.tensor` and returns the inner payload;
 *   2. a route that answers the wrapper with no inner object yields null —
 *      never an invented half-tensor;
 *   3. the decision fetcher keeps that route's top-level payload (it has no
 *      inner wrapper);
 *   4. the panels no longer contain unguarded `.join()`/`.map()` over backend
 *      fields — a shape drift must degrade to a dash, not blank the console.
 */
"use strict";

const { registerHooks } = require("node:module");
const { pathToFileURL } = require("node:url");
const path = require("node:path");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { test } = require("node:test");

const REPO = path.join(__dirname, "..", "..");
const SRC = path.join(REPO, "frontend", "src");

registerHooks({
  resolve(specifier, context, nextResolve) {
    let spec = specifier;
    if (spec.startsWith("@/"))
      spec = pathToFileURL(path.join(SRC, spec.slice(2))).href;
    const bare = spec.startsWith("file:") ? (spec.split("/").pop() ?? "") : spec;
    const isRel =
      spec.startsWith("./") || spec.startsWith("../") || spec.startsWith("file:");
    if (isRel && !/\.[a-z]+$/i.test(bare)) {
      for (const ext of [".ts", ".tsx", "/index.ts"]) {
        try {
          return nextResolve(spec + ext, context);
        } catch {
          /* try the next extension */
        }
      }
    }
    return nextResolve(spec, context);
  },
});

const mod = (rel) =>
  pathToFileURL(path.join(SRC, "features", "position-adviser", rel)).href;

// The real backend shapes, captured from the live route (verified against a
// running engine: /api/position-adviser/tensor/current-input 200).
const TENSOR_PAYLOAD = {
  status: "OK",
  model_id: "pos_adviser_tune_1790590611_2024_47827",
  feature_schema: "adviser_v1",
  feature_dimension: 12,
  sequence_length: 1,
  device: "cpu",
  dtype: "float32",
  inference_timestamp: "2026-09-28T10:28:57.875317+00:00",
  source: "last_sample",
  tensor_shape: [1, 12],
  features: [
    { index: 0, name: "unrealized_pnl_r", raw: -0.39, normalized: -0.81, finite: true, age_sec: 74.1 },
    { index: 1, name: "current_r_net", raw: -0.39, normalized: -0.81, finite: true, age_sec: 74.1 },
  ],
  validity: {
    raw_dim: 12,
    normalized_dim: 12,
    model_input_dim: 12,
    dims_match: true,
    nan_count: 0,
    inf_count: 0,
    zero_default_count: 1,
    raw_finite: true,
    normalized_finite: true,
    saturated_count: 0,
  },
  position: null,
};

const TENSOR_ENVELOPE = { status: "OK", tensor: TENSOR_PAYLOAD };

const DECISION_PAYLOAD = {
  status: "OK",
  position: {
    ticket: 152691258462,
    snapshot_id: "90d102b06c258f88",
    snapshot_age_ms: 0.0,
    inspected_state: { unrealized_pnl_r: -0.39 },
  },
  model: { model_id: "pa_001", model_dimension: 12, activation: "LIVE" },
  decision: {
    action: "REDUCE",
    confidence: 0.0341,
    probabilities: { KEEP: 0.2094, CLOSE: 0.394, REDUCE: 0.3966 },
    evaluated_at: "2026-09-28T10:27:43.704340+00:00",
    applied: false,
    not_applied_reason: "KEEP verdict or below application thresholds",
  },
  latency: { total_ms: 1.5, feature_ms: 0.4, inference_ms: 0.7 },
};

// ------------------------------------------------- document stub
// `@/types/api` -> `@/lib/errorMessages` -> `@/stores/i18nStore` is a transitive
// dependency of the transport chain, and the i18n store touches
// `document.documentElement` at module init. The module-level guards below are
// the minimum that makes the store initialisable under node:test (this suite
// never renders, so a full DOM is unnecessary).
if (!globalThis.document) {
  const el = () => ({
    style: {},
    setAttribute() {},
    getAttribute() {
      return null;
    },
    removeAttribute() {},
    dataset: {},
  });
  globalThis.document = {
    documentElement: el(),
    body: el(),
    head: el(),
    createElement: () => el(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return true;
    },
    getElementById: () => null,
  };
}
if (!globalThis.CustomEvent) {
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, init) {
      this.type = type;
      this.detail = init?.detail ?? null;
    }
  };
}

// ------------------------------------------------- transport seam (the real one)
// positionAdviserApi.tensor/decision are the ONLY surface the page state uses.
// We drive them with a stubbed transport fetch and assert on what lands in state.
async function withResponses(map, fn) {
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(typeof input === "string" ? input : input?.url ?? "");
    const found = Object.keys(map).find((k) => url.includes(k));
    if (!found) throw new Error(`unexpected fetch: ${url}`);
    const body = map[found];
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  };
  try {
    return await fn();
  } finally {
    globalThis.fetch = origFetch;
  }
}

test("tensor fetcher unwraps the legacy {status, tensor} envelope", async () => {
  await withResponses({ "/tensor/current-input": TENSOR_ENVELOPE }, async () => {
    const { positionAdviserApi } = await import(mod("api"));
    const got = await positionAdviserApi.tensor();
    assert.equal(got?.model_id, TENSOR_PAYLOAD.model_id, "model_id from the inner payload");
    assert.deepEqual(got?.tensor_shape, [1, 12], "tensor_shape is the inner array");
    assert.equal(Array.isArray(got?.features), true, "features array survives");
  });
});

test("tensor fetcher maps a wrapper with no inner object to null (no invented tensor)", async () => {
  await withResponses({ "/tensor/current-input": { status: "OK" } }, async () => {
    const { positionAdviserApi } = await import(mod("api"));
    const got = await positionAdviserApi.tensor();
    assert.equal(got, null, "absent payload is null, never a fabricated half-object");
  });
});

test("decision fetcher returns that route's top-level payload (no inner wrapper)", async () => {
  await withResponses({ "/decision/current": DECISION_PAYLOAD }, async () => {
    const { positionAdviserApi } = await import(mod("api"));
    const got = await positionAdviserApi.decision();
    assert.equal(got?.decision?.action, "REDUCE");
    assert.equal(got?.model?.model_id, "pa_001");
    assert.deepEqual(got?.latency?.total_ms, 1.5);
  });
});

// ------------------------------------------------- source-level panel guards
// The panels are TSX (React + i18n), so a node:test import cannot execute their
// render path. The crash class is unguarded property access over a backend
// field, so we pin the GUARD PRESENCE in source: every backend-derived field
// that can be absent must be reached through an Array/optional guard, and the
// crash expression (`tensor.tensor_shape.join`) must not appear.

/** Read a source file as one flat string for literal searches. */
function src(rel) {
  return fs.readFileSync(path.join(SRC, rel), "utf8");
}

test("TensorInspectorPanel never calls .join() on an unguarded backend shape", () => {
  const s = src("features/position-adviser/ui/TensorInspectorPanel.tsx");
  assert.ok(
    !s.includes("tensor.tensor_shape.join"),
    "the BUG-544 crash expression must be gone (shape is guarded before join)",
  );
  // The guard must exist and feed both the shape line and the table body.
  assert.ok(/const shape = Array\.isArray\(tensor\.tensor_shape\)/.test(s), "shape guard present");
  assert.ok(/const features = Array\.isArray\(tensor\.features\)/.test(s), "features guard present");
  assert.ok(s.includes("shape.join("), "shape join now reads the guarded local");
  assert.ok(s.includes("features.map("), "table body maps the guarded local");
});

test("TensorInspectorPanel validity counters are absent-safe", () => {
  const s = src("features/position-adviser/ui/TensorInspectorPanel.tsx");
  // `v?.nan_count ?? 0` — a missing validity block renders zeros, not a throw.
  assert.ok(s.includes("v?.nan_count"), "nan_count guarded");
  assert.ok(s.includes("v?.inf_count"), "inf_count guarded");
  assert.ok(s.includes("v?.zero_default_count"), "zero_default_count guarded");
  assert.ok(s.includes("v?.saturated_count"), "saturated_count guarded");
  assert.ok(!/\bv\.nan_count\b/.test(s), "no unguarded v.nan_count remains");
});

test("DecisionTracePanel guards decision/probabilities/latency", () => {
  const s = src("features/position-adviser/ui/DecisionTracePanel.tsx");
  // The pre-fix crash class: Object.entries(d.probabilities) with no guard.
  // The fix wraps it in a ternary on d?.probabilities.
  assert.ok(
    !/^\s*const probs = Object\.entries\(d\.probabilities\)/m.test(s),
    "probabilities must be reached through a guard, not unguarded Object.entries",
  );
  assert.ok(/d\?\.probabilities/.test(s), "probabilities guard present");
  assert.ok(/const lat = trace\.latency/.test(s), "latency local extracted");
  assert.ok(/lat\?\.total_ms != null/.test(s), "total_ms null-safe");
  assert.ok(/lat\?\.feature_ms != null/.test(s), "feature_ms null-safe");
  assert.ok(/lat\?\.inference_ms != null/.test(s), "inference_ms null-safe");
  assert.ok(s.includes("trace.position?.ticket"), "position ticket guarded");
  assert.ok(s.includes("trace.model?.model_id"), "model id guarded");
});
