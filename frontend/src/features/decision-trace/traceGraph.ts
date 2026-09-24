/**
 * traceGraph — PURE derivation of graph/forensics from observed events.
 *
 * THE CORE RULE (§13/§51): this module NEVER contains a hardcoded trading
 * topology. Nodes, edges, branches and terminal states are DERIVED from
 * TraceEvent[]. If a future runtime release emits a new stage with a new
 * event type, `buildGraph` produces a node for it automatically and
 * `isUnmapped` flags it — no frontend rewrite (§66 test pins this).
 *
 * Pure functions, no React, no I/O: Node's TS-stripping test runner imports
 * this file directly so the derivation contract is regression-pinned.
 */

import type {
  DecisionRow,
  StageName,
  TraceBundle,
  TraceEvent,
  TraceStage,
} from "./types";

/** Unknown/absent rendering — the UI's only honest placeholders. */
export const UNKNOWN = "UNKNOWN";
export const NOT_OBSERVED = "NOT OBSERVED";
export const NOT_REACHED = "NOT REACHED";
export const PROVENANCE_GAP = "PROVENANCE GAP";
export const UNMAPPED = "UNMAPPED";
export const INSUFFICIENT_DATA = "INSUFFICIENT DATA";
export const ORDER_UNCERTAIN = "ORDER UNCERTAIN";
export const TRACE_GAP = "TRACE GAP";

/** Layout rank — visualization hint only, NOT business topology. */
const RANK: Record<string, number> = {
  MARKET: 0,
  FEATURES: 1,
  REGIME: 2,
  INFERENCE: 3,
  POLICY: 4,
  POST_POLICY: 5,
  DECISION: 6,
  RISK: 7,
  EXECUTION: 8,
  GATEWAY: 9,
  MT5: 10,
  ORDER: 11,
};

export interface GraphNode {
  id: string;
  stage: StageName;
  label: string;
  count: number;
  rank: number;
  unmapped: boolean;
  terminal: boolean;
  provenanceGap: boolean;
  lastStatus: string | null;
  firstSeen: number;
  lastTs: string | null;
  /** fraction of this stage's events that are verdicts (PASS/REJECT/…). */
  rejects: number;
  passes: number;
  errors: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  count: number;
  firstSeen: number;
  /** parent->child frequency vs. parent total — branch weight for layout. */
  weight: number;
}

export interface DerivedGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** stage -> node id (first occurrence). */
  stageIndex: Record<string, string>;
  /** detected root stage (MARKET by contract, or whatever roots the DAG). */
  root: string | null;
  /** terminal nodes of observed paths (stage ids). */
  terminals: string[];
  /** stages that appeared with no parent and are not the root. */
  unmappedStages: string[];
}

const TERMINAL_STATUSES = new Set([
  "REJECTED",
  "EXECUTED",
  "FAILED",
  "TIMEOUT",
  "ERROR",
  "CANCELLED",
  "DISPATCHED",
]);

/**
 * Derive the runtime topology from raw events. Complexity is O(events) with
 * structural sharing: callers pass retained event arrays; nothing is cloned
 * per render (React layers memoize on this result's identity).
 */
export function buildGraph(events: TraceEvent[]): DerivedGraph {
  const nodeMap = new Map<string, GraphNode>();
  const edgeMap = new Map<string, GraphEdge>();
  const parentTotals = new Map<string, number>();
  const seenEvents = new Set<string>();
  const byTrace = new Map<string, TraceEvent[]>();

  for (const e of events) {
    const stage = e.stage ?? UNKNOWN;
    const id = nodeId(stage, e.component);
    if (!nodeMap.has(id)) {
      nodeMap.set(id, {
        id,
        stage,
        label: stage,
        count: 0,
        rank: RANK[stage] ?? 99,
        unmapped: false,
        terminal: false,
        provenanceGap: false,
        lastStatus: null,
        firstSeen: e.sequence,
        lastTs: null,
        rejects: 0,
        passes: 0,
        errors: 0,
      });
    }
    const n = nodeMap.get(id)!;
    n.count += 1;
    n.lastStatus = e.status ?? n.lastStatus;
    n.lastTs = e.timestamp ?? n.lastTs;
    n.firstSeen = Math.min(n.firstSeen, e.sequence);
    if (e.unmapped) n.unmapped = true;
    if (e.provenance_gap) n.provenanceGap = true;
    if (e.terminal) n.terminal = true;
    if (e.status === "REJECT" || TERMINAL_STATUSES.has(e.status ?? "")) n.rejects += 1;
    else if (e.status === "PASS") n.passes += 1;
    else if (e.status === "ERROR") n.errors += 1;

    // Causality: parent_event_id -> this event (same trace only).
    const tid = e.trace_id ?? "";
    if (tid) {
      let arr = byTrace.get(tid);
      if (!arr) {
        arr = [];
        byTrace.set(tid, arr);
      }
      arr.push(e);
    }
    if (e.event_id) seenEvents.add(e.event_id);
  }

  // Second pass: link events to their observed parents inside each trace,
  // in sequence order. A missing parent => provenance gap (visible, §44).
  for (const [, arr] of byTrace) {
    arr.sort((a, b) => a.sequence - b.sequence);
    const byEventId = new Map(arr.map((e) => [e.event_id, e]));
    let lastEvent: TraceEvent | null = null;
    for (const e of arr) {
      let parent: TraceEvent | null = null;
      const pid = e.parent_event_id;
      if (pid && byEventId.has(pid)) {
        parent = byEventId.get(pid)!;
      } else if (pid) {
        // parent named but absent from this bundle -> broken chain
        parent = null;
      } else if (lastEvent && lastEvent.stage !== e.stage) {
        // no explicit parent: chain to the previous observed event
        parent = lastEvent;
      }
      if (parent) {
        const srcId = nodeId(parent.stage, parent.component);
        const dstId = nodeId(e.stage, e.component);
        if (srcId !== dstId) {
          const key = `${srcId}->${dstId}`;
          let edge = edgeMap.get(key);
          if (!edge) {
            edge = { id: key, source: srcId, target: dstId, count: 0, firstSeen: e.sequence, weight: 0 };
            edgeMap.set(key, edge);
          }
          edge.count += 1;
          edge.firstSeen = Math.min(edge.firstSeen, e.sequence);
        }
      } else if (!pid && lastEvent === null) {
        // root candidate below
      }
      if (pid && !byEventId.has(pid)) {
        const n = nodeMap.get(nodeId(e.stage, e.component));
        if (n) n.provenanceGap = true;
      }
      lastEvent = e;
    }
  }

  for (const e of edgeMap.values()) parentTotals.set(e.source, (parentTotals.get(e.source) ?? 0) + e.count);
  for (const e of edgeMap.values()) {
    e.weight = parentTotals.get(e.source) ? e.count / parentTotals.get(e.source)! : 0;
  }

  // Root = node with no incoming edge (MARKET by contract; fall back to
  // the earliest first-seen stage so the layout is always acyclic).
  const incoming = new Set<string>();
  for (const e of edgeMap.values()) incoming.add(e.target);
  let root: string | null = null;
  for (const n of nodeMap.values()) {
    if (!incoming.has(n.id)) {
      root = n.id;
      break;
    }
  }
  if (root === null && nodeMap.size) {
    const sortedNodes = [...nodeMap.values()].sort((a, b) => a.firstSeen - b.firstSeen);
    const first = sortedNodes[0];
    if (first) root = first.id;
  }

  // Terminals: nodes with no outgoing edge, or stages marked terminal.
  const outgoing = new Set<string>();
  for (const e of edgeMap.values()) outgoing.add(e.source);
  const terminals: string[] = [];
  const unmappedStages: string[] = [];
  for (const n of nodeMap.values()) {
    if (!outgoing.has(n.id) || n.terminal) terminals.push(n.id);
    if (n.unmapped) unmappedStages.push(n.id);
  }

  const stageIndex: Record<string, string> = {};
  for (const n of nodeMap.values()) if (!(n.stage in stageIndex)) stageIndex[n.stage] = n.id;

  return {
    nodes: [...nodeMap.values()].sort((a, b) => a.rank - b.rank || a.firstSeen - b.firstSeen),
    edges: [...edgeMap.values()].sort((a, b) => b.count - a.count),
    stageIndex,
    root,
    terminals: [...new Set(terminals)],
    unmappedStages: [...new Set(unmappedStages)],
  };
}

export function nodeId(stage: StageName, component: string | null): string {
  return component ? `${stage}|${component}` : String(stage);
}

/** A stage the runtime emitted that the UI has no visual mapping for. */
export function isUnmapped(stage: StageName): boolean {
  return !(stage in RANK);
}

/** A decision row's verdict, or UNKNOWN when evidence is absent. */
export function decisionVerdict(row: DecisionRow | null): string {
  if (!row) return UNKNOWN;
  const s = row.status;
  if (!s) return UNKNOWN;
  return s;
}

/** Rejection cause as the runtime stated it — never synthesized. */
export function rejectionCause(row: DecisionRow | null): string | null {
  if (!row) return null;
  return row.rejection_reason || row.reason_code || row.reason || null;
}

/**
 * 50D/70D contract from RUNTIME EVIDENCE ONLY (§15).
 * The feature dimension the inference event itself reports is the fact.
 * Returns UNKNOWN when no evidence exists — never an inferred label.
 */
export function modelContractEvidence(events: TraceEvent[]): {
  contract: string;
  source: "inference" | "summary" | "none";
  featureDim: number | null;
  modelId: string | null;
  modelVersion: string | null;
  schemaHash: string | null;
  artifact: string | null;
  probabilities: number[] | null;
} {
  const inf = events.find((e) => e.stage === "INFERENCE");
  if (inf && inf.detail) {
    const d = inf.detail;
    const dim = numOrNull(d.feature_dim) ?? numOrNull(d.effective_feature_dim);
    return {
      contract: dim === 50 ? "50D" : dim === 70 ? "70D" : dim !== null ? `${dim}D` : UNKNOWN,
      source: "inference",
      featureDim: dim,
      modelId: strOrNull(d.model_id),
      modelVersion: strOrNull(d.model_version),
      schemaHash: strOrNull(d.schema_hash),
      artifact: strOrNull(d.artifact_path) ?? strOrNull(d.artifact),
      probabilities: arrOfNumbers(d.probabilities) ?? arrOfNumbers(d.probabilities_flat),
    };
  }
  const dec = events.find((e) => e.stage === "DECISION");
  if (dec && dec.detail) {
    const d = dec.detail;
    const dim = numOrNull(d.feature_dim) ?? numOrNull(d.feature_count);
    return {
      contract: dim === 50 ? "50D" : dim === 70 ? "70D" : dim !== null ? `${dim}D` : UNKNOWN,
      source: "summary",
      featureDim: dim,
      modelId: strOrNull(d.model_id),
      modelVersion: strOrNull(d.model_version),
      schemaHash: strOrNull(d.schema_hash),
      artifact: strOrNull(d.artifact_path) ?? strOrNull(d.artifact),
      probabilities: arrOfNumbers(d.probabilities),
    };
  }
  return {
    contract: UNKNOWN,
    source: "none",
    featureDim: null,
    modelId: null,
    modelVersion: null,
    schemaHash: null,
    artifact: null,
    probabilities: null,
  };
}

/** Events grouped by stage, in observation order (the inspector sections). */
export function groupByStage(events: TraceEvent[]): Map<string, TraceEvent[]> {
  const m = new Map<string, TraceEvent[]>();
  for (const e of events) {
    const arr = m.get(e.stage) ?? [];
    arr.push(e);
    m.set(e.stage, arr);
  }
  return m;
}

/**
 * Deterministic "WHY" explanation (§33): a fact chain built from recorded
 * events — no LLM, no invented wording. Each step cites the event that
 * proves it; the rejection point carries the runtime's own actual/required
 * values when the detail exposes them.
 */
export interface WhyStep {
  stage: string;
  status: string | null;
  latency_us: number | null;
  reason: string | null;
  evidence: TraceEvent | null;
  /** downstream stages with no events after this point. */
  notReached: string[];
}

export interface WhyExplanation {
  verdict: string;
  rejectionStage: string | null;
  steps: WhyStep[];
  actualValue: string | null;
  requiredValue: string | null;
  operator: string | null;
  rule: string | null;
  cause: string | null;
  evidenceCount: number;
  provenanceGaps: string[];
}

export function explainDecision(bundle: TraceBundle): WhyExplanation {
  const events = bundle.events ?? [];
  const summary = bundle.summary;
  const stages = groupByStage(events);
  const order = [...stages.keys()];
  const graph = buildGraph(events);

  const steps: WhyStep[] = order
    .filter((stage) => stages.has(stage))
    .map((stage) => {
      const arr = stages.get(stage)!;
      const last = arr[arr.length - 1]!;
      const after = order.slice(order.indexOf(stage) + 1);
      const lastDetailVerdict =
        last.detail && typeof last.detail.verdict === "string" ? (last.detail.verdict as string) : null;
      return {
        stage,
        status: last.status ?? lastDetailVerdict,
        latency_us: last.latency_us,
        reason: pickReason(arr),
        evidence: last,
        notReached: after,
      } as WhyStep;
    });

  // The rejection point = the terminal stage that is not a successful end.
  let rejectionStage: string | null = null;
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (!s) continue;
    const st = s.status?.toUpperCase() ?? "";
    if (st === "REJECT" || st === "REJECTED" || st === "FAILED" || st === "ERROR" || st === "TIMEOUT") {
      rejectionStage = s.stage;
      break;
    }
  }

  const lastEvent = events[events.length - 1] ?? null;
  // §35: the verdict comes from evidence, in strict precedence — a summary
  // status, else the rejection stage's observed status/verdict, else the
  // last event's status. NEVER derived from timing or presence of latency.
  const rejectionDetail = rejectionStage
    ? (stages.get(rejectionStage)?.slice(-1)[0]?.detail ?? null)
    : null;
  const rejectionVerdict =
    typeof rejectionDetail?.verdict === "string" ? (rejectionDetail.verdict as string) : null;
  const verdict =
    summary?.status ??
    (rejectionStage
      ? (stages.get(rejectionStage)?.slice(-1)[0]?.status ?? rejectionVerdict)
      : null) ??
    lastEvent?.status ??
    UNKNOWN;

  const cause = rejectionCause(summary);
  const detail = rejectionDetail;

  return {
    verdict,
    rejectionStage,
    steps,
    actualValue: detail ? fmtVal(detail.actual ?? detail.observed ?? detail.value) : null,
    requiredValue: detail ? fmtVal(detail.required ?? detail.threshold ?? detail.limit) : null,
    operator: detail ? strOrNull(detail.operator) : null,
    rule: detail ? strOrNull(detail.rule ?? detail.rule_id ?? detail.gate) : null,
    cause,
    evidenceCount: events.length,
    provenanceGaps: graph.nodes.filter((n) => n.provenanceGap).map((n) => n.stage),
  };
}

function pickReason(arr: TraceEvent[]): string | null {
  for (let i = arr.length - 1; i >= 0; i--) {
    const cur = arr[i];
    if (!cur) continue;
    const d = cur.detail;
    if (!d) continue;
    const v = d.reason ?? d.rejection_reason ?? d.reason_code ?? d.message ?? d.error;
    if (typeof v === "string" && v) return v;
  }
  return null;
}

/** Latency budget view (§36): per-stage sums from measured latency_us. */
export interface LatencyBudget {
  total_us: number | null;
  byStage: Array<{ stage: string; us: number; n: number }>;
  missing: string[];
}

export function latencyBudget(events: TraceEvent[]): LatencyBudget {
  const sums = new Map<string, number>();
  const counts = new Map<string, number>();
  let total: number | null = null;
  const observed = new Set<string>();
  for (const e of events) {
    observed.add(e.stage);
    if (e.latency_us == null) continue;
    sums.set(e.stage, (sums.get(e.stage) ?? 0) + e.latency_us);
    counts.set(e.stage, (counts.get(e.stage) ?? 0) + 1);
  }
  const byStage = [...sums.entries()].map(([stage, us]) => ({
    stage,
    us,
    n: counts.get(stage) ?? 0,
  }));
  if (byStage.length) {
    total = byStage.reduce((a, b) => a + b.us, 0);
  }
  return { total_us: total, byStage, missing: [] };
}

/** Timeline offsets in microseconds from the trace's first event (§34). */
export interface TimelineEntry {
  event: TraceEvent;
  offset_us: number;
}

export function buildTimeline(events: TraceEvent[]): TimelineEntry[] {
  if (!events.length) return [];
  const firstEv = events[0];
  const t0 = firstEv ? firstEv.monotonic_ns : null;
  return events
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((e) => {
      let offset = 0;
      if (t0 != null && e.monotonic_ns != null) offset = Math.max(0, (e.monotonic_ns - t0) / 1000);
      return { event: e, offset_us: offset };
    });
}

/** Integrity checks mirrored client-side (§45) for live surfacing. */
export interface ClientIntegrity {
  duplicateEvents: number;
  missingSequence: number;
  missingParent: number;
  rootlessMidChain: number;
  executionWithoutDecision: number;
  mt5WithoutOrder: number;
  total: number;
}

export function checkIntegrity(events: TraceEvent[]): ClientIntegrity {
  const seen = new Set<string>();
  let duplicateEvents = 0;
  let missingSequence = 0;
  const seqs = events.map((e) => e.sequence).sort((a, b) => a - b);
  for (let i = 1; i < seqs.length; i++) if ((seqs[i] ?? 0) > (seqs[i - 1] ?? 0) + 1) missingSequence++;
  for (const e of events) {
    if (e.event_id && seen.has(e.event_id)) duplicateEvents++;
    seen.add(e.event_id);
  }
  const byTrace = new Map<string, TraceEvent[]>();
  for (const e of events) {
    if (!e.trace_id) continue;
    const arr = byTrace.get(e.trace_id) ?? [];
    arr.push(e);
    byTrace.set(e.trace_id, arr);
  }
  let missingParent = 0;
  let rootlessMidChain = 0;
  let executionWithoutDecision = 0;
  let mt5WithoutOrder = 0;
  for (const [, arr] of byTrace) {
    const ids = new Set(arr.map((e) => e.event_id));
    let sawParent = false;
    for (const e of arr) {
      if (e.parent_event_id && !ids.has(e.parent_event_id)) missingParent++;
      if (e.parent_event_id) sawParent = true;
      if (!e.parent_event_id && e.stage !== "MARKET" && arr.length > 1) rootlessMidChain++;
    }
    const stages = new Set(arr.map((e) => e.stage));
    if ((stages.has("EXECUTION") || stages.has("MT5")) && !stages.has("DECISION"))
      executionWithoutDecision++;
    if (stages.has("MT5") && !stages.has("EXECUTION") && !stages.has("ORDER")) mt5WithoutOrder++;
    if (!sawParent && arr.length > 1) rootlessMidChain += 0;
  }
  const total =
    duplicateEvents +
    missingSequence +
    missingParent +
    rootlessMidChain +
    executionWithoutDecision +
    mt5WithoutOrder;
  return {
    duplicateEvents,
    missingSequence,
    missingParent,
    rootlessMidChain,
    executionWithoutDecision,
    mt5WithoutOrder,
    total,
  };
}

/** Compare two bundles factually (§65) — no scoring, no "better". */
export interface TraceDiff {
  identical: boolean;
  changes: Array<{ field: string; a: unknown; b: unknown }>;
}

export function compareBundles(a: TraceBundle, b: TraceBundle): TraceDiff {
  const changes: Array<{ field: string; a: unknown; b: unknown }> = [];
  const sa = a.summary ?? {};
  const sb = b.summary ?? {};
  const fields = new Set([...Object.keys(sa), ...Object.keys(sb)]);
  for (const f of fields) {
    const va = (sa as Record<string, unknown>)[f];
    const vb = (sb as Record<string, unknown>)[f];
    if (JSON.stringify(va) !== JSON.stringify(vb)) changes.push({ field: f, a: va, b: vb });
  }
  const ea = a.events.map((e) => `${e.stage}:${e.status}`).join("|");
  const eb = b.events.map((e) => `${e.stage}:${e.status}`).join("|");
  if (ea !== eb) changes.push({ field: "stage_chain", a: ea, b: eb });
  return { identical: changes.length === 0, changes };
}

/**
 * MT5 reachability (§24): true ONLY when a gateway response event exists.
 * An internal "execution allowed" event is NOT broker reachability.
 */
export function mt5Reachability(events: TraceEvent[]): {
  reached: boolean;
  gateway: string | null;
  state: string | null;
  ticket: string | number | null;
  evidence: TraceEvent | null;
} {
  const resp = events.find((e) => e.event_type === "MT5_RESPONSE" || e.event_type === "GATEWAY_RESPONSE");
  if (!resp || !resp.detail) {
    return { reached: false, gateway: null, state: null, ticket: null, evidence: null };
  }
  const d = resp.detail;
  return {
    reached: true,
    gateway: strOrNull(d.gateway),
    state: resp.status,
    ticket: d.ticket != null ? (d.ticket as number | string) : null,
    evidence: resp,
  };
}

/** Coalesce a burst of same-stage visual events into one (§43). */
export function coalesceVisual(events: TraceEvent[]): { events: TraceEvent[]; coalesced: number } {
  if (events.length <= 1) return { events, coalesced: 0 };
  const out: TraceEvent[] = [];
  let coalesced = 0;
  for (const e of events) {
    const last = out[out.length - 1];
    if (
      last &&
      last.stage === e.stage &&
      last.trace_id === e.trace_id &&
      (e.event_type ?? "").endsWith("_PROGRESS") &&
      !e.terminal
    ) {
      coalesced++;
      continue;
    }
    out.push(e);
  }
  return { events: out, coalesced };
}

// ---------------------------------------------------------------- helpers

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v : null;
}
function arrOfNumbers(v: unknown): number[] | null {
  return Array.isArray(v) && v.every((x) => typeof x === "number")
    ? (v as number[])
    : null;
}
function fmtVal(v: unknown): string | null {
  if (v == null) return null;
  if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/** Stages whose events prove the observed chain — for the NOT REACHED view. */
export function observedStages(events: TraceEvent[]): string[] {
  const out: string[] = [];
  for (const e of events) if (!out.includes(e.stage)) out.push(e.stage);
  return out;
}

export const TRACE_STAGES: readonly TraceStage[] = [
  "MARKET",
  "FEATURES",
  "REGIME",
  "INFERENCE",
  "POLICY",
  "POST_POLICY",
  "DECISION",
  "RISK",
  "EXECUTION",
  "GATEWAY",
  "MT5",
  "ORDER",
] as const;
