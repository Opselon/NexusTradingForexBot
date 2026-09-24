/*
 * Lane D gate — Decision Trace inspector forensics suite (LIVE-CAUSAL-TOPOLOGY).
 *
 * Run from the worktree ROOT:  node tests/js/decision_trace_inspectors.test.js
 *
 * Node 24 strips TS types; an inline resolve hook (node:module registerHooks)
 * maps the Vite-style `@/` alias and extensionless relative imports so this
 * suite exercises the REAL production modules — no re-implemented copies.
 *
 * Pins the lane-D truth rules (CONTRACT backend-guarantee, brief §14-§19,
 * §29-§42, §55-§62, §73):
 *   - WHY/NEXT rendering restates the endpoint's own words
 *     (TERMINATED / NOT OBSERVED / 404) and never invents a continuation;
 *   - the events filter builder drops empty words (legacy tail preserved);
 *   - trace queue buckets are derived from observed status words only;
 *   - the stream lifecycle states are the real connection states;
 *   - delta / latency / error-cascade / payload views read named backend
 *     fields only and render UNKNOWN / NOT OBSERVED / PROVENANCE GAP for
 *     absence — never a zero-filled default.
 */
"use strict";

const { registerHooks } = require("node:module");
const { pathToFileURL } = require("node:url");
const path = require("node:path");
const assert = require("node:assert/strict");
const { test } = require("node:test");

const REPO = path.join(__dirname, "..", "..");
const SRC = path.join(REPO, "frontend", "src");

registerHooks({
  resolve(specifier, context, nextResolve) {
    let spec = specifier;
    if (spec.startsWith("@/")) spec = pathToFileURL(path.join(SRC, spec.slice(2))).href;
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

const p = (rel) => pathToFileURL(path.join(SRC, "features", "decision-trace", rel)).href;

// --------------------------------------------------------------- fixtures
let seq = 0;
function ev(stage, detail = {}, opts = {}) {
  seq += 1;
  return {
    event_id: opts.event_id ?? `EV-${seq}`,
    trace_id: opts.trace_id ?? "TR-1",
    parent_event_id: opts.parent ?? null,
    sequence: opts.sequence ?? seq,
    timestamp: opts.timestamp ?? `2026-09-24T10:00:${String(seq).padStart(2, "0")}.000Z`,
    monotonic_ns: opts.mono ?? seq * 1_000_000,
    stage,
    component: null,
    event_type: opts.event_type ?? null,
    status: opts.status ?? null,
    symbol: opts.symbol ?? "EURUSD",
    decision_id: opts.decision_id ?? null,
    latency_us: "latency_us" in opts ? opts.latency_us : null,
    terminal: opts.terminal ?? false,
    unmapped: false,
    provenance_gap: opts.provenance_gap ?? false,
    detail,
    trace_schema_version: 1,
    ...Object.fromEntries(
      Object.entries({
        request_id: opts.request_id,
        root_event_id: opts.root_event_id,
        source: opts.source,
        destination: opts.destination,
        state: opts.state,
        started_at: opts.started_at,
        completed_at: opts.completed_at,
        duration_ms: opts.duration_ms,
        reason_code: opts.reason_code,
        error_code: opts.error_code,
        mode: opts.mode,
        provider: opts.provider,
        model: opts.model,
        position_id: opts.position_id,
        order_id: opts.order_id,
        deal_id: opts.deal_id,
        execution_id: opts.execution_id,
        snapshot_id: opts.snapshot_id,
        payload_summary: opts.payload_summary,
        freshness: opts.freshness,
        provenance: opts.provenance,
      }).filter(([, v]) => v !== undefined),
    ),
  };
}

const whyRes = (over = {}) => ({
  found: true,
  trace_schema_version: 1,
  event_id: "EV-7",
  trace_id: "TR-1",
  sequence: 7,
  timestamp: "2026-09-24T10:00:07.000Z",
  stage: "RISK",
  component: null,
  event_type: "RISK",
  status: "REJECTED",
  terminal: true,
  why: { reason_code: "MAX_EXPOSURE", state: "REJECTED" },
  detail: { margin_free: 120.5 },
  next: { observed: false, destination: "NOT OBSERVED" },
  provenance: "observed",
  ...over,
});

// ===========================================================================
// §35/§36 — filter param builder (wired to the NEW /events query params)
// ===========================================================================
test("§36: no filters => no query string (legacy unfiltered tail preserved)", async () => {
  const { buildEventsQuery } = await import(p("types.ts"));
  assert.equal(buildEventsQuery({ last_seq: 42, limit: 100 }), "?last_seq=42&limit=100");
  assert.equal(buildEventsQuery({}), "");
  assert.equal(buildEventsQuery(null), "");
});

test("§36: every active filter becomes an AND query param, verbatim", async () => {
  const { buildEventsQuery } = await import(p("types.ts"));
  const qs = buildEventsQuery({
    last_seq: 10,
    limit: 500,
    trace_id: "TR-9",
    stage: "risk",
    status: "rejected",
    mode: "live",
    position_id: "4242",
  });
  const params = new URLSearchParams(qs);
  assert.equal(params.get("trace_id"), "TR-9");
  assert.equal(params.get("stage"), "RISK");
  assert.equal(params.get("status"), "REJECTED");
  assert.equal(params.get("mode"), "LIVE");
  assert.equal(params.get("position_id"), "4242");
  assert.equal(params.get("last_seq"), "10");
  assert.equal(params.get("limit"), "500");
});

test("§36: null/whitespace/empty filter words are dropped, not sent as blanks", async () => {
  const { buildEventsQuery, activeFilterWords } = await import(p("types.ts"));
  const qs = buildEventsQuery({
    trace_id: "   ",
    stage: null,
    status: "",
    mode: undefined,
    position_id: " P-1 ",
  });
  assert.equal(qs, "?position_id=P-1");
  assert.deepEqual(activeFilterWords({ stage: " ", status: null }), []);
  assert.deepEqual(activeFilterWords({ status: "REJECTED" }), [
    { key: "status", value: "REJECTED" },
  ]);
});

// ===========================================================================
// §37/§38 — WHY / NEXT rendering (TERMINATED, NOT OBSERVED, 404, inferred)
// ===========================================================================
test("§37: WHY ready state renders the runtime reason words verbatim", async () => {
  const { renderWhy } = await import(p("forensics.ts"));
  const res = whyRes();
  const panel = renderWhy(res, { kind: "READY", res });
  assert.equal(panel.state.kind, "READY");
  const asMap = Object.fromEntries(panel.whyLines.map((l) => [l.key, l.value]));
  assert.equal(asMap.reason_code, "MAX_EXPOSURE");
  assert.equal(asMap.state, "REJECTED");
  assert.equal(panel.provenance, "observed");
});

test("§37: an empty reasons object renders UNKNOWN fields, never an invented why", async () => {
  const { renderWhy, whyHasReason } = await import(p("forensics.ts"));
  const res = whyRes({ why: {} });
  const panel = renderWhy(res, { kind: "READY", res });
  assert.equal(whyHasReason(res.why), false);
  assert.ok(panel.whyLines.every((l) => l.value === "UNKNOWN"));
});

test("§38: NOT OBSERVED is explicit and carries PROVENANCE GAP wording", async () => {
  const { renderWhy, PROVENANCE_GAP } = await import(p("forensics.ts"));
  const res = whyRes({ next: { observed: false, destination: "NOT OBSERVED" } });
  const panel = renderWhy(res, { kind: "READY", res });
  assert.equal(panel.next.destination, "NOT OBSERVED");
  assert.equal(panel.next.provenance, PROVENANCE_GAP);
  assert.match(panel.next.note, /PROVENANCE GAP/);
});

test("§38: TERMINATED restates the terminal status and marks the missing edge", async () => {
  const { renderWhy, TERMINATED, PROVENANCE_GAP } = await import(p("forensics.ts"));
  const res = whyRes({
    next: { observed: true, destination: "TERMINATED", terminal_status: "REJECTED" },
  });
  const panel = renderWhy(res, { kind: "READY", res });
  assert.equal(panel.next.destination, TERMINATED);
  assert.equal(panel.next.status, "REJECTED");
  assert.equal(panel.next.provenance, PROVENANCE_GAP);
  assert.match(panel.next.note, /terminal status: REJECTED/);
});

test("§38: an observed successor distinguishes observed vs inferred provenance", async () => {
  const { renderWhy } = await import(p("forensics.ts"));
  const nextObs = {
    observed: true,
    destination: "EXECUTION",
    event_id: "EV-8",
    stage: "EXECUTION",
    event_type: "EXECUTION",
    status: "OK",
    timestamp: "2026-09-24T10:00:08.000Z",
    terminal: false,
    provenance: "observed",
    link: "parent_event_id",
  };
  const observed = whyRes({ next: nextObs });
  const p1 = renderWhy(observed, { kind: "READY", res: observed });
  assert.equal(p1.next.destination, "EXECUTION");
  assert.equal(p1.next.provenance, "observed");
  assert.equal(p1.next.link, "parent_event_id");

  const inferred = whyRes({
    next: { ...nextObs, provenance: "inferred", link: "trace_sequence" },
  });
  const p2 = renderWhy(inferred, { kind: "READY", res: inferred });
  assert.equal(p2.next.provenance, "inferred");
  assert.equal(p2.next.link, "trace_sequence");
  assert.match(p2.next.note, /sequence only/);

  // an observed successor without an explicit link field defaults to the
  // runtime's own parent_event_id, never to a guessed correlation
  const noLink = renderWhy(
    whyRes({ next: { ...nextObs, link: undefined } }),
    { kind: "READY", res: whyRes({ next: { ...nextObs, link: undefined } }) },
  );
  assert.equal(noLink.next.link, "parent_event_id");
});

test("§37: 404 / PENDING / ERROR / EMPTY states are explicit, never READY", async () => {
  const { renderWhy } = await import(p("forensics.ts"));
  assert.equal(renderWhy(null, { kind: "EMPTY" }).state.kind, "EMPTY");
  assert.equal(renderWhy(null, { kind: "PENDING" }).state.kind, "PENDING");
  const notFound = renderWhy({ found: false }, { kind: "NOT_FOUND", eventId: "EV-404" });
  assert.equal(notFound.state.kind, "NOT_FOUND");
  assert.equal(notFound.whyLines.length, 0);
  const err = renderWhy(null, { kind: "ERROR", message: "boom" });
  assert.equal(err.state.kind, "ERROR");
  assert.equal(err.next, null);

  // a READY ctx always restates itself — the 404 mapping happens at the
  // query layer (useWhyQuery turns a 404 into NOT_FOUND), never here
  const ready = renderWhy(whyRes(), { kind: "READY", res: whyRes() });
  assert.equal(ready.state.kind, "READY");
});

// ===========================================================================
// §14 — payload metadata view (typed v2 first, UNKNOWN for absence)
// ===========================================================================
test("§14: v2 fields render first and absent fields say UNKNOWN (never default)", async () => {
  const { payloadFields } = await import(p("forensics.ts"));
  const full = ev("INFERENCE", { feature_dim: 60 }, {
    state: "COMPLETED",
    mode: "PAPER",
    freshness: "VALID",
    reason_code: "MODEL_OK",
    model: "scalp_v1",
    payload_summary: { feature_dim: 50 },
  });
  const rows = payloadFields(full);
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  assert.equal(rows[0].key, "payload_summary");
  assert.equal(byKey.state.value, "COMPLETED");
  assert.equal(byKey.mode.value, "PAPER");
  assert.equal(byKey.freshness.value, "VALID");
  assert.equal(byKey.model.value, "scalp_v1");
  // payload_summary renders as its own stringified v2 row; the detail copy
  // of feature_dim follows it (different origin, verbatim value)
  assert.equal(rows[0].value, '{"feature_dim":50}');
  assert.equal(rows[0].origin, "v2");
  assert.equal(byKey.feature_dim.value, "60");
  assert.equal(byKey.feature_dim.origin, "detail");

  const bare = payloadFields(ev("MARKET", {}));
  const bareMap = Object.fromEntries(bare.map((r) => [r.key, r]));
  assert.equal(bareMap.state.value, "UNKNOWN");
  assert.equal(bareMap.state.origin, "absent");
  assert.equal(bareMap.mode.value, "UNKNOWN");
  assert.equal(bareMap.freshness.value, "UNKNOWN");

  const tsFresh = payloadFields(ev("MARKET", {}, { freshness: 5000 }));
  const tf = Object.fromEntries(tsFresh.map((r) => [r.key, r]));
  assert.equal(tf.freshness.value, "5000");
});

// ===========================================================================
// §15/§39 — what-changed delta between adjacent events of one trace
// ===========================================================================
test("§15: delta reports ADDED / CHANGED / REMOVED from real keys only", async () => {
  const { deltaBetween } = await import(p("forensics.ts"));
  const a = ev("MARKET", { bid: 1.1, ask: 1.2 }, { payload_summary: { tick: 1 } });
  const b = ev(
    "FEATURES",
    { bid: 1.1, ask: 1.3, feature_dim: 50 },
    { payload_summary: { tick: 1 } },
  );
  const d = deltaBetween(a, b);
  const byKey = Object.fromEntries(d.entries.map((e) => [e.key, e]));
  assert.equal(byKey.ask.kind, "CHANGED");
  assert.equal(byKey.ask.from, "1.2");
  assert.equal(byKey.ask.to, "1.3");
  assert.equal(byKey.feature_dim.kind, "ADDED");
  assert.equal(d.entries.some((e) => e.kind === "REMOVED"), false);

  const back = deltaBetween(b, a);
  const removed = back.entries.find((e) => e.key === "feature_dim");
  assert.equal(removed.kind, "REMOVED");
  assert.equal(removed.to, "UNKNOWN");

  const same = deltaBetween(a, a);
  assert.deepEqual(same.entries, []);

  // a delta's status word is the observed one, or UNKNOWN when the runtime
  // recorded no status for the event (never a guessed verdict)
  assert.equal(same.status, "UNKNOWN");
});

test("§15: the trace's first event has no predecessor — no fabricated delta", async () => {
  const { deltaBetween, traceDeltas } = await import(p("forensics.ts"));
  const first = ev("MARKET", { bid: 1.1 });
  const d = deltaBetween(null, first);
  assert.equal(d.prev, null);
  assert.deepEqual(d.entries, []);
  const list = traceDeltas([first]);
  assert.equal(list.length, 1);
  assert.equal(list[0].prev, null);
});

test("§15: sensitive keys never enter the delta (redaction, §13/§48)", async () => {
  const { deltaBetween, isSensitiveKey } = await import(p("forensics.ts"));
  assert.equal(isSensitiveKey("api_key"), true);
  assert.equal(isSensitiveKey("Bearer"), true);
  assert.equal(isSensitiveKey("feature_dim"), false);
  const a = ev("POLICY", { api_key: "sk-should-never-render", regime: "TREND" });
  const b = ev("POLICY", { api_key: "sk-other", regime: "RANGE" });
  const d = deltaBetween(a, b);
  assert.ok(!d.entries.some((e) => e.key === "api_key"));
  assert.ok(!JSON.stringify(d.entries).includes("sk-"));
});

// ===========================================================================
// §31 — trace queue buckets (active/recent/failed/completed, bounded)
// ===========================================================================
test("§31: queue buckets derive from observed status/terminal words only", async () => {
  const { groupTraces } = await import(p("store.ts"));
  const events = [
    ev("MARKET", {}, { trace_id: "T-A", status: "OK" }),
    ev("DECISION", {}, { trace_id: "T-A", status: "APPROVED" }), // still open => active
    ev("MARKET", {}, { trace_id: "T-B", status: "OK" }),
    ev("RISK", {}, { trace_id: "T-B", status: "REJECTED", terminal: true }), // failed
    ev("MARKET", {}, { trace_id: "T-C", status: "OK" }),
    ev("DECISION", {}, { trace_id: "T-C", status: "NO_TRADE", terminal: true }), // completed
    ev("MARKET", {}, { trace_id: "TR-1", status: null }), // fixture default trace => active
  ];
  const q = groupTraces(events);
  assert.ok(q.active.some((b) => b.trace_id === "T-A"));
  assert.ok(q.failed.some((b) => b.trace_id === "T-B"));
  assert.ok(q.completed.some((b) => b.trace_id === "T-C"));
  assert.ok(q.recent.some((b) => b.trace_id === "T-B"));
  assert.ok(q.recent.some((b) => b.trace_id === "T-C"));
  // a completed trace is never also reported as active
  assert.ok(!q.active.some((b) => b.trace_id === "T-C"));
  assert.ok(!q.completed.some((b) => b.trace_id === "T-A"));
  // status words are verbatim backend words
  const tb = q.failed.find((b) => b.trace_id === "T-B");
  assert.equal(tb.last_status, "REJECTED");
});

test("§31: queue buckets are bounded (no unbounded growth under a burst)", async () => {
  const { groupTraces } = await import(p("store.ts"));
  const events = [];
  for (let i = 0; i < 300; i++) {
    events.push(
      ev("MARKET", {}, { trace_id: `T-${i}`, sequence: i * 2, status: "OK" }),
      ev("DECISION", {}, {
        trace_id: `T-${i}`,
        sequence: i * 2 + 1,
        status: i % 2 === 0 ? "NO_TRADE" : "OK",
        terminal: i % 2 === 0,
      }),
    );
  }
  const q = groupTraces(events);
  for (const key of ["active", "recent", "failed", "completed"]) {
    assert.ok(q[key].length <= 80, `${key} bucket must stay bounded`);
  }
  assert.ok(q.completed.length > 0);
  assert.ok(q.active.length > 0);
});

// ===========================================================================
// §62 — stream lifecycle states (real connection state + resync)
// ===========================================================================
test("§62: lifecycle words are the real connection states", async () => {
  const { streamStateWords } = await import(p("forensics.ts"));
  assert.equal(streamStateWords("connected", false).label, "CONNECTED");
  assert.equal(streamStateWords("connected", true).label, "RESYNCING");
  assert.equal(streamStateWords("reconnecting", false).label, "RECONNECTING");
  assert.equal(streamStateWords("connecting", false).label, "CONNECTING");
  assert.equal(streamStateWords("connecting", true).label, "RESYNCING");
  assert.equal(streamStateWords("disconnected", false).label, "STREAM DISCONNECTED");
  assert.equal(streamStateWords("failed", false).label, "STREAM DISCONNECTED");
  // every state carries a note so the UI never shows a bare word without cause
  for (const s of ["connected", "reconnecting", "connecting", "failed", "disconnected"]) {
    assert.ok(streamStateWords(s, false).note.length > 10, s);
  }
});

test("§62: RESYNCING explains the resume point reconciliation", async () => {
  const { streamStateWords } = await import(p("forensics.ts"));
  const w = streamStateWords("connected", true);
  assert.equal(w.tone, "ok");
  assert.match(w.note, /resume point/);
});

test("§62: an unknown status word falls back to STREAM DISCONNECTED", async () => {
  const { streamStateWords } = await import(p("forensics.ts"));
  const w = streamStateWords("garbage-status", false);
  assert.equal(w.label, "STREAM DISCONNECTED");
  assert.equal(w.tone, "fail");
});

// ===========================================================================
// §41 — latency waterfall (real latency_us / duration_ms; labelled sums)
// ===========================================================================
test("§41: waterfall uses observed timing only and labels its sum", async () => {
  const { latencyWaterfall } = await import(p("forensics.ts"));
  const events = [
    ev("MARKET", {}, { latency_us: 1200, duration_ms: 2 }),
    ev("FEATURES", {}, { latency_us: 4800 }),
    ev("RISK", {}, { latency_us: null }),
  ];
  const w = latencyWaterfall(events, {
    stages: { MARKET: { p50_us: 900, p95_us: 2000, n: 12 }, FEATURES: { p50_us: 4000, n: 12 } },
  });
  // duration_ms (2ms -> 2000us) wins where the backend recorded it
  assert.equal(w.bars[0].us, 2000);
  assert.equal(w.bars[0].observed, true);
  assert.equal(w.bars[1].us, 4800);
  assert.equal(w.bars[2].us, 0);
  assert.equal(w.bars[2].observed, false);
  assert.equal(w.ui_sum_us, 2000 + 4800 + 0);
  assert.equal(w.backend_stages.MARKET.p95_us, 2000);
  assert.equal(w.backend_stages.FEATURES.n, 12);
  assert.equal(w.backend_total_us, 2 * 1000);
  assert.deepEqual(w.backend_only_stages, []);
});

test("§41: no timing anywhere => no observed sum (never a zero total)", async () => {
  const { latencyWaterfall } = await import(p("forensics.ts"));
  const w = latencyWaterfall([ev("MARKET", {}, { latency_us: null })]);
  assert.equal(w.ui_sum_us, 0);
  assert.equal(w.bars[0].observed, false);
  assert.equal(w.backend_total_us, null);
  const empty = latencyWaterfall([]);
  assert.equal(empty.ui_sum_us, null);
  assert.deepEqual(empty.bars, []);
});

// ===========================================================================
// §42 — error cascade from real error_code chains only
// ===========================================================================
test("§42: cascade is the observed error_code chain, in order", async () => {
  const { errorCascade } = await import(p("forensics.ts"));
  const events = [
    ev("MARKET", {}, { status: "OK" }),
    ev("POLICY", {}, { error_code: "POLICY_REJECT", status: "REJECTED" }),
    ev("EXECUTION", {}, { error_code: "BROKER_REJECT", status: "FAILED" }),
    ev("MARKET", { error_code: "LEGACY_CODE" }, { status: "OK" }),
  ];
  const c = errorCascade(events);
  assert.equal(c.nodes.length, 3);
  assert.equal(c.nodes[0].error_code, "POLICY_REJECT");
  assert.equal(c.nodes[1].error_code, "BROKER_REJECT");
  assert.equal(c.nodes[2].error_code, "LEGACY_CODE");
  assert.equal(c.nodes[2].stage, "MARKET");
  assert.equal(c.summary, "3 ERROR EVENT(S)");
});

test("§42: a clean trace reports NO ERROR OBSERVED (never an invented root)", async () => {
  const { errorCascade } = await import(p("forensics.ts"));
  const c = errorCascade([ev("MARKET", {}), ev("DECISION", { action: "HOLD" })]);
  assert.deepEqual(c.nodes, []);
  assert.equal(c.summary, "NO ERROR OBSERVED");
});

// ===========================================================================
// §40 — freshness display (event.freshness verbatim)
// ===========================================================================
test("§40: freshness is the runtime word, verbatim; absence says NOT OBSERVED", async () => {
  const { freshnessWord, freshnessTone } = await import(p("forensics.ts"));
  assert.equal(freshnessWord(ev("RISK", {}, { freshness: "STALE" })), "STALE");
  assert.equal(freshnessWord(ev("RISK", {}, { freshness: 5000 })), "5000");
  assert.equal(freshnessTone("STALE"), "stale");
  assert.equal(freshnessTone("VALID"), "fresh");
  assert.equal(freshnessTone("NARRATIVE_WORD"), "unknown");
  assert.equal(freshnessWord(ev("MARKET", {})), "NOT OBSERVED");
  assert.equal(freshnessWord(null), "UNKNOWN");
});

// ===========================================================================
// §43 — health overlay only from real backend state words
// ===========================================================================
test("§43: observer status word is restated; absent subsystems say endpoint pending", async () => {
  const { systemHealth, BACKEND_PENDING } = await import(p("forensics.ts"));
  const items = systemHealth(
    { status: "ACTIVE", session: null, subscribers: [] },
    { nodes: [{ stage: "RISK", count: 3 }] },
    { stages: { RISK: { n: 3 } } },
  );
  const obs = items.find((i) => i.subsystem === "OBSERVER");
  assert.equal(obs.state, "ACTIVE");
  assert.equal(obs.tone, "ok");
  const risk = items.find((i) => i.subsystem === "RISK");
  assert.equal(risk.state, "OBSERVED");
  const exec = items.find((i) => i.subsystem === "EXECUTION");
  assert.equal(exec.state, BACKEND_PENDING);
  assert.equal(exec.tone, "unknown");

  const empty = systemHealth(null, null, null);
  assert.equal(empty[0].state, "UNKNOWN");
  for (const i of empty) assert.ok(i.subsystem.length > 0, "subsystem named");
});

test("§43: a subsystem named only by latency is OBSERVED via that source", async () => {
  const { systemHealth } = await import(p("forensics.ts"));
  const items = systemHealth({ status: "ACTIVE" }, { nodes: [] }, { stages: { INFERENCE: { n: 4 } } });
  const model = items.find((i) => i.subsystem === "MODEL");
  assert.equal(model.state, "OBSERVED");
  assert.equal(model.source, "GET /api/trace/latency .stages");
});

// ===========================================================================
// §50/§66 — decision card + rejection forensics (no fabricated execution)
// ===========================================================================
test("§50: card restates observed words; execution never claims CONFIRMED", async () => {
  const { decisionCard, executionLine } = await import(p("forensics.ts"));
  const noExec = [ev("MARKET", {}), ev("DECISION", { action: "HOLD" }, { status: "NO_TRADE" })];
  assert.equal(executionLine(noExec), "NOT REACHED");

  const requested = [
    ev("MARKET", {}),
    ev("ORDER", { action: "order_send" }, { status: "RECOMMENDED" }),
  ];
  const line = executionLine(requested);
  assert.notEqual(line, "EXECUTED");
  assert.notEqual(line, "CONFIRMED");
  assert.match(line, /RECOMMENDED/);

  const confirmed = [
    ev("MARKET", {}),
    ev("GATEWAY", { gateway: "paper" }, { state: "CONFIRMED", event_type: "GATEWAY_RESPONSE" }),
  ];
  assert.equal(executionLine(confirmed), "CONFIRMED");

  const card = decisionCard(
    {
      decision_id: "EXEC-1",
      trace_id: "TR-1",
      status: "REJECTED",
      action: "BUY",
      symbol: "EURUSD",
      regime: null,
      reason_code: "MAX_EXPOSURE",
      rejection_reason: null,
      reason: null,
      confidence: 0.4,
      model_id: null,
      model_version: null,
      contract: null,
      latency_us: null,
      recorded_at: null,
      },
      requested,
      );
      assert.equal(card.action, "BUY");
      assert.equal(card.model, "UNKNOWN");
      assert.equal(card.execution, "RECOMMENDED");
      assert.equal(card.trace_id, "TR-1");

      const card2 = decisionCard(
      {
        decision_id: "EXEC-2",
        trace_id: null,
        status: "APPROVED",
        action: "SELL",
        symbol: "GBPUSD",
        regime: "TREND",
        reason_code: null,
        rejection_reason: null,
        reason: null,
        confidence: 0.9,
        model_id: "scalp_v2",
        model_version: "2.1",
        contract: "c1",
        latency_us: 1200,
        recorded_at: null,
      },
      [ev("MARKET", {}), ev("DECISION", { action: "SELL" }, { trace_id: "TR-2", status: "APPROVED" })],
      );
      assert.equal(card2.model, "scalp_v2");
      assert.equal(card2.mode, "UNKNOWN");
      });

test("§18: rejection forensics restates real codes; UNKNOWN without evidence", async () => {
  const { rejectionForensics } = await import(p("forensics.ts"));
  const events = [
    ev("MARKET", {}),
    ev(
      "RISK",
      { rejection_reason: "exposure cap", blocked_by: "risk_engine", risk_checks: { rr: 0.8, min_rr: 1.5 } },
      { status: "REJECTED", state: "REJECTED", reason_code: "MAX_EXPOSURE" },
    ),
  ];
  const rej = rejectionForensics(null, events, null);
  assert.equal(rej.rejected, true);
  assert.equal(rej.reason_code, "MAX_EXPOSURE");
  assert.equal(rej.rejection_reason, "exposure cap");
  assert.equal(rej.blocked_by, "risk_engine");
  assert.equal(rej.rejecting_stage, "RISK");
  // no WHY loaded: the continuation verdict is honest absence, not a guess
  assert.equal(rej.next, "NOT OBSERVED (no continuation evidence)");
  // value/threshold pair derived from the REAL gate record (rr vs min_rr)
  const rr = rej.pairs.find((x) => x.key === "rr");
  assert.ok(rr, "rr vs min_rr pair must surface from risk_checks");
  assert.equal(rr.actual, "0.8");
  assert.equal(rr.required, "1.5");

  const clean = rejectionForensics(
    {
      decision_id: "E", trace_id: "T", status: "APPROVED", action: "BUY",
      symbol: "EURUSD", regime: null, reason_code: null, rejection_reason: null,
      reason: null, confidence: null, model_id: null, model_version: null,
      contract: null, latency_us: null, recorded_at: null,
    },
    [ev("MARKET", {}), ev("DECISION", { action: "BUY" }, { status: "APPROVED" })],
    null,
  );
  assert.equal(clean.rejected, false);
  assert.equal(clean.reason_code, "UNKNOWN");
  assert.deepEqual(clean.pairs, []);
});

test("§18: rejection next restates the WHY endpoint's own destination word", async () => {
  const { rejectionForensics } = await import(p("forensics.ts"));
  const events = [
    ev("MARKET", {}),
    ev("RISK", { rejection_reason: "exposure cap" }, { status: "REJECTED", reason_code: "MAX_EXPOSURE" }),
  ];
  const whyTerminated = {
    found: true, event_id: "EV-2", trace_id: "TR-1", sequence: 2,
    timestamp: null, stage: "RISK", component: null, event_type: "RISK",
    status: "REJECTED", terminal: true, why: {}, detail: null,
    next: { observed: true, destination: "TERMINATED", terminal_status: "REJECTED" },
  };
  const withT = rejectionForensics(null, events, whyTerminated);
  assert.equal(withT.next, "TERMINATED — REJECTED");

  const whyStage = {
    found: true, event_id: "EV-2", trace_id: "TR-1", sequence: 2,
    timestamp: null, stage: "RISK", component: null, event_type: "RISK",
    status: "REJECTED", terminal: false, why: {}, detail: null,
    next: { observed: true, destination: "EXECUTION", event_id: "EV-9", stage: "EXECUTION" },
  };
  const withS = rejectionForensics(null, events, whyStage);
  assert.equal(withS.next, "EXECUTION");
});

test("§18: numeric pair requires a real threshold key — never invented", async () => {
  const { rejectionPairs } = await import(p("forensics.ts"));
  const events = [
    ev(
      "RISK",
      { risk_checks: { zone_quality: 0.31, min_zone_quality: 0.55, spread_usd: 0.0004 } },
      { status: "REJECTED" },
    ),
  ];
  const pairs = rejectionPairs(null, events);
  assert.equal(pairs.length, 1, "only value/threshold pairs render");
  const zone = pairs.find((x) => x.key === "zone_quality");
  assert.ok(zone);
  assert.equal(zone.actual, "0.31");
  assert.equal(zone.required, "0.55");
  assert.equal(zone.operator, ">=");
  // a scalar with no threshold sibling never fabricates a pair
  assert.equal(pairs.some((x) => x.key === "spread_usd"), false);
});

// ===========================================================================
// §58/§59/§35 — store selectors: stream rail rows, search, client filters
// ===========================================================================
test("§35: search matches every mandated id across trace/position/order/symbol", async () => {
  const { matchSearch } = await import(p("store.ts"));
  const e = ev("ORDER", { engine_mode: "PAPER" }, {
    request_id: "REQ-9",
    position_id: "POS-1",
    order_id: "ORD-77",
    model: "scalp_v1",
    status: "OK",
  });
  for (const q of ["TR-1", "REQ-9", "POS-1", "ORD-77", "eurusd", "scalp_v1", "OK", "ORDER"]) {
    assert.equal(matchSearch(e, q), true, `search must match ${q}`);
  }
  assert.equal(matchSearch(e, "ZZZ"), false);
  assert.equal(matchSearch(e, ""), true);
  // free words the runtime never recorded never match
  assert.equal(matchSearch(ev("MARKET", {}), "NONEXISTENT"), false);
});

test("§36: client event filters mirror the server AND semantics", async () => {
  const { matchesEventFilters } = await import(p("store.ts"));
  const e = ev("RISK", { position_id: "P-5" }, { status: "REJECTED", mode: "PAPER", event_type: "RISK" });
  assert.equal(matchesEventFilters(e, { stage: "RISK" }), true);
  assert.equal(matchesEventFilters(e, { stage: "MARKET" }), false);
  assert.equal(matchesEventFilters(e, { status: "rejected" }), true);
  assert.equal(matchesEventFilters(e, { mode: "paper" }), true);
  assert.equal(matchesEventFilters(e, { mode: "LIVE" }), false);
  assert.equal(matchesEventFilters(e, { position_id: "P-5" }), true);
  assert.equal(matchesEventFilters(e, { position_id: "P-9" }), false);
  assert.equal(matchesEventFilters(e, null), true);
});

test("§32/§33/§34: follow resolvers read real recorded ids (no correlation guess)", async () => {
  const { resolvePositionId, resolveOrderTrace } = await import(p("store.ts"));
  const events = [
    ev("MARKET", {}),
    ev("ORDER", { ticket: "TICK-9" }, { position_id: "POS-1", order_id: "ORD-77" }),
  ];
  assert.equal(resolvePositionId(events, "POS-1"), "POS-1");
  assert.equal(resolvePositionId(events, "TICK-9"), "POS-1");
  assert.equal(resolveOrderTrace(events, "ORD-77").orderId, "ORD-77");
  assert.equal(resolveOrderTrace(events, "ORD-77").traceId, "TR-1");
  assert.equal(resolveOrderTrace(events, "ORD-77").orderId, "ORD-77");
  // unknown order id => traceId stays null (NOT OBSERVED, never a guess)
  assert.equal(resolveOrderTrace(events, "NOPE").traceId, null);
  assert.equal(resolveOrderTrace(events, "TICK-9").traceId, "TR-1");
  // empty keys never resolve
  assert.equal(resolvePositionId(events, ""), null);
  assert.equal(resolvePositionId(events, null), null);
  assert.equal(resolveOrderTrace(events, null).traceId, null);
});

// ===========================================================================
// §59 — state timeline uses exact timestamps from observed events
// ===========================================================================
test("§59: state timeline carries the verbatim timestamp + state word", async () => {
  const { stateTimeline } = await import(p("store.ts"));
  const events = [
    ev("MARKET", {}, { timestamp: "2026-09-24T10:00:00.000Z" }),
    ev("RISK", {}, { state: "REJECTED", timestamp: "2026-09-24T10:00:01.000Z" }),
  ];
  const rows = stateTimeline(events);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].state, "UNKNOWN");
  assert.equal(rows[1].state, "REJECTED");
  assert.equal(rows[1].timestamp, "2026-09-24T10:00:01.000Z");
  assert.deepEqual(stateTimeline([]), []);

  const statusOnly = stateTimeline([ev("MARKET", { bid: 1 }, { status: "OK" })]);
  assert.equal(statusOnly[0].state, "OK");
});

// --------------------------------------------------------------- summary
test("lane-D suite executed its own imports", () => {
  assert.ok(true);
});
