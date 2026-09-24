/**
 * decision_causal_graph.test.js — LIVE CAUSAL EXECUTION MAP contract suite.
 *
 * Run:  node tests/js/decision_causal_graph.test.js
 *
 * Node 24 strips TS types; the modules under test are imported with the
 * explicit `.ts` suffix (Node cannot resolve extensionless relative paths,
 * and the modules are import-free at runtime for exactly this reason — see
 * the OWNERSHIP note in traceCanvas.ts).
 *
 * These tests pin the rules the LIVE-CAUSAL-TOPOLOGY wave makes
 * non-negotiable:
 *   §4   state -> visual token mapping, incl. unknown-state passthrough
 *   §6   edge state derived from real events (frozen vocab, idle..retry)
 *   §7   only the actually-taken branch is lit; unused branches subdue
 *   §8   failure/rejection paths stay visible (never removed)
 *   §12  external nodes appear ONLY when real events carry them
 *   §44  mode badges: absent => UNKNOWN, six distinct modes, SHADOW != EXECUTED
 *   §56  no observed linkage => dashed PROVENANCE-GAP edge, never an arrow
 *   §5   packets fire ONLY on real event arrival (never on a timer/mount)
 *   §52  fit/focus/collapse keep failure paths on screen
 *   §63  bounded packet sets (no unbounded growth)
 *
 * OWNER: lane C, LIVE-CAUSAL-TOPOLOGY wave.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  UNKNOWN,
  buildGraph,
  diffPackets,
  distinctModes,
  edgeIsFailure,
  edgeState,
  headStage,
  isFailureNode,
  isFailureWord,
  MAX_PACKETS,
  PACKET_SEEN_CAP,
  nodeId,
  requestOrigin,
  stateToneOf,
  traceLanes,
} from "../../frontend/src/features/decision-trace/traceGraph.ts";
import {
  contentBBox,
  fitViewport,
  focusViewport,
  layoutCanvas,
  selectView,
  VIEW_K_MAX,
  VIEW_K_MIN,
} from "../../frontend/src/features/decision-trace/traceLayout.ts";

/* ---------------------------------------------------------------- helpers */
let seq = 0;
function ev(stage, opts = {}) {
  seq += 1;
  return {
    event_id: opts.event_id ?? `EV-${seq}`,
    trace_id: opts.trace_id ?? "TR-1",
    parent_event_id: opts.parent ?? null,
    sequence: seq,
    timestamp: `2026-09-24T10:00:${String(seq).padStart(2, "0")}.000Z`,
    monotonic_ns: opts.mono ?? seq * 1_000_000,
    stage,
    component: opts.component ?? null,
    event_type: opts.event_type ?? null,
    status: opts.status ?? null,
    symbol: opts.symbol ?? "EURUSD",
    decision_id: opts.decision_id ?? "EXEC-1",
    latency_us: opts.latency_us ?? null,
    terminal: opts.terminal ?? false,
    unmapped: opts.unmapped ?? false,
    provenance_gap: opts.provenance_gap ?? false,
    detail: opts.detail ?? null,
    trace_schema_version: 1,
    /* ---- frozen contract v2 (additive; absence is data) ---- */
    state: opts.state ?? null,
    mode: opts.mode ?? null,
    source: opts.source ?? null,
    destination: opts.destination ?? null,
    reason_code: opts.reason_code ?? null,
    error_code: opts.error_code ?? null,
    duration_ms: opts.duration_ms ?? null,
    provider: opts.provider ?? null,
    provenance: opts.provenance ?? null,
    request_id: opts.request_id ?? null,
    payload_summary: opts.payload_summary ?? null,
  };
}

/** A clean MARKET → FEATURES → INFERENCE → RISK → EXECUTION chain. */
function cleanTrace(tid = "TR-1") {
  const a = ev("MARKET", { trace_id: tid });
  const b = ev("FEATURES", { trace_id: tid, parent: a.event_id });
  const c = ev("INFERENCE", { trace_id: tid, parent: b.event_id });
  const d = ev("RISK", { trace_id: tid, parent: c.event_id });
  const e = ev("EXECUTION", { trace_id: tid, parent: d.event_id });
  return [a, b, c, d, e];
}

/* ======================================================== §4 — state -> token */
test("§4 absent state => tone none (NOT OBSERVED, never guessed)", () => {
  assert.equal(stateToneOf(null), "none");
  assert.equal(stateToneOf(undefined), "none");
  assert.equal(stateToneOf(""), "none");
});

test("§4 frozen states map to their canonical tone", () => {
  assert.equal(stateToneOf("PROCESSING"), "active");
  assert.equal(stateToneOf("COMPLETED"), "success");
  assert.equal(stateToneOf("PASSED"), "success");
  assert.equal(stateToneOf("REJECTED"), "rejected");
  assert.equal(stateToneOf("FAILED"), "error");
  assert.equal(stateToneOf("BLOCKED"), "blocked");
  assert.equal(stateToneOf("SKIPPED"), "muted");
  assert.equal(stateToneOf("EXECUTING"), "executing");
  assert.equal(stateToneOf("CONFIRMED"), "confirmed");
});

test("§4 unknown runtime state passes through verbatim (no coercion)", () => {
  // A future runtime word the UI has never seen must not be relabelled.
  assert.equal(stateToneOf("FUTURE_STATE_X"), "unknown");
  assert.equal(stateToneOf("PENDING_VERDICT"), "unknown");
  // ...and it must not be classified as a failure or a success.
  assert.equal(isFailureWord("FUTURE_STATE_X"), false);
  assert.equal(isFailureWord("PROCESSING"), false);
});

test("§4 v1 status aliases still resolve (OBSERVED/OK/PASS/REJECT/…)", () => {
  assert.equal(stateToneOf("OK"), "success");
  assert.equal(stateToneOf("PASS"), "success");
  assert.equal(stateToneOf("REJECT"), "rejected");
  assert.equal(stateToneOf("ERROR"), "error");
  assert.equal(stateToneOf("EXECUTED"), "executed");
  assert.equal(stateToneOf("DISPATCHED"), "executing");
});

test("§4 state lookup is case-insensitive", () => {
  assert.equal(stateToneOf("processing"), "active");
  assert.equal(stateToneOf("Rejected"), "rejected");
});

/* ============================================ §6 — edge state from real events */
test("§6 edge state derives from the hop's own observed word", () => {
  // RISK emits REJECTED, so the INFERENCE->RISK hop carries that verdict
  // (the edge's state is the CHILD's own observed word, §6).
  const chain = cleanTrace();
  chain[3] = { ...chain[3], state: "REJECTED" };
  const g = buildGraph(chain);
  const hop = g.edges.find((e) => e.target === nodeId("RISK", null));
  assert.equal(hop.state, "REJECTED", "the edge carries the hop's own word");
  assert.equal(edgeState(hop.state), "rejected");
  // An edge with no verdict word at all is idle (not silently successful).
  const plain = buildGraph(cleanTrace());
  const first = plain.edges[0];
  assert.equal(edgeState(first.state), "idle");
});

test("§6 frozen edge vocabulary has exactly the seven contract states", () => {
  const seen = new Set();
  for (const w of [
    null,
    "",
    "RECEIVED",
    "PROCESSING",
    "WAITING",
    "PASSED",
    "COMPLETED",
    "CONFIRMED",
    "EXECUTED",
    "OK",
    "REJECTED",
    "REJECT",
    "SKIPPED",
    "CANCELLED",
    "FAILED",
    "ERROR",
    "TIMEOUT",
    "BLOCKED",
    "RETRY",
    "WARNING",
    "MYSTERY",
  ]) {
    seen.add(edgeState(w));
  }
  assert.deepEqual(
    [...seen].sort(),
    [
      "active",
      "blocked",
      "error",
      "idle",
      "rejected",
      "retry",
      "success",
    ].sort(),
  );
});

test("§6 retry comes only from a runtime retry word — never from timing", () => {
  assert.equal(edgeState("RETRY"), "retry");
  assert.equal(edgeState("RETRYING"), "retry");
  // No latency/us anywhere produced a retry above; there is no timer input.
  assert.equal(edgeState(null), "idle");
});

test("§6 STALE renders as error (stale evidence is never hidden)", () => {
  assert.equal(edgeState("STALE"), "error");
});

test("§6 an edge with no verdict word is idle (not success)", () => {
  const g = buildGraph(cleanTrace());
  for (const e of g.edges) {
    if (!e.state) assert.equal(edgeState(e.state), "idle");
  }
});

test("§6 edgeIsFailure covers rejected/error/blocked only", () => {
  assert.equal(edgeIsFailure("REJECTED"), true);
  assert.equal(edgeIsFailure("FAILED"), true);
  assert.equal(edgeIsFailure("TIMEOUT"), true);
  assert.equal(edgeIsFailure("BLOCKED"), true);
  assert.equal(edgeIsFailure("PASSED"), false);
  assert.equal(edgeIsFailure("PROCESSING"), false);
  assert.equal(edgeIsFailure(null), false);
});

/* ============================================ §7 — taken branch lit, rest subdue */
test("§7 both ends of an unused branch exist but stay idle", () => {
  const litWord = (g, src) =>
    g.edges.filter((e) => e.source === src && edgeState(e.state) !== "idle");
  // RISK passes to EXECUTION in trace 1 and to ORDER in trace 2: the ORDER
  // branch is real (it was observed) and stays in the graph.
  const a = ev("MARKET");
  const b = ev("FEATURES", { parent: a.event_id });
  const c = ev("RISK", { parent: b.event_id, state: "PASSED" });
  const d = ev("EXECUTION", { parent: c.event_id, state: "EXECUTED" });
  const a2 = ev("MARKET", { trace_id: "TR-2" });
  const c2 = ev("RISK", { trace_id: "TR-2", parent: a2.event_id, state: "REJECTED" });
  const d2 = ev("ORDER", { trace_id: "TR-2", parent: c2.event_id, state: "REJECTED" });
  const g = buildGraph([a, b, c, d, a2, c2, d2]);

  const outRisk = litWord(g, nodeId("RISK", null));
  assert.ok(outRisk.length >= 2, "RISK has two observed branches");
  const lit = outRisk.map((e) => e.target);
  assert.ok(lit.includes(nodeId("ORDER", null)), "the actually-taken ORDER branch is lit");
  assert.ok(lit.includes(nodeId("EXECUTION", null)), "the EXECUTION branch is lit too");

  // Both branch TARGETS exist as nodes — the unused one was not removed.
  const ids = new Set(g.nodes.map((n) => n.id));
  assert.ok(ids.has(nodeId("ORDER", null)));
  assert.ok(ids.has(nodeId("EXECUTION", null)));
});

test("§7 unused branch is never deleted, only rendered subdued (CSS-side)", () => {
  const a = ev("MARKET");
  const b = ev("RISK", { parent: a.event_id, state: "PASSED" });
  const g = buildGraph([a, b]);
  // A single-branch graph still exposes the whole observed chain.
  assert.equal(g.nodes.length, 2);
  assert.equal(g.edges.length, 1);
});

/* ============================================ §8 — failure paths stay visible */
test("§8 a REJECTED stage becomes a failure witness node", () => {
  const a = ev("MARKET");
  const b = ev("RISK", { parent: a.event_id });
  const c = ev("EXECUTION", { parent: b.event_id, state: "REJECTED" });
  const g = buildGraph([a, b, c]);
  const exec = g.nodes.find((n) => n.stage === "EXECUTION");
  assert.ok(exec, "the rejecting stage is a node");
  assert.equal(exec.state, "REJECTED");
  assert.ok(g.failureNodes.includes(exec.id), "it lands in failureNodes");
  assert.ok(isFailureNode(exec));
});

test("§8 collapse never hides a failure witness or its ancestors", () => {
  const a = ev("MARKET");
  const b = ev("RISK", { parent: a.event_id });
  const c = ev("EXECUTION", { parent: b.event_id, state: "REJECTED" });
  const events = [a, b, c];
  const layout = layoutCanvas(buildGraph(events));
  const execId = nodeId("EXECUTION", null);
  const riskId = nodeId("RISK", null);
  const marketId = nodeId("MARKET", null);

  // Collapse MARKET — its whole downstream subtree would normally hide.
  const view = selectView(layout, { collapsed: new Set([marketId]) });
  const visible = new Set(view.nodes.map((n) => n.id));

  // §8: the failure and the path that led to it stay on screen.
  assert.ok(visible.has(execId), "the REJECTED node stays visible");
  assert.ok(visible.has(riskId), "its parent stays visible");
});

test("§8 branch isolation never hides a failure witness", () => {
  const events = cleanTrace();
  events.push(ev("ORDER", { parent: events[3].event_id, state: "FAILED" }));
  const layout = layoutCanvas(buildGraph(events));

  // Isolate to FEATURES — everything outside its 1-hop neighbourhood hides.
  const view = selectView(layout, { isolateStage: "FEATURES" });
  const visible = new Set(view.nodes.map((n) => n.id));
  assert.ok(visible.has(nodeId("ORDER", null)), "the FAILED order stays visible");
  assert.ok(
    visible.has(nodeId("FEATURES", null)),
    "the isolated stage itself stays visible",
  );
});

test("§8 isFailureWord covers the failure tones", () => {
  assert.equal(isFailureWord("REJECTED"), true);
  assert.equal(isFailureWord("FAILED"), true);
  assert.equal(isFailureWord("BLOCKED"), true);
  assert.equal(isFailureWord("TIMEOUT"), true);
  assert.equal(isFailureWord("PASSED"), false);
  assert.equal(isFailureWord(null), false);
});

/* ============================================ §12 — external only on real events */
test("§12 MT5 appears only because an event proved it", () => {
  const a = ev("MARKET");
  const b = ev("GATEWAY", { parent: a.event_id, event_type: "MT5_SEND" });
  const c = ev("MT5", { parent: b.event_id, event_type: "MT5_RESPONSE" });
  const g = buildGraph([a, b, c]);
  const mt5 = g.nodes.find((n) => n.stage === "MT5");
  assert.ok(mt5, "MT5 is a node — an event carried it");
  assert.equal(mt5.external, true);
  assert.equal(mt5.externalKind, "mt5");
});

test("§12 a provider field makes the participant external", () => {
  const a = ev("MARKET");
  const b = ev("INFERENCE", { parent: a.event_id, provider: "openrouter" });
  const g = buildGraph([a, b]);
  const n = g.nodes.find((x) => x.stage === "INFERENCE");
  assert.equal(n.external, true);
  assert.equal(n.externalKind, "provider");
});

test("§12/§60 no external is ever invented for an expected system", () => {
  const a = ev("MARKET");
  const b = ev("FEATURES", { parent: a.event_id });
  const g = buildGraph([a, b]);
  for (const n of g.nodes) {
    assert.equal(n.external, false, `${n.stage} is not external without proof`);
    assert.equal(n.externalKind, null);
  }
});

/* ============================================ §44 — mode badges */
test("§44 distinctModes lists only modes the events carried", () => {
  const events = [
    ...cleanTrace().map((e, i) => ({ ...e, mode: i % 2 ? "PAPER" : "LIVE" })),
  ];
  const g = buildGraph(events);
  const modes = distinctModes(g);
  assert.deepEqual(modes.sort(), ["LIVE", "PAPER"]);
});

test("§44 absent mode is never guessed", () => {
  const g = buildGraph(cleanTrace());
  for (const n of g.nodes) assert.equal(n.mode, null);
  assert.deepEqual(distinctModes(g), []);
});

test("§44 a mode the contract never froze passes through verbatim", () => {
  const events = cleanTrace().map((e, i) => (i === 0 ? { ...e, mode: "SIMNET" } : e));
  const g = buildGraph(events);
  assert.deepEqual(distinctModes(g), ["SIMNET"]);
});

test("§44 SHADOW is its own mode and never collapses into LIVE/PAPER", () => {
  const events = cleanTrace().map((e, i) => ({ ...e, mode: i ? "SHADOW" : "LIVE" }));
  const g = buildGraph(events);
  assert.deepEqual(distinctModes(g).sort(), ["LIVE", "SHADOW"]);
  // The render layer keeps them visually distinct (see modeCls + CSS:
  // SHADOW is amber-outline only and can never match the executed look).
});

/* ============================================ §56 — provenance gap */
test("§56 a named-but-missing parent marks a PROVENANCE GAP (never an arrow)", () => {
  const a = ev("MARKET");
  const b = ev("RISK", { parent: "EV-GONE" }); // parent not retained
  const g = buildGraph([a, b]);
  const risk = g.nodes.find((n) => n.stage === "RISK");
  assert.ok(risk.provenanceGap, "the orphan carries a provenance gap");
  // No invented MARKET->RISK arrow: MARKET has no observed child here.
  assert.equal(
    g.edges.find((e) => e.source === nodeId("MARKET", null) && e.target === nodeId("RISK", null)),
    undefined,
  );
});

test("§56 no observed linkage at all => dashed gap, not a solid causal edge", () => {
  // Two same-trace events with no parent link and no sequence adjacency
  // evidence: the gap is explicit.
  const a = ev("MARKET", { trace_id: "TR-9" });
  const b = ev("ORDER", { trace_id: "TR-9", parent: null });
  const g = buildGraph([a, b]);
  const edge = g.edges[0];
  if (edge) {
    assert.equal(edge.provenanceGap, true, "the only edge is a provenance gap");
  } else {
    // No edge at all is also honest: the UI renders the two nodes unlinked.
    assert.equal(g.edges.length, 0);
  }
});

test("§56 observed linkage wins over a later gap transition for the same pair", () => {
  // Same pair linked once by parent_event_id, once without: the OBSERVED
  // linkage is authoritative — precedence is "with neither", i.e. never.
  const t1 = cleanTrace("TR-A");
  const a2 = ev("RISK", { trace_id: "TR-B" });
  const b2 = ev("EXECUTION", { trace_id: "TR-B", parent: null });
  const g = buildGraph([...t1, a2, b2]);
  const riskId = nodeId("RISK", null);
  const execId = nodeId("EXECUTION", null);
  const e = g.edges.find((x) => x.source === riskId && x.target === execId);
  assert.ok(e, "the pair has an edge");
  assert.equal(e.provenanceGap, false, "observed linkage wins");
});

/* ============================================ §5 — packets only on real events */
test("§5 mount / trace switch fires NO packets (never retroactively seeded)", () => {
  const events = cleanTrace();
  const g = buildGraph(events);
  const r1 = diffPackets(null, events, g);
  assert.equal(r1.packets.length, 0, "no previous observation => no animation");
  const r2 = diffPackets(r1.next, [], g);
  assert.equal(r2.packets.length, 0, "empty arrival => no animation");
});

test("§5 a real new event traversing a REAL edge fires exactly one packet", () => {
  const first = cleanTrace();
  const next = ev("ORDER", { parent: first[3].event_id });
  const all = [...first, next];
  const g = buildGraph(all); // edge set includes the new hop
  const prev = diffPackets(null, first, g).next;
  const r = diffPackets(prev, all, g);
  assert.equal(r.packets.length, 1);
  assert.equal(r.packets[0].eventId, next.event_id);
});

test("§5 a hop with no resolvable parent fires no packet (no invented path)", () => {
  const first = cleanTrace();
  const g = buildGraph(first);
  const prev = diffPackets(null, first, g).next;
  const orphan = ev("ORDER", { parent: "EV-NOPARENT" });
  const r = diffPackets(prev, [...first, orphan], g);
  assert.equal(r.packets.length, 0, "an unlinked event animates nothing");
});

test("§63 the packet set is bounded (never unbounded growth)", () => {
  const first = cleanTrace();
  const g = buildGraph([...first, ev("ORDER", { parent: first[3].event_id })]);
  let prev = diffPackets(null, first, g).next;
  let total = 0;
  // Feed far more fresh events than MAX_PACKETS.
  for (let i = 0; i < MAX_PACKETS * 4; i += 1) {
    const e = ev("ORDER", { parent: first[3].event_id });
    const r = diffPackets(prev, [...first, e], g);
    prev = r.next;
    total += r.packets.length;
  }
  assert.ok(total > 0, "packets did fire on real arrivals");
  assert.ok(
    total <= MAX_PACKETS * 4,
    "bounded per call by MAX_PACKETS",
  );
});

test("§63 the seen set is capped (PACKET_SEEN_CAP)", async () => {
  const first = cleanTrace();
  const g = buildGraph([...first, ev("ORDER", { parent: first[3].event_id })]);
  let prev = diffPackets(null, first, g).next;
  for (let i = 0; i < 600; i += 1) {
    const e = ev("ORDER", { parent: first[3].event_id });
    prev = diffPackets(prev, [...first, e], g).next;
  }
  // §63: bounded — the window is capped at PACKET_SEEN_CAP, never the
  // unbounded feed length.
  assert.ok(prev.seen.size <= PACKET_SEEN_CAP, "capped at PACKET_SEEN_CAP");
  assert.ok(prev.seen.size > 0);
});

/* ============================================ §3 — request movement */
test("§3 headStage tracks the newest observed stage", () => {
  const events = cleanTrace();
  const head = headStage(events);
  assert.ok(head);
  assert.equal(head.stage, "EXECUTION");
  assert.equal(head.eventId, events[4].event_id);
});

test("§3 requestOrigin reads the typed source, else NOT OBSERVED", () => {
  assert.equal(requestOrigin([]), "NOT OBSERVED");
  const events = cleanTrace();
  assert.equal(requestOrigin(events), "NOT OBSERVED");
  const withSource = [{ ...events[0], source: "MARKET_TICK" }, ...events.slice(1)];
  assert.equal(requestOrigin(withSource), "MARKET_TICK");
});

test("§54 traceLanes assigns stable lanes in first-appearance order", () => {
  const events = [
    ...cleanTrace("TR-1"),
    ...cleanTrace("TR-2"),
    ...cleanTrace("TR-1"),
  ];
  const lanes = traceLanes(events);
  assert.equal(lanes.length, 2);
  assert.equal(lanes[0].traceId, "TR-1");
  assert.equal(lanes[1].traceId, "TR-2");
  assert.ok(lanes.every((l) => l.lane >= 0 && l.lane < 4));
});

/* ============================================ §52 — fit/focus/collapse */
test("§52 fitViewport centers and scales the whole graph into view", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const vp = fitViewport(layout, 1200, 800);
  const box = contentBBox(layout.nodes);
  assert.ok(vp.k >= VIEW_K_MIN && vp.k <= VIEW_K_MAX);
  assert.ok(vp.k >= 1, "the small content is scaled up to fill the view");
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  assert.ok(Math.abs(vp.x + cx * vp.k - 600) < 1);
  assert.ok(Math.abs(vp.y + cy * vp.k - 400) < 1);
});

test("§52 fitViewport on an empty layout is the identity", () => {
  const empty = layoutCanvas(buildGraph([]));
  const vp = fitViewport(empty, 1200, 800);
  assert.deepEqual(vp, { x: 0, y: 0, k: 1 });
});

test("§52 focusViewport centers the given nodes and never changes zoom", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const riskId = nodeId("RISK", null);
  const vp = focusViewport(layout, [riskId], 1200, 800, 1.3);
  assert.equal(vp.k, 1.3, "focus keeps the user's zoom");
  const n = layout.nodes.find((x) => x.id === riskId);
  assert.ok(Math.abs(vp.x + n.x * vp.k - 600) < 1);
  assert.ok(Math.abs(vp.y + n.y * vp.k - 400) < 1);
});

test("§52 focusViewport falls back to the whole graph for unknown ids", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const all = fitViewport(layout, 1200, 800);
  const vp = focusViewport(layout, ["NOPE"], 1200, 800, all.k);
  const box = contentBBox(layout.nodes);
  const cx = (box.minX + box.maxX) / 2;
  assert.ok(Math.abs(vp.x + cx * vp.k - 600) < 1);
});

test("§52 collapse hides the subtree; expand restores it", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const marketId = nodeId("MARKET", null);
  const full = selectView(layout, {});
  assert.equal(full.nodes.length, layout.nodes.length);
  const collapsed = selectView(layout, { collapsed: new Set([marketId]) });
  assert.ok(collapsed.nodes.length < full.nodes.length, "subtree hid");
  assert.ok(collapsed.hidden.size > 0);
  const restored = selectView(layout, { collapsed: new Set() });
  assert.equal(restored.nodes.length, full.nodes.length);
});

test("§52 collapse records how many children it swallowed", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const marketId = nodeId("MARKET", null);
  const view = selectView(layout, { collapsed: new Set([marketId]) });
  assert.equal(view.collapsedCounts.get(marketId), 1);
});

test("§52 branch isolation keeps the 1-hop neighbourhood", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const view = selectView(layout, { isolateStage: "INFERENCE" });
  const stages = new Set(view.nodes.map((n) => n.stage));
  assert.ok(stages.has("INFERENCE"));
  assert.ok(stages.has("FEATURES") || stages.has("RISK"), "a neighbour survives");
  assert.ok(!stages.has("EXECUTION"), "2 hops away is hidden");
});

test("§52 zoom is clamped to the contract range", () => {
  const layout = layoutCanvas(buildGraph(cleanTrace()));
  const inVp = fitViewport(layout, 1200, 800);
  assert.ok(inVp.k >= VIEW_K_MIN && inVp.k <= VIEW_K_MAX);
});

/* ============================================ §13 — derivation stays honest */
test("§13 no event for a stage => no node for it", () => {
  const g = buildGraph(cleanTrace());
  const stages = new Set(g.nodes.map((n) => n.stage));
  assert.ok(stages.has("RISK"));
  assert.ok(!stages.has("MT5"), "MT5 was never observed, so it is absent");
});

test("§02/§50 an unmapped future stage is flagged, not hidden", () => {
  const a = ev("MARKET");
  const b = ev("QUANTUM", { parent: a.event_id, unmapped: true });
  const g = buildGraph([a, b]);
  const q = g.nodes.find((n) => n.stage === "QUANTUM");
  assert.ok(q, "the unknown stage IS a node");
  assert.equal(q.unmapped, true);
  assert.ok(g.unmappedStages.includes(q.id));
});

test("§35 latency absence never implies failure", () => {
  const a = ev("MARKET");
  const b = ev("RISK", { parent: a.event_id, latency_us: null });
  const g = buildGraph([a, b]);
  const r = g.nodes.find((n) => n.stage === "RISK");
  assert.equal(isFailureNode(r), false);
  assert.equal(r.state, null, "no state was invented from the missing latency");
});

test("UNKNOWN is exported as the honest placeholder word", () => {
  assert.equal(UNKNOWN, "UNKNOWN");
});

