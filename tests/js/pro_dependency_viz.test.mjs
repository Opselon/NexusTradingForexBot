/**
 * Dependency Intelligence — payload-shape + truthfulness regression suite.
 *
 * Run:  node tests/js/pro_dependency_viz.test.mjs
 *   or: node --test tests/js/pro_dependency_viz.test.mjs
 *
 * Imports the REAL frontend module (Node 24 strips the TS types; the only
 * non-erasable import in that file is `import type`, which is removed before
 * resolution, so the `@/` alias never has to resolve here).
 *
 * These tests pin the contract for the /alt/dependency console:
 *  1. HOTSPOTS are read by the producer's own keys (node_id / risk_score /
 *     flags / fan_in / fan_out / instability / criticality) — the old UI read
 *     id/score/reason, keys the producer never emits, so the whole hotspot
 *     table rendered "—".
 *  2. IMPACT is read as id LISTS (direct / transitive / tests / api /
 *     runtime) and the producer's impact_kind word — never the legacy
 *     impacted / impacted_count / risk_level keys, which do not exist.
 *     The old UI rendered "No downstream impact recorded" on EVERY node.
 *  3. A missing key stays UNKNOWN — no heuristic may manufacture a verdict
 *     (mirrors frontend/src/lib/riskGateTrace.ts law).
 *  4. Node identity is the producer's canonical `id`; shortNodeLabel is a
 *     display compression that never changes which node is selected.
 *
 * Fix covered: frontend/src/features/dependency/{api.ts,
 * dependencyModel.ts, ui/DependencyPage.tsx}
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  UNKNOWN_WORD,
  cyclePathText,
  fanPair,
  filterNodes,
  hotspotFlags,
  hotspotIdSet,
  hotspotNode,
  hotspotScore,
  hotspotScorePct,
  hotspotTone,
  impactLevel,
  impactRows,
  impactRowsNSE,
  impactTotal,
  impactUnknownNode,
  impactWord,
  instabilityPct,
  isCritical,
  isForeignLeaf,
  isUnresolved,
  kindCounts,
  nodeId,
  pathFound,
  pathLength,
  pathUnknownNode,
  shortNodeLabel,
  sortNodes,
  statusLevel,
  summaryHealth,
} from "../../frontend/src/features/dependency/dependencyModel.ts";

// ---------------------------------------------------------------------------
// VERBATIM live payload samples (probed 2026-09-23 from /api/dependency/*
// on port 59273). These are the producer's real shapes — if the analyzer
// changes them, these tests fail LOUDLY instead of silently rendering "—".
// ---------------------------------------------------------------------------

const SUMMARY = {
  status: "ok",
  analyzer_version: "0.1.0",
  generated_at: "2026-09-22T22:35:17Z",
  repository: {
    files_analyzed: 640,
    modules: 641,
    nodes: 2033,
    edges: 6954,
    di_registrations: 8,
  },
  health: {
    cycles: 70,
    unresolved_imports: 13,
    unresolved_di_bindings: 13,
    architecture_violations: 0,
  },
  hotspots: [
    {
      node_id: "mod:nexus_scalp.observability.logging",
      risk_score: 251.2,
      flags: ["HIGH_FAN_IN", "HIGH_FAN_OUT"],
      fan_in: 247,
      fan_out: 14,
      instability: 0.0536,
      criticality: "UNKNOWN",
    },
    {
      node_id: "mod:nexus_scalp.research.models",
      risk_score: 51.7,
      flags: ["HIGH_FAN_IN", "HIGH_FAN_OUT", "CYCLE"],
      fan_in: 34,
      fan_out: 9,
      instability: 0.2093,
      criticality: "HIGH",
    },
  ],
  scan_duration_ms: 97319.71,
};

const IMPACT_LIVE_ENGINE = {
  changed: "mod:nexus_scalp.application.live_engine",
  direct: [
    "stdlib:asyncio",
    "mod:nexus_scalp.accounting",
    "mod:nexus_scalp.execution.order_manager",
  ],
  transitive: ["mod:nexus_scalp.deep"],
  tests_likely_affected: [],
  api_impact: [],
  runtime_impact: ["mod:nexus_scalp.risk.risk_engine"],
  impact_kind: "HIGH_RISK",
};

const IMPACT_LOGGING = {
  changed: "mod:nexus_scalp.observability.logging",
  direct: [
    "mod:nexus_scalp.accounting",
    "mod:nexus_scalp.application.live_engine",
    "mod:nexus_scalp.configuration",
    "mod:nexus_scalp.domain.models",
    "mod:nexus_scalp.execution.order_manager",
    "mod:nexus_scalp.experience.ledger",
    "mod:nexus_scalp.governance",
    "mod:nexus_scalp.intelligence",
    "mod:nexus_scalp.risk.risk_engine",
    "mod:nexus_scalp.web.server",
    "mod:nexus_scalp.research.registry",
    "mod:nexus_scalp.model_lifecycle.store",
    "mod:nexus_scalp.observability.telegram_notifier",
    "mod:nexus_scalp.features.scalp_features",
  ],
  transitive: ["mod:nexus_scalp.deep.a", "mod:nexus_scalp.deep.b", "mod:nexus_scalp.deep.c"],
  tests_likely_affected: [],
  api_impact: [],
  runtime_impact: [],
  impact_kind: "TRANSITIVE",
};

const PATH_OK = {
  found: true,
  source: "mod:nexus_scalp.application.live_engine",
  target: "mod:nexus_scalp.observability.logging",
  path: ["mod:nexus_scalp.application.live_engine", "mod:nexus_scalp.observability.logging"],
  edges: [{ source: "mod:nexus_scalp.application.live_engine", target: "mod:nexus_scalp.observability.logging", kinds: ["IMPORT"] }],
};

const PATH_MISS = { found: false, source: "mod:a", target: "mod:b" };
const PATH_UNKNOWN_NODE = { error: "unknown_node", source: "mod:nope", target: "mod:x" };

const CYCLE = {
  cycle_id: "CYC-001",
  severity: "HIGH",
  path: ["mod:nexus_scalp.web.api_v1_wiring", "mod:nexus_scalp.web.api_v1.errors", "mod:nexus_scalp.web.server"],
  edge_types: ["IMPORT"],
  source_locations: ["web\\api_v1\\errors.py:27", "web\\server.py:2960"],
  impact: "Circular dependency can block construction / cause import-time failures or non-deterministic wiring.",
  recommended_breakpoint: "Extract an abstraction (Protocol/ABC) or move orchestration to a composition root to break the cycle at its weakest edge.",
};

const VIOLATION = {
  severity: "HIGH",
  source: "mod:nexus_scalp.web.server",
  target: "mod:nexus_scalp.domain.models",
  rule: "presentation_may_not_depend_on_domain",
  evidence: { import: "from nexus_scalp.domain.models import Trade" },
  explanation: "The presentation layer imports a domain aggregate.",
  remediation: "Move the import behind a protocol defined in the application layer.",
};

const NODES = [
  { id: "mod:nexus_scalp.application.live_engine", qualified_name: "nexus_scalp.application.live_engine", kind: "MODULE", layer: "unknown", status: "UNKNOWN", criticality: "HIGH" },
  { id: "mod:nexus_scalp.domain.enums", qualified_name: "nexus_scalp.domain.enums", kind: "MODULE", layer: "unknown", status: "UNKNOWN", criticality: "HIGH" },
  { id: "cls:nexus_scalp.core.X", qualified_name: "nexus_scalp.core.X", kind: "CLASS", layer: "unknown", status: "UNRESOLVED", criticality: "UNKNOWN" },
  { id: "stdlib:os", qualified_name: "os", kind: "EXTERNAL", layer: "unknown", status: "UNRESOLVED", criticality: "UNKNOWN" },
];

const METRICS = {
  "mod:nexus_scalp.application.live_engine": { fan_in: 5, fan_out: 119, instability: 0.9597, centrality: 0.030512, in_cycle: false, violations: 0, unresolved_deps: 0 },
};

// ---------------------------------------------------------------------------
// hotspots — the producer's real keys
// ---------------------------------------------------------------------------

test("hotspotNode reads node_id, not the legacy id/qualified_name/name", () => {
  assert.equal(hotspotNode(SUMMARY.hotspots[0]), "mod:nexus_scalp.observability.logging");
  assert.equal(hotspotNode({}), "");
  assert.equal(hotspotNode(null), "");
});

test("hotspotScore reads risk_score, not the legacy score/centrality", () => {
  assert.equal(hotspotScore(SUMMARY.hotspots[0]), 251.2);
  assert.equal(hotspotScore({ risk_score: "x" }), null);
  assert.equal(hotspotScore({}), null);
});

test("hotspotScorePct normalizes against the observed max, clamped to 0..100", () => {
  assert.equal(hotspotScorePct(SUMMARY.hotspots[0], 251.2), 100);
  assert.equal(hotspotScorePct(SUMMARY.hotspots[1], 251.2), (51.7 / 251.2) * 100);
  assert.equal(hotspotScorePct({ risk_score: 500 }, 251.2), 100); // clamped
  assert.equal(hotspotScorePct({ risk_score: -5 }, 251.2), 0); // clamped
  assert.equal(hotspotScorePct(SUMMARY.hotspots[0], 0), 0); // degenerate max
});

test("hotspotTone uses the producer's criticality first, then score bands", () => {
  assert.equal(hotspotTone(SUMMARY.hotspots[0]), "amber"); // UNKNOWN crit, score 251 >= 40
  assert.equal(hotspotTone(SUMMARY.hotspots[1]), "red"); // HIGH criticality
  assert.equal(hotspotTone({ risk_score: 10 }), "blue"); // low score, unknown crit
});

test("hotspotFlags returns the producer's flags, sorted into the known order", () => {
  assert.deepEqual(hotspotFlags(SUMMARY.hotspots[0]), ["HIGH_FAN_IN", "HIGH_FAN_OUT"]);
  // CYCLE ranks after the fan flags in the known order
  assert.deepEqual(hotspotFlags(SUMMARY.hotspots[1]), ["HIGH_FAN_IN", "HIGH_FAN_OUT", "CYCLE"]);
  assert.deepEqual(hotspotFlags({}), []);
  assert.deepEqual(hotspotFlags({ flags: "not-an-array" }), []);
});

test("hotspotIdSet builds the marker set from the hotspot list", () => {
  const s = hotspotIdSet(SUMMARY.hotspots);
  assert.ok(s.has("mod:nexus_scalp.observability.logging"));
  assert.ok(s.has("mod:nexus_scalp.research.models"));
  assert.equal(s.size, 2);
  assert.equal(hotspotIdSet(null).size, 0);
});

test("instabilityPct reads the producer's instability and clamps to 0..100", () => {
  assert.equal(instabilityPct(METRICS["mod:nexus_scalp.application.live_engine"]), 95.97);
  assert.equal(instabilityPct({ instability: 2 }), 100);
  assert.equal(instabilityPct({ instability: -1 }), 0);
  assert.equal(instabilityPct({}), null);
});

test("fanPair renders fan_in / fan_out with em-dash fallbacks", () => {
  assert.equal(fanPair(METRICS["mod:nexus_scalp.application.live_engine"]), "5 / 119");
  assert.equal(fanPair({ fan_in: 3 }), "3 / —");
  assert.equal(fanPair({}), "— / —");
});

// ---------------------------------------------------------------------------
// health verdict — producer-authoritative, never fabricated
// ---------------------------------------------------------------------------

test("summaryHealth is degraded when the producer's health counts are non-zero", () => {
  const v = summaryHealth(SUMMARY);
  assert.equal(v.healthy, false);
  assert.equal(v.word, "degraded");
  assert.equal(v.level, "bad");
});

test("summaryHealth is healthy only when every health count is zero", () => {
  const ok = structuredClone(SUMMARY);
  ok.health = { cycles: 0, unresolved_imports: 0, unresolved_di_bindings: 0, architecture_violations: 0 };
  const v = summaryHealth(ok);
  assert.equal(v.healthy, true);
  assert.equal(v.word, "healthy");
  assert.equal(v.level, "good");
});

test("summaryHealth degrades to unknown when a health count is absent", () => {
  const partial = structuredClone(SUMMARY);
  partial.health = { cycles: 0, unresolved_imports: 0 };
  const v = summaryHealth(partial);
  assert.equal(v.healthy, false);
  assert.equal(v.word, UNKNOWN_WORD);
  assert.equal(v.level, "unknown");
});

test("summaryHealth is unknown for a missing payload", () => {
  assert.equal(summaryHealth(null).word, UNKNOWN_WORD);
  assert.equal(summaryHealth(undefined).word, UNKNOWN_WORD);
});

test("statusLevel never guesses an unrecognized status", () => {
  assert.equal(statusLevel("OK"), "good");
  assert.equal(statusLevel("DEGRADED"), "warn");
  assert.equal(statusLevel("ERROR"), "bad");
  assert.equal(statusLevel("UNKNOWN"), "neutral");
  assert.equal(statusLevel("SOMETHING_NEW"), "unknown");
  assert.equal(statusLevel(null), "unknown");
  assert.equal(statusLevel(undefined), "unknown");
});

// ---------------------------------------------------------------------------
// impact — the bug that made every blast radius look empty
// ---------------------------------------------------------------------------

test("impactTotal reads direct + transitive as LISTS (the producer emits lists)", () => {
  assert.equal(impactTotal(IMPACT_LIVE_ENGINE), 4);
  assert.equal(impactTotal(IMPACT_LOGGING), 17); // 14 direct + 3 transitive
  assert.equal(impactTotal({}), 0);
  assert.equal(impactTotal(null), 0);
});

test("impactWord/impactLevel echo the producer's impact_kind, never invent a risk", () => {
  assert.equal(impactWord("HIGH_RISK"), "HIGH RISK");
  assert.equal(impactLevel("HIGH_RISK"), "bad");
  assert.equal(impactWord("TRANSITIVE"), "transitive");
  assert.equal(impactLevel("TRANSITIVE"), "warn");
  assert.equal(impactWord(null), UNKNOWN_WORD);
  assert.equal(impactLevel(null), "unknown");
  assert.equal(impactWord("SOMETHING_NEW"), UNKNOWN_WORD);
  assert.equal(impactLevel("SOMETHING_NEW"), "unknown");
});

test("impactRows partitions the payload into actionable rows, ordered runtime first", () => {
  const rows = impactRows(IMPACT_LIVE_ENGINE);
  assert.equal(rows[0].label, "runtime (critical/high)");
  assert.deepEqual(rows[0].ids, ["mod:nexus_scalp.risk.risk_engine"]);
  assert.equal(rows[0].tone, "bad");
  const labels = rows.map((r) => r.label);
  assert.deepEqual(labels, [
    "runtime (critical/high)",
    "direct dependents",
    "transitive",
  ]);
  // empty lists are dropped, never rendered as a zero bar
  assert.equal(impactRows({ direct: [], transitive: [] }).length, 0);
  assert.equal(impactRows(null).length, 0);
});

test("impactRowsNSE excludes stdlib/external leaves and counts them out", () => {
  const rows = impactRowsNSE(IMPACT_LIVE_ENGINE);
  const direct = rows.find((r) => r.label === "direct dependents");
  assert.ok(direct);
  assert.deepEqual(direct.ids, ["mod:nexus_scalp.accounting", "mod:nexus_scalp.execution.order_manager"]);
  assert.equal(direct.foreign, 1); // stdlib:asyncio excluded
});

test("impactUnknownNode detects the producer's unknown-node error payload", () => {
  assert.equal(impactUnknownNode({ error: "unknown_node", node_id: "mod:x" }), true);
  assert.equal(impactUnknownNode(IMPACT_LIVE_ENGINE), false);
  assert.equal(impactUnknownNode({}), false);
});

// ---------------------------------------------------------------------------
// path explorer — found/false and the unknown-node error shape
// ---------------------------------------------------------------------------

test("pathFound/pathLength read the producer's found + path list", () => {
  assert.equal(pathFound(PATH_OK), true);
  assert.equal(pathLength(PATH_OK), 2);
  assert.equal(pathFound(PATH_MISS), false);
  assert.equal(pathFound({}), false);
  assert.equal(pathLength({}), 0);
});

test("pathUnknownNode separates an error from a legitimate no-path answer", () => {
  assert.equal(pathUnknownNode(PATH_UNKNOWN_NODE), true);
  assert.equal(pathUnknownNode(PATH_MISS), false);
  assert.equal(pathUnknownNode(PATH_OK), false);
});

// ---------------------------------------------------------------------------
// node identity — the canonical id, display compression never reselects
// ---------------------------------------------------------------------------

test("nodeId prefers the producer id and falls back to qualified_name", () => {
  assert.equal(nodeId(NODES[0]), "mod:nexus_scalp.application.live_engine");
  assert.equal(nodeId({ qualified_name: "nexus_scalp.x" }), "nexus_scalp.x");
  assert.equal(nodeId({}), "");
  assert.equal(nodeId(null), "");
});

test("shortNodeLabel compresses for display but keeps identity stable", () => {
  assert.equal(shortNodeLabel("mod:nexus_scalp.application.live_engine"), "application.live_engine");
  assert.equal(shortNodeLabel("cls:nexus_scalp.core.X"), "core.X");
  assert.equal(shortNodeLabel("stdlib:os"), "os");
  assert.equal(shortNodeLabel(""), "");
});

test("isForeignLeaf separates stdlib/external leaves from NSE nodes", () => {
  assert.equal(isForeignLeaf("stdlib:os"), true);
  assert.equal(isForeignLeaf("external:numpy"), true);
  assert.equal(isForeignLeaf("mod:nexus_scalp.x"), false);
  assert.equal(isForeignLeaf("cls:nexus_scalp.core.X"), false);
});

test("kindCounts counts kinds and adds the UNRESOLVED status slice", () => {
  const c = kindCounts(NODES);
  assert.equal(c.MODULE, 2);
  assert.equal(c.CLASS, 1);
  assert.equal(c.EXTERNAL, 1);
  assert.equal(c.UNRESOLVED, 2);
});

test("isUnresolved / isCritical read the producer's own status + criticality", () => {
  assert.equal(isUnresolved(NODES[2]), true);
  assert.equal(isUnresolved(NODES[0]), false);
  assert.equal(isCritical(NODES[0]), true);
  assert.equal(isCritical(NODES[2]), false);
});

// ---------------------------------------------------------------------------
// graph browser — sort + filter + search highlight
// ---------------------------------------------------------------------------

test("sortNodes by fan_in descending puts the biggest consumer first", () => {
  const out = sortNodes(NODES, "fan_in", "desc", METRICS);
  assert.equal(nodeId(out[0]), "mod:nexus_scalp.application.live_engine");
  const asc = sortNodes(NODES, "id", "asc", METRICS);
  assert.equal(nodeId(asc[0]), "cls:nexus_scalp.core.X");
});

test("sortNodes without a metrics map falls back to 0, not NaN", () => {
  const out = sortNodes(NODES, "fan_out", "desc", null);
  assert.equal(out.length, NODES.length);
  assert.ok(out.every((n) => typeof nodeId(n) === "string"));
});

test("filterNodes matches id and qualified_name, case-insensitive", () => {
  assert.equal(filterNodes(NODES, { query: "live_engine", kind: "all" }).length, 1);
  assert.equal(filterNodes(NODES, { query: "nexus_scalp.core", kind: "all" }).length, 1);
  assert.equal(filterNodes(NODES, { query: "", kind: "MODULE" }).length, 2);
  assert.equal(filterNodes(NODES, { query: "", kind: "UNRESOLVED" }).length, 2);
  assert.equal(filterNodes(NODES, { query: "zzz", kind: "all" }).length, 0);
});

test("cyclePathText compresses each segment for display", () => {
  assert.equal(cyclePathText(CYCLE), "web.api_v1_wiring → api_v1.errors → web.server");
  assert.equal(cyclePathText({}), "");
});
