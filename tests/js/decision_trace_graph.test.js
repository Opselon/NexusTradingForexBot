/**
 * Decision Trace — client-side honesty contract suite (Phases 9-11).
 *
 * Run:  node tests/js/decision_trace_graph.test.mjs
 *   or: node --test tests/js/decision_trace_graph.test.mjs
 *
 * Node 24 strips TS types; the only import in traceGraph.ts is
 * `import type`, which is removed before resolution — no bundler needed.
 *
 * These tests pin the rules the master prompt makes non-negotiable:
 *   §02  no invented data      — absent evidence renders UNKNOWN, never a
 *                                fabricated value
 *   §13  no hardcoded topology — a node exists ONLY because an event proved
 *                                its stage; an unseen stage produces no node
 *   §35  no verdict from timing — latency absence never implies failure
 *   §43  coalescing never drops terminal evidence
 *   §45  integrity counters are fact-based
 *   §50  unmapped future stages are flagged, not hidden
 *   §65  compare reports field changes only, no scoring
 *   §77  mt5 reachability is proven by a response event, nothing else
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  UNKNOWN,
  NOT_OBSERVED,
  buildGraph,
  buildTimeline,
  checkIntegrity,
  coalesceVisual,
  compareBundles,
  explainDecision,
  isUnmapped,
  latencyBudget,
  modelContractEvidence,
  mt5Reachability,
  observedStages,
} from "../../frontend/src/features/decision-trace/traceGraph.ts";
import { layoutCanvas } from "../../frontend/src/features/decision-trace/traceLayout.ts";

// ---------------------------------------------------------------- helpers
let seq = 0;
function ev(stage, detail = {}, opts = {}) {
  seq += 1;
  return {
    event_id: `EV-${seq}`,
    trace_id: opts.trace_id ?? "TR-1",
    parent_event_id: opts.parent ?? null,
    sequence: seq,
    timestamp: `2026-09-23T10:00:${String(seq).padStart(2, "0")}.000Z`,
    monotonic_ns: opts.mono ?? seq * 1_000_000,
    stage,
    component: null,
    event_type: opts.event_type ?? null,
    status: opts.status ?? null,
    symbol: "EURUSD",
    decision_id: opts.decision_id ?? "EXEC-1",
    latency_us: opts.latency_us ?? null,
    terminal: opts.terminal ?? false,
    unmapped: opts.unmapped ?? false,
    provenance_gap: opts.provenance_gap ?? false,
    detail,
    trace_schema_version: 1,
  };
}

const bundle = (events, summary = null) => ({
  query: "EXEC-1",
  trace_id: "TR-1",
  decision_id: "EXEC-1",
  events,
  summary,
  found: true,
});

// ===========================================================================
// §13 — the graph is DERIVED. A stage with no event can never appear.
// ===========================================================================
test("§13: no events -> empty graph (no hardcoded pipeline)", () => {
  const g = buildGraph([]);
  assert.equal(g.nodes.length, 0, "empty event stream must yield zero nodes");
  assert.equal(g.edges.length, 0);
  assert.equal(g.root, null);
  assert.equal(layoutCanvas(g).nodes.length, 0);
});

test("§13: only observed stages become nodes, in observation order", () => {
  const events = [
    ev("MARKET", { symbol: "EURUSD" }),
    ev("FEATURES", { count: 50 }, { latency_us: 300 }),
    ev("RISK", { verdict: "REJECT", reason: "exposure" }, { latency_us: 120 }),
  ];
  const g = buildGraph(events);
  const stages = g.nodes.map((n) => n.stage);
  assert.deepEqual(stages, ["MARKET", "FEATURES", "RISK"]);
  // a stage that never ran must NOT exist as a node
  assert.ok(!stages.includes("MT5"), "MT5 must not exist without evidence");
  assert.ok(!stages.includes("DECISION"), "DECISION must not exist without evidence");
  assert.equal(g.root, "MARKET");
});

test("§66: an unknown future stage still gets a node and is flagged UNMAPPED", () => {
  const events = [ev("MARKET"), ev("QUANTUM_GATE", { verdict: "PASS" })];
  const g = buildGraph(events);
  const n = g.nodes.find((x) => x.stage === "QUANTUM_GATE");
  assert.ok(n, "a new runtime stage must render as a node (never silently hidden)");
  assert.equal(isUnmapped("QUANTUM_GATE"), true);
  assert.equal(isUnmapped("MARKET"), false);
});

// ===========================================================================
// §02 — absent evidence renders UNKNOWN, never a manufactured value
// ===========================================================================
test("§02: model contract with no inference evidence is UNKNOWN", () => {
  const c = modelContractEvidence([ev("MARKET"), ev("FEATURES")]);
  assert.equal(c.contract, UNKNOWN);
  assert.equal(c.source, "none");
  assert.equal(c.modelId, null);
  assert.equal(c.featureDim, null);
});

test("§02: 50D vs 70D is read from the runtime, never assumed", () => {
  const e50 = ev("INFERENCE", { model_id: "scalp_v1", feature_dim: 50 });
  const e70 = ev("INFERENCE", { model_id: "scalp_v2", effective_feature_dim: 70 });
  assert.equal(modelContractEvidence([e50]).contract, "50D");
  assert.equal(modelContractEvidence([e70]).contract, "70D");
});

test("§02: an unknown dimension is NOT coerced to 50D or 70D", () => {
  const e = ev("INFERENCE", { model_id: "exp", feature_dim: 64 });
  const c = modelContractEvidence([e]);
  assert.equal(c.contract, "64D");
  assert.ok(c.contract !== "50D" && c.contract !== "70D");
});

test("§35: latency absence never produces a verdict", () => {
  const events = [ev("MARKET"), ev("RISK", { verdict: "REJECT" })]; // no latency_us
  const b = latencyBudget(events);
  assert.equal(b.total_us, null, "no timing => no budget, not a zero budget");
  assert.equal(b.byStage.length, 0);
});

test("§26: latency budget sums only observed timing", () => {
  const events = [
    ev("MARKET", {}, { latency_us: 1000 }),
    ev("FEATURES", {}, { latency_us: 3000 }),
    ev("RISK", {}, { latency_us: null }),
  ];
  const b = latencyBudget(events);
  assert.equal(b.total_us, 4000);
  assert.equal(b.byStage.length, 2);
});

// ===========================================================================
// §77 — MT5 reachability is proven ONLY by a response event
// ===========================================================================
test("§77: without a gateway response, MT5 is NOT reached", () => {
  const events = [ev("MARKET"), ev("EXECUTION", { action: "dispatch" })];
  const m = mt5Reachability(events);
  assert.equal(m.reached, false, "an order dispatch is not broker-reached evidence");
  assert.equal(m.gateway, null);
});

test("§77: a paper gateway response counts as reached with gateway=paper", () => {
  const events = [
    ev("MARKET"),
    ev("GATEWAY", { gateway: "paper", result: "filled" }, { event_type: "GATEWAY_RESPONSE" }),
  ];
  const m = mt5Reachability(events);
  assert.equal(m.reached, true);
  assert.equal(m.gateway, "paper");
});

test("§77: a real MT5 response carries the ticket", () => {
  const events = [
    ev("MARKET"),
    ev("MT5", { gateway: "remote_mt5", ticket: 12345 }, { event_type: "MT5_RESPONSE" }),
  ];
  const m = mt5Reachability(events);
  assert.equal(m.reached, true);
  assert.equal(m.gateway, "remote_mt5");
  assert.equal(m.ticket, 12345);
});

// ===========================================================================
// §43 — visual coalescing never drops terminal or verdict evidence
// ===========================================================================
test("§43: coalescing drops only redundant PROGRESS frames", () => {
  const events = [
    ev("EXECUTION", { n: 1 }, { event_type: "EXECUTION_PROGRESS" }),
    ev("EXECUTION", { n: 2 }, { event_type: "EXECUTION_PROGRESS" }),
    ev("EXECUTION", { result: "filled" }, { event_type: "EXECUTION_RESULT", terminal: true }),
  ];
  const out = coalesceVisual(events);
  assert.equal(out.coalesced, 1, "one redundant progress frame coalesced");
  assert.equal(out.events.length, 2, "the terminal frame survived");
  assert.equal(out.events[1].event_type, "EXECUTION_RESULT");
});

test("§43: terminal events are never coalesced even when consecutive", () => {
  const events = [
    ev("ORDER", { status: "FILLED" }, { terminal: true }),
    ev("ORDER", { status: "FILLED" }, { terminal: true }),
  ];
  const out = coalesceVisual(events);
  assert.equal(out.coalesced, 0);
  assert.equal(out.events.length, 2);
});

// ===========================================================================
// §34 / §26 — timeline offsets are monotonic and anchored to the first event
// ===========================================================================
test("§34: timeline is anchored to the first event and ordered", () => {
  const events = [
    ev("MARKET", {}, { mono: 0 }),
    ev("RISK", {}, { mono: 5_000_000 }),
    ev("MT5", {}, { mono: 12_000_000 }),
  ];
  const t = buildTimeline(events);
  assert.equal(t.length, 3);
  assert.equal(t[0].offset_us, 0);
  assert.equal(t[1].offset_us, 5000);
  assert.equal(t[2].offset_us, 12000);
});

test("§34: a trace with no monotonic clock still yields a timeline", () => {
  const events = [ev("MARKET", {}, { mono: null }), ev("RISK", {}, { mono: null })];
  const t = buildTimeline(events);
  assert.equal(t.length, 2);
  assert.equal(t[0].offset_us, 0);
});

// ===========================================================================
// §45 — client integrity checks are fact-based
// ===========================================================================
test("§45: a duplicate event id is counted", () => {
  const events = [ev("MARKET"), ev("MARKET", {}, { trace_id: "TR-2" })];
  // force a shared id
  events[1].event_id = events[0].event_id;
  const c = checkIntegrity(events);
  assert.ok(c.duplicateEvents >= 1);
  assert.ok(c.total >= 1);
});

test("§45: a sequence gap is counted, not silently healed", () => {
  const events = [ev("MARKET"), ev("FEATURES")];
  events[1].sequence = events[0].sequence + 5;
  const c = checkIntegrity(events);
  assert.ok(c.missingSequence >= 1);
});

test("§45: a clean trace reports zero integrity problems", () => {
  const m = ev("MARKET");
  const f = ev("FEATURES", {}, { parent: m.event_id });
  const d = ev("DECISION", {}, { parent: f.event_id });
  const c = checkIntegrity([m, f, d]);
  assert.equal(c.total, 0);
});

// ===========================================================================
// §65 — compare reports field changes only; no winner, no score
// ===========================================================================
test("§65: compare is a factual diff with no scoring", () => {
  const a = bundle([ev("MARKET")], { decision_id: "EXEC-1", model_id: "m1", regime: "TRENDING" });
  const b = bundle([ev("MARKET")], { decision_id: "EXEC-2", model_id: "m1", regime: "RANGING" });
  const d = compareBundles(a, b);
  assert.equal(d.identical, false);
  const fields = d.changes.map((c) => c.field);
  assert.ok(fields.includes("regime"), "the changed regime is reported");
  assert.ok(!fields.includes("model_id"), "an unchanged field is not reported");
});

// ===========================================================================
// §30-35 — the WHY engine explains from evidence, never from timing
// ===========================================================================
test("§30: a rejected trace names the rejection stage and reason", () => {
  const m = ev("MARKET");
  const i = ev("INFERENCE", { model_id: "scalp_v1", feature_dim: 50 }, { parent: m.event_id });
  const r = ev("RISK", { verdict: "REJECT", reason: "max exposure exceeded" }, { parent: i.event_id, status: "REJECT" });
  const why = explainDecision(bundle([m, i, r]));
  assert.equal(why.verdict, "REJECT");
  assert.equal(why.rejectionStage, "RISK");
  const stepReasons = why.steps.map((s) => s.reason ?? "").join(" ");
  assert.ok(
    (why.cause ?? "").includes("max exposure exceeded") ||
      stepReasons.includes("max exposure exceeded"),
    "the observed rejection reason is surfaced verbatim",
  );
  assert.equal(why.evidenceCount, 3);
});

test("§30: a trace with no rejection is not labelled rejected", () => {
  const m = ev("MARKET");
  const d = ev("DECISION", { action: "NO_TRADE" }, { parent: m.event_id, status: "NO_TRADE" });
  const why = explainDecision(bundle([m, d]));
  assert.notEqual(why.verdict, "REJECT");
  assert.equal(why.rejectionStage, null);
});

test("§51: observedStages lists exactly what ran", () => {
  const events = [ev("MARKET"), ev("RISK")];
  assert.deepEqual(observedStages(events), ["MARKET", "RISK"]);
});

// ===========================================================================
// §64-71 — layout: every positioned node traces back to a graph node
// ===========================================================================
test("§64: layout positions exactly the derived nodes", () => {
  const events = [ev("MARKET"), ev("DECISION", {}, { parent: "EV-1" }), ev("RISK", {}, { parent: "EV-2" })];
  const g = buildGraph(events);
  const L = layoutCanvas(g);
  assert.equal(L.nodes.length, g.nodes.length, "layout never invents a node");
  assert.equal(L.edges.length, g.edges.length, "layout never invents an edge");
  for (const n of L.nodes) {
    assert.equal(typeof n.x, "number");
    assert.equal(typeof n.y, "number");
    assert.ok(n.x >= 0 && n.y >= 0);
  }
  assert.ok(L.width > 0 && L.height > 0);
});

test("§69: layout is deterministic across calls", () => {
  const events = [ev("MARKET"), ev("RISK", {}, { parent: "EV-1" })];
  const g = buildGraph(events);
  const a = layoutCanvas(g);
  const b = layoutCanvas(g);
  assert.deepEqual(
    a.nodes.map((n) => [n.id, n.x, n.y]),
    b.nodes.map((n) => [n.id, n.x, n.y]),
  );
});

test("§66: an empty graph layouts to nothing, never a placeholder node", () => {
  const L = layoutCanvas(buildGraph([]));
  assert.equal(L.nodes.length, 0);
  assert.equal(L.width, 0);
});
